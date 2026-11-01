# Copyright (c) 2026 David Michael Indraputra

"""The checks an evidence profile runs on a run: one function per concern, listed in CHECKS.

To add a check, write a function that takes an ``EvidenceContext`` and returns findings, and add
it to ``CHECKS``. A finding's ``check`` name says which function produced it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...foundations.contracts import StrictModel
from ...foundations.errors import AgentSdkError
from ...observability.attestation import AttestationVerdict, verify_attestations
from ...observability.audit_log import AuditTranscriptStore
from ...observability.clock_check import find_time_regressions, parse_utc
from ...observability.recorder import RecorderVerdict, compare_with_recorder
from ...observability.run_checkpoints import (
    CheckpointSigner,
    CheckpointVerdict,
    TerminalLinkState,
    check_run_heads,
)
from ...observability.telemetry_store import TelemetryStore
from ...observability.transparency import verify_run_tree_heads
from ..retention import pruned_heads_lookup
from .build import Components
from .settings import EvidenceSettings


class EvidenceFinding(StrictModel):
    check: str
    ok: bool
    detail: str


class EvidenceReport(StrictModel):
    run_id: str
    findings: list[EvidenceFinding]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.findings)


@dataclass(frozen=True)
class EvidenceContext:
    settings: EvidenceSettings
    components: Components
    run_root: Path
    telemetry: TelemetryStore
    audit: AuditTranscriptStore
    run_id: str

    @property
    def verifier(self) -> CheckpointSigner | None:
        ring = self.components.ring
        return ring.verifier() if ring is not None else None


type Check = Callable[[EvidenceContext], list[EvidenceFinding]]


def check_ledger_chains(context: EvidenceContext) -> list[EvidenceFinding]:
    findings = []
    for label, broken in (
        ("telemetry-chain", context.telemetry.chain_break(context.run_id)),
        ("audit-chain", context.audit.chain_break(context.run_id)),
    ):
        findings.append(
            EvidenceFinding(
                check=label,
                ok=broken is None,
                detail="intact" if broken is None else f"{broken.kind}: {broken.message}",
            )
        )
    return findings


def check_host_signed(context: EvidenceContext) -> list[EvidenceFinding]:
    verifier, sink, roots = context.verifier, context.components.sink, context.components.roots
    if verifier is None or sink is None:
        return [
            EvidenceFinding(
                check="run-heads",
                ok=True,
                detail="no host-signed checkpoints are configured; the recorder's "
                "attestation is the evidence",
            )
        ]
    report = check_run_heads(
        context.telemetry,
        context.audit,
        context.run_id,
        sink,
        verifier,
        pruned_lookup=pruned_heads_lookup(context.run_root, verifier),
        trusted_roots=roots or None,
    )
    checked = report.verification
    findings = [
        EvidenceFinding(
            check="run-heads", ok=checked.ok, detail=f"{checked.verdict.value}: {checked.detail}"
        ),
        _tombstone_finding(checked),
        _timestamp_finding(context.settings, checked),
        EvidenceFinding(
            check="terminal-link",
            ok=report.terminal_link.state is not TerminalLinkState.BROKEN,
            detail=f"{report.terminal_link.state.value}: {report.terminal_link.detail}",
        ),
    ]
    results = verify_run_tree_heads(
        context.telemetry,
        context.audit,
        context.run_id,
        sink,
        verifier,
        trusted_roots=roots or None,
    )
    findings.extend(
        EvidenceFinding(
            check=f"tree-heads:{store}", ok=item.ok, detail=f"{item.verdict.value}: {item.detail}"
        )
        for store, item in results.items()
    )
    return findings


def _tombstone_finding(checked: Any) -> EvidenceFinding:
    if checked.verdict is not CheckpointVerdict.PRUNED:
        return EvidenceFinding(check="signed-tombstones", ok=True, detail="no prune to vouch for")
    return EvidenceFinding(
        check="signed-tombstones",
        ok=bool(checked.tombstone_signed),
        detail="the prune is vouched for by a verified signature"
        if checked.tombstone_signed
        else "the run was pruned and no verified signature vouches for it",
    )


def _timestamp_finding(settings: EvidenceSettings, checked: Any) -> EvidenceFinding:
    if not settings.require_timestamps:
        return EvidenceFinding(
            check="timestamps",
            ok=True,
            detail=f"{checked.verified_timestamps} of {checked.stamped_checkpoints} stamped "
            "checkpoints verified (not required)",
        )
    complete = (
        checked.checkpoints > 0
        and checked.stamped_checkpoints == checked.checkpoints
        and checked.verified_timestamps == checked.checkpoints
    )
    return EvidenceFinding(
        check="timestamps",
        ok=complete,
        detail=f"{checked.verified_timestamps} of {checked.checkpoints} checkpoints carry a "
        "verified trusted timestamp",
    )


def check_time_order(context: EvidenceContext) -> list[EvidenceFinding]:
    reports = {
        "telemetry": find_time_regressions(
            (event.sequence, event.occurred_at_utc)
            for event in context.telemetry.iter_events(context.run_id)
        ),
        "audit": find_time_regressions(
            (entry.sequence, entry.occurred_at_utc)
            for entry in context.audit.iter_entries(context.run_id)
        ),
    }
    problems = [
        f"{name}: {item.total_regressions} regression(s), first at sequence "
        f"{item.regressions[0].sequence} ({item.regressions[0].seconds_back:.1f}s back)"
        for name, item in reports.items()
        if item.regressions
    ] + [
        f"{name}: {item.unparsable} time(s) that do not parse"
        for name, item in reports.items()
        if item.unparsable
    ]
    required = context.settings.require_ordered_time
    return [
        EvidenceFinding(
            check="time-order",
            ok=not problems or not required,
            detail="recorded times never run backwards"
            if not problems
            else "; ".join(problems) + ("" if required else " (reported, not required)"),
        )
    ]


def check_recorder(context: EvidenceContext) -> list[EvidenceFinding]:
    settings, parts = context.settings, context.components
    reader, verifier = parts.reader, parts.recorder_verifier
    if reader is None or verifier is None:
        return [
            EvidenceFinding(
                check="recorder",
                ok=not settings.require_recorder,
                detail="no recorder read token and public key are configured, so the "
                "recorder's copy cannot be compared"
                + ("; the profile requires it" if settings.require_recorder else ""),
            )
        ]
    try:
        comparison = compare_with_recorder(
            context.telemetry, context.audit, context.run_id, reader, verifier
        )
        strict = settings.require_final_attestation
        unsent = [
            store
            for store, item in comparison.items()
            if strict and item.verdict is RecorderVerdict.ADVANCED
        ]
        findings = [
            EvidenceFinding(
                check="recorder",
                ok=all(item.ok for item in comparison.values()) and not unsent,
                detail="; ".join(
                    f"{store}: {item.verdict.value}" for store, item in comparison.items()
                )
                + (
                    f"; the {', '.join(unsent)} ledger holds records the recorder never "
                    "received, which a profile that requires a final attestation does not allow"
                    if unsent
                    else ""
                ),
            )
        ]
        attested = verify_attestations(
            context.telemetry,
            context.audit,
            context.run_id,
            reader.attestation_lines(context.run_id),
            verifier,
            require_final=strict,
        )
        findings.append(
            EvidenceFinding(
                check="recorder-attestation",
                ok=attested.ok and not (strict and attested.verdict is AttestationVerdict.ADVANCED),
                detail=f"{attested.verdict.value}: {attested.detail}",
            )
        )
        findings.append(_receipt_finding(context))
    except AgentSdkError as error:
        return [
            EvidenceFinding(check="recorder", ok=False, detail=f"{error.code}: {error.message}")
        ]
    return findings


def _receipt_finding(context: EvidenceContext) -> EvidenceFinding:
    reader = context.components.reader
    assert reader is not None
    limit = context.settings.max_receipt_delay_seconds
    skew = context.settings.clock_skew_tolerance_seconds
    late = early = checked = 0
    worst = 0.0
    receipts: dict[str, list[tuple[int, str]]] = {}
    for store in ("telemetry", "audit"):
        entries = reader.entries(context.run_id, store, include_records=False)
        receipts[store] = [
            (item.sequence, item.received_at_utc) for item in entries if item.received_at_utc
        ]
        for item in entries:
            claimed = parse_utc(item.claimed_at_utc or "")
            received = parse_utc(item.received_at_utc or "")
            if claimed is None or received is None:
                continue
            checked += 1
            delay = (received - claimed).total_seconds()
            worst = max(worst, delay)
            late += limit is not None and delay > limit
            early += delay < -skew
    backwards = sum(
        find_time_regressions(items, tolerance_seconds=skew).total_regressions
        for items in receipts.values()
    )
    problems = []
    if late:
        problems.append(
            f"{late} record(s) reached the recorder more than {limit:g}s after they claim"
        )
    if early:
        problems.append(
            f"{early} record(s) claim a time later than the recorder's clock by over {skew:g}s"
        )
    if backwards:
        problems.append(f"the recorder's own receipt times ran backwards {backwards} time(s)")
    return EvidenceFinding(
        check="receipt-times",
        ok=not problems,
        detail="; ".join(problems)
        if problems
        else f"{checked} record(s) checked against the recorder's clock; the longest delay "
        f"between a record's claimed time and its receipt was {worst:.1f}s",
    )


CHECKS: tuple[Check, ...] = (
    check_ledger_chains,
    check_host_signed,
    check_time_order,
    check_recorder,
)
