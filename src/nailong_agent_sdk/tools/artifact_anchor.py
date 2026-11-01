# Copyright (c) 2026 David Michael Indraputra

"""Signed anchors of the artifact write log, kept outside the workspace it describes.

The write log chains its records with an unkeyed hash, so an edit in the middle is detected but
anyone who can write the file can recompute the whole chain, and the newest records can be cut
without a trace. An anchor records the length and head hash of the log at one moment, signed with
a key the writer does not hold, in an append-only file the operator keeps elsewhere. Verification
detects a log that was resealed, truncated or rewritten after it was anchored.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, ValidationError

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import canonical_json
from ..observability.run_checkpoints import CheckpointSigner, CheckpointSink, resolve_verifier
from .artifacts import ArtifactStore

ANCHOR_SCHEMA = "agent-sdk-artifact-log-anchor-v1"


def artifact_anchor_name(run_id: str) -> str:
    return f"{run_id}.artifacts"


class ArtifactAnchorVerdict(StrEnum):
    MATCHES = "matches"
    ADVANCED = "advanced"
    TRUNCATED = "truncated"
    DIVERGED = "diverged"
    CHAIN_BROKEN = "chain-broken"
    SIGNATURE_INVALID = "signature-invalid"
    NO_ANCHOR = "no-anchor"
    UNREADABLE = "unreadable"


class ArtifactLogAnchor(StrictModel):
    schema_version: str = ANCHOR_SCHEMA
    name: str = Field(min_length=1)
    records: int = Field(ge=0)
    head: str | None = None
    anchored_at_utc: str
    signer_id: str
    signature: str = ""

    def signed_message(self) -> bytes:
        unsigned = self.model_copy(update={"signature": ""}).model_dump(mode="json")
        return canonical_json(unsigned).encode("utf-8")


class ArtifactAnchorVerification(StrictModel):
    verdict: ArtifactAnchorVerdict
    anchors: int = Field(default=0, ge=0)
    anchored_records: int = Field(default=0, ge=0)
    current_records: int = Field(default=0, ge=0)
    detail: str

    @property
    def ok(self) -> bool:
        return self.verdict in {ArtifactAnchorVerdict.MATCHES, ArtifactAnchorVerdict.ADVANCED}


def anchor_artifact_log(
    store: ArtifactStore,
    sink: CheckpointSink,
    name: str,
    signer: CheckpointSigner,
    *,
    now: datetime | None = None,
) -> ArtifactLogAnchor:
    """Sign and append the log length and head; a log that is not intact is never signed."""

    check = store.verify_history()
    if not check.ok:
        raise AgentSdkError(
            "ARTIFACT_ANCHOR_CHAIN_BROKEN",
            f'The artifact write log of anchor "{name}" is not intact and was not anchored: '
            f"{check.reason}",
            {"name": name, "broken_at": check.broken_at},
        )
    store.sync_log()
    hashes = store.log_hashes()
    draft = ArtifactLogAnchor(
        name=name,
        records=len(hashes),
        head=hashes[-1] if hashes else None,
        anchored_at_utc=(now or datetime.now(UTC)).isoformat(),
        signer_id=signer.signer_id,
    )
    anchor = draft.model_copy(update={"signature": signer.sign(draft.signed_message())})
    sink.append(name, anchor.model_dump_json())
    return anchor


def verify_artifact_anchor(
    store: ArtifactStore, sink: CheckpointSink, name: str, signer: CheckpointSigner
) -> ArtifactAnchorVerification:
    """Compare the write log with every anchor kept for ``name``; the newest decides the verdict."""

    try:
        lines = sink.read_all(name)
    except (OSError, AgentSdkError) as error:
        return _verdict(
            ArtifactAnchorVerdict.UNREADABLE,
            f'The anchors of "{name}" cannot be read at {sink.location(name)}: '
            f"{type(error).__name__}: {error}",
        )
    if not lines:
        return _verdict(
            ArtifactAnchorVerdict.NO_ANCHOR,
            f'No anchor of "{name}" exists at {sink.location(name)}, so the write log is '
            "tamper-evident only: its tail and its whole chain can be rewritten undetected.",
        )
    anchors: list[ArtifactLogAnchor] = []
    for number, line in enumerate(lines, start=1):
        try:
            anchor = ArtifactLogAnchor.model_validate_json(line)
        except (ValueError, ValidationError) as error:
            return _verdict(
                ArtifactAnchorVerdict.UNREADABLE,
                f'Anchor {number} of "{name}" is not a valid anchor record: '
                f"{type(error).__name__}: {error}",
                anchors=len(anchors),
            )
        resolved, problem = resolve_verifier(signer, anchor.signer_id, anchor.anchored_at_utc)
        if resolved is None:
            return _verdict(
                ArtifactAnchorVerdict.SIGNATURE_INVALID,
                f'Anchor {number} of "{name}" cannot be trusted: {problem}.',
                anchors=len(anchors),
            )
        if anchor.schema_version != ANCHOR_SCHEMA or not resolved.verify(
            anchor.signed_message(), anchor.signature
        ):
            return _verdict(
                ArtifactAnchorVerdict.SIGNATURE_INVALID,
                f'Anchor {number} of "{name}" does not carry a valid signature from signer '
                f'"{resolved.signer_id}": it was edited or signed with another key.',
                anchors=len(anchors),
            )
        anchors.append(anchor)
    check = store.verify_history()
    if not check.ok:
        return _verdict(
            ArtifactAnchorVerdict.CHAIN_BROKEN,
            f"The artifact write log is not intact: {check.reason}",
            anchors=len(anchors),
        )
    hashes = store.log_hashes()
    current = len(hashes)
    for number, anchor in enumerate(anchors, start=1):
        counts = {
            "anchors": len(anchors),
            "anchored_records": anchor.records,
            "current_records": current,
        }
        if current < anchor.records:
            return _verdict(
                ArtifactAnchorVerdict.TRUNCATED,
                f"Anchor {number} recorded {anchor.records} writes but the log holds {current}: "
                f"{anchor.records - current} newest writes were removed.",
                **counts,
            )
        present = hashes[anchor.records - 1] if anchor.records else None
        if present != anchor.head:
            return _verdict(
                ArtifactAnchorVerdict.DIVERGED,
                f"Write {anchor.records} of the log is {_short(present)} but anchor {number} "
                f"recorded {_short(anchor.head)}: the log was rewritten after it was anchored.",
                **counts,
            )
    newest = anchors[-1]
    counts = {
        "anchors": len(anchors),
        "anchored_records": newest.records,
        "current_records": current,
    }
    if current == newest.records:
        return _verdict(
            ArtifactAnchorVerdict.MATCHES,
            f"The log holds exactly the {current} anchored writes.",
            **counts,
        )
    return _verdict(
        ArtifactAnchorVerdict.ADVANCED,
        f"The log extends the anchored {newest.records} writes by {current - newest.records}; "
        "anchor it again to cover them.",
        **counts,
    )


def _verdict(kind: ArtifactAnchorVerdict, detail: str, **counts: int) -> ArtifactAnchorVerification:
    return ArtifactAnchorVerification(verdict=kind, detail=detail, **counts)


def _short(digest: str | None) -> str:
    return "no writes" if digest is None else digest[:12]
