# Copyright (c) 2026 David Michael Indraputra

"""Signed checkpoints of a run's telemetry and audit chain heads, kept outside the run root.

Each chain detects an edit in the middle. The newest entries can still be removed together, or
both chains rewritten, without a trace, because nothing outside the run root remembers how long
they were. A checkpoint records the length and head hash of both chains at one moment, signed,
in a file the operator keeps elsewhere; verification against it reports a run that was
truncated or rewritten afterwards.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field, ValidationError, computed_field

from ..foundations.atomic_io import exclusive_file_lock, fsync_directory
from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import canonical_json, sha256_hex
from ..foundations.identifiers import file_safe_name
from .audit_log import AuditTranscriptStore
from .telemetry_store import TelemetryStore

CHECKPOINT_SCHEMA = "agent-sdk-run-head-checkpoint-v1"
MIN_HMAC_KEY_BYTES = 32
MAX_SIGNER_ID_CHARS = 128
TERMINAL_TELEMETRY_EVENT = "agent.profile-completed"
TERMINAL_AUDIT_EVENT = "profile-completed"


class CheckpointSigner(Protocol):
    @property
    def signer_id(self) -> str: ...

    def sign(self, message: bytes) -> str: ...

    def verify(self, message: bytes, signature: str) -> bool: ...


class HmacSha256Signer:
    """Shared-secret signer from the standard library; the key never appears in a repr."""

    def __init__(self, key: bytes | str, *, signer_id: str = "hmac-sha256") -> None:
        material = key.encode("utf-8") if isinstance(key, str) else bytes(key)
        if len(material) < MIN_HMAC_KEY_BYTES:
            raise ValueError(
                f"An HMAC checkpoint key must be at least {MIN_HMAC_KEY_BYTES} bytes, "
                f"got {len(material)}."
            )
        if not signer_id.strip() or len(signer_id) > MAX_SIGNER_ID_CHARS:
            raise ValueError(
                f"signer_id must be 1 to {MAX_SIGNER_ID_CHARS} non-blank characters, "
                f"got {signer_id!r}."
            )
        self._key = material
        self._signer_id = signer_id

    @property
    def signer_id(self) -> str:
        return self._signer_id

    def sign(self, message: bytes) -> str:
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def verify(self, message: bytes, signature: str) -> bool:
        return hmac.compare_digest(self.sign(message).encode(), signature.encode())

    def __repr__(self) -> str:
        return f"HmacSha256Signer(signer_id={self._signer_id!r})"


class CheckpointVerdict(StrEnum):
    MATCHES = "matches"
    ADVANCED = "advanced"
    TRUNCATED = "truncated"
    DIVERGED = "diverged"
    CHAIN_BROKEN = "chain-broken"
    SIGNATURE_INVALID = "signature-invalid"
    NO_CHECKPOINT = "no-checkpoint"
    UNREADABLE = "unreadable"


class RunHeadCheckpoint(StrictModel):
    schema_version: str = CHECKPOINT_SCHEMA
    run_id: str = Field(min_length=1)
    run_root_label: str
    telemetry_sequence: int = Field(ge=0)
    telemetry_head: str | None = None
    audit_sequence: int = Field(ge=0)
    audit_head: str | None = None
    created_at_utc: str
    signer_id: str
    signature: str = ""

    def signed_message(self) -> bytes:
        unsigned = self.model_copy(update={"signature": ""}).model_dump(mode="json")
        return canonical_json(unsigned).encode("utf-8")


class CheckpointVerification(StrictModel):
    verdict: CheckpointVerdict
    checkpoints: int = Field(default=0, ge=0)
    checkpointed_telemetry_sequence: int = Field(default=0, ge=0)
    current_telemetry_sequence: int = Field(default=0, ge=0)
    checkpointed_audit_sequence: int = Field(default=0, ge=0)
    current_audit_sequence: int = Field(default=0, ge=0)
    detail: str

    @property
    def ok(self) -> bool:
        return self.verdict in {CheckpointVerdict.MATCHES, CheckpointVerdict.ADVANCED}


class TerminalLinkState(StrEnum):
    LINKED = "linked"
    BROKEN = "broken"
    ABSENT = "absent"


class TerminalLinkVerification(StrictModel):
    state: TerminalLinkState
    detail: str

    @property
    def linked(self) -> bool:
        return self.state is TerminalLinkState.LINKED


class RunHeadReport(StrictModel):
    verification: CheckpointVerification
    terminal_link: TerminalLinkVerification

    @computed_field
    @property
    def ok(self) -> bool:
        return self.verification.ok and self.terminal_link.state is not TerminalLinkState.BROKEN


def run_root_label_of(telemetry: TelemetryStore) -> str:
    return sha256_hex(str(telemetry.root.parent))[:16]


def checkpoint_path(destination: Path, run_id: str) -> Path:
    return destination / f"{file_safe_name(run_id)}.checkpoints.jsonl"


def build_run_head_checkpoint(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    signer: CheckpointSigner,
    *,
    now: datetime | None = None,
) -> RunHeadCheckpoint:
    telemetry_sequence, telemetry_head = _verified_telemetry_head(telemetry, run_id)
    audit_sequence, audit_head = _verified_audit_head(audit, run_id)
    if telemetry_sequence == 0 and audit_sequence == 0:
        raise AgentSdkError(
            "RUN_CHECKPOINT_RUN_ABSENT",
            f'Run "{run_id}" has no telemetry events and no audit entries, so there is no head '
            "to sign.",
            {"run_id": run_id},
        )
    draft = RunHeadCheckpoint(
        run_id=run_id,
        run_root_label=run_root_label_of(telemetry),
        telemetry_sequence=telemetry_sequence,
        telemetry_head=telemetry_head,
        audit_sequence=audit_sequence,
        audit_head=audit_head,
        created_at_utc=(now or datetime.now(UTC)).isoformat(),
        signer_id=signer.signer_id,
    )
    return draft.model_copy(update={"signature": signer.sign(draft.signed_message())})


def read_checkpoints(path: Path) -> list[RunHeadCheckpoint]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise AgentSdkError(
            "RUN_CHECKPOINT_UNREADABLE",
            f'The checkpoint file "{path}" cannot be read: {type(error).__name__}: {error}',
            {"path": str(path)},
        ) from error
    checkpoints: list[RunHeadCheckpoint] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            checkpoint = RunHeadCheckpoint.model_validate_json(line)
        except (ValueError, ValidationError) as error:
            raise AgentSdkError(
                "RUN_CHECKPOINT_UNREADABLE",
                f'Line {number} of the checkpoint file "{path}" cannot be read: '
                f"{type(error).__name__}: {error}",
                {"path": str(path), "line": number},
            ) from error
        if checkpoint.schema_version != CHECKPOINT_SCHEMA:
            raise AgentSdkError(
                "RUN_CHECKPOINT_UNREADABLE",
                f'Line {number} of the checkpoint file "{path}" has schema_version '
                f'"{checkpoint.schema_version}", expected "{CHECKPOINT_SCHEMA}".',
                {"path": str(path), "line": number},
            )
        checkpoints.append(checkpoint)
    return checkpoints


def export_run_head_checkpoint(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    signer: CheckpointSigner,
    destination: Path,
    *,
    now: datetime | None = None,
) -> RunHeadCheckpoint:
    root = telemetry.root.parent
    target_directory = destination.resolve()
    if target_directory == root or root in target_directory.parents:
        raise AgentSdkError(
            "RUN_CHECKPOINT_INSIDE_RUN_ROOT",
            f'The checkpoint destination "{destination}" is inside the run root "{root}"; a '
            "checkpoint kept next to the run can be rewritten with it, so write it to another "
            "volume or host.",
            {"destination": str(destination)},
        )
    path = checkpoint_path(target_directory, run_id)
    if path.exists():
        existing = verify_run_head_checkpoints(telemetry, audit, run_id, path, signer)
        if existing.verdict in _REFUSES_APPEND:
            raise AgentSdkError(
                "RUN_CHECKPOINT_MISMATCH",
                f'The run "{run_id}" does not match its checkpoints in "{path}" '
                f"({existing.verdict.value}): {existing.detail} Nothing was appended.",
                {"path": str(path), "verdict": existing.verdict.value},
            )
    checkpoint = build_run_head_checkpoint(telemetry, audit, run_id, signer, now=now)
    _append(path, checkpoint)
    return checkpoint


def verify_run_head_checkpoints(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    source: Path | Sequence[RunHeadCheckpoint],
    signer: CheckpointSigner,
) -> CheckpointVerification:
    if isinstance(source, Path):
        try:
            checkpoints = read_checkpoints(source)
        except AgentSdkError as error:
            return CheckpointVerification(
                verdict=CheckpointVerdict.UNREADABLE, detail=error.message
            )
    else:
        checkpoints = list(source)
    if not checkpoints:
        return CheckpointVerification(
            verdict=CheckpointVerdict.NO_CHECKPOINT,
            detail=f'There is no checkpoint of run "{run_id}".',
        )
    refusal = _checkpoint_problem(checkpoints, run_id, signer)
    newest = checkpoints[-1]
    summary: dict[str, Any] = {
        "checkpoints": len(checkpoints),
        "checkpointed_telemetry_sequence": newest.telemetry_sequence,
        "checkpointed_audit_sequence": newest.audit_sequence,
    }
    if refusal is not None:
        return CheckpointVerification(verdict=refusal[0], detail=refusal[1], **summary)
    telemetry_break = telemetry.chain_break(run_id)
    if telemetry_break is not None:
        return CheckpointVerification(
            verdict=CheckpointVerdict.CHAIN_BROKEN,
            detail=(
                f'The telemetry chain of run "{run_id}" is broken at sequence '
                f"{telemetry_break.sequence} ({telemetry_break.kind}): {telemetry_break.message}"
            ),
            **summary,
        )
    audit_break = audit.chain_break(run_id)
    if audit_break is not None:
        return CheckpointVerification(
            verdict=CheckpointVerdict.CHAIN_BROKEN,
            detail=(
                f'The audit chain of run "{run_id}" is broken at sequence '
                f"{audit_break.sequence} ({audit_break.kind}): {audit_break.message}"
            ),
            **summary,
        )
    current_telemetry, _ = _telemetry_head(telemetry, run_id)
    current_audit, _ = _audit_head(audit, run_id)
    summary["current_telemetry_sequence"] = current_telemetry
    summary["current_audit_sequence"] = current_audit
    shortfalls = [
        f"{label} holds {current} of the {recorded} checkpointed {unit}"
        for label, current, recorded, unit in (
            ("the telemetry chain", current_telemetry, newest.telemetry_sequence, "events"),
            ("the audit chain", current_audit, newest.audit_sequence, "entries"),
        )
        if current < recorded
    ]
    if shortfalls:
        return CheckpointVerification(
            verdict=CheckpointVerdict.TRUNCATED,
            detail=f'Run "{run_id}" was truncated after it was checkpointed: '
            + "; ".join(shortfalls)
            + ".",
            **summary,
        )
    rewritten = []
    if _telemetry_hash_at(telemetry, run_id, newest.telemetry_sequence) != newest.telemetry_head:
        rewritten.append(
            f"telemetry event {newest.telemetry_sequence} is no longer "
            f"{_short(newest.telemetry_head)}"
        )
    if _audit_hash_at(audit, run_id, newest.audit_sequence) != newest.audit_head:
        rewritten.append(
            f"audit entry {newest.audit_sequence} is no longer {_short(newest.audit_head)}"
        )
    if rewritten:
        return CheckpointVerification(
            verdict=CheckpointVerdict.DIVERGED,
            detail=f'Run "{run_id}" was rewritten after it was checkpointed: '
            + "; ".join(rewritten)
            + ".",
            **summary,
        )
    if current_telemetry == newest.telemetry_sequence and current_audit == newest.audit_sequence:
        return CheckpointVerification(
            verdict=CheckpointVerdict.MATCHES,
            detail=(
                f'Run "{run_id}" holds exactly the checkpointed {current_telemetry} telemetry '
                f"events and {current_audit} audit entries."
            ),
            **summary,
        )
    return CheckpointVerification(
        verdict=CheckpointVerdict.ADVANCED,
        detail=(
            f'Run "{run_id}" extends its newest checkpoint by '
            f"{current_telemetry - newest.telemetry_sequence} telemetry events and "
            f"{current_audit - newest.audit_sequence} audit entries; checkpoint it again to "
            "cover them."
        ),
        **summary,
    )


def verify_terminal_link(
    telemetry: TelemetryStore, audit: AuditTranscriptStore, run_id: str
) -> TerminalLinkVerification:
    events = telemetry.list_events(run_id, limit=1_000, event_types=[TERMINAL_TELEMETRY_EVENT])
    entries = [
        entry for entry in audit.iter_entries(run_id) if entry.event_type == TERMINAL_AUDIT_EVENT
    ]
    if not events or not entries:
        return TerminalLinkVerification(
            state=TerminalLinkState.ABSENT,
            detail=(
                f'Run "{run_id}" has no terminal pair: '
                f"{len(events)} {TERMINAL_TELEMETRY_EVENT} events and {len(entries)} "
                f"{TERMINAL_AUDIT_EVENT} audit entries."
            ),
        )
    event, entry = events[-1], entries[-1]
    audit_head = event.payload.get("audit_head")
    telemetry_head = entry.payload.get("telemetry_head")
    if audit_head is None and telemetry_head is None:
        return TerminalLinkVerification(
            state=TerminalLinkState.ABSENT,
            detail=(
                f'The terminal pair of run "{run_id}" was written before runs cross-linked '
                "their heads, so there is no link to check."
            ),
        )
    if not isinstance(audit_head, dict) or not isinstance(telemetry_head, dict):
        return TerminalLinkVerification(
            state=TerminalLinkState.BROKEN,
            detail=(
                f'Only one half of the cross-link of run "{run_id}" is present: the telemetry '
                f"event has audit_head={audit_head!r} and the audit entry has "
                f"telemetry_head={telemetry_head!r}."
            ),
        )
    recorded_entries = audit_head.get("entries")
    if not isinstance(recorded_entries, int) or recorded_entries < 0:
        return TerminalLinkVerification(
            state=TerminalLinkState.BROKEN,
            detail=f"The telemetry event's audit_head.entries is {recorded_entries!r}.",
        )
    actual_audit = _audit_hash_at(audit, run_id, recorded_entries)
    if actual_audit != audit_head.get("head_hash"):
        return TerminalLinkVerification(
            state=TerminalLinkState.BROKEN,
            detail=(
                f"The telemetry event recorded audit entry {recorded_entries} as "
                f"{_short(audit_head.get('head_hash'))} but it is {_short(actual_audit)}."
            ),
        )
    if telemetry_head != {"sequence": event.sequence, "integrity_hash": event.integrity_hash}:
        return TerminalLinkVerification(
            state=TerminalLinkState.BROKEN,
            detail=(
                f"The audit entry recorded telemetry event {telemetry_head.get('sequence')} as "
                f"{_short(telemetry_head.get('integrity_hash'))} but the terminal event is "
                f"{event.sequence} ({_short(event.integrity_hash)})."
            ),
        )
    return TerminalLinkVerification(
        state=TerminalLinkState.LINKED,
        detail=(
            f"Audit entry {entry.sequence} commits to telemetry event {event.sequence}, which "
            f"commits to audit entry {recorded_entries}."
        ),
    )


def check_run_heads(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    source: Path | Sequence[RunHeadCheckpoint],
    signer: CheckpointSigner,
) -> RunHeadReport:
    return RunHeadReport(
        verification=verify_run_head_checkpoints(telemetry, audit, run_id, source, signer),
        terminal_link=verify_terminal_link(telemetry, audit, run_id),
    )


_REFUSES_APPEND = frozenset(
    {
        CheckpointVerdict.TRUNCATED,
        CheckpointVerdict.DIVERGED,
        CheckpointVerdict.CHAIN_BROKEN,
        CheckpointVerdict.SIGNATURE_INVALID,
        CheckpointVerdict.UNREADABLE,
    }
)


def _checkpoint_problem(
    checkpoints: Sequence[RunHeadCheckpoint], run_id: str, signer: CheckpointSigner
) -> tuple[CheckpointVerdict, str] | None:
    previous: RunHeadCheckpoint | None = None
    for index, checkpoint in enumerate(checkpoints, start=1):
        if checkpoint.run_id != run_id:
            return (
                CheckpointVerdict.UNREADABLE,
                f'Checkpoint {index} is of run "{checkpoint.run_id}", not "{run_id}".',
            )
        if checkpoint.signer_id != signer.signer_id:
            return (
                CheckpointVerdict.SIGNATURE_INVALID,
                f'Checkpoint {index} was signed by "{checkpoint.signer_id}" but the verifier '
                f'is "{signer.signer_id}".',
            )
        if not signer.verify(checkpoint.signed_message(), checkpoint.signature):
            return (
                CheckpointVerdict.SIGNATURE_INVALID,
                f"Checkpoint {index} (created {checkpoint.created_at_utc}) fails its "
                "signature: it was edited, or it was signed with another key.",
            )
        if previous is not None and (
            checkpoint.telemetry_sequence < previous.telemetry_sequence
            or checkpoint.audit_sequence < previous.audit_sequence
        ):
            return (
                CheckpointVerdict.DIVERGED,
                f"Checkpoint {index} is shorter than checkpoint {index - 1}: the run was "
                "replaced between them.",
            )
        previous = checkpoint
    return None


def _append(path: Path, checkpoint: RunHeadCheckpoint) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(
            path.with_suffix(".lock"), timeout_code="RUN_CHECKPOINT_LOCK_TIMEOUT"
        ):
            with path.open("ab") as stream:
                stream.write(checkpoint.model_dump_json().encode("utf-8"))
                stream.write(b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        fsync_directory(path.parent)
    except OSError as error:
        raise AgentSdkError(
            "RUN_CHECKPOINT_WRITE_FAILED",
            f'Could not append a checkpoint to "{path}": {type(error).__name__}: {error}',
            {"path": str(path)},
        ) from error


def _telemetry_head(telemetry: TelemetryStore, run_id: str) -> tuple[int, str | None]:
    sequence, head = 0, None
    for event in telemetry.iter_events(run_id):
        sequence, head = event.sequence, event.integrity_hash
    return sequence, head


def _audit_head(audit: AuditTranscriptStore, run_id: str) -> tuple[int, str | None]:
    sequence, head = 0, None
    for entry in audit.iter_entries(run_id):
        sequence, head = entry.sequence, entry.integrity_hash
    return sequence, head


def _verified_telemetry_head(telemetry: TelemetryStore, run_id: str) -> tuple[int, str | None]:
    failure = telemetry.chain_break(run_id)
    if failure is not None:
        raise AgentSdkError(
            "RUN_CHECKPOINT_CHAIN_BROKEN",
            f'The telemetry chain of run "{run_id}" is broken at sequence {failure.sequence} '
            f"({failure.kind}): {failure.message} A broken chain is not signed.",
            {"run_id": run_id, "store": "telemetry", "sequence": failure.sequence},
        )
    return _telemetry_head(telemetry, run_id)


def _verified_audit_head(audit: AuditTranscriptStore, run_id: str) -> tuple[int, str | None]:
    failure = audit.chain_break(run_id)
    if failure is not None:
        raise AgentSdkError(
            "RUN_CHECKPOINT_CHAIN_BROKEN",
            f'The audit chain of run "{run_id}" is broken at sequence {failure.sequence} '
            f"({failure.kind}): {failure.message} A broken chain is not signed.",
            {"run_id": run_id, "store": "audit", "sequence": failure.sequence},
        )
    return _audit_head(audit, run_id)


def _telemetry_hash_at(telemetry: TelemetryStore, run_id: str, sequence: int) -> str | None:
    if sequence == 0:
        return None
    found = telemetry.list_events(
        run_id, limit=1, after_sequence=sequence - 1, through_sequence=sequence
    )
    return found[0].integrity_hash if found else None


def _audit_hash_at(audit: AuditTranscriptStore, run_id: str, sequence: int) -> str | None:
    if sequence == 0:
        return None
    found = None
    for entry in audit.iter_entries(run_id, through_sequence=sequence):
        found = entry
    return found.integrity_hash if found is not None and found.sequence == sequence else None


def _short(digest: object) -> str:
    return "no entries" if not isinstance(digest, str) else digest[:12]
