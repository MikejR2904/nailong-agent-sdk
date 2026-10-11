# Copyright (c) 2026 David Michael Indraputra

"""Readable, bounded audit transcripts outside BaseAgent model working memory."""

from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from pydantic import Field, ValidationError, ValidationInfo, field_validator

from ..foundations.atomic_io import exclusive_file_lock, replace_atomic, unique_temporary_path
from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError, assert_no_hidden_reasoning, redact_secrets
from ..foundations.hashing import canonical_hash, canonical_json, sha256_hex
from ..foundations.identifiers import file_safe_name
from ..foundations.logging import get_logger
from ..foundations.text import assert_well_formed_text
from .telemetry_helpers import first_chain_break
from .telemetry_models import ChainBreak

_TAIL_CHUNK_BYTES = 65_536
_logger = get_logger("audit")


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

    @field_validator("event_type", "run_id", "task_id")
    @classmethod
    def identifiers_are_well_formed(cls, value: str | None, info: ValidationInfo) -> str | None:
        return None if value is None else assert_well_formed_text(value, info.field_name)

    @field_validator("payload")
    @classmethod
    def safe_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        assert_no_hidden_reasoning(payload)
        return payload


class AuditFootprint(StrictModel):
    run_id: str
    entries: int = Field(ge=0)
    bytes: int = Field(ge=0)
    head_hash: str | None = None
    last_at_utc: str | None = None


class AuditTranscriptStore:
    """Append-only JSONL audit records plus a rendered review transcript.

    It stores only bounded, redacted public inputs/outputs and result handles. It is
    intentionally external to ProjectState and never feeds raw history into ModelContext.
    """

    _append_locks: dict[Path, threading.RLock] = {}
    _append_locks_guard = threading.Lock()

    def __init__(
        self,
        root: Path,
        *,
        max_payload_chars: int = 8_192,
        max_open_handles: int = 32,
        read_only: bool = False,
    ) -> None:
        if max_payload_chars < 256:
            raise ValueError("max_payload_chars must be at least 256.")
        if max_open_handles < 1:
            raise ValueError("max_open_handles must be at least 1.")
        self._root = root.resolve() / ".agent-audit-logs"
        self._read_only = read_only
        if not read_only:
            self._root.mkdir(parents=True, exist_ok=True)
        self._max_payload_chars = max_payload_chars
        self._max_open_handles = max_open_handles
        self._lock = threading.RLock()
        # A bounded LRU avoids repeated opens for active runs without allowing a
        # long-lived host to retain one descriptor for every historical run.
        self._handles: OrderedDict[str, IO[bytes]] = OrderedDict()
        self._handle_evictions = 0
        self._eviction_reported = False

    @property
    def handle_evictions(self) -> int:
        return self._handle_evictions

    def _require_writable(self, operation: str) -> None:
        if self._read_only:
            raise RuntimeError(
                f'Audit store at "{self._root}" was opened read-only; "{operation}" is '
                "not available."
            )

    def _handle_for(self, run_id: str) -> IO[bytes]:
        cached = self._handles.get(run_id)
        if cached is not None:
            self._handles.move_to_end(run_id)
            return cached
        if len(self._handles) >= self._max_open_handles:
            _evicted_run_id, evicted_handle = self._handles.popitem(last=False)
            evicted_handle.close()
            self._count_eviction()
        handle = self._jsonl_path(run_id).open("a+b")
        self._handles[run_id] = handle
        return handle

    def _count_eviction(self) -> None:
        self._handle_evictions += 1
        if self._eviction_reported or self._handle_evictions < self._max_open_handles:
            return
        self._eviction_reported = True
        _logger.warning(
            "Audit transcript store evicted %d append handles: more than max_open_handles=%d "
            "runs append at once, so each append past that reopens its file. Raise "
            "max_open_handles (the MCP service reads AGENT_RUNTIME_AUDIT_MAX_OPEN_HANDLES).",
            self._handle_evictions,
            self._max_open_handles,
        )

    def append(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        task_id: str | None = None,
        iteration: int | None = None,
    ) -> AuditLogEntry:
        self._require_writable("append")
        with self._lock:
            with self._append_transaction(run_id):
                # Append-mode writes use the true end even after tail inspection.
                handle = self._handle_for(run_id)
                handle.seek(0, os.SEEK_END)
                end = handle.tell()
                tail = _read_tail_entry(handle, end, self._jsonl_path(run_id).name)
                _require_transcript_owner(tail, run_id, self._jsonl_path(run_id).name)
                prepared = AuditLogEntry(
                    sequence=(tail.sequence if tail is not None else 0) + 1,
                    event_type=event_type,
                    occurred_at_utc=datetime.now(UTC).isoformat(),
                    run_id=run_id,
                    task_id=task_id,
                    iteration=iteration,
                    payload=_bound_and_redact(payload, self._max_payload_chars),
                    previous_hash=tail.integrity_hash if tail is not None else None,
                )
                complete = prepared.model_copy(
                    update={"integrity_hash": canonical_hash(prepared.model_dump(mode="json"))}
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
        entries: list[AuditLogEntry] = []
        for entry in self.iter_entries(run_id, through_sequence=through_sequence):
            entries.append(entry)
            if len(entries) >= limit:
                break
        return entries

    def footprint(self, run_id: str) -> AuditFootprint:
        path = self._jsonl_path(run_id)
        rendered = self._root / f"{file_safe_name(run_id)}.transcript.md"
        size = sum(item.stat().st_size for item in (path, rendered) if item.is_file())
        tail = None
        with self._lock:
            if path.is_file() and path.stat().st_size:
                with path.open("rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    tail = _read_tail_entry(handle, handle.tell(), path.name)
                _require_transcript_owner(tail, run_id, path.name)
        return AuditFootprint(
            run_id=run_id,
            entries=tail.sequence if tail is not None else 0,
            bytes=size,
            head_hash=tail.integrity_hash if tail is not None else None,
            last_at_utc=tail.occurred_at_utc if tail is not None else None,
        )

    def prune_run(self, run_id: str, *, dry_run: bool = False) -> AuditFootprint:
        if not dry_run:
            self._require_writable("prune_run")
        footprint = self.footprint(run_id)
        if dry_run or footprint.bytes == 0:
            return footprint
        path = self._jsonl_path(run_id)
        rendered = self._root / f"{file_safe_name(run_id)}.transcript.md"
        with self._lock:
            with self._append_transaction(run_id):
                handle = self._handles.pop(run_id, None)
                if handle is not None:
                    handle.close()
                for target in (path, rendered):
                    target.unlink(missing_ok=True)
        path.with_suffix(".lock").unlink(missing_ok=True)
        return footprint

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
        with path.open(encoding="utf-8", newline="\n") as handle:
            for line in handle:
                if line.strip():
                    entry = AuditLogEntry.model_validate_json(line)
                    _require_transcript_owner(entry, run_id, path.name)
                    if entry.sequence > boundary:
                        return
                    yield entry

    def entry_count(self, run_id: str, *, through_sequence: int | None = None) -> int:
        """Return the complete persisted transcript length for an audit run."""

        return sum(1 for _ in self.iter_entries(run_id, through_sequence=through_sequence))

    def verify(self, run_id: str) -> bool:
        return self.chain_break(run_id) is None

    def chain_break(self, run_id: str) -> ChainBreak | None:
        boundary = self.snapshot_sequence(run_id)
        return _entries_chain_break(self.iter_entries(run_id, through_sequence=boundary))

    @staticmethod
    def _verify_entries(entries: Iterator[AuditLogEntry]) -> bool:
        return _entries_chain_break(entries) is None

    def render_markdown(self, run_id: str) -> Path:
        self._require_writable("render_markdown")
        boundary = self.snapshot_sequence(run_id)
        entries = self.list_entries(run_id, through_sequence=boundary)
        failure = _entries_chain_break(self.iter_entries(run_id, through_sequence=boundary))
        verified_count = _verified_entry_count(
            self.iter_entries(run_id, through_sequence=boundary), failure
        )
        integrity_valid = failure is None
        path = self._root / f"{file_safe_name(run_id)}.transcript.md"
        lines = [
            f"# Agent audit transcript: `{run_id}`",
            "",
            f"Integrity chain valid: `{integrity_valid}`",
            *([] if failure is None else [f"First integrity failure: {failure.message}"]),
            f"Verified transcript entries: `{verified_count}`",
            f"Verified through sequence: `{boundary if failure is None else failure.sequence - 1}`",
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
            tail = _read_tail_entry(handle, end, path.name)
        if tail is None:
            return None, 0
        return tail.integrity_hash, tail.sequence

    def _jsonl_path(self, run_id: str) -> Path:
        return self._root / f"{file_safe_name(run_id)}.jsonl"

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
        with (
            self._local_append_lock(path),
            exclusive_file_lock(path.with_suffix(".lock"), timeout_code="AUDIT_LOCK_TIMEOUT"),
        ):
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
    encoded = canonical_json(redacted)
    if len(encoded) <= max_chars:
        if isinstance(redacted, dict):
            return redacted
        return {"value": redacted}
    return {
        "truncated": True,
        "content_hash": sha256_hex(encoded),
        "original_chars": len(encoded),
        "preview": encoded[:max_chars],
    }


def _last_line(handle: IO[bytes], end: int) -> bytes:
    chunks: list[bytes] = []
    position = end
    seen_content = False
    while position > 0:
        size = min(_TAIL_CHUNK_BYTES, position)
        position -= size
        handle.seek(position)
        chunk = handle.read(size)
        if not seen_content:
            chunk = chunk.rstrip()
            if not chunk:
                continue
            seen_content = True
        boundary = chunk.rfind(b"\n")
        if boundary != -1:
            chunks.append(chunk[boundary + 1 :])
            break
        chunks.append(chunk)
    return b"".join(reversed(chunks))


def _read_tail_entry(handle: IO[bytes], end: int, label: str) -> AuditLogEntry | None:
    line = _last_line(handle, end)
    if not line:
        return None
    try:
        return AuditLogEntry.model_validate_json(line)
    except ValidationError as error:
        first = error.errors()[0]
        raise AgentSdkError(
            "AUDIT_TRANSCRIPT_CORRUPT",
            f'The last line of audit transcript "{label}" is not a valid audit entry '
            f"({first['type']}: {first['msg']}), so nothing can be appended after it until the "
            "transcript is repaired or archived.",
            {"transcript": label, "line_bytes": len(line)},
        ) from error


def _require_transcript_owner(entry: AuditLogEntry | None, run_id: str, label: str) -> None:
    if entry is not None and entry.run_id != run_id:
        raise AgentSdkError(
            "AUDIT_TRANSCRIPT_RUN_MISMATCH",
            f'Audit transcript "{label}" holds entries of run "{entry.run_id}", so run '
            f'"{run_id}" cannot share it: the two ids map to the same file on this file system.',
            {"transcript": label, "stored_run_id": entry.run_id, "requested_run_id": run_id},
        )


def _verified_entry_count(entries: Iterator[AuditLogEntry], failure: ChainBreak | None) -> int:
    count = 0
    try:
        for entry in entries:
            if failure is not None and entry.sequence >= failure.sequence:
                break
            count += 1
    except ValueError:
        pass
    return count


def _entries_chain_break(entries: Iterator[AuditLogEntry]) -> ChainBreak | None:
    return first_chain_break(
        entries,
        noun="audit entry",
        previous_attribute="previous_hash",
        expected_hash=lambda entry: canonical_hash(
            entry.model_copy(update={"integrity_hash": ""}).model_dump(mode="json")
        ),
    )


def _atomic_write(path: Path, content: str) -> None:
    temporary = unique_temporary_path(path)
    temporary.write_text(content, encoding="utf-8")
    replace_atomic(temporary, path)
