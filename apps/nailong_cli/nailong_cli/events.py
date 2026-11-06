"""One typed event stream for everything the CLI does: sessions, turns, tools, plans, memory.

Each event has an id, a type from a fixed list, the ids of the session, run, plan, task and agent it
belongs to, the id of the event that caused it, and W3C trace and span ids, so a whole piece of work
can be followed from the turn that started it down to each tool call. A span is a start event and
its end event with the same span id. The log is append-only, one JSON line per event, and each line
carries the hash of the line before it, so a removed or edited line shows. `check` reads a log and
reports what is missing: a start with no end, an end with no start, a parent that was never
recorded, a gap in the sequence, a broken chain.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nailong_agent_sdk.observability.trace_context import (
    format_traceparent,
    new_span_id,
    new_trace_id,
)

from .storage import append_durably

SPAN_PAIRS = {
    "session.start": "session.end",
    "turn.start": "turn.end",
    "tool.start": "tool.end",
    "plan.start": "plan.end",
    "task.start": "task.end",
}
POINT_EVENTS = {
    "task.retry": ("task",),
    "discovery.published": ("id", "topic"),
    "discovery.accepted": ("id",),
    "discovery.rejected": ("id",),
    "wiki.written": ("page", "revision"),
    "memory.compacted": ("turns",),
    "recovery.closed": ("event_type",),
}
REQUIRED = {
    "session.start": (),
    "session.end": (),
    "turn.start": (),
    "turn.end": ("status",),
    "tool.start": ("tool",),
    "tool.end": ("tool", "status"),
    "plan.start": ("tasks",),
    "plan.end": ("outcome",),
    "task.start": ("task",),
    "task.end": ("task", "status"),
    **POINT_EVENTS,
}
EVENT_TYPES = tuple(REQUIRED)
MAX_PAYLOAD_CHARS = 2_000
GENESIS = "0" * 64


class EventError(ValueError):
    pass


@dataclass(frozen=True)
class Span:
    """What a new event inherits: the event that caused it and the trace it belongs to."""

    trace_id: str
    span_id: str
    event_id: str
    plan_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None


current: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "nailong_current_span", default=None
)


@dataclass
class EventLog:
    path: Path
    run_id: str = field(default_factory=lambda: f"run-{uuid.uuid4().hex[:12]}")
    session_id: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _sequence: int = 0
    _previous: str = GENESIS
    _open: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    trace_id: str = field(default_factory=new_trace_id)
    root: Span | None = None

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_file():
            for event in read_events(self.path):
                self._sequence = event["sequence"]
                self._previous = event["hash"]

    def emit(
        self, event_type: str, payload: dict[str, Any] | None = None, **ids: str | None
    ) -> dict:
        """Record one event; it is a child of the current span unless ids say otherwise."""
        if event_type not in REQUIRED:
            raise EventError(
                f"{event_type!r} is not an event type; the types are {list(EVENT_TYPES)}."
            )
        body = {key: _clip(value) for key, value in (payload or {}).items()}
        missing = [name for name in REQUIRED[event_type] if name not in body]
        if missing:
            raise EventError(f"{event_type} needs {missing} in its payload.")
        parent = current.get() or self.root
        is_end = event_type in SPAN_PAIRS.values()
        is_start = event_type in SPAN_PAIRS
        span_id = ids.get("span_id")
        if is_end:
            if span_id is None:
                raise EventError(f"{event_type} ends a span; pass the span_id of its start.")
        elif is_start:
            span_id = span_id or new_span_id()
        else:
            span_id = new_span_id()
        with self._lock:
            opener = self._open.get(span_id) if is_end else None
            inherited = opener or {}
            self._sequence += 1
            record = {
                "sequence": self._sequence,
                "event_id": f"ev-{uuid.uuid4().hex[:12]}",
                "event_type": event_type,
                "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "run_id": self.run_id,
                "session_id": ids.get("session_id") or self.session_id,
                "plan_id": ids.get("plan_id")
                or inherited.get("plan_id")
                or (parent.plan_id if parent else None),
                "task_id": ids.get("task_id")
                or inherited.get("task_id")
                or (parent.task_id if parent else None),
                "agent_id": ids.get("agent_id")
                or inherited.get("agent_id")
                or (parent.agent_id if parent else None),
                "parent_event_id": ids.get("parent_event_id")
                or inherited.get("parent_event_id")
                or (parent.event_id if parent else None),
                "trace_id": ids.get("trace_id")
                or inherited.get("trace_id")
                or (parent.trace_id if parent else self.trace_id),
                "span_id": span_id,
                "parent_span_id": ids.get("parent_span_id")
                or inherited.get("parent_span_id")
                or (parent.span_id if parent else None),
                "traceparent": "",
                "payload": body,
                "previous_hash": self._previous,
            }
            record["traceparent"] = format_traceparent(record["trace_id"], span_id)
            record["hash"] = _digest(record)
            append_durably(self.path, json.dumps(record, separators=(",", ":")))
            self._previous = record["hash"]
            if is_start:
                self._open[span_id] = record
            elif is_end:
                self._open.pop(span_id, None)
        return record

    @contextlib.contextmanager
    def span(
        self, start_type: str, payload: dict[str, Any] | None = None, **ids: str | None
    ) -> Iterator[Span]:
        """Emit the start, make it the current span for everything inside, emit the end."""
        end_type = SPAN_PAIRS[start_type]
        started = self.emit(start_type, payload, **ids)
        inside = Span(
            started["trace_id"],
            started["span_id"],
            started["event_id"],
            started["plan_id"],
            started["task_id"],
            started["agent_id"],
        )
        token = current.set(inside)
        outcome: dict[str, Any] = {}
        try:
            yield inside
        except BaseException as error:
            outcome = {"status": "error", "error": f"{type(error).__name__}: {error}"}
            raise
        finally:
            current.reset(token)
            ending = {**(payload or {}), **outcome}
            ending.setdefault("status", "ok")
            ending.setdefault("outcome", ending["status"])
            self.emit(end_type, _fit(end_type, ending), span_id=started["span_id"])

    def start(
        self, start_type: str, payload: dict[str, Any] | None = None, **ids: str | None
    ) -> dict:
        return self.emit(start_type, payload, **ids)

    def end(self, started: dict, status: str = "ok", **payload: Any) -> dict:
        end_type = SPAN_PAIRS[started["event_type"]]
        body = {**started["payload"], **payload, "status": status, "outcome": status}
        return self.emit(
            end_type,
            _fit(end_type, body),
            span_id=started["span_id"],
            plan_id=started["plan_id"],
            task_id=started["task_id"],
            agent_id=started["agent_id"],
            parent_event_id=started["parent_event_id"],
            trace_id=started["trace_id"],
            parent_span_id=started["parent_span_id"],
        )

    def open_spans(self) -> list[dict[str, Any]]:
        return list(self._open.values())


def _fit(end_type: str, body: dict[str, Any]) -> dict[str, Any]:
    wanted = REQUIRED[end_type]
    keep = {"status", "error", "reason", *wanted}
    return {key: value for key, value in body.items() if key in keep}


def _clip(value: Any) -> Any:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return text if len(text) <= MAX_PAYLOAD_CHARS else text[:MAX_PAYLOAD_CHARS] + "...(truncated)"


def _digest(record: dict[str, Any]) -> str:
    material = {key: value for key, value in record.items() if key != "hash"}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def read_events(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    events = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            if number == len(lines):
                continue
            raise EventError(
                f"Line {number} of {path} is not JSON and is not the last line, so a crash did "
                "not cut it off."
            ) from None
    return events


def check(events: list[dict[str, Any]], *, running: bool = False) -> list[str]:
    """What is wrong with a log. `running` allows spans that have started and not yet ended."""
    problems: list[str] = []
    previous, expected = GENESIS, 1
    ids = {event["event_id"] for event in events}
    started: dict[str, dict[str, Any]] = {}
    for event in events:
        where = f"event {event['sequence']} ({event['event_type']})"
        if event["sequence"] != expected:
            problems.append(f"{where}: sequence jumps from {expected - 1} to {event['sequence']}")
            expected = event["sequence"]
        expected += 1
        if event["previous_hash"] != previous:
            problems.append(f"{where}: does not follow the event before it (chain broken)")
        if event["hash"] != _digest(event):
            problems.append(f"{where}: its content does not match its hash (edited)")
        previous = event["hash"]
        parent = event.get("parent_event_id")
        if parent and parent not in ids:
            problems.append(f"{where}: its parent {parent} was never recorded")
        kind = event["event_type"]
        if kind in SPAN_PAIRS:
            started[event["span_id"]] = event
        elif kind in SPAN_PAIRS.values():
            opener = started.pop(event["span_id"], None)
            if opener is None:
                problems.append(f"{where}: ends a span that never started")
            elif SPAN_PAIRS[opener["event_type"]] != kind:
                problems.append(f"{where}: ends a {opener['event_type']} span")
    if not running:
        for opener in started.values():
            problems.append(
                f"event {opener['sequence']} ({opener['event_type']}): started and never ended"
            )
    return problems


def recover(path: Path, run_id: str | None = None) -> list[dict[str, Any]]:
    """Close every span a dead process left open, marked interrupted, so the log accounts for it."""
    events = read_events(path)
    open_spans: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["event_type"] in SPAN_PAIRS:
            open_spans[event["span_id"]] = event
        elif event["event_type"] in SPAN_PAIRS.values():
            open_spans.pop(event["span_id"], None)
    if not open_spans:
        return []
    log = EventLog(path, run_id=run_id or events[-1]["run_id"], session_id=events[-1]["session_id"])
    closed = []
    for opener in sorted(open_spans.values(), key=lambda item: -item["sequence"]):
        closed.append(log.end(opener, "interrupted"))
        log.emit(
            "recovery.closed",
            {"event_type": opener["event_type"]},
            parent_event_id=opener["event_id"],
        )
    return closed


def pid_label() -> str:
    return f"pid-{os.getpid()}"


class ToolTracer:
    """Records each tool call as a span under whatever span is current (a turn or a plan task)."""

    def __init__(self, log: EventLog) -> None:
        self._log = log
        self._pending: dict[tuple, list[tuple[dict[str, Any], Span | None]]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(event: Any) -> tuple:
        arguments = json.dumps(event.arguments, sort_keys=True, default=str)
        return (event.tool, event.iteration, hashlib.sha256(arguments.encode()).hexdigest()[:12])

    async def before(self, event: Any) -> None:
        started = self._log.start("tool.start", {"tool": event.tool, "iteration": event.iteration})
        previous = current.get()
        current.set(
            Span(
                started["trace_id"],
                started["span_id"],
                started["event_id"],
                started["plan_id"],
                started["task_id"],
                started["agent_id"],
            )
        )
        with self._lock:
            self._pending.setdefault(self._key(event), []).append((started, previous))
        return None

    async def after(self, event: Any) -> None:
        with self._lock:
            queue = self._pending.get(self._key(event))
            entry = queue.pop(0) if queue else None
        if entry is None:
            return
        started, previous = entry
        current.set(previous)
        status = event.result.status if event.result is not None else "unknown"
        self._log.end(started, status)


def mark_alive(path: Path) -> None:
    path.with_suffix(".pid").write_text(str(os.getpid()), encoding="utf-8")


def mark_done(path: Path) -> None:
    path.with_suffix(".pid").unlink(missing_ok=True)


def recover_dead(directory: Path, skip: Path | None = None) -> dict[str, int]:
    """Close the open spans of every log whose process is gone; return spans closed per log."""
    from .taskstate import pid_alive

    closed: dict[str, int] = {}
    for path in sorted(directory.glob("run-*.jsonl")) if directory.is_dir() else []:
        if skip is not None and path == skip:
            continue
        marker = path.with_suffix(".pid")
        try:
            holder = int(marker.read_text(encoding="utf-8").strip()) if marker.is_file() else 0
        except ValueError:
            holder = 0
        if holder and pid_alive(holder):
            continue
        done = recover(path)
        if done:
            closed[path.name] = len(done)
        marker.unlink(missing_ok=True)
    return closed
