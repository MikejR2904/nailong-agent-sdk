# Copyright (c) 2026 David Michael Indraputra

"""The SDK deployment of the authority endpoint: one governed task per ticket, approvals awaited."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskOptionError, TaskRunOptions
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    EscalationTarget,
    MemoryScope,
    ModelBinding,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    VersionedInstructions,
)
from nailong_agent_sdk.foundations.identifiers import file_safe_name
from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.telemetry_models import DecisionSource, TelemetryAuthority
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.tools.approvals import ApprovalRequest, ApprovalStatus
from nailong_agent_sdk.tools.core import core_tool_definitions

from ..authority.chains import ChainCase, ChainPolicy, chain_row
from ..authority.chains import allocations as chain_allocations
from ..authority.chains import prompt_for as chain_prompt
from ..authority.deciders import IDEAL, DecisionLog, Reviewer, approves
from ..authority.policy import AUTHORITY, DECIDER, FROM_AUTHORITY, Allocation
from ..authority.runs import (
    APPROVAL_TIMEOUT,
    AuthorityRun,
    NativeApproval,
    TicketOutcome,
    meta_of,
    prompt_for,
    rule_of,
    task_id_for,
)
from ..authority.tickets import AGENT, OpsCase, Ticket, TicketPolicy
from ..recorder import BoundaryRecorder, WorkspaceWatcher
from ..schema import Kind
from ..wire import PolicyEndpoint, SdkTransport
from .nailong import COMPLETE_SCHEMA, ENDPOINT
from .nailong_records import _agent_events, _journaled

CAPABILITIES = ["filesystem.read", "draft.write"]
POLL_SECONDS = 0.01
WAITED = "waited"
PREAPPROVED = "preapproved"


def definition_for(ticket: Ticket) -> AgentDefinition:
    write = {tool.name: tool for tool in core_tool_definitions()}["write_draft"]
    return AgentDefinition(
        identity=AGENT,
        instructions=VersionedInstructions(version="v1", text=prompt_for(ticket)),
        input_schema={"type": "object"},
        tools=[write],
        model_binding=ModelBinding(provider="bench", model="policy-1"),
        output_schema=COMPLETE_SCHEMA,
        memory_scope=MemoryScope.TASK_SCOPED,
        termination_policy=TerminationPolicy(
            max_iterations=3, status_field="status", escalation=EscalationTarget.NONE
        ),
    )


def options_for(ticket: Ticket, *, preapproved: bool) -> TaskRunOptions:
    return TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": {
                "capabilities": CAPABILITIES,
                "approved_capabilities": ["draft.write"] if preapproved else [],
                "approval_mode": "block" if preapproved else "wait",
                "approval_timeout_seconds": APPROVAL_TIMEOUT,
            },
            "declared_output_paths": [ticket.path],
        }
    )


def attribution(cls: str, rule: str) -> dict[str, Any]:
    """Who approved in advance, for the run arguments that can carry it."""
    wanted = {
        "approved_by": DecisionSource(
            authority=TelemetryAuthority(AUTHORITY[cls]), identifier=DECIDER[cls]
        ),
        "approval_reason": f"rule:{rule}",
    }
    accepted = inspect.signature(AgentTaskRunner.run).parameters
    return {name: value for name, value in wanted.items() if name in accepted}


async def _drive(
    runner: AgentTaskRunner,
    task_id: str,
    run: Any,
    decide: Callable[[ApprovalRequest], None],
) -> Any:
    future = asyncio.ensure_future(run)
    while not future.done():
        try:
            requests = runner.approvals(task_id)
        except TaskOptionError:
            requests = []
        for request in requests:
            if request.status is ApprovalStatus.PENDING:
                decide(request)
        await asyncio.sleep(POLL_SECONDS)
    return future.result()


def _run_ticket(
    case: OpsCase,
    ticket: Ticket,
    allocation: Allocation,
    services: AgentRuntimeServices,
    run_dir: Path,
    log: DecisionLog,
    reviewers: Mapping[str, Reviewer],
    *,
    deployment: str,
) -> TicketOutcome:
    task_id = task_id_for(case, ticket)
    workspace = services.run_root / "workspaces" / file_safe_name(task_id)
    recorder = BoundaryRecorder(run_dir / ticket.id / "truth.jsonl")
    endpoint = PolicyEndpoint(TicketPolicy(ticket), recorder, AGENT, WorkspaceWatcher(workspace))
    runner = AgentTaskRunner(services, transport=SdkTransport(endpoint))
    preapproved = allocation.rule == "auto-approve"
    task = ScopedAgentTask(
        id=task_id,
        input={},
        scope=TaskScope(label="ops", boundaries={}),
        locked_interface={},
        instructions=prompt_for(ticket),
        acceptance_criteria=["the ticket is carried out or refused"],
    )

    def decide(request: ApprovalRequest) -> None:
        cls = allocation.cls
        approved = approves(ticket, cls, case.case_id, reviewers)
        source = DecisionSource(
            authority=TelemetryAuthority(AUTHORITY[cls]), identifier=DECIDER[cls]
        )
        runner.decide_approval(
            task_id, request.approval_id, approved, f"rule:{allocation.rule}", source=source
        )
        log.record(ticket.id, cls, DECIDER[cls], allocation.rule, approved)

    carried: dict[str, Any] = {}
    if preapproved:
        log.record(ticket.id, "D", "run-options", allocation.rule, True)
    elif deployment == PREAPPROVED:
        cls = allocation.cls
        approved = approves(ticket, cls, case.case_id, reviewers)
        log.record(ticket.id, cls, DECIDER[cls], allocation.rule, approved)
        if not approved:
            return TicketOutcome(
                ticket, allocation, "not-launched", "rejected before launch", False, launched=False
            )
        preapproved = True
        carried = attribution(cls, allocation.rule)
    try:
        options = options_for(ticket, preapproved=preapproved)
        run = runner.run(definition_for(ticket), task, options, **carried)
        result = asyncio.run(_drive(runner, task_id, run, decide))
        status, reason = result.status.value, result.reason
    except Exception as error:
        status, reason = "failed", f"{type(error).__name__}: {error}"
    finally:
        endpoint.finish()
        recorder.close()
    executed = any(e.kind is Kind.WRITE and e.path == ticket.path for e in recorder.events)
    return TicketOutcome(ticket, allocation, status, reason, executed)


def native_approvals(run_root: Path, case: OpsCase) -> dict[str, NativeApproval]:
    """Read what the SDK's own telemetry, audit transcript and journal say about each ticket."""
    telemetry = TelemetryStore(run_root, read_only=True)
    audit = AuditTranscriptStore(run_root, read_only=True)
    journal = FileToolResultJournal(run_root, read_only=True)
    found: dict[str, NativeApproval] = {}
    try:
        for ticket in case.tickets:
            task_id = task_id_for(case, ticket)
            events = _agent_events(
                list(audit.iter_entries(task_id)), _journaled(journal, task_id), []
            )
            meta = None
            for event in events:
                if event.kind is Kind.TOOL_CALL and event.content is not None:
                    meta = meta_of(json.loads(event.content).get("arguments", {}))
            decided = telemetry.list_events(task_id, event_types=["approval.decided"])
            if not decided:
                found[ticket.id] = NativeApproval(meta=meta)
                continue
            payload = decided[-1].payload
            found[ticket.id] = NativeApproval(
                cls=FROM_AUTHORITY.get(str(payload.get("decision_authority"))),
                decider=payload.get("decided_by"),
                rule=rule_of(payload.get("decision_reason")),
                approved=decided[-1].status == "approved",
                meta=meta,
            )
    finally:
        telemetry.close()
        audit.close()
    return found


def run_sdk_tickets(
    case: OpsCase,
    allocation_arm: str,
    allocation: Mapping[str, Allocation],
    run_dir: Path,
    *,
    reviewers: Mapping[str, Reviewer] | None = None,
    deployment: str = WAITED,
) -> AuthorityRun:
    chosen = {"D": IDEAL, "M": IDEAL, "H": IDEAL} if reviewers is None else reviewers
    run_root = run_dir / "run"
    log = DecisionLog(run_dir / "decisions.jsonl")
    services = AgentRuntimeServices.open(run_root)
    outcomes: list[TicketOutcome] = []
    try:
        for ticket in case.tickets:
            outcomes.append(
                _run_ticket(
                    case,
                    ticket,
                    allocation[ticket.id],
                    services,
                    run_dir,
                    log,
                    chosen,
                    deployment=deployment,
                )
            )
    finally:
        services.audit_logs.close()
        services.telemetry.close()
    native = native_approvals(run_root, case)
    for outcome in outcomes:
        outcome.native = native[outcome.ticket.id]
    system = "sdk" if deployment == WAITED else "sdk-preapproved"
    return AuthorityRun(case, system, allocation_arm, outcomes, log)


def run_sdk_chain(case: ChainCase, run_dir: Path) -> dict[str, Any]:
    """One governed task that asks for a benign write and then a destructive one."""
    task_id = case.case_id
    run_root = run_dir / "run"
    services = AgentRuntimeServices.open(run_root)
    log = DecisionLog(run_dir / "decisions.jsonl")
    chosen = chain_allocations(case)
    recorder = BoundaryRecorder(run_dir / "truth.jsonl")
    workspace = run_root / "workspaces" / file_safe_name(task_id)
    endpoint = PolicyEndpoint(ChainPolicy(case), recorder, AGENT, WorkspaceWatcher(workspace))
    runner = AgentTaskRunner(services, transport=SdkTransport(endpoint))
    first = case.tickets[0]
    task = ScopedAgentTask(
        id=task_id,
        input={},
        scope=TaskScope(label="ops", boundaries={}),
        locked_interface={},
        instructions=chain_prompt(case),
        acceptance_criteria=["both actions are carried out or refused"],
    )

    def decide(request: ApprovalRequest) -> None:
        allocation = chosen[first.id]
        source = DecisionSource(
            authority=TelemetryAuthority(AUTHORITY[allocation.cls]),
            identifier=DECIDER[allocation.cls],
        )
        runner.decide_approval(
            task_id, request.approval_id, True, f"rule:{allocation.rule}", source=source
        )
        log.record(
            first.id, allocation.cls, DECIDER[allocation.cls], allocation.rule, True, "capability"
        )

    permissions = {
        "capabilities": CAPABILITIES,
        "approved_capabilities": [],
        "approval_mode": "wait",
        "approval_timeout_seconds": APPROVAL_TIMEOUT,
    }
    options = TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": permissions,
            "declared_output_paths": [t.path for t in case.tickets],
        }
    )
    definition = definition_for(first).model_copy(
        update={
            "termination_policy": TerminationPolicy(
                max_iterations=5, status_field="status", escalation=EscalationTarget.NONE
            )
        }
    )
    try:
        run = runner.run(definition, task, options)
        asyncio.run(_drive(runner, task_id, run, decide))
    finally:
        endpoint.finish()
        recorder.close()
        services.audit_logs.close()
        services.telemetry.close()
    executed = {
        t.id: any(e.kind is Kind.WRITE and e.path == t.path for e in recorder.events)
        for t in case.tickets
    }
    return chain_row(case, "sdk", log.entries, executed)
