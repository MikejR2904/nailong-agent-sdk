# Copyright (c) 2026 David Michael Indraputra

"""Structured, privacy-conscious runtime profiling for one bounded agent invocation.

The profiler records observable elapsed and process CPU time for harness phases. It never
records prompts, model reasoning, raw tool payloads, or provider-internal traces.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import monotonic_ns, process_time_ns
from typing import Any

from pydantic import Field, field_validator

from ..foundations.atomic_io import replace_atomic
from ..foundations.canonical import canonical_json, sha256_json
from ..foundations.contracts import StrictModel


class ProfileSpanKind(StrEnum):
    RUN = "run"
    CONTEXT_PROJECTION = "context-projection"
    MODEL_TURN = "model-turn"
    TOOL = "tool"
    VERIFICATION = "verification"


class ProfileSpanStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed-out"


class ProfileSpan(StrictModel):
    """One observable harness phase, measured from a monotonic clock."""

    span_id: str = Field(min_length=1)
    parent_span_id: str | None = None
    kind: ProfileSpanKind
    name: str = Field(min_length=1)
    started_at_utc: str
    duration_ns: int = Field(ge=0)
    cpu_duration_ns: int = Field(ge=0)
    status: ProfileSpanStatus
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def attributes_are_bounded_and_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        _assert_profile_safe(value)
        if len(canonical_json(value)) > 4_096:
            raise ValueError("profile span attributes exceed the 4,096-character bound")
        return value


class ProfilePhaseSummary(StrictModel):
    kind: ProfileSpanKind
    count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    cancelled_count: int = Field(ge=0)
    timed_out_count: int = Field(ge=0)
    duration_ns: int = Field(ge=0)
    cpu_duration_ns: int = Field(ge=0)


class AgentRunProfile(StrictModel):
    """Stable profile record that SDK consumers may persist or aggregate."""

    schema_version: str = "agent-run-profile-v1"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    agent_identity: str = Field(min_length=1)
    started_at_utc: str
    finished_at_utc: str | None = None
    status: ProfileSpanStatus | None = None
    wall_duration_ns: int | None = Field(default=None, ge=0)
    process_cpu_duration_ns: int | None = Field(default=None, ge=0)
    spans: list[ProfileSpan] = Field(default_factory=list)
    summaries: list[ProfilePhaseSummary] = Field(default_factory=list)
    integrity_hash: str = ""


@dataclass(frozen=True)
class ProfileSpanHandle:
    """Opaque handle returned to harness code while a span is active."""

    span_id: str


@dataclass(frozen=True)
class _ActiveSpan:
    handle: ProfileSpanHandle
    parent_span_id: str | None
    kind: ProfileSpanKind
    name: str
    started_at_utc: str
    started_monotonic_ns: int
    started_cpu_ns: int
    attributes: dict[str, Any]


class AgentRunProfiler:
    """Thread-safe profiler for a single BaseAgent invocation.

    The public lifecycle is ``begin_run()``, zero or more ``start_span()/finish_span()``
    pairs, then ``finish_run()``. BaseAgent owns that lifecycle; standalone consumers may
    use this class to profile a compatible custom harness.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._run_id: str | None = None
        self._task_id: str | None = None
        self._agent_identity: str | None = None
        self._started_at_utc: str | None = None
        self._started_monotonic_ns: int | None = None
        self._started_cpu_ns: int | None = None
        self._finished_at_utc: str | None = None
        self._status: ProfileSpanStatus | None = None
        self._spans: list[ProfileSpan] = []
        self._active: dict[str, _ActiveSpan] = {}
        self._counter = 0
        self._final_profile: AgentRunProfile | None = None

    def begin_run(self, run_id: str, task_id: str, agent_identity: str) -> ProfileSpanHandle:
        with self._lock:
            if self._run_id is not None:
                raise RuntimeError("AgentRunProfiler may profile only one run.")
            self._run_id = run_id
            self._task_id = task_id
            self._agent_identity = agent_identity
            self._started_at_utc = _utc_now()
            self._started_monotonic_ns = monotonic_ns()
            self._started_cpu_ns = process_time_ns()
            return self.start_span(ProfileSpanKind.RUN, "agent-run")

    def start_span(
        self,
        kind: ProfileSpanKind,
        name: str,
        *,
        attributes: dict[str, Any] | None = None,
        parent_span_id: str | None = None,
    ) -> ProfileSpanHandle:
        with self._lock:
            if self._run_id is None:
                raise RuntimeError("begin_run() must be called before starting a profile span.")
            if self._finished_at_utc is not None:
                raise RuntimeError("Cannot start a profile span after finish_run().")
            safe_attributes = dict(attributes or {})
            _assert_profile_safe(safe_attributes)
            if len(canonical_json(safe_attributes)) > 4_096:
                raise ValueError("profile span attributes exceed the 4,096-character bound")
            self._counter += 1
            handle = ProfileSpanHandle(span_id=f"span-{self._counter}")
            self._active[handle.span_id] = _ActiveSpan(
                handle=handle,
                parent_span_id=parent_span_id,
                kind=kind,
                name=name,
                started_at_utc=_utc_now(),
                started_monotonic_ns=monotonic_ns(),
                started_cpu_ns=process_time_ns(),
                attributes=safe_attributes,
            )
            return handle

    def finish_span(
        self,
        handle: ProfileSpanHandle,
        status: ProfileSpanStatus,
        *,
        attributes: dict[str, Any] | None = None,
    ) -> ProfileSpan:
        with self._lock:
            active = self._active.pop(handle.span_id, None)
            if active is None:
                raise RuntimeError(f'Unknown or already-finished profile span "{handle.span_id}".')
            span = ProfileSpan(
                span_id=active.handle.span_id,
                parent_span_id=active.parent_span_id,
                kind=active.kind,
                name=active.name,
                started_at_utc=active.started_at_utc,
                duration_ns=max(0, monotonic_ns() - active.started_monotonic_ns),
                cpu_duration_ns=max(0, process_time_ns() - active.started_cpu_ns),
                status=status,
                attributes={**active.attributes, **(attributes or {})},
            )
            self._spans.append(span)
            return span

    def finish_run(self, status: ProfileSpanStatus) -> AgentRunProfile:
        with self._lock:
            if self._run_id is None:
                raise RuntimeError("begin_run() must be called before finish_run().")
            if self._finished_at_utc is not None:
                return self.snapshot()
            for active in list(self._active.values()):
                self.finish_span(active.handle, ProfileSpanStatus.CANCELLED)
            self._finished_at_utc = _utc_now()
            self._status = status
            self._final_profile = self.snapshot()
            return self._final_profile

    def snapshot(self) -> AgentRunProfile:
        with self._lock:
            if self._final_profile is not None:
                return self._final_profile
            if (
                self._run_id is None
                or self._task_id is None
                or self._agent_identity is None
                or self._started_at_utc is None
                or self._started_monotonic_ns is None
                or self._started_cpu_ns is None
            ):
                raise RuntimeError("begin_run() must be called before snapshot().")
            wall_duration = (
                max(0, monotonic_ns() - self._started_monotonic_ns)
                if self._finished_at_utc is not None
                else None
            )
            cpu_duration = (
                max(0, process_time_ns() - self._started_cpu_ns)
                if self._finished_at_utc is not None
                else None
            )
            payload = {
                "schema_version": "agent-run-profile-v1",
                "run_id": self._run_id,
                "task_id": self._task_id,
                "agent_identity": self._agent_identity,
                "started_at_utc": self._started_at_utc,
                "finished_at_utc": self._finished_at_utc,
                "status": self._status.value if self._status is not None else None,
                "wall_duration_ns": wall_duration,
                "process_cpu_duration_ns": cpu_duration,
                "spans": [span.model_dump(mode="json") for span in self._spans],
                "summaries": [
                    summary.model_dump(mode="json") for summary in _summaries(self._spans)
                ],
                "integrity_hash": "",
            }
            return AgentRunProfile.model_validate(
                {**payload, "integrity_hash": sha256_json(payload)}
            )

    def write_json(self, destination: Path) -> Path:
        """Atomically persist a completed profile owned by the SDK consumer."""

        profile = self.snapshot()
        if profile.finished_at_utc is None:
            raise RuntimeError("finish_run() is required before writing a profile.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.tmp")
        temporary.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        replace_atomic(temporary, destination)
        return destination


def _summaries(spans: list[ProfileSpan]) -> list[ProfilePhaseSummary]:
    summaries: dict[ProfileSpanKind, dict[str, int]] = {}
    for span in spans:
        summary = summaries.setdefault(
            span.kind,
            {
                "count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "blocked_count": 0,
                "cancelled_count": 0,
                "timed_out_count": 0,
                "duration_ns": 0,
                "cpu_duration_ns": 0,
            },
        )
        summary["count"] += 1
        summary[f"{span.status.value.replace('-', '_')}_count"] += 1
        summary["duration_ns"] += span.duration_ns
        summary["cpu_duration_ns"] += span.cpu_duration_ns
    return [
        ProfilePhaseSummary(kind=kind, **summary)
        for kind, summary in sorted(summaries.items(), key=lambda item: item[0].value)
    ]


def _assert_profile_safe(value: Any) -> None:
    prohibited = {"chain_of_thought", "hidden_reasoning", "reasoning_trace", "scratchpad"}
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in prohibited:
                raise ValueError("Profile attributes must not contain hidden model reasoning.")
            _assert_profile_safe(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_profile_safe(nested)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
