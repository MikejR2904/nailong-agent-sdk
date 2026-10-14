# Copyright (c) 2026 David Michael Indraputra

"""Independent anchors of the retention tombstone chain, kept outside the run root.

The tombstone log chains its entries, so an edit in the middle is detected; the newest entries
can still be removed together without a trace, because nothing outside the log remembers how
long it was. An anchor records the length and head hash at one moment in a file the operator
keeps elsewhere, and verification against it detects a log that was truncated or rewritten.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import Field, ValidationError

from ..foundations.atomic_io import fsync_directory, replace_atomic, unique_temporary_path
from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import canonical_hash, sha256_hex

ANCHOR_SCHEMA = "agent-sdk-tombstone-anchor-v1"


class SealedEntry(Protocol):
    @property
    def integrity_hash(self) -> str: ...


class AnchorVerdict(StrEnum):
    MATCHES = "matches"
    ADVANCED = "advanced"
    TRUNCATED = "truncated"
    DIVERGED = "diverged"
    CHAIN_BROKEN = "chain-broken"
    UNREADABLE = "unreadable"


class TombstoneAnchor(StrictModel):
    schema_version: str = ANCHOR_SCHEMA
    run_root_label: str
    entries: int = Field(ge=0)
    head_hash: str | None = None
    anchored_at_utc: str
    integrity_hash: str = ""

    def computed_hash(self) -> str:
        return canonical_hash(
            self.model_copy(update={"integrity_hash": ""}).model_dump(mode="json")
        )

    def intact(self) -> bool:
        return bool(self.integrity_hash) and self.integrity_hash == self.computed_hash()


class AnchorVerification(StrictModel):
    verdict: AnchorVerdict
    anchored_entries: int = Field(default=0, ge=0)
    current_entries: int = Field(default=0, ge=0)
    detail: str

    @property
    def ok(self) -> bool:
        return self.verdict in {AnchorVerdict.MATCHES, AnchorVerdict.ADVANCED}


def run_root_label(run_root: Path) -> str:
    return sha256_hex(str(run_root.resolve()))[:16]


def anchor_of(
    run_root: Path, entries: Sequence[SealedEntry], *, now: datetime | None = None
) -> TombstoneAnchor:
    moment = now or datetime.now(UTC)
    draft = TombstoneAnchor(
        run_root_label=run_root_label(run_root),
        entries=len(entries),
        head_hash=entries[-1].integrity_hash if entries else None,
        anchored_at_utc=moment.isoformat(),
    )
    return draft.model_copy(update={"integrity_hash": draft.computed_hash()})


def read_anchor(path: Path) -> TombstoneAnchor:
    try:
        anchor = TombstoneAnchor.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as error:
        raise AgentSdkError(
            "RETENTION_ANCHOR_UNREADABLE",
            f'The tombstone anchor "{path}" cannot be read: {type(error).__name__}: {error}',
            {"path": str(path)},
        ) from error
    if anchor.schema_version != ANCHOR_SCHEMA:
        raise AgentSdkError(
            "RETENTION_ANCHOR_UNREADABLE",
            f'The tombstone anchor "{path}" has schema_version "{anchor.schema_version}", '
            f'expected "{ANCHOR_SCHEMA}".',
            {"path": str(path)},
        )
    if not anchor.intact():
        raise AgentSdkError(
            "RETENTION_ANCHOR_UNREADABLE",
            f'The tombstone anchor "{path}" fails its own integrity hash, so it was edited '
            "after it was written.",
            {"path": str(path)},
        )
    return anchor


def compare_anchor(anchor: TombstoneAnchor, entries: Sequence[SealedEntry]) -> AnchorVerification:
    current = len(entries)
    if current < anchor.entries:
        return AnchorVerification(
            verdict=AnchorVerdict.TRUNCATED,
            anchored_entries=anchor.entries,
            current_entries=current,
            detail=(
                f"The anchor recorded {anchor.entries} tombstones ending at "
                f"{_short(anchor.head_hash)} but the log holds {current}: "
                f"{anchor.entries - current} newest entries were removed."
            ),
        )
    present = entries[anchor.entries - 1].integrity_hash if anchor.entries else None
    if present != anchor.head_hash:
        return AnchorVerification(
            verdict=AnchorVerdict.DIVERGED,
            anchored_entries=anchor.entries,
            current_entries=current,
            detail=(
                f"Tombstone {anchor.entries} is {_short(present)} but the anchor recorded "
                f"{_short(anchor.head_hash)}: the log was rewritten after it was anchored."
            ),
        )
    if current == anchor.entries:
        return AnchorVerification(
            verdict=AnchorVerdict.MATCHES,
            anchored_entries=anchor.entries,
            current_entries=current,
            detail=f"The log holds exactly the {current} anchored tombstones.",
        )
    return AnchorVerification(
        verdict=AnchorVerdict.ADVANCED,
        anchored_entries=anchor.entries,
        current_entries=current,
        detail=(
            f"The log extends the anchored {anchor.entries} tombstones by "
            f"{current - anchor.entries}; anchor it again to cover them."
        ),
    )


def write_anchor(anchor: TombstoneAnchor, destination: Path) -> None:
    target = destination.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = unique_temporary_path(target)
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            stream.write(anchor.model_dump_json(indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        replace_atomic(temporary, target)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise AgentSdkError(
            "RETENTION_ANCHOR_WRITE_FAILED",
            f'Could not write the tombstone anchor "{destination}": '
            f"{type(error).__name__}: {error}",
            {"destination": str(destination)},
        ) from error
    fsync_directory(target.parent)


def _short(digest: str | None) -> str:
    return "no entries" if digest is None else digest[:12]
