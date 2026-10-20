# Copyright (c) 2026 David Michael Indraputra

"""The LangGraph arm for multi-agent cases: a StateGraph whose nodes are prebuilt ReAct agents."""

from __future__ import annotations

import json
import operator
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage, messages_to_dict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import create_react_agent

from ..acceptance import Acceptance
from ..cases import Case, FaultSpec
from ..recorder import BoundaryRecorder, WorkspaceWatcher
from ..truth import build_truth
from ..wire import PolicyEndpoint
from .langgraph_arm import LgRun, PolicyChatModel, build_tools
from .langgraph_gate import GATE_NODE, gate_node, rejection


class PlanState(TypedDict):
    history: Annotated[list[BaseMessage], operator.add]
    verdict: dict[str, Any]


def run_langgraph_graph(
    case: Case, fault: FaultSpec | None, run_dir: Path, *, acceptance: Acceptance | None = None
) -> LgRun:
    from .nailong import run_id_for

    run_id = run_id_for(case, fault)
    workspace = run_dir / "lg" / "workspace"
    case.materialize(workspace, fault)
    recorder = BoundaryRecorder(run_dir / "truth.jsonl")
    nodes = {node.label: node for node in case.nodes()}
    endpoints = {
        label: PolicyEndpoint(
            policy=case.node_policy(label, fault),
            recorder=recorder,
            agent_id=label,
            watcher=WorkspaceWatcher(workspace, paths=frozenset(node.outputs)),
        )
        for label, node in nodes.items()
    }
    saver = InMemorySaver()
    agents = {
        label: create_react_agent(
            PolicyChatModel(endpoint=endpoints[label], label=label),
            build_tools(workspace, node.outputs),
            name=label,
            checkpointer=saver,
        )
        for label, node in nodes.items()
    }

    def thread_of(label: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": f"{run_id}.{label}"}}

    def make_node(label: str) -> Any:
        limit = 2 * case.node_max_iterations(label, fault) + 1
        text = nodes[label].task_text

        def run(state: PlanState) -> dict[str, list[BaseMessage]]:
            result = agents[label].invoke(
                {"messages": [("user", text)]}, {**thread_of(label), "recursion_limit": limit}
            )
            return {"history": result["messages"]}

        return run

    builder = StateGraph(PlanState)
    for label in nodes:
        builder.add_node(label, make_node(label))
    for label, node in nodes.items():
        if node.dependencies:
            builder.add_edge(list(node.dependencies), label)
        else:
            builder.add_edge(START, label)
    needed = {dependency for node in nodes.values() for dependency in node.dependencies}
    if acceptance is not None:
        builder.add_node(GATE_NODE, gate_node(acceptance, workspace))
        builder.add_edge(GATE_NODE, END)
    for label in nodes:
        if label not in needed:
            builder.add_edge(label, END if acceptance is None else GATE_NODE)
    graph = builder.compile(checkpointer=InMemorySaver())
    config: dict[str, Any] = {"configurable": {"thread_id": run_id}}
    status, reason = "completed", None
    verdict = None
    try:
        graph.invoke({"history": []}, config)
        if acceptance is not None:
            verdict = graph.get_state(config).values.get("verdict")
            reason = rejection(verdict)
            status = "completed" if reason is None else "failed"
    except Exception as error:
        status, reason = "failed", f"{type(error).__name__}: {error}"
    finally:
        for endpoint in endpoints.values():
            endpoint.finish()
        recorder.close()
    messages = [
        message
        for label in nodes
        for message in agents[label].get_state(thread_of(label)).values.get("messages", [])
    ]
    records_path = run_dir / "lg" / "messages.json"
    records_path.write_text(
        json.dumps(messages_to_dict(messages), sort_keys=True), encoding="utf-8"
    )
    return LgRun(
        case,
        fault,
        run_id,
        run_dir,
        workspace,
        status,
        reason,
        build_truth(case, recorder.events),
        records_path,
        verdict,
    )
