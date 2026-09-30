# Copyright (c) 2026 David Michael Indraputra

"""Typed records for background command and agent-run tasks."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel


class TaskKind(StrEnum):
    COMMAND_TEMPLATE = "command-template"
    AGENT_RUN = "agent-run"


class TaskStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


class TaskRecord(StrictModel):
    """A pollable handle for one background unit of work.

    ``result`` holds a ``ProcessExecutionRecord`` payload for a command task or
    a host-supplied summary for an agent-run task; it is set only once the
    task reaches a terminal status.
    """

    task_id: str = Field(min_length=1)
    kind: TaskKind
    description: str
    status: TaskStatus
    created_at_utc: str
    started_at_utc: str
    ended_at_utc: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
