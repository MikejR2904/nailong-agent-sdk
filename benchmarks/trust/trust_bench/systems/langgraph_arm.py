# Copyright (c) 2026 David Michael Indraputra

"""The LangGraph arm: a prebuilt ReAct agent, same tools and policy, behind a fake chat model."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage, messages_to_dict
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import create_react_agent
from pydantic import ConfigDict

from ..acceptance import Acceptance
from ..cases import Case, FaultSpec
from ..recorder import BoundaryRecorder, WorkspaceWatcher
from ..schema import RunTruth
from ..truth import build_truth
from ..wire import PolicyEndpoint, tool_envelope
from .langgraph_gate import GATE_NODE, GateState, gate_node, rejection


def _failed(tool: str, message: str) -> str:
    error = f'Core tool "{tool}" raised ValueError: {message}'
    return json.dumps(tool_envelope(error=error, tool=tool), sort_keys=True)


def _inside(root: Path, relative: str) -> Path:
    target = Path(os.path.normpath(root / relative))
    if root not in (target, *target.parents):
        raise ValueError("The path must stay below the workspace.")
    return target


def build_tools(workspace: Path, declared: Sequence[str]) -> list[BaseTool]:
    root = workspace.resolve()

    def glob_tool(pattern: str, limit: int = 200) -> str:
        try:
            found = sorted(
                p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file()
            )
        except (ValueError, NotImplementedError) as error:
            return _failed("glob", str(error))
        output = {"matches": found[:limit], "limit": limit}
        return json.dumps(tool_envelope(output), sort_keys=True)

    def read_file_tool(path: str, offset: int = 0, limit: int = 200) -> str:
        try:
            target = _inside(root, path)
            if not target.is_file():
                raise ValueError("Requested file does not exist or is not a regular file.")
            try:
                lines = target.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError as error:
                raise ValueError("Requested file is not valid UTF-8 text.") from error
        except ValueError as error:
            return _failed("read_file", str(error))
        chosen = lines[offset : offset + limit]
        output = {
            "path": path,
            "offset": offset,
            "lines": [{"line": offset + i + 1, "text": text} for i, text in enumerate(chosen)],
            "truncated": len(lines) > offset + limit,
        }
        return json.dumps(tool_envelope(output), sort_keys=True)

    def write_draft_tool(path: str, content: str) -> str:
        try:
            if path not in declared:
                raise ValueError(f'"{path}" is not a declared output path.')
            target = _inside(root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8")
            target.write_bytes(data)
        except ValueError as error:
            return _failed("write_draft", str(error))
        sha = hashlib.sha256(data).hexdigest()
        output = {
            "artifact_id": f"sha256:{sha}",
            "kind": "draft",
            "relative_path": path,
            "sha256": sha,
            "size_bytes": len(data),
        }
        return json.dumps(tool_envelope(output), sort_keys=True)

    specs = {
        "glob": (glob_tool, "List workspace paths matched by a relative glob."),
        "read_file": (read_file_tool, "Read a bounded UTF-8 file below the workspace."),
        "write_draft": (write_draft_tool, "Write a declared output path."),
    }
    return [
        StructuredTool.from_function(func=func, name=name, description=description)
        for name, (func, description) in specs.items()
    ]


class PolicyChatModel(BaseChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    endpoint: Any = None
    label: str = "agent"

    @property
    def _llm_type(self) -> str:
        return "trust-bench-policy"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> PolicyChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        tool_messages = [
            (str(message.tool_call_id), str(message.content))
            for message in messages
            if isinstance(message, ToolMessage)
        ]
        decision = self.endpoint.respond(tool_messages)
        if decision.calls:
            calls = [
                {"name": c.name, "args": dict(c.arguments), "id": c.id, "type": "tool_call"}
                for c in decision.calls
            ]
            message = AIMessage(content="", tool_calls=calls, name=self.label)
        else:
            message = AIMessage(
                content=json.dumps(dict(decision.final or {}), sort_keys=True), name=self.label
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


@dataclass
class LgRun:
    case: Case
    fault: FaultSpec | None
    run_id: str
    run_dir: Path
    workspace: Path
    status: str
    reason: str | None
    truth: RunTruth
    records_path: Path
    verdict: dict[str, Any] | None = None


def run_langgraph(
    case: Case, fault: FaultSpec | None, run_dir: Path, *, acceptance: Acceptance | None = None
) -> LgRun:
    from .nailong import run_id_for

    if len(case.nodes()) > 1:
        from .langgraph_graph import run_langgraph_graph

        return run_langgraph_graph(case, fault, run_dir, acceptance=acceptance)
    run_id = run_id_for(case, fault)
    workspace = run_dir / "lg" / "workspace"
    case.materialize(workspace, fault)
    recorder = BoundaryRecorder(run_dir / "truth.jsonl")
    watcher = WorkspaceWatcher(workspace)
    endpoint = PolicyEndpoint(
        policy=case.policy(fault), recorder=recorder, agent_id=case.agent_label, watcher=watcher
    )
    graph = create_react_agent(
        PolicyChatModel(endpoint=endpoint, label=case.agent_label),
        build_tools(workspace, case.output_paths),
        name=case.agent_label,
        checkpointer=InMemorySaver(),
    )
    config = {
        "configurable": {"thread_id": run_id},
        "recursion_limit": 2 * case.max_iterations(fault) + 1,
    }
    status, reason = "completed", None
    verdict = None
    try:
        if acceptance is None:
            graph.invoke({"messages": [("user", case.task_text)]}, config)
        else:
            verdict = _gated_invoke(graph, config, case.task_text, acceptance, workspace, run_id)
            reason = rejection(verdict)
            status = "completed" if reason is None else "failed"
    except Exception as error:
        status, reason = "failed", f"{type(error).__name__}: {error}"
    finally:
        endpoint.finish()
        recorder.close()
    messages = graph.get_state(config).values.get("messages", [])
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


def _gated_invoke(
    agent: Any,
    config: dict[str, Any],
    task: str,
    acceptance: Acceptance,
    workspace: Path,
    run_id: str,
) -> dict[str, Any] | None:
    def run_agent(state: GateState) -> dict[str, list[BaseMessage]]:
        return {"history": agent.invoke({"messages": [("user", task)]}, config)["messages"]}

    builder = StateGraph(GateState)
    builder.add_node("agent", run_agent)
    builder.add_node(GATE_NODE, gate_node(acceptance, workspace))
    builder.add_edge(START, "agent")
    builder.add_edge("agent", GATE_NODE)
    builder.add_edge(GATE_NODE, END)
    outer = builder.compile(checkpointer=InMemorySaver())
    thread = {"configurable": {"thread_id": f"{run_id}.gate"}}
    outer.invoke({"history": []}, thread)
    return outer.get_state(thread).values.get("verdict")
