# Copyright (c) 2026 David Michael Indraputra

"""Tie a failing finding of an agent run to the model turn in its audit ledger that led to it."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import closing
from typing import Any, Literal

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import canonical_json
from ..observability.audit_log import AuditLogEntry, AuditTranscriptStore
from .diagnose import DiagnosisFinding

MAX_CANDIDATE_CALLS = 20
MAX_PREVIEW_CHARS = 400
_TOOL_OUTCOMES_EVENT = "agent.tool-batch-completed"
_OUTPUT_REJECTED_EVENT = "agent.output-rejected"
_CALL_CODES = frozenset(
    {
        "TOOL_NOT_DECLARED",
        "TOOL_EXECUTOR_MISSING",
        "TOOL_DENIED_BY_POLICY",
        "TOOL_DENIED_BY_HOOK",
        "TOOL_APPROVAL_REQUIRED",
        "TOOL_BATCH_DEPENDENCIES_UNRESOLVED",
        "TOOL_LIFECYCLE_UNEXPECTED_ERROR",
        "HARNESS_TOOL_UNKNOWN",
        "HARNESS_TOOL_EXECUTION_FAILED",
        "CORE_TOOL_EXECUTION_FAILED",
        "WATCHDOG_TOOL_TIMEOUT",
        "EPISODE_RECORDING_FAILED",
        "ELASTIC_REQUEST_INVALID",
    }
)
_OUTPUT_CODES = frozenset({"VERIFICATION_GATE_REJECTED"})
_BLOCKED_CODES = frozenset({"MODEL_REPORTED_BLOCKED"})

type DecisionRelation = Literal[
    "issued-the-call",
    "issued-the-batch",
    "produced-the-output",
    "reported-itself-blocked",
    "unlinked",
]


class DecisionRef(StrictModel):
    """The model turn a finding traces back to and exactly how that turn is tied to it."""

    relation: DecisionRelation
    iteration: int | None = None
    audit_sequence: int | None = None
    turn_type: str | None = None
    tool: str | None = None
    tool_call_id: str | None = None
    candidate_call_ids: list[str] = Field(default_factory=list)
    arguments_preview: str | None = None
    detail: str | None = None
    entry_truncated: bool = False
    note: str | None = None


def link_decisions(
    audit: AuditTranscriptStore | None,
    run_id: str,
    findings: list[DiagnosisFinding],
    run_findings: Iterable[DiagnosisFinding],
) -> list[DecisionRef]:
    """Tie each finding to the model turn behind it, reading the audit ledger once."""

    relations = [_relation(item) for item in findings]
    wanted = {
        item.iteration
        for item, relation in zip(findings, relations, strict=True)
        if relation is not None and item.iteration is not None
    }
    turns, problem = _model_turns(audit, run_id, wanted)
    outcomes = [item for item in run_findings if item.event_type == _TOOL_OUTCOMES_EVENT]
    return [
        _decision(item, relation, turns, problem, outcomes)
        for item, relation in zip(findings, relations, strict=True)
    ]


def unlinked_note(note: str) -> DecisionRef:
    """Record why no model decision could be tied to a finding."""

    return DecisionRef(relation="unlinked", note=note)


def link_call(
    audit: AuditTranscriptStore | None,
    run_id: str,
    tool_call_id: str,
    tool: str | None = None,
) -> DecisionRef:
    """Tie one tool call to the model turn that issued it, reading the audit ledger once."""

    iteration, problem = _requesting_iteration(audit, run_id, tool_call_id)
    if iteration is None:
        return unlinked_note(problem)
    finding = DiagnosisFinding(
        sequence=1,
        event_type=_TOOL_OUTCOMES_EVENT,
        iteration=iteration,
        severity="error",
        message=f'Tool call "{tool_call_id}" returned a result the caller rejected.',
        subject=tool,
        details={"tool_call_id": tool_call_id},
    )
    return link_decisions(audit, run_id, [finding], [])[0]


def decision_sentence(decision: DecisionRef) -> str:
    """Say in one sentence which model turn a reference names and how it is tied."""

    entry = f" (audit entry {decision.audit_sequence})" if decision.audit_sequence else ""
    match decision.relation:
        case "issued-the-call":
            which = (
                f'{decision.tool or "a tool"} call "{decision.tool_call_id}"'
                if decision.tool_call_id
                else "the failing call"
            )
            return f"The model turn at iteration {decision.iteration} issued {which}{entry}."
        case "issued-the-batch":
            return (
                f"The model turn at iteration {decision.iteration} issued a batch of calls and "
                f"the failure does not say which one{entry}."
            )
        case "produced-the-output":
            return f"The model turn at iteration {decision.iteration} produced that output{entry}."
        case "reported-itself-blocked":
            return (
                f"The model turn at iteration {decision.iteration} reported itself blocked{entry}."
            )
        case _:
            return f"No model decision is tied to it: {decision.note}"


def _relation(finding: DiagnosisFinding) -> DecisionRelation | None:
    if finding.event_type == _TOOL_OUTCOMES_EVENT:
        return "issued-the-call"
    if finding.event_type == _OUTPUT_REJECTED_EVENT:
        return "produced-the-output"
    if finding.code in _CALL_CODES:
        return "issued-the-call"
    if finding.code in _OUTPUT_CODES:
        return "produced-the-output"
    if finding.code in _BLOCKED_CODES:
        return "reported-itself-blocked"
    return None


def _requesting_iteration(
    audit: AuditTranscriptStore | None, run_id: str, tool_call_id: str
) -> tuple[int | None, str]:
    if audit is None:
        return None, "The run root has no audit ledger."
    try:
        with closing(audit.iter_entries(run_id)) as entries:
            for entry in entries:
                if entry.event_type != "tool-requested":
                    continue
                details = entry.payload.get("details")
                if not isinstance(details, dict) or details.get("tool_call_id") != tool_call_id:
                    continue
                if entry.iteration is None:
                    return None, (
                        f'The audit entry {entry.sequence} that requested call "{tool_call_id}" '
                        "records no iteration."
                    )
                return entry.iteration, ""
    except (AgentSdkError, ValueError, OSError) as error:
        return None, f"The audit ledger could not be read: {type(error).__name__}: {error}"
    return None, f'The audit ledger holds no tool-requested entry for call "{tool_call_id}".'


def _model_turns(
    audit: AuditTranscriptStore | None, run_id: str, iterations: set[int]
) -> tuple[dict[int, AuditLogEntry], str | None]:
    if not iterations:
        return {}, None
    if audit is None:
        return {}, "The run root has no audit ledger."
    found: dict[int, AuditLogEntry] = {}
    last = max(iterations)
    try:
        with closing(audit.iter_entries(run_id)) as entries:
            for entry in entries:
                if entry.iteration is not None and entry.iteration > last:
                    break
                if entry.event_type == "model-turn" and entry.iteration in iterations:
                    found[entry.iteration] = entry
                    if len(found) == len(iterations):
                        break
    except (AgentSdkError, ValueError, OSError) as error:
        return found, f"The audit ledger could not be read: {type(error).__name__}: {error}"
    return found, None


def _decision(
    finding: DiagnosisFinding,
    relation: DecisionRelation | None,
    turns: Mapping[int, AuditLogEntry],
    problem: str | None,
    outcomes: list[DiagnosisFinding],
) -> DecisionRef:
    if relation is None:
        subject = f"The failure code {finding.code}" if finding.code else finding.event_type
        return _unlinked(
            finding,
            f"{subject} is not tied to one model decision: it comes from limits, the provider, "
            "the context budget, the gate or the environment.",
        )
    iteration = finding.iteration
    if iteration is None:
        return _unlinked(
            finding, "The finding records no iteration, so no model turn can be tied to it."
        )
    entry = turns.get(iteration)
    if entry is None:
        return _unlinked(
            finding,
            problem or f"The audit ledger holds no model-turn entry for iteration {iteration}.",
        )
    call_ids = (
        _failing_call_ids(finding, iteration, outcomes) if relation == "issued-the-call" else []
    )
    turn = entry.payload.get("turn")
    if entry.payload.get("truncated") is True or not isinstance(turn, dict):
        return _opaque(finding, relation, entry, call_ids)
    kind = turn.get("type")
    if relation == "reported-itself-blocked":
        if kind != "blocked":
            return _unlinked(finding, _wrong_turn(entry, kind, "a blocked report"))
        return _ref(relation, entry, kind, detail=_preview(turn.get("reason")))
    if relation == "produced-the-output":
        if kind != "final":
            return _unlinked(finding, _wrong_turn(entry, kind, "a final output"))
        return _ref(relation, entry, kind, detail=_preview(turn.get("output")))
    calls = _calls(turn)
    if not calls:
        return _unlinked(finding, _wrong_turn(entry, kind, "a tool call"))
    by_id = {call["id"]: call for call in calls}
    if len(call_ids) == 1 or (not call_ids and len(calls) == 1):
        wanted = call_ids[0] if call_ids else calls[0]["id"]
        call = by_id.get(wanted)
        if call is None:
            return _unlinked(
                finding,
                f'Call "{wanted}" is not in the model turn at iteration {iteration}, which issued '
                f"{sorted(by_id)[:MAX_CANDIDATE_CALLS]}.",
            )
        return _ref(
            "issued-the-call",
            entry,
            kind,
            tool=call.get("name"),
            tool_call_id=wanted,
            arguments_preview=_preview(call.get("arguments")),
        )
    candidates = [item for item in call_ids if item in by_id] or sorted(by_id)
    return _ref(
        "issued-the-batch",
        entry,
        kind,
        candidate_call_ids=candidates[:MAX_CANDIDATE_CALLS],
        note=(
            f"{len(candidates)} calls of the batch could have produced this failure and the "
            "finding does not say which."
        ),
    )


def _failing_call_ids(
    finding: DiagnosisFinding, iteration: int, outcomes: list[DiagnosisFinding]
) -> list[str]:
    own = finding.details.get("tool_call_id")
    if isinstance(own, str) and own:
        return [own]
    return [
        str(item.details["tool_call_id"])
        for item in outcomes
        if item.iteration == iteration
        and item.details.get("tool_call_id")
        and (finding.code is None or item.code == finding.code)
    ]


def _calls(turn: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = [turn.get("call")] if turn.get("type") == "tool-call" else turn.get("calls")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and isinstance(item.get("id"), str)]


def _ref(
    relation: DecisionRelation, entry: AuditLogEntry, kind: object, **fields: Any
) -> DecisionRef:
    return DecisionRef(
        relation=relation,
        iteration=entry.iteration,
        audit_sequence=entry.sequence,
        turn_type=kind if isinstance(kind, str) else None,
        **fields,
    )


def _opaque(
    finding: DiagnosisFinding,
    relation: DecisionRelation,
    entry: AuditLogEntry,
    call_ids: list[str],
) -> DecisionRef:
    payload = entry.payload
    if payload.get("truncated") is True:
        note = (
            f"The audit entry was truncated at {payload.get('original_chars')} characters (content "
            f"hash {payload.get('content_hash')}), so the turn itself cannot be read."
        )
    else:
        note = f'The model-turn entry {entry.sequence} holds no "turn" object.'
    batch = relation == "issued-the-call" and len(call_ids) > 1
    return DecisionRef(
        relation="issued-the-batch" if batch else relation,
        iteration=entry.iteration,
        audit_sequence=entry.sequence,
        tool=finding.subject if relation == "issued-the-call" else None,
        tool_call_id=call_ids[0] if len(call_ids) == 1 else None,
        candidate_call_ids=call_ids[:MAX_CANDIDATE_CALLS] if batch else [],
        entry_truncated=payload.get("truncated") is True,
        note=note,
    )


def _unlinked(finding: DiagnosisFinding, note: str) -> DecisionRef:
    return DecisionRef(relation="unlinked", iteration=finding.iteration, note=note)


def _wrong_turn(entry: AuditLogEntry, kind: object, expected: str) -> str:
    return (
        f"The model turn at iteration {entry.iteration} (audit entry {entry.sequence}) is a "
        f"{kind} turn, not {expected}."
    )


def _preview(value: object) -> str:
    text = value if isinstance(value, str) else canonical_json(value)
    return text if len(text) <= MAX_PREVIEW_CHARS else f"{text[: MAX_PREVIEW_CHARS - 3]}..."
