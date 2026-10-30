# Copyright (c) 2026 David Michael Indraputra

"""What Jev is shown: a bounded, redacted rendering of a run's own records."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..cases import Case
from ..diagnosers import Symptom
from ..schema import Event, Kind, StepRef, SystemRecords
from .budget import estimate_tokens

CONTENT_CAPS = (6000, 3000, 1500, 600, 300)
TEXT_CAPS = (1500, 800, 400, 200, 100)
MAX_STATE_TOKENS = 24_000
MISSING = "the record of this is missing"
WRITE_FIELDS = frozenset({"content"})


class StateTooLarge(ValueError):
    """A rendering does not fit the model's limit even at the tightest cut."""


def _cut(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return f"{text[:cap]}... [{len(text) - cap} more characters]"


def _compact(value: Any, text_cap: int, content_cap: int, key: str = "") -> Any:
    if isinstance(value, str):
        return _cut(value, content_cap if key in WRITE_FIELDS else text_cap)
    if isinstance(value, Mapping):
        lines = value.get("lines")
        if isinstance(lines, list) and all(isinstance(item, Mapping) for item in lines):
            rest = {k: v for k, v in value.items() if k not in {"lines", "truncated"}}
            rest["text"] = "\n".join(str(item.get("text", "")) for item in lines)
            return _compact(rest, text_cap, content_cap)
        return {str(k): _compact(v, text_cap, content_cap, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_compact(item, text_cap, content_cap) for item in value]
    return value


def step_id(agent: str, index: int) -> str:
    return f"{agent}#{index}"


def parse_step(identifier: str) -> StepRef:
    agent, _, index = identifier.rpartition("#")
    return (agent, int(index))


def _load(event: Event | None) -> Any:
    if event is None or event.content is None:
        return None
    return json.loads(event.content)


def _decision(event: Event, text_cap: int, content_cap: int) -> dict[str, Any]:
    agent, index = str(event.agent_id), int(event.model_index or 0)
    entry: dict[str, Any] = {"decision": step_id(agent, index), "agent": agent}
    body = _load(event)
    if body is None:
        entry["note"] = MISSING
        return entry
    if body.get("calls"):
        entry["calls"] = _compact(body["calls"], text_cap, content_cap)
    if body.get("final") is not None:
        entry["final"] = _compact(body["final"], text_cap, content_cap)
    return entry


def _result(
    call_id: str, call: Event | None, result: Event | None, text_cap: int, content_cap: int
) -> dict[str, Any]:
    entry: dict[str, Any] = {"result_of": call_id, "tool": None if call is None else call.name}
    body = _load(result)
    if body is None:
        entry["note"] = MISSING
        return entry
    entry["status"] = body.get("status")
    if body.get("status") == "succeeded":
        entry["output"] = _compact(body.get("output"), text_cap, content_cap)
    else:
        entry["error"] = _cut(str(body.get("error")), text_cap)
    return entry


def trace(
    records: SystemRecords, agents: Sequence[str], text_cap: int, content_cap: int
) -> list[dict[str, Any]]:
    events = records.events
    calls = {e.call_id: e for e in events if e.kind is Kind.TOOL_CALL}
    results = {e.call_id: e for e in events if e.kind is Kind.TOOL_RESULT}
    writes: dict[str | None, list[Event]] = {}
    for event in events:
        if event.kind is Kind.WRITE:
            writes.setdefault(event.call_id, []).append(event)
    issued: dict[StepRef, list[str]] = {}
    for event in events:
        if event.kind is Kind.TOOL_CALL and event.model_index is not None and event.call_id:
            issued.setdefault((str(event.agent_id), event.model_index), []).append(event.call_id)
    seen = [str(e.agent_id) for e in events if e.kind is Kind.MODEL_CALL]
    order = [a for a in agents if a in seen] + [a for a in dict.fromkeys(seen) if a not in agents]
    out: list[dict[str, Any]] = []
    for agent in order:
        turns = [e for e in events if e.kind is Kind.MODEL_CALL and e.agent_id == agent]
        for turn in sorted(turns, key=lambda e: e.model_index or 0):
            out.append(_decision(turn, text_cap, content_cap))
            for call_id in issued.get((agent, turn.model_index or 0), []):
                out.append(
                    _result(
                        call_id, calls.get(call_id), results.get(call_id), text_cap, content_cap
                    )
                )
                for write in writes.get(call_id, []):
                    out.append(
                        {
                            "write": write.path,
                            "digest": (write.digest or "")[:12],
                            "by_call": call_id,
                        }
                    )
    return out


def describe_step(entry: Mapping[str, Any]) -> str:
    identifier = entry["decision"]
    if "note" in entry:
        return f"{identifier}: {MISSING}"
    if "final" in entry:
        return f"{identifier}: final answer"
    names = ", ".join(
        f"{call['name']} {call.get('arguments', {}).get('path', '')}".strip()
        for call in entry.get("calls", [])
    )
    return f"{identifier}: {names}"


def diagnosis_input(
    case: Case, records: SystemRecords, symptom: Symptom
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    agents = [node.label for node in case.nodes()]
    for text_cap, content_cap in zip(TEXT_CAPS, CONTENT_CAPS, strict=True):
        events = trace(records, agents, text_cap, content_cap)
        state: dict[str, Any] = {
            "task": case.task_text,
            "symptom": {"kind": symptom.kind, "path": symptom.path},
            "events": events,
        }
        if estimate_tokens(json.dumps(state)) <= MAX_STATE_TOKENS and len(events) <= 256:
            candidates = [(e["decision"], describe_step(e)) for e in events if "decision" in e]
            return state, candidates
    raise StateTooLarge(
        f"{case.case_id}: the record has {len(events)} events and does not fit "
        f"{MAX_STATE_TOKENS} tokens even when every string is cut to {TEXT_CAPS[-1]} characters."
    )


def acceptance_input(case: Case, workspace: Path) -> dict[str, Any]:
    given: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace).as_posix()
        if not path.is_file() or relative == case.deliverable:
            continue
        if any(part.startswith(".") for part in path.relative_to(workspace).parts):
            continue
        try:
            given[relative] = _cut(path.read_text(encoding="utf-8"), CONTENT_CAPS[0])
        except UnicodeDecodeError:
            given[relative] = "[the file is not readable text]"
    target = workspace / case.deliverable
    content = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    return {
        "task": case.task_text,
        "files_given": given,
        "delivered": {"path": case.deliverable, "content": _cut(content, CONTENT_CAPS[0])},
    }


def allocation_input(policy_text: str, view: Mapping[str, Any]) -> dict[str, Any]:
    return {"policy": policy_text, "request": dict(view)}
