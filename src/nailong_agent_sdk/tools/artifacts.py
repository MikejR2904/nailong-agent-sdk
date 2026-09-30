# Copyright (c) 2026 David Michael Indraputra

"""Content-addressed artifacts and append-only write occurrences under one run root."""

from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from ..foundations.atomic_io import replace_atomic
from ..foundations.contracts import StrictModel


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


class ArtifactStore:
    """Persist artifacts below one root with immutable content and write attribution.

    ``artifact_id`` remains a content hash for compatibility and deduplication. Every
    registration additionally receives a unique immutable occurrence record, so the
    same bytes written by separate runs cannot overwrite historical attribution.
    """

    def __init__(self, run_root: Path) -> None:
        self._root = run_root.resolve()
        self._manifest_root = self._root / ".agent-artifacts"
        self._content_root = self._manifest_root / "content"
        self._occurrence_root = self._manifest_root / "occurrences"
        for directory in (
            self._root,
            self._manifest_root,
            self._content_root,
            self._occurrence_root,
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
            # Legacy records predate immutable content blobs. Preserve read
            # compatibility while refusing a changed path through hash validation.
            content = self._resolve_relative(record.relative_path).read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != record.sha256:
            raise ValueError(
                f'Artifact "{artifact_id}" content hash no longer matches its manifest.'
            )
        return content.decode("utf-8")

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        path = self._existing_manifest_path(artifact_id)
        if path is None:
            return None
        return ArtifactRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def get_occurrence(self, occurrence_id: str) -> ArtifactWriteOccurrence | None:
        path = self._occurrence_root / f"{occurrence_id}.json"
        if not path.is_file():
            return None
        return ArtifactWriteOccurrence.model_validate_json(path.read_text(encoding="utf-8"))

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

        base = self.read_text(base_artifact_id).splitlines(keepends=True)
        draft = self.read_text(draft_artifact_id).splitlines(keepends=True)
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
        del target  # Content is retained below .agent-artifacts, not by mutable output path.
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = f"sha256:{digest}"
        occurrence_id = f"occ-{uuid.uuid4().hex}"
        occurrence = ArtifactWriteOccurrence(
            occurrence_id=occurrence_id,
            artifact_id=artifact_id,
            relative_path=relative_path,
            sha256=digest,
            size_bytes=len(content),
            kind=kind,
            manifest=manifest,
            written_at_utc=datetime.now(UTC).isoformat(),
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
        return record

    def _content_path(self, digest: str) -> Path:
        return self._content_root / digest

    def _manifest_path(self, artifact_id: str) -> Path:
        # Keep logical content IDs portable on disk without changing their public form.
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
            target.relative_to(self._root)
        except ValueError as error:
            raise ValueError("Artifact path escapes the configured run root.") from error
        return target

    @staticmethod
    def _atomic_write_bytes(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_bytes(content)
        _replace_with_retry(temporary, target)


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_." else "_" for character in value
    )


def _replace_with_retry(temporary: Path, target: Path, *, attempts: int = 5) -> None:
    """Compatibility wrapper for the shared atomic publication primitive."""

    replace_atomic(temporary, target, attempts=attempts)
