# Copyright (c) 2026 David Michael Indraputra

"""Typed records for git worktrees isolating concurrent agent execution."""

from __future__ import annotations

from pydantic import Field

from ..foundations.contracts import StrictModel


class AgentWorktreeInfo(StrictModel):
    """One git worktree checked out for a single concurrently-running agent."""

    slug: str = Field(min_length=1)
    path: str
    branch: str
    base_repository_path: str
    created_at_utc: str
    agent_id: str | None = None
