# Copyright (c) 2026 David Michael Indraputra

"""Content-addressed archives for the original snapshots of compacted episodes.

An archive entry is the whole ``EpisodeRecord`` as it was while live, serialized with the
SDK's canonical JSON and named by the SHA-256 of that text. Reading verifies the name
against the bytes, so a corrupt or substituted entry is reported, never returned.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..foundations.atomic_io import fsync_directory, replace_atomic, unique_temporary_path
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import canonical_json, sha256_hex
from .episode_models import (
    ArchivedEpisode,
    ArchivedSummary,
    EpisodeRecord,
    SemanticEpisodeSummary,
)

ARCHIVE_SCHEMA = "episode-archive-v1"
ARCHIVE_ID_PREFIX = "archive-"
SUMMARY_ARCHIVE_SCHEMA = "summary-archive-v1"
SUMMARY_ID_PREFIX = "summary-archive-"
_HEX_DIGEST_LENGTH = 64


def snapshot_text(record: EpisodeRecord) -> str:
    return canonical_json(
        {"schema_version": ARCHIVE_SCHEMA, "record": record.model_dump(mode="json")}
    )


def archive_id_for(digest: str) -> str:
    return f"{ARCHIVE_ID_PREFIX}{digest}"


def _digest_of(archive_id: str, prefix: str = ARCHIVE_ID_PREFIX) -> str:
    digest = archive_id.removeprefix(prefix)
    if (
        not archive_id.startswith(prefix)
        or len(digest) != _HEX_DIGEST_LENGTH
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise AgentSdkError(
            "EPISODE_ARCHIVE_ID_INVALID",
            f'Archive id {archive_id!r} is not valid: archive ids look like "{prefix}" followed '
            f"by {_HEX_DIGEST_LENGTH} lowercase hex characters.",
        )
    return digest


def summary_snapshot_text(summary: SemanticEpisodeSummary) -> str:
    return canonical_json(
        {"schema_version": SUMMARY_ARCHIVE_SCHEMA, "summary": summary.model_dump(mode="json")}
    )


def summary_archive_id_for(digest: str) -> str:
    return f"{SUMMARY_ID_PREFIX}{digest}"


def decode_summary_snapshot(archive_id: str, text: str) -> SemanticEpisodeSummary:
    expected = _digest_of(archive_id, SUMMARY_ID_PREFIX)
    actual = sha256_hex(text)
    if actual != expected:
        raise AgentSdkError(
            "SUMMARY_ARCHIVE_CORRUPT",
            f'Archived summary "{archive_id}" does not match its content hash: the entry '
            f"hashes to {actual} but is named for {expected}.",
            {"archive_id": archive_id, "expected_hash": expected, "actual_hash": actual},
        )
    try:
        payload = json.loads(text)
        if payload.get("schema_version") != SUMMARY_ARCHIVE_SCHEMA:
            raise ValueError(f"schema_version is {payload.get('schema_version')!r}")
        summary = SemanticEpisodeSummary.model_validate(payload["summary"])
    except (ValueError, KeyError, AttributeError) as error:
        raise AgentSdkError(
            "SUMMARY_ARCHIVE_CORRUPT",
            f'Archived summary "{archive_id}" has a hash that matches but is not a valid '
            f"{SUMMARY_ARCHIVE_SCHEMA} snapshot: {type(error).__name__}: {error}",
            {"archive_id": archive_id},
        ) from error
    if not summary.intact():
        raise AgentSdkError(
            "SUMMARY_ARCHIVE_CORRUPT",
            f'Archived summary "{archive_id}" ({summary.summary_id}) fails its own integrity '
            "hash, so it was sealed in a state that no longer matches its content.",
            {"archive_id": archive_id, "summary_id": summary.summary_id},
        )
    return summary


def decode_snapshot(archive_id: str, text: str) -> EpisodeRecord:
    expected = _digest_of(archive_id)
    actual = sha256_hex(text)
    if actual != expected:
        raise AgentSdkError(
            "EPISODE_ARCHIVE_CORRUPT",
            f'Archived episode "{archive_id}" does not match its content hash: the entry '
            f"hashes to {actual} but is named for {expected}.",
            {"archive_id": archive_id, "expected_hash": expected, "actual_hash": actual},
        )
    try:
        payload = json.loads(text)
        if payload.get("schema_version") != ARCHIVE_SCHEMA:
            raise ValueError(f"schema_version is {payload.get('schema_version')!r}")
        return EpisodeRecord.model_validate(payload["record"])
    except (ValueError, KeyError, AttributeError) as error:
        raise AgentSdkError(
            "EPISODE_ARCHIVE_CORRUPT",
            f'Archived episode "{archive_id}" has a hash that matches but is not a valid '
            f"{ARCHIVE_SCHEMA} snapshot: {type(error).__name__}: {error}",
            {"archive_id": archive_id},
        ) from error


class InMemoryEpisodeArchive:
    """Process-local archive; its entries last as long as the object does."""

    def __init__(self) -> None:
        self._entries: dict[str, str] = {}
        self._summary_entries: dict[str, str] = {}

    def put(self, record: EpisodeRecord) -> ArchivedEpisode:
        text = snapshot_text(record)
        digest = sha256_hex(text)
        archive_id = archive_id_for(digest)
        self._write(archive_id, text)
        return ArchivedEpisode(
            archive_id=archive_id,
            episode_id=record.id,
            content_hash=digest,
            byte_count=len(text.encode("utf-8")),
        )

    def get(self, archive_id: str) -> EpisodeRecord:
        return decode_snapshot(archive_id, self._read(archive_id))

    def has(self, archive_id: str) -> bool:
        return archive_id in self._entries

    def put_summary(self, summary: SemanticEpisodeSummary) -> ArchivedSummary:
        if not summary.intact():
            raise AgentSdkError(
                "SUMMARY_ARCHIVE_UNSEALED",
                f'Summary "{summary.summary_id}" is not sealed or fails its integrity hash, so '
                "it was not archived.",
                {"summary_id": summary.summary_id},
            )
        text = summary_snapshot_text(summary)
        digest = sha256_hex(text)
        archive_id = summary_archive_id_for(digest)
        self._write_summary(archive_id, text)
        return ArchivedSummary(
            archive_id=archive_id,
            summary_id=summary.summary_id,
            content_hash=digest,
            byte_count=len(text.encode("utf-8")),
        )

    def get_summary(self, archive_id: str) -> SemanticEpisodeSummary:
        return decode_summary_snapshot(archive_id, self._read_summary(archive_id))

    def has_summary(self, archive_id: str) -> bool:
        return archive_id in self._summary_entries

    def _write_summary(self, archive_id: str, text: str) -> None:
        self._summary_entries[archive_id] = text

    def _read_summary(self, archive_id: str) -> str:
        _digest_of(archive_id, SUMMARY_ID_PREFIX)
        text = self._summary_entries.get(archive_id)
        if text is None:
            raise AgentSdkError(
                "SUMMARY_ARCHIVE_MISSING",
                f'No archived summary "{archive_id}" exists in this archive.',
                {"archive_id": archive_id},
            )
        return text

    def _write(self, archive_id: str, text: str) -> None:
        self._entries[archive_id] = text

    def _read(self, archive_id: str) -> str:
        _digest_of(archive_id)
        text = self._entries.get(archive_id)
        if text is None:
            raise AgentSdkError(
                "EPISODE_ARCHIVE_MISSING",
                f'No archived episode "{archive_id}" exists in this archive.',
                {"archive_id": archive_id},
            )
        return text


class FileEpisodeArchive(InMemoryEpisodeArchive):
    """One JSON file per snapshot under ``<run_root>/.agent-memory/archive``.

    A write is flushed and fsynced before it is published with an atomic rename, then read
    back and compared with the hash it was named for; ``put`` returns only after that.
    """

    def __init__(self, run_root: Path) -> None:
        super().__init__()
        self._directory = run_root.resolve() / ".agent-memory" / "archive"
        self._directory.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._directory

    def has(self, archive_id: str) -> bool:
        _digest_of(archive_id)
        return (self._directory / f"{archive_id}.json").is_file()

    def has_summary(self, archive_id: str) -> bool:
        _digest_of(archive_id, SUMMARY_ID_PREFIX)
        return (self._directory / "summaries" / f"{archive_id}.json").is_file()

    def _write(self, archive_id: str, text: str) -> None:
        self._publish(
            self._directory / f"{archive_id}.json", archive_id, _digest_of(archive_id), text
        )

    def _write_summary(self, archive_id: str, text: str) -> None:
        directory = self._directory / "summaries"
        directory.mkdir(parents=True, exist_ok=True)
        self._publish(
            directory / f"{archive_id}.json",
            archive_id,
            _digest_of(archive_id, SUMMARY_ID_PREFIX),
            text,
        )

    def _publish(self, target: Path, archive_id: str, digest: str, text: str) -> None:
        if target.is_file() and sha256_hex(target.read_text(encoding="utf-8")) == digest:
            return
        temporary = unique_temporary_path(target)
        try:
            with temporary.open("w", encoding="utf-8", newline="") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            replace_atomic(temporary, target)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise AgentSdkError(
                "EPISODE_ARCHIVE_WRITE_FAILED",
                f'Could not write archived entry "{archive_id}" to "{target}": '
                f"{type(error).__name__}: {error}",
                {"archive_id": archive_id},
            ) from error
        fsync_directory(target.parent)
        written = target.read_text(encoding="utf-8")
        if sha256_hex(written) != digest:
            raise AgentSdkError(
                "EPISODE_ARCHIVE_WRITE_FAILED",
                f'Archived entry "{archive_id}" read back from "{target}" does not match the '
                "hash it was named for; the original has not been released.",
                {"archive_id": archive_id},
            )

    def _read(self, archive_id: str) -> str:
        _digest_of(archive_id)
        target = self._directory / f"{archive_id}.json"
        if not target.is_file():
            raise AgentSdkError(
                "EPISODE_ARCHIVE_MISSING",
                f'No archived episode "{archive_id}" exists at "{target}".',
                {"archive_id": archive_id},
            )
        return target.read_text(encoding="utf-8")

    def _read_summary(self, archive_id: str) -> str:
        _digest_of(archive_id, SUMMARY_ID_PREFIX)
        target = self._directory / "summaries" / f"{archive_id}.json"
        if not target.is_file():
            raise AgentSdkError(
                "SUMMARY_ARCHIVE_MISSING",
                f'No archived summary "{archive_id}" exists at "{target}".',
                {"archive_id": archive_id},
            )
        return target.read_text(encoding="utf-8")
