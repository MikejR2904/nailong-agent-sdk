# Copyright (c) 2026 David Michael Indraputra

"""The LangGraph deployment of the authority endpoint: interrupt before the tools, then resume."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage, messages_to_dict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent

from ..authority.chains import ChainCase, ChainPolicy, chain_row
from ..authority.chains import allocations as chain_allocations
from ..authority.chains import prompt_for as chain_prompt
from ..authority.deciders import IDEAL, DecisionLog, Reviewer, approves
from ..authority.policy import DECIDER, Allocation
from ..authority.runs import (
    AuthorityRun,
    NativeApproval,
    TicketOutcome,
    meta_of,
    prompt_for,
    task_id_for,
)
from ..authority.tickets import AGENT, OpsCase, Ticket, TicketPolicy
from ..recorder import BoundaryRecorder, WorkspaceWatcher
from ..schema import Kind
from ..wire import PolicyEndpoint, tool_envelope
from .langgraph_arm import PolicyChatModel, build_tools

ANNOTATED = "langgraph"
PLAIN = "langgraph-plain"
RECURSION_LIMIT = 9


def _rejection(call: Mapping[str, Any]) -> ToolMessage:
    envelope = tool_envelope(
        error="The action was rejected by its decider and was not carried out.",
        tool=str(call["name"]),
    )
    return ToolMessage(
        content=json.dumps(envelope, sort_keys=True),
        tool_call_id=str(call["id"]),
        name=str(call["name"]),
    )


def _decide(
    agent: Any,
    config: dict[str, Any],
    case: OpsCase,
    ticket: Ticket,
    allocation: Allocation,
    log: DecisionLog,
    reviewers: Mapping[str, Reviewer],
    annotate: bool,
) -> None:
    state = agent.get_state(config)
    if not state.next:
        return
    pending = state.values["messages"][-1]
    cls = allocation.cls
    approved = approves(ticket, cls, case.case_id, reviewers)
    log.record(ticket.id, cls, DECIDER[cls], allocation.rule, approved)
    if annotate:
        note = {
            "class": cls,
            "decider": DECIDER[cls],
            "rule": allocation.rule,
            "approved": approved,
        }
        kwargs = {**pending.additional_kwargs, "approval": note}
        agent.update_state(
            config, {"messages": [pending.model_copy(update={"additional_kwargs": kwargs})]}
        )
    if approved:
        agent.invoke(None, config)
        return
    rejections = [_rejection(call) for call in pending.tool_calls]
    agent.update_state(config, {"messages": rejections}, as_node="tools")
    agent.invoke(None, config)


def _run_ticket(
    case: OpsCase,
    ticket: Ticket,
    allocation: Allocation,
    run_dir: Path,
    log: DecisionLog,
    reviewers: Mapping[str, Reviewer],
    annotate: bool,
) -> TicketOutcome:
    workspace = run_dir / ticket.id / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    recorder = BoundaryRecorder(run_dir / ticket.id / "truth.jsonl")
    endpoint = PolicyEndpoint(TicketPolicy(ticket), recorder, AGENT, WorkspaceWatcher(workspace))
    intercept = allocation.rule != "auto-approve"
    agent = create_react_agent(
        PolicyChatModel(endpoint=endpoint, label=AGENT),
        build_tools(workspace, [ticket.path]),
        name=AGENT,
        checkpointer=InMemorySaver(),
        interrupt_before=["tools"] if intercept else None,
    )
    config = {
        "configurable": {"thread_id": task_id_for(case, ticket)},
        "recursion_limit": RECURSION_LIMIT,
    }
    if not intercept:
        log.record(ticket.id, "D", "run-options", allocation.rule, True)
    status, reason = "completed", None
    try:
        agent.invoke({"messages": [("user", prompt_for(ticket))]}, config)
        if intercept:
            _decide(agent, config, case, ticket, allocation, log, reviewers, annotate)
    except Exception as error:
        status, reason = "failed", f"{type(error).__name__}: {error}"
    finally:
        endpoint.finish()
        recorder.close()
    messages = agent.get_state(config).values.get("messages", [])
    (run_dir / ticket.id / "messages.json").write_text(
        json.dumps(messages_to_dict(messages), sort_keys=True), encoding="utf-8"
    )
    executed = any(e.kind is Kind.WRITE and e.path == ticket.path for e in recorder.events)
    return TicketOutcome(ticket, allocation, status, reason, executed)


def native_approval(path: Path) -> NativeApproval:
    """Read what the persisted message history of one ticket says about its decision."""
    messages = json.loads(path.read_text(encoding="utf-8"))
    meta = None
    note: Mapping[str, Any] = {}
    for message in messages:
        data = message.get("data", {})
        if message.get("type") != "ai" or not data.get("tool_calls"):
            continue
        meta = meta_of(data["tool_calls"][0].get("args", {}))
        note = data.get("additional_kwargs", {}).get("approval", {})
    if not note:
        return NativeApproval(meta=meta)
    return NativeApproval(
        cls=note.get("class"),
        decider=note.get("decider"),
        rule=note.get("rule"),
        approved=note.get("approved"),
        meta=meta,
    )


def run_langgraph_tickets(
    case: OpsCase,
    allocation_arm: str,
    allocation: Mapping[str, Allocation],
    run_dir: Path,
    *,
    annotate: bool = True,
    reviewers: Mapping[str, Reviewer] | None = None,
) -> AuthorityRun:
    chosen = {"D": IDEAL, "M": IDEAL, "H": IDEAL} if reviewers is None else reviewers
    log = DecisionLog(run_dir / "decisions.jsonl")
    outcomes = []
    for ticket in case.tickets:
        outcome = _run_ticket(case, ticket, allocation[ticket.id], run_dir, log, chosen, annotate)
        outcome.native = native_approval(run_dir / ticket.id / "messages.json")
        outcomes.append(outcome)
    return AuthorityRun(case, ANNOTATED if annotate else PLAIN, allocation_arm, outcomes, log)


def run_langgraph_chain(case: ChainCase, run_dir: Path) -> dict[str, Any]:
    """One agent run that asks for a benign write and then a destructive one."""
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    recorder = BoundaryRecorder(run_dir / "truth.jsonl")
    endpoint = PolicyEndpoint(ChainPolicy(case), recorder, AGENT, WorkspaceWatcher(workspace))
    agent = create_react_agent(
        PolicyChatModel(endpoint=endpoint, label=AGENT),
        build_tools(workspace, [t.path for t in case.tickets]),
        name=AGENT,
        checkpointer=InMemorySaver(),
        interrupt_before=["tools"],
    )
    config = {"configurable": {"thread_id": case.case_id}, "recursion_limit": 15}
    chosen = chain_allocations(case)
    by_path = {t.path: t for t in case.tickets}
    log = DecisionLog(run_dir / "decisions.jsonl")
    try:
        agent.invoke({"messages": [("user", chain_prompt(case))]}, config)
        while True:
            state = agent.get_state(config)
            if not state.next:
                break
            pending = state.values["messages"][-1]
            ticket = by_path[pending.tool_calls[0]["args"]["path"]]
            allocation = chosen[ticket.id]
            log.record(ticket.id, allocation.cls, DECIDER[allocation.cls], allocation.rule, True)
            agent.invoke(None, config)
    finally:
        endpoint.finish()
        recorder.close()
    executed = {
        t.id: any(e.kind is Kind.WRITE and e.path == t.path for e in recorder.events)
        for t in case.tickets
    }
    return chain_row(case, "langgraph", log.entries, executed)
