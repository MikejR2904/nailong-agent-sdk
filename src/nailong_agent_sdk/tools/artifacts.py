# Copyright (c) 2026 David Michael Indraputra

"""Content-addressed artifacts, write occurrences and the ordered history of each path."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from ..foundations.atomic_io import exclusive_file_lock, replace_atomic, unique_temporary_path
from ..foundations.contracts import StrictModel
from ..foundations.hashing import canonical_hash
from ..foundations.paths import relative_to_base
from ..foundations.text import split_lines

_ARTIFACT_ID = re.compile(r"sha256:[0-9a-f]{64}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_OCCURRENCE_ID = re.compile(r"occ-[0-9a-f]{32}")
_TAIL_CHUNK_BYTES = 4_096
_INDEX_MARKER = ".indexed"
_WRITE_LOG = "writes.jsonl"
_WRITE_LOCK = ".write.lock"
MAX_LISTED_PATHS = 200
_TAILS: dict[str, tuple[int, int, str | None]] = {}


class ArtifactRecord(StrictModel):
    """Content identity with the occurrence that produced this returned record."""

    artifact_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    kind: str = Field(min_length=1)
    manifest: dict[str, Any] = Field(default_factory=dict)
    occurrence_id: str | None = None


class ArtifactWriteOccurrence(StrictModel):
    """Immutable attribution for one artifact write, independent of content identity."""

    occurrence_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    kind: str = Field(min_length=1)
    manifest: dict[str, Any] = Field(default_factory=dict)
    written_at_utc: str
    write_sequence: int | None = Field(default=None, ge=1)


class ArtifactVersion(StrictModel):
    """One write of one path: its place in that path's history and in the store's write order."""

    sequence: int = Field(ge=1)
    write_sequence: int = Field(ge=1)
    occurrence_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    kind: str = Field(min_length=1)
    manifest: dict[str, Any] = Field(default_factory=dict)
    written_at_utc: str


class HistoryCheck(StrictModel):
    """Whether the write log is intact, each record chained to the one before it."""

    ok: bool
    records: int = Field(ge=0)
    chained: int = Field(ge=0)
    head: str | None = None
    broken_at: int | None = Field(default=None, ge=1)
    reason: str | None = None


class ArtifactStore:
    """Persist artifacts below one root with immutable content and write attribution.

    ``artifact_id`` remains a content hash for compatibility and deduplication. Every
    registration additionally receives a unique immutable occurrence record, so the
    same bytes written by separate runs cannot overwrite historical attribution. All
    writes are also appended to one log, under a lock that orders concurrent writers,
    so the versions of a path and who wrote each can be read back in order.
    """

    def __init__(self, run_root: Path, *, read_only: bool = False) -> None:
        self._root = run_root.resolve()
        self._read_only = read_only
        self._manifest_root = self._root / ".agent-artifacts"
        self._content_root = self._manifest_root / "content"
        self._occurrence_root = self._manifest_root / "occurrences"
        self._history_root = self._manifest_root / "history"
        if not read_only:
            for directory in (
                self._root,
                self._manifest_root,
                self._content_root,
                self._occurrence_root,
                self._history_root,
            ):
                directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        return self._root

    def write_text(
        self,
        relative_path: str,
        content: str,
        *,
        kind: str = "draft",
        manifest: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        self._require_writable("write_text")
        target = self._resolve_relative(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode("utf-8")
        with self._lock:
            self._atomic_write_bytes(target, encoded)
            return self._register(target, relative_path, encoded, kind, manifest or {})

    def register_existing(
        self,
        relative_path: str,
        *,
        kind: str,
        manifest: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        self._require_writable("register_existing")
        target = self._resolve_relative(relative_path)
        if not target.is_file():
            raise ValueError(f'Declared artifact "{relative_path}" does not exist.')
        content = target.read_bytes()
        with self._lock:
            return self._register(target, relative_path, content, kind, manifest or {})

    def read_text(self, artifact_id: str) -> str:
        record = self.get(artifact_id)
        if record is None:
            raise ValueError(f'Artifact "{artifact_id}" is unknown.')
        content_path = self._content_path(record.sha256)
        if content_path.is_file():
            content = content_path.read_bytes()
        else:
            content = self._resolve_relative(record.relative_path).read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != record.sha256:
            raise ValueError(
                f'Artifact "{artifact_id}" content hash no longer matches its manifest.'
            )
        return content.decode("utf-8")

    def read_bytes(self, sha256: str) -> bytes:
        if _DIGEST.fullmatch(sha256) is None:
            raise ValueError(f'"{sha256}" is not a SHA-256 digest of 64 lowercase hex characters.')
        content_path = self._content_path(sha256)
        if not content_path.is_file():
            raise ValueError(
                f'Artifact content "{sha256}" is not stored under "{self._content_root}".'
            )
        content = content_path.read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != sha256:
            raise ValueError(
                f'Artifact content stored as "{sha256}" now hashes to "{actual}"; it was changed.'
            )
        return content

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        if _ARTIFACT_ID.fullmatch(artifact_id) is None:
            return None
        path = self._existing_manifest_path(artifact_id)
        if path is None:
            return None
        return ArtifactRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def get_occurrence(self, occurrence_id: str) -> ArtifactWriteOccurrence | None:
        if _OCCURRENCE_ID.fullmatch(occurrence_id) is None:
            return None
        path = self._occurrence_root / f"{occurrence_id}.json"
        if not path.is_file():
            return None
        return ArtifactWriteOccurrence.model_validate_json(path.read_text(encoding="utf-8"))

    def history(self, relative_path: str) -> list[ArtifactVersion]:
        key = self._history_key_for(relative_path)
        if self._indexed():
            return _versions_in_log(self._write_log, key)
        return [
            item
            for item in self._legacy_versions()
            if self._history_key_for(item.relative_path) == key
        ]

    def verify_history(self) -> HistoryCheck:
        return _check_log(self._write_log)

    def log_hashes(self) -> list[str | None]:
        return [record.get("integrity_hash") for record in _log_records(self._write_log)]

    def paths(self, *, limit: int = MAX_LISTED_PATHS) -> list[str]:
        spelled: dict[str, str] = {}
        if self._indexed():
            for record in _log_records(self._write_log):
                spelled.setdefault(str(record["path_key"]), str(record["relative_path"]))
        else:
            for item in self._legacy_versions():
                spelled.setdefault(self._history_key_for(item.relative_path), item.relative_path)
        return sorted(spelled.values())[:limit]

    def occurrence_matches_task_draft(
        self,
        occurrence_id: str,
        artifact_id: str,
        *,
        run_id: str,
        node_id: str,
        task_id: str,
        declared_output_paths: tuple[str, ...],
    ) -> bool:
        """Return whether an immutable occurrence proves this task wrote the draft."""

        occurrence = self.get_occurrence(occurrence_id)
        if occurrence is None or occurrence.artifact_id != artifact_id:
            return False
        manifest = occurrence.manifest
        return (
            occurrence.relative_path in declared_output_paths
            and manifest.get("run_id") == run_id
            and manifest.get("node_id") == node_id
            and manifest.get("task_id") == task_id
        )

    def diff(self, base_artifact_id: str, draft_artifact_id: str) -> dict[str, Any]:
        import difflib

        base = split_lines(self.read_text(base_artifact_id), keepends=True)
        draft = split_lines(self.read_text(draft_artifact_id), keepends=True)
        return {
            "base_artifact_id": base_artifact_id,
            "draft_artifact_id": draft_artifact_id,
            "unified_diff": "".join(
                difflib.unified_diff(
                    base, draft, fromfile=base_artifact_id, tofile=draft_artifact_id
                )
            ),
        }

    def _register(
        self,
        target: Path,
        relative_path: str,
        content: bytes,
        kind: str,
        manifest: dict[str, Any],
    ) -> ArtifactRecord:
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = f"sha256:{digest}"
        self._ensure_indexed()
        with exclusive_file_lock(
            self._history_root / _WRITE_LOCK, timeout_code="ARTIFACT_HISTORY_LOCK_TIMEOUT"
        ):
            last_sequence, previous_hash = self._tail_state()
            write_sequence = last_sequence + 1
            occurrence_id = f"occ-{uuid.uuid4().hex}"
            written_at = datetime.now(UTC).isoformat()
            occurrence = ArtifactWriteOccurrence(
                occurrence_id=occurrence_id,
                artifact_id=artifact_id,
                relative_path=relative_path,
                sha256=digest,
                size_bytes=len(content),
                kind=kind,
                manifest=manifest,
                written_at_utc=written_at,
                write_sequence=write_sequence,
            )
            content_target = self._content_path(digest)
            if not content_target.exists():
                self._atomic_write_bytes(content_target, content)
            record = ArtifactRecord(
                artifact_id=artifact_id,
                relative_path=relative_path,
                sha256=digest,
                size_bytes=len(content),
                kind=kind,
                manifest=manifest,
                occurrence_id=occurrence_id,
            )
            canonical_path = self._manifest_path(artifact_id)
            if self._existing_manifest_path(artifact_id) is None:
                self._atomic_write_bytes(
                    canonical_path, record.model_dump_json(indent=2).encode("utf-8")
                )
            self._atomic_write_bytes(
                self._occurrence_root / f"{occurrence_id}.json",
                occurrence.model_dump_json(indent=2).encode("utf-8"),
            )
            line, integrity_hash = _log_line(occurrence, self._history_key(target), previous_hash)
            _append_line(self._write_log, line)
            _TAILS[str(self._write_log)] = (
                self._write_log.stat().st_size,
                write_sequence,
                integrity_hash,
            )
        return record

    def _tail_state(self) -> tuple[int, str | None]:
        try:
            size = self._write_log.stat().st_size
        except FileNotFoundError:
            return 0, None
        known = _TAILS.get(str(self._write_log))
        if known is not None and known[0] == size:
            return known[1], known[2]
        tail = _repair_and_read_tail(self._write_log)
        if tail is None:
            return 0, None
        return int(tail["write_sequence"]), tail.get("integrity_hash")

    def _indexed(self) -> bool:
        return (self._history_root / _INDEX_MARKER).is_file()

    def _ensure_indexed(self) -> None:
        marker = self._history_root / _INDEX_MARKER
        if marker.is_file():
            return
        with exclusive_file_lock(
            self._history_root / _WRITE_LOCK, timeout_code="ARTIFACT_HISTORY_LOCK_TIMEOUT"
        ):
            if marker.is_file():
                return
            self._write_log.unlink(missing_ok=True)
            _TAILS.pop(str(self._write_log), None)
            previous_hash: str | None = None
            for item in self._legacy_occurrences():
                line, previous_hash = _log_line(
                    item, self._history_key_for(item.relative_path), previous_hash
                )
                _append_line(self._write_log, line)
            marker.write_text("1", encoding="utf-8")

    def _legacy_occurrences(self) -> list[ArtifactWriteOccurrence]:
        if not self._occurrence_root.is_dir():
            return []
        occurrences = [
            ArtifactWriteOccurrence.model_validate_json(path.read_text("utf-8"))
            for path in self._occurrence_root.glob("occ-*.json")
        ]
        occurrences.sort(key=lambda item: (item.written_at_utc, item.occurrence_id))
        return [
            item.model_copy(update={"write_sequence": index})
            for index, item in enumerate(occurrences, start=1)
        ]

    def _legacy_versions(self) -> list[ArtifactVersion]:
        ordinals: dict[str, int] = {}
        versions: list[ArtifactVersion] = []
        for item in self._legacy_occurrences():
            key = self._history_key_for(item.relative_path)
            ordinals[key] = ordinals.get(key, 0) + 1
            versions.append(_version_of(item, ordinals[key]))
        return versions

    def _require_writable(self, operation: str) -> None:
        if self._read_only:
            raise RuntimeError(
                f'Artifact store at "{self._root}" was opened read-only; "{operation}" is not '
                "available."
            )

    @property
    def _write_log(self) -> Path:
        return self._history_root / _WRITE_LOG

    def _history_key_for(self, relative_path: str) -> str:
        return self._history_key(self._resolve_relative(relative_path))

    def _history_key(self, target: Path) -> str:
        key = relative_to_base(target, self._root).as_posix()
        return key.lower() if os.name == "nt" else key

    def _content_path(self, digest: str) -> Path:
        return self._content_root / digest

    def _manifest_path(self, artifact_id: str) -> Path:
        return self._manifest_root / f"{_safe_name(artifact_id)}.json"

    def _existing_manifest_path(self, artifact_id: str) -> Path | None:
        portable = self._manifest_path(artifact_id)
        if portable.is_file():
            return portable
        legacy = self._manifest_root / f"{artifact_id}.json"
        return legacy if legacy.is_file() else None

    def _resolve_relative(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute() or not relative_path.strip():
            raise ValueError("Artifact paths must be non-empty and relative to the run root.")
        target = (self._root / candidate).resolve()
        try:
            relative_to_base(target, self._root)
        except ValueError as error:
            raise ValueError("Artifact path escapes the configured run root.") from error
        return target

    @staticmethod
    def _atomic_write_bytes(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = unique_temporary_path(target)
        temporary.write_bytes(content)
        _replace_with_retry(temporary, target)


def _version_of(item: ArtifactWriteOccurrence, sequence: int) -> ArtifactVersion:
    return ArtifactVersion(
        sequence=sequence,
        write_sequence=item.write_sequence or sequence,
        occurrence_id=item.occurrence_id,
        artifact_id=item.artifact_id,
        relative_path=item.relative_path,
        sha256=item.sha256,
        size_bytes=item.size_bytes,
        kind=item.kind,
        manifest=item.manifest,
        written_at_utc=item.written_at_utc,
    )


def _log_line(
    item: ArtifactWriteOccurrence, key: str, previous_hash: str | None
) -> tuple[str, str]:
    body = {
        "write_sequence": item.write_sequence,
        "path_key": key,
        "occurrence_id": item.occurrence_id,
        "artifact_id": item.artifact_id,
        "relative_path": item.relative_path,
        "sha256": item.sha256,
        "size_bytes": item.size_bytes,
        "kind": item.kind,
        "manifest": item.manifest,
        "written_at_utc": item.written_at_utc,
        "previous_hash": previous_hash,
    }
    integrity_hash = canonical_hash(body)
    line = json.dumps(
        {**body, "integrity_hash": integrity_hash}, separators=(",", ":"), ensure_ascii=False
    )
    return line, integrity_hash


def _complete_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").split("\n")[:-1]


def _log_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number, line in enumerate(_complete_lines(path), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as error:
            raise ValueError(
                f'Line {number} of the artifact write log "{path.name}" is not a write record: '
                f"{type(error).__name__}: {error}"
            ) from error
        records.append(record)
    return records


def _versions_in_log(path: Path, key: str) -> list[ArtifactVersion]:
    needle = f'"path_key":{json.dumps(key, ensure_ascii=False)}'
    versions: list[ArtifactVersion] = []
    for number, line in enumerate(_complete_lines(path), start=1):
        if needle not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError as error:
            raise ValueError(
                f'Line {number} of the artifact write log "{path.name}" is not a write record: '
                f"{type(error).__name__}: {error}"
            ) from error
        if record.get("path_key") != key:
            continue
        for name in ("path_key", "previous_hash", "integrity_hash"):
            record.pop(name, None)
        versions.append(ArtifactVersion(sequence=len(versions) + 1, **record))
    return versions


def _check_log(path: Path) -> HistoryCheck:
    records = chained = previous_sequence = 0
    previous_hash: str | None = None
    for number, line in enumerate(_complete_lines(path), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as error:
            problem: str | None = f"is not a write record: {type(error).__name__}: {error}"
        else:
            problem = _record_problem(record, previous_sequence, previous_hash, chained)
        if problem is not None:
            return HistoryCheck(
                ok=False,
                records=records,
                chained=chained,
                head=previous_hash,
                broken_at=number,
                reason=f'Line {number} of the artifact write log "{path.name}" {problem}.',
            )
        records += 1
        previous_sequence = record["write_sequence"]
        if record.get("integrity_hash") is not None:
            chained += 1
            previous_hash = record["integrity_hash"]
    return HistoryCheck(ok=True, records=records, chained=chained, head=previous_hash)


def _record_problem(
    record: Any, previous_sequence: int, previous_hash: str | None, chained: int
) -> str | None:
    if not isinstance(record, dict):
        return "is not a write record: a JSON object was expected"
    sequence = record.get("write_sequence")
    if sequence != previous_sequence + 1:
        return f"has write_sequence {sequence} after {previous_sequence}"
    stored = record.get("integrity_hash")
    if stored is None:
        if chained:
            return "has no integrity_hash although the records before it are chained"
        return None
    if record.get("previous_hash") != previous_hash:
        return (
            f"has previous_hash {record.get('previous_hash')!r}, not the hash of the record "
            f"before it ({previous_hash!r})"
        )
    body = {name: value for name, value in record.items() if name != "integrity_hash"}
    if canonical_hash(body) != stored:
        return "has an integrity_hash that does not match its content"
    return None


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(line.encode("utf-8") + b"\n")


def _repair_and_read_tail(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        end = stream.tell()
        if end == 0:
            return None
        stream.seek(end - 1)
        torn = stream.read(1) != b"\n"
        position, buffer = end, b""
        while position > 0 and buffer.count(b"\n") < 2:
            size = min(_TAIL_CHUNK_BYTES, position)
            position -= size
            stream.seek(position)
            buffer = stream.read(size) + buffer
    complete = buffer[: buffer.rfind(b"\n") + 1]
    if torn:
        with path.open("r+b") as stream:
            stream.truncate(end - len(buffer) + len(complete))
    lines = [line for line in complete.split(b"\n") if line.strip()]
    return json.loads(lines[-1]) if lines else None


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_." else "_" for character in value
    )


def _replace_with_retry(temporary: Path, target: Path, *, attempts: int = 5) -> None:
    """Compatibility wrapper for the shared atomic publication primitive."""

    replace_atomic(temporary, target, attempts=attempts)
