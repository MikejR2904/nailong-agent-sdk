# Copyright (c) 2026 David Michael Indraputra

"""One deterministic policy behind every system's model boundary, recorded independently."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .recorder import BoundaryRecorder, WorkspaceWatcher
from .schema import Event, Kind, canonical, digest_of

WRITE_TOOLS = frozenset({"write_draft", "edit_draft"})


@dataclass(frozen=True)
class ToolCallSpec:
    id: str
    name: str
    arguments: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class Decision:
    calls: tuple[ToolCallSpec, ...] = ()
    final: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if bool(self.calls) == (self.final is not None):
            raise ValueError(
                "a decision is either tool calls or a final output, not both or neither."
            )


@dataclass(frozen=True)
class Observation:
    call_id: str
    name: str
    arguments: Mapping[str, Any]
    envelope: Mapping[str, Any]

    @property
    def ok(self) -> bool:
        return self.envelope.get("status") == "succeeded"

    @property
    def output(self) -> Any:
        return self.envelope.get("output")


class Policy(Protocol):
    def decide(self, observed: Sequence[Observation]) -> Decision: ...


def tool_envelope(
    output: Any = None, *, error: str | None = None, tool: str = ""
) -> dict[str, Any]:
    if error is None:
        return {"error": None, "failure": None, "output": output, "status": "succeeded"}
    failure = {
        "code": "CORE_TOOL_EXECUTION_FAILED",
        "message": error,
        "details": {"tool": tool, "error_type": "ValueError"},
    }
    return {"error": error, "failure": failure, "output": None, "status": "failed"}


def parse_envelope(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return {"error": None, "failure": None, "output": content, "status": "unparsed"}
    if isinstance(value, dict) and "status" in value:
        return value
    return {"error": None, "failure": None, "output": value, "status": "unparsed"}


def decision_payload(
    calls: Sequence[Mapping[str, Any]], final: Mapping[str, Any] | None
) -> dict[str, Any]:
    return {
        "calls": [
            {"id": call["id"], "name": call["name"], "arguments": dict(call["arguments"])}
            for call in calls
        ],
        "final": None if final is None else dict(final),
    }


def normalized_decision(decision: Decision) -> dict[str, Any]:
    return decision_payload([call.as_dict() for call in decision.calls], decision.final)


def normalize_path(value: str) -> str:
    return value.replace("\\", "/")


@dataclass
class PolicyEndpoint:
    policy: Policy
    recorder: BoundaryRecorder
    agent_id: str
    watcher: WorkspaceWatcher | None = None
    _issued: dict[str, ToolCallSpec] = field(default_factory=dict)
    _pending: list[str] = field(default_factory=list)
    _observations: list[Observation] = field(default_factory=list)
    _model_index: int = 0

    def respond(self, tool_messages: Sequence[tuple[str, str]]) -> Decision:
        contents = dict(tool_messages)
        arrived = [call_id for call_id in self._pending if call_id in contents]
        for call_id in arrived:
            self._pending.remove(call_id)
            spec = self._issued[call_id]
            envelope = parse_envelope(contents[call_id])
            self.recorder.record(
                Event(
                    Kind.TOOL_RESULT,
                    agent_id=self.agent_id,
                    call_id=call_id,
                    name=spec.name,
                    digest=digest_of(envelope),
                    status=str(envelope.get("status")),
                    content=canonical(envelope),
                )
            )
            self._observations.append(
                Observation(call_id, spec.name, dict(spec.arguments), envelope)
            )
        self.flush_writes()
        decision = self.policy.decide(list(self._observations))
        self._record_decision(decision)
        return decision

    def flush_writes(self) -> None:
        if self.watcher is None:
            return
        for path, digest in self.watcher.changes():
            self.recorder.record(
                Event(
                    Kind.WRITE,
                    agent_id=self.agent_id,
                    call_id=self._writer_of(path),
                    path=path,
                    digest=digest,
                )
            )

    def finish(self) -> None:
        self.flush_writes()

    def _writer_of(self, path: str) -> str | None:
        for call_id in reversed(list(self._issued)):
            spec = self._issued[call_id]
            if spec.name in WRITE_TOOLS and normalize_path(str(spec.arguments.get("path"))) == path:
                return call_id
        return None

    def _record_decision(self, decision: Decision) -> None:
        index = self._model_index
        self._model_index += 1
        payload = normalized_decision(decision)
        self.recorder.record(
            Event(
                Kind.MODEL_CALL,
                agent_id=self.agent_id,
                model_index=index,
                digest=digest_of(payload),
                content=canonical(payload),
            )
        )
        for spec in decision.calls:
            self._issued[spec.id] = spec
            self._pending.append(spec.id)
            self.recorder.record(
                Event(
                    Kind.TOOL_CALL,
                    agent_id=self.agent_id,
                    model_index=index,
                    call_id=spec.id,
                    name=spec.name,
                    digest=digest_of(spec.as_dict()),
                    content=canonical(spec.as_dict()),
                )
            )


def sdk_body(decision: Decision) -> dict[str, Any]:
    if decision.calls:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(dict(call.arguments))},
                }
                for call in decision.calls
            ],
        }
        finish = "tool_calls"
    else:
        message = {
            "role": "assistant",
            "content": json.dumps({"type": "final", "output": dict(decision.final or {})}),
        }
        finish = "stop"
    return {
        "id": "chatcmpl-bench",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }


class SdkTransport:
    def __init__(self, endpoint: PolicyEndpoint) -> None:
        self.endpoint = endpoint

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        messages = payload.get("messages", [])
        tool_messages = [
            (str(message["tool_call_id"]), str(message.get("content") or ""))
            for message in messages
            if message.get("role") == "tool"
        ]
        return sdk_body(self.endpoint.respond(tool_messages))
