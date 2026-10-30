# Copyright (c) 2026 David Michael Indraputra

"""Signed anchors of a graph run's record, kept outside the run root.

A run record carries a hash of its history and a hash of itself, but both are unkeyed: whoever
can write the run root can re-save an altered or shortened record with consistent hashes. An
anchor signs the history length, the hash of that history prefix and the record hash at one
moment, in an append-only sink the operator keeps elsewhere. A later check replays the history
to the anchored length and compares: fewer entries is truncation, a different prefix hash is a
rewrite, and the same length with a different record hash means a result was changed without
touching the history.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import Field, ValidationError

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import canonical_json
from ..observability.run_checkpoints import CheckpointSigner, CheckpointSink
from .run_state_store import RunStateStore

ANCHOR_SCHEMA = "agent-sdk-run-record-anchor-v1"


class AnchorTarget(Protocol):
    """Where and with what key anchors are written: a ``RunHeadCheckpointer`` fits."""

    @property
    def sink(self) -> CheckpointSink: ...

    @property
    def signer(self) -> CheckpointSigner: ...


def run_anchor_name(run_id: str) -> str:
    return f"{run_id}.run"


class RunAnchorVerdict(StrEnum):
    MATCHES = "matches"
    ADVANCED = "advanced"
    TRUNCATED = "truncated"
    DIVERGED = "diverged"
    CHAIN_BROKEN = "chain-broken"
    SIGNATURE_INVALID = "signature-invalid"
    NO_ANCHOR = "no-anchor"
    UNREADABLE = "unreadable"


class RunRecordAnchor(StrictModel):
    schema_version: str = ANCHOR_SCHEMA
    run_id: str = Field(min_length=1)
    history_entries: int = Field(ge=0)
    history_hash: str
    run_hash: str
    anchored_at_utc: str
    signer_id: str
    signature: str = ""

    def signed_message(self) -> bytes:
        unsigned = self.model_copy(update={"signature": ""}).model_dump(mode="json")
        return canonical_json(unsigned).encode("utf-8")


class RunAnchorVerification(StrictModel):
    verdict: RunAnchorVerdict
    anchors: int = Field(default=0, ge=0)
    anchored_entries: int = Field(default=0, ge=0)
    current_entries: int = Field(default=0, ge=0)
    detail: str

    @property
    def ok(self) -> bool:
        return self.verdict in {RunAnchorVerdict.MATCHES, RunAnchorVerdict.ADVANCED}


def anchor_run_record(
    store: RunStateStore,
    sink: CheckpointSink,
    run_id: str,
    signer: CheckpointSigner,
    *,
    now: datetime | None = None,
) -> RunRecordAnchor:
    """Sign and append the run's history length and hashes; a run that fails its own check is
    never anchored."""

    try:
        record = store.load(run_id)
    except ValueError as error:
        raise AgentSdkError(
            "RUN_ANCHOR_CHAIN_BROKEN",
            f'The record of run "{run_id}" is not intact and was not anchored: {error}',
            {"run_id": run_id},
        ) from error
    if record.history_integrity_hash is None:
        raise AgentSdkError(
            "RUN_ANCHOR_LEGACY_RECORD",
            f'Run "{run_id}" is stored as a legacy full snapshot with no history hash, so there '
            "is no history prefix to anchor; save it once with the current store first.",
            {"run_id": run_id},
        )
    draft = RunRecordAnchor(
        run_id=run_id,
        history_entries=record.history_entry_count,
        history_hash=record.history_integrity_hash,
        run_hash=record.run_hash,
        anchored_at_utc=(now or datetime.now(UTC)).isoformat(),
        signer_id=signer.signer_id,
    )
    anchor = draft.model_copy(update={"signature": signer.sign(draft.signed_message())})
    sink.append(run_anchor_name(run_id), anchor.model_dump_json())
    return anchor


def verify_run_anchor(
    store: RunStateStore,
    sink: CheckpointSink,
    name: str,
    run_id: str,
    signer: CheckpointSigner,
) -> RunAnchorVerification:
    """Compare a run's record with every anchor kept under ``name``; the newest decides."""

    def verdict(kind: RunAnchorVerdict, detail: str, **counts: int) -> RunAnchorVerification:
        return RunAnchorVerification(verdict=kind, detail=detail, **counts)

    try:
        lines = sink.read_all(name)
    except (OSError, AgentSdkError) as error:
        return verdict(
            RunAnchorVerdict.UNREADABLE,
            f'The anchors of "{name}" cannot be read at {sink.location(name)}: '
            f"{type(error).__name__}: {error}",
        )
    if not lines:
        return verdict(
            RunAnchorVerdict.NO_ANCHOR,
            f'No anchor "{name}" exists at {sink.location(name)}, so the run record is '
            "tamper-evident only: a consistently re-saved record cannot be told from the original.",
        )
    anchors: list[RunRecordAnchor] = []
    for number, line in enumerate(lines, start=1):
        try:
            anchor = RunRecordAnchor.model_validate_json(line)
        except (ValueError, ValidationError) as error:
            return verdict(
                RunAnchorVerdict.UNREADABLE,
                f'Anchor {number} of "{name}" is not a valid anchor record: '
                f"{type(error).__name__}: {error}",
            )
        if (
            anchor.schema_version != ANCHOR_SCHEMA
            or anchor.run_id != run_id
            or not signer.verify(anchor.signed_message(), anchor.signature)
        ):
            return verdict(
                RunAnchorVerdict.SIGNATURE_INVALID,
                f'Anchor {number} of "{name}" does not carry a valid signature from signer '
                f'"{signer.signer_id}" for run "{run_id}": it was edited or signed with another '
                "key.",
            )
        anchors.append(anchor)
    try:
        record = store.load(run_id)
    except ValueError as error:
        return verdict(
            RunAnchorVerdict.CHAIN_BROKEN,
            f"The run record fails its own integrity check: {error}",
            anchors=len(anchors),
        )
    current = record.history_entry_count
    for number, anchor in enumerate(anchors, start=1):
        counts = {
            "anchors": len(anchors),
            "anchored_entries": anchor.history_entries,
            "current_entries": current,
        }
        if current < anchor.history_entries:
            return verdict(
                RunAnchorVerdict.TRUNCATED,
                f"Anchor {number} recorded {anchor.history_entries} history entries but the "
                f"run holds {current}: {anchor.history_entries - current} newest entries were "
                "removed.",
                **counts,
            )
        _, prefix = store.history_prefix(run_id, anchor.history_entries)
        if prefix != anchor.history_hash:
            return verdict(
                RunAnchorVerdict.DIVERGED,
                f'The first {anchor.history_entries} history entries of run "{run_id}" hash to '
                f"{prefix[:12]} but anchor {number} recorded {anchor.history_hash[:12]}: the "
                "history was rewritten after it was anchored.",
                **counts,
            )
    newest = anchors[-1]
    counts = {
        "anchors": len(anchors),
        "anchored_entries": newest.history_entries,
        "current_entries": current,
    }
    if current == newest.history_entries:
        if record.run_hash != newest.run_hash:
            return verdict(
                RunAnchorVerdict.DIVERGED,
                f'The history of run "{run_id}" is the anchored one but the record hashes to '
                f"{record.run_hash[:12]} where the anchor recorded {newest.run_hash[:12]}: a "
                "result or status was changed after it was anchored.",
                **counts,
            )
        return verdict(
            RunAnchorVerdict.MATCHES,
            f"The run holds exactly the {current} anchored history entries and the anchored "
            "record.",
            **counts,
        )
    return verdict(
        RunAnchorVerdict.ADVANCED,
        f"The run extends the anchored {newest.history_entries} history entries by "
        f"{current - newest.history_entries}; anchor it again to cover them.",
        **counts,
    )
