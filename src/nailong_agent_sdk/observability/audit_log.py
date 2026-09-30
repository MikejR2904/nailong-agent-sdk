# Copyright (c) 2026 David Michael Indraputra

"""Readable, bounded audit transcripts outside BaseAgent model working memory."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from pydantic import Field, field_validator

from ..foundations.atomic_io import replace_atomic
from ..foundations.contracts import StrictModel
from ..foundations.errors import assert_no_hidden_reasoning, redact_secrets


class AuditLogEntry(StrictModel):
    schema_version: str = "agent-audit-log-v1"
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1)
    occurred_at_utc: str
    run_id: str = Field(min_length=1)
    task_id: str | None = None
    iteration: int | None = Field(default=None, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    previous_hash: str | None = None
    integrity_hash: str = ""

    @field_validator("payload")
    @classmethod
    def safe_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        assert_no_hidden_reasoning(payload)
        return payload


class AuditTranscriptStore:
    """Append-only JSONL audit records plus a rendered review transcript.

    It stores only bounded, redacted public inputs/outputs and result handles. It is
    intentionally external to ProjectState and never feeds raw history into ModelContext.
    """

    _append_locks: dict[Path, threading.RLock] = {}
    _append_locks_guard = threading.Lock()

    def __init__(
        self, root: Path, *, max_payload_chars: int = 8_192, max_open_handles: int = 32
    ) -> None:
        if max_payload_chars < 256:
            raise ValueError("max_payload_chars must be at least 256.")
        if max_open_handles < 1:
            raise ValueError("max_open_handles must be at least 1.")
        self._root = root.resolve() / ".agent-audit-logs"
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_payload_chars = max_payload_chars
        self._max_open_handles = max_open_handles
        self._lock = threading.RLock()
        # A bounded LRU avoids repeated opens for active runs without allowing a
        # long-lived host to retain one descriptor for every historical run.
        self._handles: OrderedDict[str, IO[bytes]] = OrderedDict()

    def _handle_for(self, run_id: str) -> IO[bytes]:
        cached = self._handles.get(run_id)
        if cached is not None:
            self._handles.move_to_end(run_id)
            return cached
        if len(self._handles) >= self._max_open_handles:
            _evicted_run_id, evicted_handle = self._handles.popitem(last=False)
            evicted_handle.close()
        handle = self._jsonl_path(run_id).open("a+b")
        self._handles[run_id] = handle
        return handle

    def append(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        task_id: str | None = None,
        iteration: int | None = None,
    ) -> AuditLogEntry:
        with self._lock:
            with self._append_transaction(run_id):
                # Append-mode writes use the true end even after tail inspection.
                handle = self._handle_for(run_id)
                handle.seek(0, os.SEEK_END)
                end = handle.tell()
                previous, sequence = _parse_tail(handle, end)
                prepared = AuditLogEntry(
                    sequence=sequence + 1,
                    event_type=event_type,
                    occurred_at_utc=datetime.now(UTC).isoformat(),
                    run_id=run_id,
                    task_id=task_id,
                    iteration=iteration,
                    payload=_bound_and_redact(payload, self._max_payload_chars),
                    previous_hash=previous,
                )
                complete = prepared.model_copy(
                    update={"integrity_hash": _hash(prepared.model_dump(mode="json"))}
                )
                handle.write(complete.model_dump_json().encode("utf-8"))
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
                return complete

    def list_entries(
        self,
        run_id: str,
        *,
        limit: int = 1_000,
        through_sequence: int | None = None,
    ) -> list[AuditLogEntry]:
        if limit < 1 or limit > 10_000:
            raise ValueError("Audit-log page size must be between 1 and 10000.")
        path = self._jsonl_path(run_id)
        if not path.exists():
            return []
        entries = [
            AuditLogEntry.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        if through_sequence is not None:
            entries = [entry for entry in entries if entry.sequence <= through_sequence]
        return entries[:limit]

    def snapshot_sequence(self, run_id: str) -> int:
        """Return the local append sequence used as a verification boundary."""

        with self._lock:
            _previous, sequence = self._tail(self._jsonl_path(run_id))
        return sequence

    def iter_entries(
        self, run_id: str, *, through_sequence: int | None = None
    ) -> Iterator[AuditLogEntry]:
        """Stream one complete transcript sequence through a fixed boundary."""

        boundary = self.snapshot_sequence(run_id) if through_sequence is None else through_sequence
        path = self._jsonl_path(run_id)
        if not path.exists():
            return
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    entry = AuditLogEntry.model_validate_json(line)
                    if entry.sequence > boundary:
                        return
                    yield entry

    def entry_count(self, run_id: str, *, through_sequence: int | None = None) -> int:
        """Return the complete persisted transcript length for an audit run."""

        return sum(1 for _ in self.iter_entries(run_id, through_sequence=through_sequence))

    def verify(self, run_id: str) -> bool:
        boundary = self.snapshot_sequence(run_id)
        return self._verify_entries(self.iter_entries(run_id, through_sequence=boundary))

    @staticmethod
    def _verify_entries(entries: Iterator[AuditLogEntry]) -> bool:
        previous: str | None = None
        for entry in entries:
            expected = _hash(
                entry.model_copy(update={"integrity_hash": ""}).model_dump(mode="json")
            )
            if entry.previous_hash != previous or entry.integrity_hash != expected:
                return False
            previous = entry.integrity_hash
        return True

    def render_markdown(self, run_id: str) -> Path:
        boundary = self.snapshot_sequence(run_id)
        entries = self.list_entries(run_id, through_sequence=boundary)
        entry_count = self.entry_count(run_id, through_sequence=boundary)
        integrity_valid = self._verify_entries(self.iter_entries(run_id, through_sequence=boundary))
        path = self._root / f"{_safe_name(run_id)}.transcript.md"
        lines = [
            f"# Agent audit transcript: `{run_id}`",
            "",
            f"Integrity chain valid: `{integrity_valid}`",
            f"Verified transcript entries: `{entry_count}`",
            f"Verified through sequence: `{boundary}`",
            f"Rendered entries: `{len(entries)}`",
            "",
        ]
        for entry in entries:
            lines.extend(
                [
                    f"## {entry.sequence}. {entry.event_type}",
                    "",
                    f"- Time: `{entry.occurred_at_utc}`",
                    f"- Iteration: `{entry.iteration}`",
                    f"- Integrity hash: `{entry.integrity_hash}`",
                    "",
                    "```json",
                    json.dumps(entry.payload, indent=2, sort_keys=True),
                    "```",
                    "",
                ]
            )
        _atomic_write(path, "\n".join(lines))
        return path

    def _tail(self, path: Path) -> tuple[str | None, int]:
        if not path.exists() or not path.stat().st_size:
            return None, 0
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            end = handle.tell()
            return _parse_tail(handle, end)

    def _jsonl_path(self, run_id: str) -> Path:
        return self._root / f"{_safe_name(run_id)}.jsonl"

    def close(self) -> None:
        """Close cached append handles; repeated calls are safe.

        Call this before deterministic removal or renaming of the audit root on
        platforms that retain open-file handles.
        """

        with self._lock:
            for handle in self._handles.values():
                handle.close()
            self._handles.clear()

    @contextmanager
    def _append_transaction(self, run_id: str) -> Iterator[None]:
        """Lock one tail-read/hash/write/fsync transaction for a run.

        A process-local lock coordinates separate store instances. A companion
        lock file extends serialization to other processes sharing this run root,
        preventing two writers from deriving the same sequence and predecessor.
        """

        path = self._jsonl_path(run_id).resolve()
        with self._local_append_lock(path), _interprocess_lock(path.with_suffix(".lock")):
            yield

    @classmethod
    @contextmanager
    def _local_append_lock(cls, path: Path) -> Iterator[None]:
        with cls._append_locks_guard:
            lock = cls._append_locks.setdefault(path, threading.RLock())
        with lock:
            yield


def _bound_and_redact(value: Any, max_chars: int) -> dict[str, Any]:
    redacted = redact_secrets(value)
    encoded = _canonical_json(redacted)
    if len(encoded) <= max_chars:
        if isinstance(redacted, dict):
            return redacted
        return {"value": redacted}
    return {
        "truncated": True,
        "content_hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "original_chars": len(encoded),
        "preview": encoded[:max_chars],
    }


def _parse_tail(handle: Any, end: int) -> tuple[str | None, int]:
    """Read the last line from an already-open handle positioned at ``end``."""

    if end == 0:
        return None, 0
    seek = max(0, end - 65_536)
    handle.seek(seek)
    lines = handle.read().decode("utf-8", errors="replace").splitlines()
    if not lines:
        return None, 0
    tail = AuditLogEntry.model_validate_json(lines[-1])
    return tail.integrity_hash, tail.sequence


@contextmanager
def _interprocess_lock(path: Path) -> Iterator[None]:
    """Serialize one audit append across processes without widening tool authority."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_." else "_" for character in value
    )


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    replace_atomic(temporary, path)
