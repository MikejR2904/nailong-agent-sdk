# Copyright (c) 2026 David Michael Indraputra

"""Attribute a stopped run to the nodes and model turns that stopped it, from its ledgers alone."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..observability.audit_log import AuditTranscriptStore
from ..state.graph import StateGraph
from .decision_links import DecisionRef, decision_sentence, link_decisions, unlinked_note
from .diagnose import (
    DiagnosisFinding,
    OpenLedgers,
    Verdict,
    diagnose_open,
    open_ledgers,
    scan_events,
)

ATTRIBUTION_SCHEMA_VERSION = "agent-sdk-failure-attribution-v1"
MAX_ATTRIBUTED_ROOTS = 50
MAX_ATTRIBUTED_BLOCKED = 50
MAX_RELEASED_LISTED = 100
MAX_PARTNERS_LISTED = 50
MAX_PATH_NODES = 20
MAX_LINKED_ROOTS = 10
MAX_SUMMARY_CHARS = 1_200
RESPONSIBILITY_DIGITS = 6


class RootAttribution(StrictModel):
    """A node that stopped the run for its own reason and how much of the stoppage it explains."""

    node_id: str
    status: str
    reason: str | None = None
    failure_code: str | None = None
    graph_event_sequence: int | None = None
    agent_run_id: str | None = None
    responsibility: float = Field(ge=1)
    blocked_count: int = Field(ge=0)
    released_alone: list[str] = Field(default_factory=list)
    released_alone_omitted: int = Field(default=0, ge=0)
    also_requires: list[str] = Field(default_factory=list)
    also_requires_omitted: int = Field(default=0, ge=0)
    agent_terminal_decision: DecisionRef | None = None


class BlockedAttribution(StrictModel):
    """A node held up by a dependency, the independent failures behind it and one path to them."""

    node_id: str
    root_causes: list[str]
    root_causes_omitted: int = Field(default=0, ge=0)
    path: list[str]
    path_length: int = Field(ge=1)


class GraphAttribution(StrictModel):
    node_count: int = Field(ge=0)
    root_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    total_responsibility: float = Field(ge=0)
    roots: list[RootAttribution] = Field(default_factory=list)
    roots_omitted: int = Field(default=0, ge=0)
    blocked: list[BlockedAttribution] = Field(default_factory=list)
    blocked_omitted: int = Field(default=0, ge=0)
    blocked_without_root: list[str] = Field(default_factory=list)


class FailureAttribution(StrictModel):
    schema_version: str = ATTRIBUTION_SCHEMA_VERSION
    run_id: str
    verdict: Verdict
    ok: bool
    summary: str
    evidence_intact: bool | None = None
    terminal: DiagnosisFinding | None = None
    terminal_decision: DecisionRef | None = None
    first_error: DiagnosisFinding | None = None
    first_error_decision: DecisionRef | None = None
    graph: GraphAttribution | None = None
    graph_record_error: str | None = None


def attribute_failure(
    run_root: Path, run_id: str, *, node_id: str | None = None
) -> FailureAttribution:
    """Name what stopped a run, what each stop held up and the model turn behind it, read-only."""

    with open_ledgers(run_root, run_id) as ledgers:
        diagnosed = diagnose_open(ledgers, run_id)
        diagnosis, graph = diagnosed.diagnosis, diagnosed.graph
        terminal, first_error = diagnosis.terminal, diagnosis.first_error
        terminal_decision = first_error_decision = attribution = None
        if graph is not None:
            attribution = _linked(ledgers, attribute_graph(graph, node_id=node_id))
        elif node_id is not None:
            raise ValueError(
                f'Node "{node_id}" was requested but run "{run_id}" has no readable graph run '
                "record, so it has no nodes."
            )
        else:
            terminal_decision, first_error_decision = _agent_decisions(
                ledgers.audit, run_id, diagnosed.scan.findings, terminal, first_error
            )
        intact = None if diagnosis.integrity is None else diagnosis.integrity.intact
        return FailureAttribution(
            run_id=run_id,
            verdict=diagnosis.verdict,
            ok=diagnosis.ok,
            summary=_summary(
                run_id,
                diagnosis.verdict,
                terminal,
                terminal_decision,
                first_error,
                first_error_decision,
                attribution,
                intact,
                diagnosis.graph_record_error,
            ),
            evidence_intact=intact,
            terminal=terminal,
            terminal_decision=terminal_decision,
            first_error=first_error,
            first_error_decision=first_error_decision,
            graph=attribution,
            graph_record_error=diagnosis.graph_record_error,
        )


def attribute_graph(graph: StateGraph, *, node_id: str | None = None) -> GraphAttribution:
    """Rank the nodes that stopped a graph run by the part of the stoppage each one explains."""

    sets = graph.root_cause_sets()
    roots = graph.failure_roots()
    if node_id is not None:
        _require_blocked(graph, sets, node_id)
    tally = _tally(roots, sets)
    sequences = _stop_sequences(graph, roots)
    ordered = sorted(roots, key=lambda root: (-tally.weights[root], sequences.get(root, 0), root))
    listed = ordered[:MAX_ATTRIBUTED_ROOTS]
    partners = _partners(tally.shares, listed)
    blocked_ids = [node_id] if node_id is not None else list(sets)[:MAX_ATTRIBUTED_BLOCKED]
    return GraphAttribution(
        node_count=len(graph.nodes),
        root_count=len(roots),
        blocked_count=len(sets),
        total_responsibility=round(sum(tally.weights.values()), RESPONSIBILITY_DIGITS),
        roots=[_root(graph, root, tally, partners[root], sequences.get(root)) for root in listed],
        roots_omitted=len(ordered) - len(listed),
        blocked=[_blocked(graph, item, sets[item]) for item in blocked_ids],
        blocked_omitted=len(sets) - len(blocked_ids),
        blocked_without_root=[item for item, caused_by in sets.items() if not caused_by][
            :MAX_PARTNERS_LISTED
        ],
    )


@dataclass(frozen=True)
class _Tally:
    weights: dict[str, float]
    held: Counter[str]
    alone: dict[str, list[str]]
    shares: Counter[frozenset[str]]


def _tally(roots: Iterable[str], sets: Mapping[str, frozenset[str]]) -> _Tally:
    shares = Counter(sets.values())
    weights = dict.fromkeys(roots, 1.0)
    held: Counter[str] = Counter()
    alone: dict[str, list[str]] = defaultdict(list)
    for node_id, caused_by in sets.items():
        if len(caused_by) == 1:
            alone[next(iter(caused_by))].append(node_id)
    for caused_by, count in shares.items():
        if not caused_by:
            continue
        share = count / len(caused_by)
        for root in caused_by:
            weights[root] += share
            held[root] += count
    return _Tally(
        {root: round(weight, RESPONSIBILITY_DIGITS) for root, weight in weights.items()},
        held,
        alone,
        shares,
    )


def _partners(shares: Iterable[frozenset[str]], listed: Iterable[str]) -> dict[str, set[str]]:
    wanted = frozenset(listed)
    partners: dict[str, set[str]] = {root: set() for root in wanted}
    for caused_by in shares:
        if len(caused_by) < 2:
            continue
        for root in caused_by & wanted:
            partners[root] |= caused_by
    for root, others in partners.items():
        others.discard(root)
    return partners


def _stop_sequences(graph: StateGraph, roots: Iterable[str]) -> dict[str, int]:
    wanted = {root: graph.status(root).value for root in roots}
    sequences: dict[str, int] = {}
    for event in graph.events:
        if wanted.get(event.node_id) == event.status.value:
            sequences[event.node_id] = event.sequence
    return sequences


def _root(
    graph: StateGraph,
    node_id: str,
    tally: _Tally,
    partners: set[str],
    sequence: int | None,
) -> RootAttribution:
    result = graph.result(node_id)
    released = tally.alone.get(node_id, [])
    others = sorted(partners)
    return RootAttribution(
        node_id=node_id,
        status=graph.status(node_id).value,
        reason=result.reason if result is not None else None,
        failure_code=result.failure_code if result is not None else None,
        graph_event_sequence=sequence,
        agent_run_id=result.agent_run_id if result is not None else None,
        responsibility=tally.weights[node_id],
        blocked_count=tally.held[node_id],
        released_alone=released[:MAX_RELEASED_LISTED],
        released_alone_omitted=max(0, len(released) - MAX_RELEASED_LISTED),
        also_requires=others[:MAX_PARTNERS_LISTED],
        also_requires_omitted=max(0, len(others) - MAX_PARTNERS_LISTED),
    )


def _blocked(graph: StateGraph, node_id: str, caused_by: frozenset[str]) -> BlockedAttribution:
    causes = sorted(caused_by)
    path = graph.blocking_path(node_id)
    return BlockedAttribution(
        node_id=node_id,
        root_causes=causes[:MAX_PARTNERS_LISTED],
        root_causes_omitted=max(0, len(causes) - MAX_PARTNERS_LISTED),
        path=list(path[:MAX_PATH_NODES]),
        path_length=len(path),
    )


def _require_blocked(graph: StateGraph, sets: Mapping[str, frozenset[str]], node_id: str) -> None:
    if node_id not in graph.nodes:
        raise ValueError(
            f'Node "{node_id}" is not in the graph; nodes: {sorted(graph.nodes)[:20]}.'
        )
    if node_id not in sets:
        raise ValueError(
            f'Node "{node_id}" is not blocked by a dependency (status '
            f"{graph.status(node_id).value}); only dependency-blocked nodes have root causes: "
            f"{list(sets)[:20]}."
        )


def _agent_decisions(
    audit: AuditTranscriptStore | None,
    run_id: str,
    run_findings: list[DiagnosisFinding],
    terminal: DiagnosisFinding | None,
    first_error: DiagnosisFinding | None,
) -> tuple[DecisionRef | None, DecisionRef | None]:
    same = first_error is not None and first_error == terminal
    distinct = [item for item in (terminal, None if same else first_error) if item is not None]
    refs = iter(link_decisions(audit, run_id, distinct, run_findings))
    terminal_ref = next(refs) if terminal is not None else None
    if first_error is None:
        return terminal_ref, None
    return terminal_ref, terminal_ref if same else next(refs)


def _linked(ledgers: OpenLedgers, attribution: GraphAttribution) -> GraphAttribution:
    linked: list[RootAttribution] = []
    for position, root in enumerate(attribution.roots):
        if root.agent_run_id is None:
            linked.append(root)
            continue
        decision = (
            unlinked_note(
                f"Only the {MAX_LINKED_ROOTS} roots with the highest responsibility are linked "
                "to model decisions."
            )
            if position >= MAX_LINKED_ROOTS
            else _root_decision(ledgers, root.agent_run_id)
        )
        linked.append(root.model_copy(update={"agent_terminal_decision": decision}))
    return attribution.model_copy(update={"roots": linked})


def _root_decision(ledgers: OpenLedgers, agent_run_id: str) -> DecisionRef:
    if ledgers.telemetry is None:
        return unlinked_note("The run root has no telemetry database.")
    scan = scan_events(list(ledgers.telemetry.iter_events(agent_run_id)))
    if scan.terminal is None:
        return unlinked_note(f'The agent run "{agent_run_id}" recorded no terminal failure event.')
    (decision,) = link_decisions(ledgers.audit, agent_run_id, [scan.terminal], scan.findings)
    return decision


def _summary(
    run_id: str,
    verdict: Verdict,
    terminal: DiagnosisFinding | None,
    terminal_decision: DecisionRef | None,
    first_error: DiagnosisFinding | None,
    first_error_decision: DecisionRef | None,
    graph: GraphAttribution | None,
    intact: bool | None,
    record_error: str | None,
) -> str:
    parts: list[str] = []
    if intact is False:
        parts.append("The evidence for this run is not intact, so this attribution is unverified.")
    if record_error is not None:
        parts.append(f"The graph run record could not be read: {record_error}")
    if graph is not None:
        parts.append(_graph_sentence(run_id, verdict, graph))
    elif terminal is None and first_error is None:
        parts.append(
            f'Run "{run_id}" completed; there is nothing to attribute.'
            if verdict == "completed"
            else f'Run "{run_id}" is {verdict} and recorded no failing event to attribute.'
        )
    else:
        if terminal is not None:
            parts.append(_finding_sentence(run_id, verdict, terminal, terminal_decision))
        else:
            parts.append(f'Run "{run_id}" {verdict}.')
        if first_error is not None and first_error != terminal:
            parts.append(_error_sentence(first_error, first_error_decision))
    text = " ".join(parts)
    return text if len(text) <= MAX_SUMMARY_CHARS else f"{text[: MAX_SUMMARY_CHARS - 3]}..."


def _finding_sentence(
    run_id: str, verdict: Verdict, finding: DiagnosisFinding, decision: DecisionRef | None
) -> str:
    where = f" at iteration {finding.iteration}" if finding.iteration is not None else ""
    code = f" ({finding.code})" if finding.code else ""
    head = f'Run "{run_id}" {verdict}{where}{code}: {finding.message}'
    return f"{head} {decision_sentence(decision)}" if decision is not None else head


def _error_sentence(finding: DiagnosisFinding, decision: DecisionRef | None) -> str:
    where = f", at iteration {finding.iteration}" if finding.iteration is not None else ""
    code = f" ({finding.code})" if finding.code else ""
    head = f"The first error{where}{code}: {finding.message}"
    return f"{head} {decision_sentence(decision)}" if decision is not None else head


def _graph_sentence(run_id: str, verdict: Verdict, graph: GraphAttribution) -> str:
    if not graph.roots:
        return (
            f'Graph run "{run_id}" completed; there is nothing to attribute.'
            if verdict == "completed"
            else f'Graph run "{run_id}" is {verdict}: no node failed, was cancelled or blocked '
            "itself."
        )
    top = graph.roots[0]
    code = f" ({top.failure_code})" if top.failure_code else ""
    reason = f": {top.reason}" if top.reason else ""
    others = graph.root_count - 1
    sentence = (
        f'Graph run "{run_id}" {verdict}: node "{top.node_id}" carries the most responsibility '
        f"({top.responsibility:g} of {graph.total_responsibility:g}); it {top.status}{code}"
        f"{reason}, holds up {top.blocked_count} node(s) and repairing it alone releases "
        f"{len(top.released_alone) + top.released_alone_omitted}."
    )
    if others:
        sentence += f" {others} other node(s) stopped independently."
    if top.agent_terminal_decision is not None:
        behind = decision_sentence(top.agent_terminal_decision)
        sentence += f' Its agent run "{top.agent_run_id}": {behind}'
    return sentence
