# Copyright (c) 2026 David Michael Indraputra

"""Read-only diagnosis of one run: where it first went wrong, what ended it and what it blocked."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from ..agent.retention import tombstone_for_run
from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..observability.audit_log import AuditTranscriptStore
from ..observability.run_checkpoints import verify_terminal_link
from ..observability.telemetry_models import TelemetryEvent
from ..observability.telemetry_store import TelemetryStore
from ..state.graph import StateGraph
from ..state.graph_models import GraphNodeStatus
from ..state.run_state_store import RunStateStore

MAX_FINDINGS = 50
MAX_ROOT_FAILURES = 50
MAX_LISTED_DESCENDANTS = 100
MAX_MESSAGE_CHARS = 600
MAX_UNFINISHED_NODES = 50
DIAGNOSIS_SCHEMA_VERSION = "agent-sdk-run-diagnosis-v1"

type Verdict = Literal["completed", "failed", "blocked", "cancelled", "incomplete"]


class DiagnosisFinding(StrictModel):
    """One recorded event that says something did not go as intended."""

    sequence: int = Field(ge=1)
    event_type: str
    iteration: int | None = None
    severity: Literal["error", "warning"]
    code: str | None = None
    message: str
    subject: str | None = None
    status: str | None = None
    terminal: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class IntegrityDiagnosis(StrictModel):
    telemetry_events: int = Field(ge=0)
    audit_entries: int = Field(ge=0)
    telemetry_chain_valid: bool
    audit_chain_valid: bool
    telemetry_chain_failure: dict[str, Any] | None = None
    audit_chain_failure: dict[str, Any] | None = None
    terminal_link: str
    terminal_link_detail: str

    @property
    def intact(self) -> bool:
        return (
            self.telemetry_chain_valid and self.audit_chain_valid and self.terminal_link != "broken"
        )


class GraphRootFailure(StrictModel):
    """A node that stopped a graph run for its own reason, and everything it held up."""

    node_id: str
    status: str
    reason: str | None = None
    failure_code: str | None = None
    diagnostics: list[str] = Field(default_factory=list)
    graph_event_sequence: int | None = None
    agent_run_id: str | None = None
    agent_terminal: DiagnosisFinding | None = None
    agent_first_problem: DiagnosisFinding | None = None
    blocked_descendants: list[str] = Field(default_factory=list)
    blocked_descendants_omitted: int = Field(default=0, ge=0)


class GraphDiagnosis(StrictModel):
    run_id: str
    node_count: int = Field(ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    roots: list[GraphRootFailure] = Field(default_factory=list)
    roots_omitted: int = Field(default=0, ge=0)
    blocked_downstream: int = Field(default=0, ge=0)
    unfinished: dict[str, str] = Field(default_factory=dict)
    unfinished_omitted: int = Field(default=0, ge=0)
    blocked_without_root: list[str] = Field(default_factory=list)


class RunDiagnosis(StrictModel):
    schema_version: str = DIAGNOSIS_SCHEMA_VERSION
    run_id: str
    verdict: Verdict
    ok: bool
    summary: str
    terminal: DiagnosisFinding | None = None
    first_problem: DiagnosisFinding | None = None
    first_error: DiagnosisFinding | None = None
    findings: list[DiagnosisFinding] = Field(default_factory=list)
    findings_omitted: int = Field(default=0, ge=0)
    integrity: IntegrityDiagnosis | None = None
    graph: GraphDiagnosis | None = None
    graph_record_error: str | None = None


@dataclass(frozen=True)
class OpenLedgers:
    """The read-only stores of one run root, opened once and shared by what reads them."""

    run_root: Path
    telemetry: TelemetryStore | None
    audit: AuditTranscriptStore | None
    runs: RunStateStore | None


@dataclass(frozen=True)
class Diagnosed:
    """A diagnosis together with the unbounded scan and graph it was drawn from."""

    diagnosis: RunDiagnosis
    scan: RunScan
    graph: StateGraph | None


@contextmanager
def open_ledgers(run_root: Path, run_id: str) -> Iterator[OpenLedgers]:
    if not run_root.is_dir():
        raise ValueError(f'Run root "{run_root}" does not exist or is not a directory.')
    resolved = run_root.resolve()
    database = resolved / ".agent-telemetry" / "telemetry.sqlite3"
    runs = RunStateStore(resolved, read_only=True)
    has_record = runs.exists(run_id)
    if not database.is_file() and not has_record:
        raise ValueError(
            f'Run root "{run_root}" has no telemetry database at "{database}" and no graph run '
            f'record for "{run_id}".'
        )
    telemetry = TelemetryStore(resolved, read_only=True) if database.is_file() else None
    audit = AuditTranscriptStore(resolved, read_only=True) if telemetry is not None else None
    try:
        yield OpenLedgers(resolved, telemetry, audit, runs if has_record else None)
    finally:
        if telemetry is not None:
            telemetry.close()
        if audit is not None:
            audit.close()


def diagnose_run(run_root: Path, run_id: str) -> RunDiagnosis:
    """Report where a run first went wrong without writing anything under the run root."""

    with open_ledgers(run_root, run_id) as ledgers:
        return diagnose_open(ledgers, run_id).diagnosis


def diagnose_open(ledgers: OpenLedgers, run_id: str) -> Diagnosed:
    run_root, telemetry, audit, runs = (
        ledgers.run_root,
        ledgers.telemetry,
        ledgers.audit,
        ledgers.runs,
    )
    events = list(telemetry.iter_events(run_id)) if telemetry is not None else []
    graph, record_error = _load_graph(runs, run_id)
    if not events and graph is None and record_error is None:
        raise ValueError(_unknown_run_message(run_root, run_id))
    scan = scan_events(events)
    findings, terminal = scan.findings, scan.terminal
    integrity = (
        _integrity(telemetry, audit, run_id, len(events))
        if telemetry is not None and audit is not None and events
        else None
    )
    graph_diagnosis = diagnose_graph(run_id, graph, telemetry) if graph is not None else None
    verdict = (
        _graph_verdict(graph_diagnosis)
        if graph_diagnosis is not None
        else _agent_verdict(scan.status)
    )
    ok = verdict == "completed" and (integrity is None or integrity.intact)
    first_problem = findings[0] if findings else None
    first_error = next((item for item in findings if item.severity == "error"), None)
    summary = _summary(
        run_id,
        verdict,
        terminal,
        first_problem,
        first_error,
        graph_diagnosis,
        integrity,
        record_error,
    )
    diagnosis = RunDiagnosis(
        run_id=run_id,
        verdict=verdict,
        ok=ok,
        summary=summary,
        terminal=terminal,
        first_problem=first_problem,
        first_error=first_error,
        findings=findings[:MAX_FINDINGS],
        findings_omitted=max(0, len(findings) - MAX_FINDINGS),
        integrity=integrity,
        graph=graph_diagnosis,
        graph_record_error=record_error,
    )
    return Diagnosed(diagnosis, scan, graph)


def _unknown_run_message(run_root: Path, run_id: str) -> str:
    try:
        tombstone = tombstone_for_run(run_root, run_id)
    except AgentSdkError:
        tombstone = None
    if tombstone is None:
        return (
            f'Run "{run_id}" is unknown: it has no telemetry events and no graph run record '
            f'under "{run_root}". Pass a telemetry run id or a graph run id.'
        )
    return (
        f'Run "{run_id}" was pruned at {tombstone.pruned_at_utc} by retention tombstone '
        f"{tombstone.sequence}; it held {tombstone.telemetry.event_count} events ending at hash "
        f"{tombstone.telemetry.head_hash}."
    )


def _load_graph(runs: RunStateStore | None, run_id: str) -> tuple[StateGraph | None, str | None]:
    if runs is None:
        return None, None
    try:
        return StateGraph.from_snapshot(runs.load(run_id).graph), None
    except (ValueError, KeyError, AgentSdkError, OSError) as error:
        return None, f"{type(error).__name__}: {error}"


def _integrity(
    telemetry: TelemetryStore, audit: AuditTranscriptStore, run_id: str, event_count: int
) -> IntegrityDiagnosis:
    telemetry_failure = telemetry.chain_break(run_id)
    audit_failure = audit.chain_break(run_id)
    link = verify_terminal_link(telemetry, audit, run_id)
    return IntegrityDiagnosis(
        telemetry_events=event_count,
        audit_entries=audit.entry_count(run_id),
        telemetry_chain_valid=telemetry_failure is None,
        audit_chain_valid=audit_failure is None,
        telemetry_chain_failure=telemetry_failure.model_dump(mode="json")
        if telemetry_failure
        else None,
        audit_chain_failure=audit_failure.model_dump(mode="json") if audit_failure else None,
        terminal_link=link.state.value,
        terminal_link_detail=link.detail,
    )


@dataclass(frozen=True)
class RunScan:
    """The findings of one run's telemetry events, the one that ended it and its final status."""

    findings: list[DiagnosisFinding]
    terminal: DiagnosisFinding | None
    status: str | None


def scan_events(events: list[TelemetryEvent]) -> RunScan:
    findings: list[DiagnosisFinding] = []
    agent_terminal: DiagnosisFinding | None = None
    run_terminal: DiagnosisFinding | None = None
    agent_status: str | None = None
    run_status: str | None = None
    for event in events:
        if event.event_type == "agent.terminated":
            agent_status = str(_details(event).get("status", "unknown"))
        elif event.event_type in {"run.completed", "run.terminated"}:
            run_status = event.status
        extractor = _EXTRACTORS.get(event.event_type)
        if extractor is None:
            continue
        for finding in extractor(event):
            findings.append(finding)
            if finding.terminal and event.event_type == "agent.terminated":
                agent_terminal = finding
            elif finding.terminal:
                run_terminal = finding
    if agent_terminal is not None and run_terminal is not None:
        findings = [item for item in findings if item is not run_terminal]
    return RunScan(findings, agent_terminal or run_terminal, agent_status or run_status)


def _details(event: TelemetryEvent) -> dict[str, Any]:
    details = event.payload.get("details")
    return details if isinstance(details, dict) else {}


def _iteration(event: TelemetryEvent) -> int | None:
    value = event.payload.get("iteration")
    return value if isinstance(value, int) else None


def _text(value: object, fallback: str) -> str:
    text = value if isinstance(value, str) and value else fallback
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return f"{text[: MAX_MESSAGE_CHARS - 3]}..."


def _code(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _terminated(event: TelemetryEvent) -> list[DiagnosisFinding]:
    details = _details(event)
    status = str(details.get("status", "unknown"))
    if status == "completed":
        return []
    failure = details.get("failure_details")
    named = failure if isinstance(failure, dict) else {}
    call_id = _code(named.get("tool_call_id"))
    return [
        DiagnosisFinding(
            sequence=event.sequence,
            event_type=event.event_type,
            iteration=_iteration(event),
            severity="warning" if status == "cancelled" else "error",
            code=_code(details.get("failure_code")),
            message=_text(details.get("reason"), f"The run ended {status}."),
            subject=_code(named.get("tool")),
            status=status,
            terminal=True,
            details={"tool_call_id": call_id} if call_id else {},
        )
    ]


def _run_terminated(event: TelemetryEvent) -> list[DiagnosisFinding]:
    status = event.status
    return [
        DiagnosisFinding(
            sequence=event.sequence,
            event_type=event.event_type,
            iteration=None,
            severity="warning" if status == "cancelled" else "error",
            message=_text(event.payload.get("reason"), f"The run ended {status}."),
            status=status,
            terminal=True,
        )
    ]


def _verification(event: TelemetryEvent) -> list[DiagnosisFinding]:
    details = _details(event)
    if details.get("passed") is not False:
        return []
    return [
        DiagnosisFinding(
            sequence=event.sequence,
            event_type=event.event_type,
            iteration=_iteration(event),
            severity="error",
            code=_code(details.get("failure_code")),
            message=_text(details.get("reason"), "The verification gate rejected the output."),
            subject=_code(details.get("gate_id")),
            details={
                key: details[key]
                for key in ("verifier_id", "verifier_kind", "output_digest")
                if key in details
            },
        )
    ]


def _output_rejected(event: TelemetryEvent) -> list[DiagnosisFinding]:
    details = _details(event)
    return [
        DiagnosisFinding(
            sequence=event.sequence,
            event_type=event.event_type,
            iteration=_iteration(event),
            severity="warning",
            code=_code(details.get("failure_code")),
            message=_text(details.get("reason"), "The candidate output was rejected."),
        )
    ]


def _tool_outcomes(event: TelemetryEvent) -> list[DiagnosisFinding]:
    outcomes = _details(event).get("outcomes")
    if not isinstance(outcomes, list):
        return []
    findings: list[DiagnosisFinding] = []
    for outcome in outcomes:
        if not isinstance(outcome, dict) or outcome.get("status") == "succeeded":
            continue
        status = str(outcome.get("status", "unknown"))
        tool = _code(outcome.get("tool"))
        call_id = _code(outcome.get("tool_call_id"))
        findings.append(
            DiagnosisFinding(
                sequence=event.sequence,
                event_type=event.event_type,
                iteration=_iteration(event),
                severity="warning" if status == "blocked" else "error",
                code=_code(outcome.get("failure_code")),
                message=_text(
                    outcome.get("error"),
                    f'Tool "{tool}" call "{call_id}" {status} and no reason was recorded.',
                ),
                subject=tool,
                details={"tool_call_id": call_id, "status": status},
            )
        )
    return findings


def _retrying(event: TelemetryEvent) -> list[DiagnosisFinding]:
    details = _details(event)
    code = _code(details.get("code"))
    return [
        DiagnosisFinding(
            sequence=event.sequence,
            event_type=event.event_type,
            iteration=_iteration(event),
            severity="warning",
            code=code,
            message=f"Transient provider error {code or 'unknown'}; retry attempt "
            f"{details.get('attempt')} after {details.get('delay_seconds')}s.",
        )
    ]


def _model_cancelled(event: TelemetryEvent) -> list[DiagnosisFinding]:
    return [
        DiagnosisFinding(
            sequence=event.sequence,
            event_type=event.event_type,
            iteration=_iteration(event),
            severity="warning",
            message="The model turn was cancelled.",
            details={
                key: value
                for key, value in _details(event).items()
                if isinstance(value, str | int | float | bool)
            },
        )
    ]


def _listener_failed(event: TelemetryEvent) -> list[DiagnosisFinding]:
    details = _details(event)
    return [
        DiagnosisFinding(
            sequence=event.sequence,
            event_type=event.event_type,
            iteration=_iteration(event),
            severity="warning",
            code=_code(details.get("error_type")),
            message=_text(details.get("message"), "The stream listener raised."),
        )
    ]


def _checkpoint_failed(event: TelemetryEvent) -> list[DiagnosisFinding]:
    payload = event.payload
    return [
        DiagnosisFinding(
            sequence=event.sequence,
            event_type=event.event_type,
            severity="warning",
            code=_code(payload.get("error_code")) or _code(payload.get("error_type")),
            message=_text(payload.get("error"), "The run-head checkpoint failed."),
        )
    ]


def _graph_executed(event: TelemetryEvent) -> list[DiagnosisFinding]:
    if event.status == "completed":
        return []
    failed = event.payload.get("failed_node_ids") or []
    blocked = event.payload.get("blocked_node_ids") or []
    return [
        DiagnosisFinding(
            sequence=event.sequence,
            event_type=event.event_type,
            severity="error",
            message=f"Graph execution {event.status}: failed nodes {failed}, blocked nodes "
            f"{blocked}.",
            details={"failed_node_ids": failed, "blocked_node_ids": blocked},
        )
    ]


def _stage_failure(event: TelemetryEvent) -> list[DiagnosisFinding]:
    return [
        DiagnosisFinding(
            sequence=event.sequence,
            event_type=event.event_type,
            severity="error",
            message=_text(event.payload.get("reason"), "The controller recorded a stage failure."),
            subject=event.status,
        )
    ]


def _controller_cancelled(event: TelemetryEvent) -> list[DiagnosisFinding]:
    return [
        DiagnosisFinding(
            sequence=event.sequence,
            event_type=event.event_type,
            severity="warning",
            message=_text(event.payload.get("reason"), "The controller was cancelled."),
        )
    ]


def _provenance(event: TelemetryEvent) -> list[DiagnosisFinding]:
    if event.status != "rejected":
        return []
    return [
        DiagnosisFinding(
            sequence=event.sequence,
            event_type=event.event_type,
            severity="error",
            message="The fan-in provenance contract was rejected.",
        )
    ]


_EXTRACTORS: dict[str, Callable[[TelemetryEvent], list[DiagnosisFinding]]] = {
    "agent.terminated": _terminated,
    "agent.verification-completed": _verification,
    "agent.output-rejected": _output_rejected,
    "agent.tool-batch-completed": _tool_outcomes,
    "agent.model-turn-retrying": _retrying,
    "agent.model-turn-cancelled": _model_cancelled,
    "agent.stream-listener-failed": _listener_failed,
    "run.terminated": _run_terminated,
    "run.checkpoint-failed": _checkpoint_failed,
    "graph.executed": _graph_executed,
    "controller.stage-failure": _stage_failure,
    "controller.cancelled": _controller_cancelled,
    "provenance.checked": _provenance,
}


def _agent_verdict(status: str | None) -> Verdict:
    if status is None:
        return "incomplete"
    if status == "completed":
        return "completed"
    return {"blocked": "blocked", "cancelled": "cancelled"}.get(status, "failed")


def diagnose_graph(
    run_id: str, graph: StateGraph, telemetry: TelemetryStore | None = None
) -> GraphDiagnosis:
    """Name the nodes that stopped a graph and exactly the nodes each one held up."""

    nodes = sorted(graph.nodes)
    statuses = {node_id: graph.status(node_id) for node_id in nodes}
    last_event: dict[tuple[str, str], int] = {}
    for event in graph.events:
        last_event[(event.node_id, event.status.value)] = event.sequence
    root_ids = list(graph.failure_roots())
    root_ids.sort(
        key=lambda node_id: (
            last_event.get((node_id, statuses[node_id].value), 0),
            node_id,
        )
    )
    held: dict[str, list[str]] = {}
    blocked = graph.root_cause_sets()
    for node_id, caused_by in blocked.items():
        for root in caused_by:
            held.setdefault(root, []).append(node_id)
    roots = [
        _root_failure(node_id, graph, statuses[node_id], last_event, telemetry, held)
        for node_id in root_ids[:MAX_ROOT_FAILURES]
    ]
    unattributed = [node_id for node_id, caused_by in blocked.items() if not caused_by]
    unfinished = {
        node_id: status.value
        for node_id, status in statuses.items()
        if status in {GraphNodeStatus.PENDING, GraphNodeStatus.RUNNABLE, GraphNodeStatus.RUNNING}
    }
    listed = dict(list(unfinished.items())[:MAX_UNFINISHED_NODES])
    return GraphDiagnosis(
        run_id=run_id,
        node_count=len(nodes),
        status_counts=dict(sorted(Counter(item.value for item in statuses.values()).items())),
        roots=roots,
        roots_omitted=max(0, len(root_ids) - MAX_ROOT_FAILURES),
        blocked_downstream=len(blocked),
        unfinished=listed,
        unfinished_omitted=len(unfinished) - len(listed),
        blocked_without_root=unattributed,
    )


def _root_failure(
    node_id: str,
    graph: StateGraph,
    status: GraphNodeStatus,
    last_event: dict[tuple[str, str], int],
    telemetry: TelemetryStore | None,
    held: dict[str, list[str]],
) -> GraphRootFailure:
    result = graph.result(node_id)
    descendants = held.get(node_id, [])
    agent_run_id = result.agent_run_id if result is not None else None
    terminal = first_problem = None
    if agent_run_id is not None and telemetry is not None:
        scan = scan_events(list(telemetry.iter_events(agent_run_id)))
        terminal = scan.terminal
        first_problem = scan.findings[0] if scan.findings else None
    return GraphRootFailure(
        node_id=node_id,
        status=status.value,
        reason=result.reason if result is not None else None,
        failure_code=result.failure_code if result is not None else None,
        diagnostics=list(result.diagnostics) if result is not None else [],
        graph_event_sequence=last_event.get((node_id, status.value)),
        agent_run_id=agent_run_id,
        agent_terminal=terminal,
        agent_first_problem=first_problem,
        blocked_descendants=descendants[:MAX_LISTED_DESCENDANTS],
        blocked_descendants_omitted=max(0, len(descendants) - MAX_LISTED_DESCENDANTS),
    )


def _graph_verdict(graph: GraphDiagnosis) -> Verdict:
    counts = graph.status_counts
    if counts.get("failed"):
        return "failed"
    if counts.get("cancelled"):
        return "cancelled"
    if counts.get("blocked"):
        return "blocked"
    if graph.unfinished:
        return "incomplete"
    return "completed"


def _summary(
    run_id: str,
    verdict: Verdict,
    terminal: DiagnosisFinding | None,
    first_problem: DiagnosisFinding | None,
    first_error: DiagnosisFinding | None,
    graph: GraphDiagnosis | None,
    integrity: IntegrityDiagnosis | None,
    record_error: str | None,
) -> str:
    parts: list[str] = []
    if integrity is not None and not integrity.intact:
        broken = integrity.telemetry_chain_failure or integrity.audit_chain_failure
        where = f" at sequence {broken.get('sequence')} ({broken.get('kind')})" if broken else ""
        parts.append(f"The evidence for this run is not intact{where}.")
    if graph is not None:
        parts.append(_graph_sentence(run_id, verdict, graph))
    elif terminal is not None:
        code = terminal.code or "no failure code"
        iteration = f" at iteration {terminal.iteration}" if terminal.iteration is not None else ""
        parts.append(f'Run "{run_id}" {verdict}{iteration}: {code}: {terminal.message}')
    elif verdict == "completed":
        parts.append(f'Run "{run_id}" completed.')
    elif integrity is None:
        parts.append(f'Run "{run_id}" has no telemetry events and no readable graph run record.')
    else:
        parts.append(f'Run "{run_id}" is {verdict}: its ledger holds no terminal event.')
    if first_error is not None and first_error is not terminal:
        parts.append(
            f"The first error was {first_error.event_type} at event {first_error.sequence}"
            f"{_code_suffix(first_error)}: {first_error.message}"
        )
    elif first_problem is not None and first_error is None and verdict == "completed":
        parts.append(
            f"It recorded a recoverable problem at event {first_problem.sequence}"
            f"{_code_suffix(first_problem)}: {first_problem.message}"
        )
    if record_error is not None:
        parts.append(f"The graph run record could not be read: {record_error}")
    text = " ".join(parts)
    return text if len(text) <= 2 * MAX_MESSAGE_CHARS else f"{text[: 2 * MAX_MESSAGE_CHARS - 3]}..."


def _code_suffix(finding: DiagnosisFinding) -> str:
    return f" ({finding.code})" if finding.code else ""


def _graph_sentence(run_id: str, verdict: Verdict, graph: GraphDiagnosis) -> str:
    if not graph.roots:
        return f'Graph run "{run_id}" {verdict}: {graph.node_count} nodes, {graph.status_counts}.'
    first = graph.roots[0]
    code = f" ({first.failure_code})" if first.failure_code else ""
    reason = f": {first.reason}" if first.reason else ""
    more = (
        f"; {len(graph.roots) + graph.roots_omitted - 1} other node(s) stopped independently"
        if len(graph.roots) + graph.roots_omitted > 1
        else ""
    )
    return (
        f'Graph run "{run_id}" {verdict}: node "{first.node_id}" {first.status}{code}{reason}'
        f"{more}; {graph.blocked_downstream} node(s) blocked downstream."
    )
