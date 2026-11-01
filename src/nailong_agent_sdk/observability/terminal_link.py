# Copyright (c) 2026 David Michael Indraputra

"""The cross-link between a run's last telemetry event and its last audit entry."""

from __future__ import annotations

from enum import StrEnum

from ..foundations.contracts import StrictModel
from .audit_log import AuditLogEntry, AuditTranscriptStore
from .ledger_state import short_digest
from .signed_records import resolve_verifier as resolve_verifier
from .signers import MAX_SIGNER_ID_CHARS as MAX_SIGNER_ID_CHARS
from .signers import MIN_HMAC_KEY_BYTES as MIN_HMAC_KEY_BYTES
from .signers import CheckpointSigner as CheckpointSigner
from .signers import HmacSha256Signer as HmacSha256Signer
from .telemetry_store import TelemetryStore

TERMINAL_TELEMETRY_EVENT = "agent.profile-completed"


TERMINAL_AUDIT_EVENT = "terminal-link"


LEGACY_TERMINAL_AUDIT_EVENT = "profile-completed"


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
                f"{short_digest(audit_head.get('head_hash'))} but it is "
                f"{short_digest(actual_audit)}."
            ),
        )
    if telemetry_head != {"sequence": event.sequence, "integrity_hash": event.integrity_hash}:
        return TerminalLinkVerification(
            state=TerminalLinkState.BROKEN,
            detail=(
                f"The audit entry recorded telemetry event {telemetry_head.get('sequence')} as "
                f"{short_digest(telemetry_head.get('integrity_hash'))} but the terminal event is "
                f"{event.sequence} ({short_digest(event.integrity_hash)})."
            ),
        )
    return TerminalLinkVerification(
        state=TerminalLinkState.LINKED,
        detail=(
            f"Audit entry {entry.sequence} commits to telemetry event {event.sequence}, which "
            f"commits to audit entry {recorded_entries}."
        ),
    )


def _audit_hash_at(audit: AuditTranscriptStore, run_id: str, sequence: int) -> str | None:
    if sequence == 0:
        return None
    found = None
    for entry in audit.iter_entries(run_id, through_sequence=sequence):
        found = entry
    return found.integrity_hash if found is not None and found.sequence == sequence else None
