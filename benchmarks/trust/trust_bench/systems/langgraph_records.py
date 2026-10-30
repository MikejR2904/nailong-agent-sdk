# Copyright (c) 2026 David Michael Indraputra

"""Map LangGraph's own message record (its checkpoint state) into the common event schema."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage, messages_from_dict

from ..schema import (
    ArtifactVersion,
    Event,
    Kind,
    SystemRecords,
    canonical,
    digest_bytes,
    digest_of,
)
from ..wire import WRITE_TOOLS, decision_payload, normalize_path, parse_envelope
from .langgraph_arm import LgRun


def _final_output(message: AIMessage) -> dict[str, Any] | None:
    try:
        value = json.loads(str(message.content))
    except ValueError:
        return {"text": str(message.content)}
    return value if isinstance(value, dict) else {"text": str(message.content)}


def langgraph_records(run: LgRun) -> SystemRecords:
    raw = json.loads(run.records_path.read_text(encoding="utf-8"))
    messages = messages_from_dict(raw)
    events: list[Event] = []
    versions: dict[str, list[ArtifactVersion]] = {}
    blobs: dict[str, bytes] = {}
    issued: dict[str, tuple[str | None, dict[str, Any]]] = {}
    counters: dict[str | None, int] = {}
    for message in messages:
        if isinstance(message, AIMessage):
            agent = message.name
            index = counters.get(agent, 0)
            counters[agent] = index + 1
            calls = [
                {"id": c["id"], "name": c["name"], "arguments": c.get("args", {})}
                for c in message.tool_calls
            ]
            final = None if calls else _final_output(message)
            payload = decision_payload(calls, final)
            events.append(
                Event(
                    Kind.MODEL_CALL,
                    agent_id=agent,
                    model_index=index,
                    digest=digest_of(payload),
                    content=canonical(payload),
                )
            )
            for call in payload["calls"]:
                issued[str(call["id"])] = (agent, call)
                events.append(
                    Event(
                        Kind.TOOL_CALL,
                        agent_id=agent,
                        model_index=index,
                        call_id=str(call["id"]),
                        name=str(call["name"]),
                        digest=digest_of(call),
                        content=canonical(call),
                    )
                )
        elif isinstance(message, ToolMessage):
            call_id = str(message.tool_call_id)
            envelope = parse_envelope(str(message.content))
            agent, call = issued.get(call_id, (None, {}))
            events.append(
                Event(
                    Kind.TOOL_RESULT,
                    agent_id=agent,
                    call_id=call_id,
                    name=call.get("name"),
                    status=str(envelope.get("status")),
                    digest=digest_of(envelope),
                    content=canonical(envelope),
                )
            )
            if call.get("name") in WRITE_TOOLS and envelope.get("status") == "succeeded":
                arguments = call.get("arguments", {})
                path = normalize_path(str(arguments.get("path")))
                data = str(arguments.get("content", "")).encode("utf-8")
                digest = digest_bytes(data)
                blobs[digest] = data
                versions.setdefault(path, []).append(
                    ArtifactVersion(path, digest, call_id, agent, len(versions.get(path, [])) + 1)
                )
                events.append(
                    Event(Kind.WRITE, agent_id=agent, call_id=call_id, path=path, digest=digest)
                )
    notes = [
        "actor comes from message names set by the harness's model; stock chat models set none"
    ]
    return SystemRecords("langgraph", events, versions, blobs.get, notes)
