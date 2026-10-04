# Copyright (c) 2026 David Michael Indraputra

"""Git worktree isolation for agents executing concurrently against one repository.

Each worktree gives one concurrently-running agent its own checkout and
branch, so parallel file edits (e.g. sibling ``StateGraph`` waves) cannot
collide on the same working tree. This manager tracks only worktrees it
created itself, in memory, for the lifetime of the owning process — it does
not scan the filesystem to rediscover worktrees left over from a prior run.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from .worktree_models import AgentWorktreeInfo

_VALID_SLUG_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")
_MAX_SLUG_LENGTH = 64


def validate_worktree_slug(slug: str) -> str:
    """Return ``slug`` unchanged if it is safe to use as a path segment, else raise."""

    if not slug:
        raise ValueError("Worktree slug must not be empty.")
    if len(slug) > _MAX_SLUG_LENGTH:
        raise ValueError(f'Worktree slug "{slug}" exceeds {_MAX_SLUG_LENGTH} characters.')
    if slug.startswith(("/", "\\")):
        raise ValueError(f'Worktree slug "{slug}" must not be an absolute path.')
    for segment in slug.split("/"):
        if segment in (".", ".."):
            raise ValueError(f'Worktree slug "{slug}" must not contain "." or ".." segments.')
        if not _VALID_SLUG_SEGMENT.fullmatch(segment):
            raise ValueError(
                f'Worktree slug "{slug}" segments must contain only letters, digits, '
                "dots, underscores, and dashes."
            )
    return slug


class AgentWorktreeManager:
    """Create, list, and remove git worktrees beneath one managed base directory."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._worktrees: dict[str, AgentWorktreeInfo] = {}
        self._slug_locks: dict[str, asyncio.Lock] = {}

    async def create_worktree(
        self,
        repository_path: Path,
        slug: str,
        *,
        branch: str | None = None,
        agent_id: str | None = None,
    ) -> AgentWorktreeInfo:
        """Create a new worktree for ``slug``, or return the one already tracked for it."""

        validate_worktree_slug(slug)
        async with self._slug_locks.setdefault(slug, asyncio.Lock()):
            tracked = self._worktrees.get(slug)
            if tracked is not None:
                return tracked
            return await self._create_untracked(repository_path, slug, branch, agent_id)

    async def _create_untracked(
        self,
        repository_path: Path,
        slug: str,
        branch: str | None,
        agent_id: str | None,
    ) -> AgentWorktreeInfo:
        colliding = next(
            (other for other in self._worktrees if other.lower() == slug.lower() and other != slug),
            None,
        )
        if colliding is not None:
            raise ValueError(
                f'Worktree slug "{slug}" differs from the tracked slug "{colliding}" only by '
                "letter case, so both would use the same directory on a case-insensitive "
                "file system; choose a distinct slug."
            )
        resolved_repository = repository_path.resolve()
        await _require_repository_root(resolved_repository)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        flat_slug = _flatten_slug(slug)
        worktree_path = self._base_dir / flat_slug
        if worktree_path.exists():
            raise ValueError(
                f'Worktree path "{worktree_path}" already exists but is not tracked by this '
                "manager; remove it manually before reusing this slug."
            )
        worktree_branch = branch or f"agent-worktree-{flat_slug}"
        branch_exists, _stdout, _error = await _run_git(
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/heads/{worktree_branch}",
            cwd=resolved_repository,
        )
        arguments = (
            ("worktree", "add", str(worktree_path), worktree_branch)
            if branch_exists == 0
            else ("worktree", "add", "-b", worktree_branch, str(worktree_path), "HEAD")
        )
        code, _stdout, error = await _run_git(*arguments, cwd=resolved_repository)
        if code != 0:
            raise RuntimeError(f"git worktree add failed: {error}")

        info = AgentWorktreeInfo(
            slug=slug,
            path=str(worktree_path),
            branch=worktree_branch,
            base_repository_path=str(resolved_repository),
            created_at_utc=_now(),
            agent_id=agent_id,
        )
        self._worktrees[slug] = info
        return info

    async def remove_worktree(self, slug: str) -> bool:
        """Remove a tracked worktree. Returns ``False`` if no worktree is tracked for it."""

        info = self._worktrees.get(slug)
        if info is None:
            return False
        code, _stdout, error = await _run_git(
            "worktree", "remove", "--force", info.path, cwd=info.base_repository_path
        )
        if code != 0:
            raise RuntimeError(f"git worktree remove failed: {error}")
        del self._worktrees[slug]
        return True

    def get_worktree(self, slug: str) -> AgentWorktreeInfo | None:
        return self._worktrees.get(slug)

    def list_worktrees(self) -> list[AgentWorktreeInfo]:
        return sorted(self._worktrees.values(), key=lambda info: info.slug)


async def _require_repository_root(repository: Path) -> None:
    code, top_level, error = await _run_git("rev-parse", "--show-toplevel", cwd=repository)
    if code != 0:
        raise RuntimeError(f"git worktree add failed: {error}")
    if Path(top_level).resolve() != repository:
        raise ValueError(
            f'repository_path "{repository}" is not the root of a git repository: git reports '
            f'the enclosing repository "{top_level}". Pass that root explicitly so worktrees and '
            "branches are never created in a repository the caller did not name."
        )


def _flatten_slug(slug: str) -> str:
    return slug.replace("/", "+")


async def _run_git(*args: str, cwd: Path | str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    return (
        process.returncode or 0,
        stdout_bytes.decode("utf-8", errors="replace").strip(),
        stderr_bytes.decode("utf-8", errors="replace").strip(),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
