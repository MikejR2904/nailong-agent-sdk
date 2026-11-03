"""--worktree: work in a separate git checkout so the main working tree is left alone."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from nailong_agent_sdk.tools.worktrees import AgentWorktreeManager

from .config import PROJECT_DIRECTORY
from .shell import run_shell

_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}")


@dataclass(frozen=True)
class Worktree:
    slug: str
    path: Path
    branch: str
    base_commit: str
    manager: AgentWorktreeManager


async def create_worktree(project: Path, name: str | None) -> Worktree:
    slug = name or f"session-{uuid.uuid4().hex[:6]}"
    if not _SLUG.fullmatch(slug):
        raise ValueError(
            f'Worktree name "{slug}" must be letters, digits, ".", "_" or "-", up to 41 characters.'
        )
    head = await run_shell("git rev-parse HEAD", project, timeout_seconds=30)
    if not head.successful:
        raise ValueError(
            f"--worktree needs a git repository with at least one commit in {project}: "
            f"{head.output.strip()[-200:]}"
        )
    manager = AgentWorktreeManager(project / PROJECT_DIRECTORY / "worktrees")
    info = await manager.create_worktree(project, slug, branch=f"nailong/{slug}")
    return Worktree(slug, Path(info.path), info.branch, head.output.strip(), manager)


async def finish_worktree(worktree: Worktree) -> str:
    status = await run_shell("git status --porcelain", worktree.path, timeout_seconds=30)
    head = await run_shell("git rev-parse HEAD", worktree.path, timeout_seconds=30)
    unchanged = (
        status.successful
        and not status.output.strip()
        and head.successful
        and head.output.strip() == worktree.base_commit
    )
    if unchanged:
        await worktree.manager.remove_worktree(worktree.slug)
        return f"The worktree {worktree.slug} had no changes and was removed."
    return (
        f"The worktree {worktree.slug} has changes. Path: {worktree.path}  "
        f"Branch: {worktree.branch}"
    )
