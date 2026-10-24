# Copyright (c) 2026 David Michael Indraputra

"""Signed checkpoints of a run's telemetry and audit chain heads, kept outside the run root.

Each chain detects an edit in the middle. The newest entries can still be removed together, or
both chains rewritten, without a trace, because nothing outside the run root remembers how long
they were. A checkpoint records the length and head hash of both chains at one moment, signed,
in a store the operator keeps elsewhere; verification against it reports a run that was
truncated or rewritten afterwards, and a run that retention pruned is recognised from its
tombstone instead of being reported as truncated.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
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
from ..foundations.logging import get_logger
from .audit_log import AuditLogEntry, AuditTranscriptStore
from .telemetry_models import ChainBreak
from .telemetry_store import TelemetryStore

_logger = get_logger("run-checkpoints")
CHECKPOINT_SCHEMA = "agent-sdk-run-head-checkpoint-v1"
MIN_HMAC_KEY_BYTES = 32
MAX_SIGNER_ID_CHARS = 128
TERMINAL_TELEMETRY_EVENT = "agent.profile-completed"
TERMINAL_AUDIT_EVENT = "terminal-link"
LEGACY_TERMINAL_AUDIT_EVENT = "profile-completed"


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


def checkpoint_path(destination: Path, run_id: str) -> Path:
    return destination / f"{file_safe_name(run_id)}.checkpoints.jsonl"


class CheckpointSink(Protocol):
    def location(self, run_id: str) -> str: ...

    def read_all(self, run_id: str) -> list[str]: ...

    def append(self, run_id: str, record: str) -> None: ...

    def is_inside(self, root: Path) -> bool: ...


class FileCheckpointSink:
    """One append-only JSONL file per run under a directory the operator keeps elsewhere."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory.resolve()

    @property
    def directory(self) -> Path:
        return self._directory

    def location(self, run_id: str) -> str:
        return str(checkpoint_path(self._directory, run_id))

    def read_all(self, run_id: str) -> list[str]:
        path = checkpoint_path(self._directory, run_id)
        if not path.exists():
            return []
        return _read_lines(path)

    def append(self, run_id: str, record: str) -> None:
        path = checkpoint_path(self._directory, run_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with exclusive_file_lock(
                path.with_suffix(".lock"), timeout_code="RUN_CHECKPOINT_LOCK_TIMEOUT"
            ):
                if path.exists():
                    _discard_unterminated_tail(path)
                with path.open("ab") as stream:
                    stream.write(record.encode("utf-8"))
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

    def is_inside(self, root: Path) -> bool:
        resolved = root.resolve()
        return self._directory == resolved or resolved in self._directory.parents


class CheckpointVerdict(StrEnum):
    MATCHES = "matches"
    ADVANCED = "advanced"
    PRUNED = "pruned"
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


class PrunedHeads(StrictModel):
    run_id: str = Field(min_length=1)
    telemetry_sequence: int = Field(ge=0)
    telemetry_head: str | None = None
    audit_sequence: int = Field(ge=0)
    audit_head: str | None = None
    pruned_at_utc: str
    tombstone_sequence: int = Field(ge=1)


type PrunedLookup = Callable[[str], Sequence[PrunedHeads]]


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
        return self.verdict in {
            CheckpointVerdict.MATCHES,
            CheckpointVerdict.ADVANCED,
            CheckpointVerdict.PRUNED,
        }


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


@dataclass(frozen=True)
class _ChainState:
    failure: ChainBreak | None
    sequence: int
    head: str | None
    hashes: dict[int, str]


@dataclass(frozen=True)
class _RunState:
    telemetry: _ChainState
    audit: _ChainState


@dataclass(frozen=True)
class _Assessment:
    refusal: tuple[CheckpointVerdict, str] | None
    live: list[RunHeadCheckpoint]
    retired: list[RunHeadCheckpoint]
    pruned: list[PrunedHeads]
    lookup_problem: str | None


_REFUSES_APPEND = frozenset(
    {
        CheckpointVerdict.TRUNCATED,
        CheckpointVerdict.DIVERGED,
        CheckpointVerdict.CHAIN_BROKEN,
        CheckpointVerdict.SIGNATURE_INVALID,
        CheckpointVerdict.UNREADABLE,
    }
)


def run_root_label_of(telemetry: TelemetryStore) -> str:
    return sha256_hex(str(telemetry.root.parent))[:16]


def build_run_head_checkpoint(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    signer: CheckpointSigner,
    *,
    now: datetime | None = None,
) -> RunHeadCheckpoint:
    state = _inspect(telemetry, audit, run_id)
    return _signed_from(state, telemetry, run_id, signer, now)


def read_checkpoints(path: Path) -> list[RunHeadCheckpoint]:
    return _parse(_read_lines(path), str(path))


def export_run_head_checkpoint(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    signer: CheckpointSigner,
    destination: Path | CheckpointSink,
    *,
    now: datetime | None = None,
    pruned_lookup: PrunedLookup | None = None,
) -> RunHeadCheckpoint:
    sink = destination if not isinstance(destination, Path) else FileCheckpointSink(destination)
    root = telemetry.root.parent
    if sink.is_inside(root):
        raise AgentSdkError(
            "RUN_CHECKPOINT_INSIDE_RUN_ROOT",
            f'The checkpoint destination "{sink.location(run_id)}" is inside the run root '
            f'"{root}"; a checkpoint kept next to the run can be rewritten with it, so write it '
            "to another volume or host.",
            {"destination": sink.location(run_id)},
        )
    try:
        checkpoints = _parse(sink.read_all(run_id), sink.location(run_id))
    except AgentSdkError as error:
        raise AgentSdkError(
            "RUN_CHECKPOINT_MISMATCH",
            f'The checkpoints of run "{run_id}" in "{sink.location(run_id)}" '
            f"({CheckpointVerdict.UNREADABLE.value}): {error.message} Nothing was appended.",
            {"path": sink.location(run_id), "verdict": CheckpointVerdict.UNREADABLE.value},
        ) from error
    assessment = _assess(checkpoints, run_id, signer, pruned_lookup)
    state = _inspect_for(telemetry, audit, run_id, assessment)
    if checkpoints:
        existing = _verdict(run_id, checkpoints, assessment, state)
        if existing.verdict in _REFUSES_APPEND:
            raise AgentSdkError(
                "RUN_CHECKPOINT_MISMATCH",
                f'The run "{run_id}" does not match its checkpoints in '
                f'"{sink.location(run_id)}" ({existing.verdict.value}): {existing.detail} '
                "Nothing was appended.",
                {"path": sink.location(run_id), "verdict": existing.verdict.value},
            )
    checkpoint = _signed_from(state, telemetry, run_id, signer, now)
    sink.append(run_id, checkpoint.model_dump_json())
    return checkpoint


def verify_run_head_checkpoints(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    source: Path | CheckpointSink | Sequence[RunHeadCheckpoint],
    signer: CheckpointSigner,
    *,
    pruned_lookup: PrunedLookup | None = None,
) -> CheckpointVerification:
    try:
        checkpoints = _load(source, run_id)
    except AgentSdkError as error:
        return CheckpointVerification(verdict=CheckpointVerdict.UNREADABLE, detail=error.message)
    if not checkpoints:
        return CheckpointVerification(
            verdict=CheckpointVerdict.NO_CHECKPOINT,
            detail=f'There is no checkpoint of run "{run_id}".',
        )
    assessment = _assess(checkpoints, run_id, signer, pruned_lookup)
    state = _inspect_for(telemetry, audit, run_id, assessment)
    return _verdict(run_id, checkpoints, assessment, state)


def verify_terminal_link(
    telemetry: TelemetryStore, audit: AuditTranscriptStore, run_id: str
) -> TerminalLinkVerification:
    events = telemetry.list_events(run_id, limit=1_000, event_types=[TERMINAL_TELEMETRY_EVENT])
    links: list[AuditLogEntry] = []
    legacy: list[AuditLogEntry] = []
    for item in audit.iter_entries(run_id):
        if item.event_type == TERMINAL_AUDIT_EVENT:
            links.append(item)
        elif item.event_type == LEGACY_TERMINAL_AUDIT_EVENT:
            legacy.append(item)
    entries = links or legacy
    if not events or not entries:
        return TerminalLinkVerification(
            state=TerminalLinkState.ABSENT,
            detail=(
                f'Run "{run_id}" has no terminal pair: '
                f"{len(events)} {TERMINAL_TELEMETRY_EVENT} events and {len(links)} "
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
    source: Path | CheckpointSink | Sequence[RunHeadCheckpoint],
    signer: CheckpointSigner,
    *,
    pruned_lookup: PrunedLookup | None = None,
) -> RunHeadReport:
    return RunHeadReport(
        verification=verify_run_head_checkpoints(
            telemetry, audit, run_id, source, signer, pruned_lookup=pruned_lookup
        ),
        terminal_link=verify_terminal_link(telemetry, audit, run_id),
    )


def _read_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise AgentSdkError(
            "RUN_CHECKPOINT_UNREADABLE",
            f'The checkpoint file "{path}" cannot be read: {type(error).__name__}: {error}',
            {"path": str(path)},
        ) from error
    if text and not text.endswith("\n"):
        text = text[: text.rfind("\n") + 1]
    return text.splitlines()


def _discard_unterminated_tail(path: Path) -> None:
    """Drop the half of a record a crash cut off; an acknowledged append ends with a newline."""

    data = path.read_bytes()
    if not data or data.endswith(b"\n"):
        return
    keep = data.rfind(b"\n") + 1
    _logger.warning(
        'The checkpoint file "%s" ended in %d unterminated bytes (sha256 %s), a write cut off '
        "by a crash; they were discarded before the next record was appended.",
        path,
        len(data) - keep,
        sha256_hex(data[keep:]),
    )
    with path.open("r+b") as stream:
        stream.truncate(keep)
        stream.flush()
        os.fsync(stream.fileno())


def _parse(lines: Sequence[str], label: str) -> list[RunHeadCheckpoint]:
    checkpoints: list[RunHeadCheckpoint] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            checkpoint = RunHeadCheckpoint.model_validate_json(line)
        except (ValueError, ValidationError) as error:
            raise AgentSdkError(
                "RUN_CHECKPOINT_UNREADABLE",
                f'Line {number} of the checkpoints at "{label}" cannot be read: '
                f"{type(error).__name__}: {error}",
                {"path": label, "line": number},
            ) from error
        if checkpoint.schema_version != CHECKPOINT_SCHEMA:
            raise AgentSdkError(
                "RUN_CHECKPOINT_UNREADABLE",
                f'Line {number} of the checkpoints at "{label}" has schema_version '
                f'"{checkpoint.schema_version}", expected "{CHECKPOINT_SCHEMA}".',
                {"path": label, "line": number},
            )
        checkpoints.append(checkpoint)
    return checkpoints


def _load(
    source: Path | CheckpointSink | Sequence[RunHeadCheckpoint], run_id: str
) -> list[RunHeadCheckpoint]:
    if isinstance(source, Path):
        return read_checkpoints(source)
    if isinstance(source, list | tuple):
        return list(source)
    return _parse(source.read_all(run_id), source.location(run_id))


def _same_heads(checkpoint: RunHeadCheckpoint, pruned: PrunedHeads) -> bool:
    return (
        checkpoint.telemetry_sequence == pruned.telemetry_sequence
        and checkpoint.telemetry_head == pruned.telemetry_head
        and checkpoint.audit_sequence == pruned.audit_sequence
        and checkpoint.audit_head == pruned.audit_head
    )


def _assess(
    checkpoints: Sequence[RunHeadCheckpoint],
    run_id: str,
    signer: CheckpointSigner,
    pruned_lookup: PrunedLookup | None,
) -> _Assessment:
    for index, checkpoint in enumerate(checkpoints, start=1):
        if checkpoint.run_id != run_id:
            return _refused(
                CheckpointVerdict.UNREADABLE,
                f'Checkpoint {index} is of run "{checkpoint.run_id}", not "{run_id}".',
            )
        if checkpoint.signer_id != signer.signer_id:
            return _refused(
                CheckpointVerdict.SIGNATURE_INVALID,
                f'Checkpoint {index} was signed by "{checkpoint.signer_id}" but the verifier '
                f'is "{signer.signer_id}".',
            )
        if not signer.verify(checkpoint.signed_message(), checkpoint.signature):
            return _refused(
                CheckpointVerdict.SIGNATURE_INVALID,
                f"Checkpoint {index} (created {checkpoint.created_at_utc}) fails its "
                "signature: it was edited, or it was signed with another key.",
            )
    pruned: list[PrunedHeads] = []
    lookup_problem: str | None = None
    if pruned_lookup is not None and checkpoints:
        try:
            pruned = list(pruned_lookup(run_id))
        except Exception as error:
            lookup_problem = f"{type(error).__name__}: {error}"
    retired = 0
    for index in range(len(checkpoints) - 1, -1, -1):
        if any(_same_heads(checkpoints[index], item) for item in pruned):
            retired = index + 1
            break
    for offset, segment in ((0, checkpoints[:retired]), (retired, checkpoints[retired:])):
        for position in range(1, len(segment)):
            previous, current = segment[position - 1], segment[position]
            if (
                current.telemetry_sequence < previous.telemetry_sequence
                or current.audit_sequence < previous.audit_sequence
            ):
                number = offset + position + 1
                return _refused(
                    CheckpointVerdict.DIVERGED,
                    f"Checkpoint {number} is shorter than checkpoint {number - 1}: the run was "
                    "replaced between them.",
                )
    return _Assessment(
        refusal=None,
        live=list(checkpoints[retired:]),
        retired=list(checkpoints[:retired]),
        pruned=pruned,
        lookup_problem=lookup_problem,
    )


def _refused(verdict: CheckpointVerdict, detail: str) -> _Assessment:
    return _Assessment(
        refusal=(verdict, detail), live=[], retired=[], pruned=[], lookup_problem=None
    )


def _inspect(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    *,
    telemetry_sequences: Collection[int] = (),
    audit_sequences: Collection[int] = (),
) -> _RunState:
    return _RunState(
        telemetry=_telemetry_state(telemetry, run_id, telemetry_sequences),
        audit=_audit_state(audit, run_id, audit_sequences),
    )


def _inspect_for(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    assessment: _Assessment,
) -> _RunState:
    if assessment.refusal is not None:
        return _RunState(_ChainState(None, 0, None, {}), _ChainState(None, 0, None, {}))
    newest = assessment.live[-1] if assessment.live else None
    return _inspect(
        telemetry,
        audit,
        run_id,
        telemetry_sequences=(newest.telemetry_sequence,) if newest is not None else (),
        audit_sequences=(newest.audit_sequence,) if newest is not None else (),
    )


def _telemetry_state(
    telemetry: TelemetryStore, run_id: str, wanted: Collection[int]
) -> _ChainState:
    failure = telemetry.chain_break(run_id)
    if failure is not None:
        return _ChainState(failure, 0, None, {})
    sequence, head, hashes = 0, None, {}
    for event in telemetry.iter_events(run_id):
        sequence, head = event.sequence, event.integrity_hash
        if event.sequence in wanted:
            hashes[event.sequence] = event.integrity_hash
    return _ChainState(None, sequence, head, hashes)


def _audit_state(audit: AuditTranscriptStore, run_id: str, wanted: Collection[int]) -> _ChainState:
    failure = audit.chain_break(run_id)
    if failure is not None:
        return _ChainState(failure, 0, None, {})
    sequence, head, hashes = 0, None, {}
    for entry in audit.iter_entries(run_id):
        sequence, head = entry.sequence, entry.integrity_hash
        if entry.sequence in wanted:
            hashes[entry.sequence] = entry.integrity_hash
    return _ChainState(None, sequence, head, hashes)


def _signed_from(
    state: _RunState,
    telemetry: TelemetryStore,
    run_id: str,
    signer: CheckpointSigner,
    now: datetime | None,
) -> RunHeadCheckpoint:
    for label, chain in (("telemetry", state.telemetry), ("audit", state.audit)):
        failure = chain.failure
        if failure is not None:
            raise AgentSdkError(
                "RUN_CHECKPOINT_CHAIN_BROKEN",
                f'The {label} chain of run "{run_id}" is broken at sequence {failure.sequence} '
                f"({failure.kind}): {failure.message} A broken chain is not signed.",
                {"run_id": run_id, "store": label, "sequence": failure.sequence},
            )
    if state.telemetry.sequence == 0 and state.audit.sequence == 0:
        raise AgentSdkError(
            "RUN_CHECKPOINT_RUN_ABSENT",
            f'Run "{run_id}" has no telemetry events and no audit entries, so there is no head '
            "to sign.",
            {"run_id": run_id},
        )
    draft = RunHeadCheckpoint(
        run_id=run_id,
        run_root_label=run_root_label_of(telemetry),
        telemetry_sequence=state.telemetry.sequence,
        telemetry_head=state.telemetry.head,
        audit_sequence=state.audit.sequence,
        audit_head=state.audit.head,
        created_at_utc=(now or datetime.now(UTC)).isoformat(),
        signer_id=signer.signer_id,
    )
    return draft.model_copy(update={"signature": signer.sign(draft.signed_message())})


def _verdict(
    run_id: str,
    checkpoints: Sequence[RunHeadCheckpoint],
    assessment: _Assessment,
    state: _RunState,
) -> CheckpointVerification:
    newest = assessment.live[-1] if assessment.live else checkpoints[-1]
    summary: dict[str, Any] = {
        "checkpoints": len(checkpoints),
        "checkpointed_telemetry_sequence": newest.telemetry_sequence,
        "checkpointed_audit_sequence": newest.audit_sequence,
    }
    if assessment.refusal is not None:
        return CheckpointVerification(
            verdict=assessment.refusal[0], detail=assessment.refusal[1], **summary
        )
    for label, chain in (("telemetry", state.telemetry), ("audit", state.audit)):
        failure = chain.failure
        if failure is not None:
            return CheckpointVerification(
                verdict=CheckpointVerdict.CHAIN_BROKEN,
                detail=(
                    f'The {label} chain of run "{run_id}" is broken at sequence '
                    f"{failure.sequence} ({failure.kind}): {failure.message}"
                ),
                **summary,
            )
    current_telemetry, current_audit = state.telemetry.sequence, state.audit.sequence
    summary["current_telemetry_sequence"] = current_telemetry
    summary["current_audit_sequence"] = current_audit
    absent = current_telemetry == 0 and current_audit == 0
    if not assessment.live:
        return _retired_verdict(run_id, assessment, summary, absent)
    stones = _pruned_after(assessment.pruned, newest.created_at_utc)
    if absent and stones:
        return _tombstone_verdict(run_id, newest, stones[-1], summary)
    shortfalls = [
        f"{label} holds {current} of the {recorded} checkpointed {unit}"
        for label, current, recorded, unit in (
            ("the telemetry chain", current_telemetry, newest.telemetry_sequence, "events"),
            ("the audit chain", current_audit, newest.audit_sequence, "entries"),
        )
        if current < recorded
    ]
    if shortfalls:
        lookup = (
            f" The tombstone log could not be read to check whether retention pruned the "
            f"run: {assessment.lookup_problem}."
            if absent and assessment.lookup_problem is not None
            else ""
        )
        return CheckpointVerification(
            verdict=CheckpointVerdict.TRUNCATED,
            detail=f'Run "{run_id}" was truncated after it was checkpointed: '
            + "; ".join(shortfalls)
            + "."
            + lookup,
            **summary,
        )
    rewritten = []
    if state.telemetry.hashes.get(newest.telemetry_sequence) != newest.telemetry_head:
        rewritten.append(
            f"telemetry event {newest.telemetry_sequence} is no longer "
            f"{_short(newest.telemetry_head)}"
        )
    if state.audit.hashes.get(newest.audit_sequence) != newest.audit_head:
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


def _retired_verdict(
    run_id: str, assessment: _Assessment, summary: dict[str, Any], absent: bool
) -> CheckpointVerification:
    last = assessment.retired[-1]
    stone = next(item for item in reversed(assessment.pruned) if _same_heads(last, item))
    if absent:
        return CheckpointVerification(
            verdict=CheckpointVerdict.PRUNED,
            detail=(
                f'Run "{run_id}" was pruned by retention at {stone.pruned_at_utc} '
                f"(tombstone {stone.tombstone_sequence}); the tombstone holds the same final "
                f"heads as the newest checkpoint, {last.telemetry_sequence} telemetry events "
                f"and {last.audit_sequence} audit entries."
            ),
            **summary,
        )
    return CheckpointVerification(
        verdict=CheckpointVerdict.NO_CHECKPOINT,
        detail=(
            f'Every checkpoint of run "{run_id}" belongs to an earlier run of that id that '
            f"retention pruned (tombstone {stone.tombstone_sequence}); none covers the run "
            "that exists now, so checkpoint it."
        ),
        **summary,
    )


def _tombstone_verdict(
    run_id: str, newest: RunHeadCheckpoint, stone: PrunedHeads, summary: dict[str, Any]
) -> CheckpointVerification:
    shortfalls = [
        f"{label} held {current} of the {recorded} checkpointed {unit}"
        for label, current, recorded, unit in (
            ("the telemetry chain", stone.telemetry_sequence, newest.telemetry_sequence, "events"),
            ("the audit chain", stone.audit_sequence, newest.audit_sequence, "entries"),
        )
        if current < recorded
    ]
    if shortfalls:
        return CheckpointVerification(
            verdict=CheckpointVerdict.TRUNCATED,
            detail=(
                f'Run "{run_id}" was truncated before retention pruned it (tombstone '
                f"{stone.tombstone_sequence}): " + "; ".join(shortfalls) + "."
            ),
            **summary,
        )
    rewritten = []
    if (
        stone.telemetry_sequence == newest.telemetry_sequence
        and stone.telemetry_head != newest.telemetry_head
    ):
        rewritten.append(
            f"the final telemetry event is {_short(stone.telemetry_head)}, not "
            f"{_short(newest.telemetry_head)}"
        )
    if stone.audit_sequence == newest.audit_sequence and stone.audit_head != newest.audit_head:
        rewritten.append(
            f"the final audit entry is {_short(stone.audit_head)}, not {_short(newest.audit_head)}"
        )
    if rewritten:
        return CheckpointVerification(
            verdict=CheckpointVerdict.DIVERGED,
            detail=(
                f'Run "{run_id}" was rewritten before retention pruned it (tombstone '
                f"{stone.tombstone_sequence}): " + "; ".join(rewritten) + "."
            ),
            **summary,
        )
    grown = (
        stone.telemetry_sequence - newest.telemetry_sequence,
        stone.audit_sequence - newest.audit_sequence,
    )
    return CheckpointVerification(
        verdict=CheckpointVerdict.PRUNED,
        detail=(
            f'Run "{run_id}" was pruned by retention at {stone.pruned_at_utc} (tombstone '
            f"{stone.tombstone_sequence}) after growing by {grown[0]} telemetry events and "
            f"{grown[1]} audit entries beyond its newest checkpoint; those events are gone "
            "and can no longer be checked against it."
        ),
        **summary,
    )


def _pruned_after(pruned: Sequence[PrunedHeads], created_at_utc: str) -> list[PrunedHeads]:
    try:
        created = datetime.fromisoformat(created_at_utc)
    except ValueError:
        return []
    later: list[PrunedHeads] = []
    for item in pruned:
        try:
            if datetime.fromisoformat(item.pruned_at_utc) >= created:
                later.append(item)
        except ValueError:
            continue
    return later


def _audit_hash_at(audit: AuditTranscriptStore, run_id: str, sequence: int) -> str | None:
    if sequence == 0:
        return None
    found = None
    for entry in audit.iter_entries(run_id, through_sequence=sequence):
        found = entry
    return found.integrity_hash if found is not None and found.sequence == sequence else None


def _short(digest: object) -> str:
    return "no entries" if not isinstance(digest, str) else digest[:12]
