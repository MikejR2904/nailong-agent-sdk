# Copyright (c) 2026 David Michael Indraputra

"""Run heads signed by the recorder, so no key on the host that ran the agent is trusted."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, ValidationError

from ..foundations.contracts import StrictModel
from ..foundations.jcs import schema_message
from ..foundations.text import split_lines
from .audit_log import AuditTranscriptStore
from .run_checkpoints import CheckpointSigner, _inspect, resolve_verifier
from .telemetry_store import TelemetryStore

ATTESTATION_SCHEMAS = ("agent-sdk-recorder-attestation-v2",)
ATTESTATION_SCHEMA = ATTESTATION_SCHEMAS[-1]
_EDITED = "it fails its signature, so it was edited or signed with another key"


def attestation_name(run_id: str) -> str:
    return f"{run_id}.attestation"


class RunAttestation(StrictModel):
    schema_version: str = ATTESTATION_SCHEMA
    run_id: str = Field(min_length=1)
    telemetry_sequence: int = Field(ge=0)
    telemetry_head: str | None = None
    audit_sequence: int = Field(ge=0)
    audit_head: str | None = None
    attested_at_utc: str
    final: bool = False
    signer_id: str
    signature: str = ""

    def signed_message(self) -> bytes:
        unsigned = self.model_copy(update={"signature": ""}).model_dump(mode="json")
        return schema_message(self.schema_version, unsigned)


def sign_attestation(
    run_id: str,
    telemetry: tuple[int, str | None],
    audit: tuple[int, str | None],
    signer: CheckpointSigner,
    *,
    final: bool,
    now: datetime | None = None,
) -> RunAttestation:
    draft = RunAttestation(
        run_id=run_id,
        telemetry_sequence=telemetry[0],
        telemetry_head=telemetry[1],
        audit_sequence=audit[0],
        audit_head=audit[1],
        attested_at_utc=(now or datetime.now(UTC)).isoformat(),
        final=final,
        signer_id=signer.signer_id,
    )
    return draft.model_copy(update={"signature": signer.sign(draft.signed_message())})


class AttestationVerdict(StrEnum):
    MATCHES = "matches"
    ADVANCED = "advanced"
    TRUNCATED = "truncated"
    DIVERGED = "diverged"
    CHAIN_BROKEN = "chain-broken"
    SIGNATURE_INVALID = "signature-invalid"
    NOT_FINAL = "not-final"
    NO_ATTESTATION = "no-attestation"
    UNREADABLE = "unreadable"


class AttestationVerification(StrictModel):
    verdict: AttestationVerdict
    attestations: int = Field(default=0, ge=0)
    final: bool = False
    attested_telemetry_sequence: int = Field(default=0, ge=0)
    attested_audit_sequence: int = Field(default=0, ge=0)
    current_telemetry_sequence: int = Field(default=0, ge=0)
    current_audit_sequence: int = Field(default=0, ge=0)
    detail: str

    @property
    def ok(self) -> bool:
        return self.verdict in {AttestationVerdict.MATCHES, AttestationVerdict.ADVANCED}


def parse_attestations(lines: Sequence[str], run_id: str) -> list[RunAttestation]:
    parsed: list[RunAttestation] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = RunAttestation.model_validate_json(line)
        except (ValueError, ValidationError) as error:
            raise ValueError(
                f"Attestation {number} is not a valid record: {type(error).__name__}: {error}"
            ) from error
        if item.schema_version not in ATTESTATION_SCHEMAS:
            raise ValueError(
                f'Attestation {number} has schema_version "{item.schema_version}", expected one '
                f"of {list(ATTESTATION_SCHEMAS)}."
            )
        if item.run_id != run_id:
            raise ValueError(f'Attestation {number} is of run "{item.run_id}", not "{run_id}".')
        parsed.append(item)
    return parsed


def verify_attestations(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    lines: Sequence[str],
    verifier: CheckpointSigner,
    *,
    require_final: bool = False,
) -> AttestationVerification:
    lines = [part for line in lines for part in split_lines(line)]
    try:
        attestations = parse_attestations(lines, run_id)
    except ValueError as error:
        return AttestationVerification(verdict=AttestationVerdict.UNREADABLE, detail=str(error))
    if not attestations:
        return AttestationVerification(
            verdict=AttestationVerdict.NO_ATTESTATION,
            detail=f'The recorder holds no attestation of run "{run_id}".',
        )
    for number, item in enumerate(attestations, start=1):
        resolved, problem = resolve_verifier(verifier, item.signer_id, item.attested_at_utc)
        if resolved is None or not resolved.verify(item.signed_message(), item.signature):
            return AttestationVerification(
                verdict=AttestationVerdict.SIGNATURE_INVALID,
                attestations=len(attestations),
                detail=f"Attestation {number} cannot be trusted: {problem or _EDITED}.",
            )
    for number in range(1, len(attestations)):
        before, after = attestations[number - 1], attestations[number]
        if (
            after.telemetry_sequence < before.telemetry_sequence
            or after.audit_sequence < before.audit_sequence
        ):
            return AttestationVerification(
                verdict=AttestationVerdict.DIVERGED,
                attestations=len(attestations),
                detail=f"Attestation {number + 1} is shorter than attestation {number}: the "
                "recorder's own record of the run went backwards.",
            )
    newest = attestations[-1]
    state = _inspect(
        telemetry,
        audit,
        run_id,
        telemetry_sequences=(newest.telemetry_sequence,),
        audit_sequences=(newest.audit_sequence,),
    )
    summary = {
        "attestations": len(attestations),
        "final": newest.final,
        "attested_telemetry_sequence": newest.telemetry_sequence,
        "attested_audit_sequence": newest.audit_sequence,
        "current_telemetry_sequence": state.telemetry.sequence,
        "current_audit_sequence": state.audit.sequence,
    }
    for label, chain in (("telemetry", state.telemetry), ("audit", state.audit)):
        if chain.failure is not None:
            return AttestationVerification(
                verdict=AttestationVerdict.CHAIN_BROKEN,
                detail=f'The {label} chain of run "{run_id}" is broken at sequence '
                f"{chain.failure.sequence} ({chain.failure.kind}): {chain.failure.message}",
                **summary,
            )
    shortfalls = [
        f"{label} holds {current} of the {attested} attested {unit}"
        for label, current, attested, unit in (
            ("the telemetry chain", state.telemetry.sequence, newest.telemetry_sequence, "events"),
            ("the audit chain", state.audit.sequence, newest.audit_sequence, "entries"),
        )
        if current < attested
    ]
    if shortfalls:
        return AttestationVerification(
            verdict=AttestationVerdict.TRUNCATED,
            detail=f'Run "{run_id}" was truncated after the recorder attested it: '
            + "; ".join(shortfalls)
            + ".",
            **summary,
        )
    rewritten = []
    if state.telemetry.hashes.get(newest.telemetry_sequence) != newest.telemetry_head:
        rewritten.append(
            f"telemetry event {newest.telemetry_sequence} is no longer the attested one"
        )
    if state.audit.hashes.get(newest.audit_sequence) != newest.audit_head:
        rewritten.append(f"audit entry {newest.audit_sequence} is no longer the attested one")
    if rewritten:
        return AttestationVerification(
            verdict=AttestationVerdict.DIVERGED,
            detail=f'Run "{run_id}" was rewritten after the recorder attested it: '
            + "; ".join(rewritten)
            + ".",
            **summary,
        )
    if require_final and not newest.final:
        return AttestationVerification(
            verdict=AttestationVerdict.NOT_FINAL,
            detail="The recorder's newest attestation is not final: it never received the "
            "host's closing seal, so the run ended without telling the recorder, or crashed.",
            **summary,
        )
    extra_telemetry = state.telemetry.sequence - newest.telemetry_sequence
    extra_audit = state.audit.sequence - newest.audit_sequence
    if extra_telemetry == 0 and extra_audit == 0:
        return AttestationVerification(
            verdict=AttestationVerdict.MATCHES,
            detail=f"The ledgers hold exactly what the recorder attested"
            f"{' and sealed as final' if newest.final else ''}.",
            **summary,
        )
    after_final = " after the final seal" if newest.final else ""
    return AttestationVerification(
        verdict=AttestationVerdict.ADVANCED,
        detail=f"The ledgers extend the recorder's newest attestation by {extra_telemetry} "
        f"telemetry events and {extra_audit} audit entries{after_final}; those are covered only "
        "by the recorder's own copy, if it has them.",
        **summary,
    )
