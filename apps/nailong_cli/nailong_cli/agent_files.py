"""Subagents defined as Markdown files, and subagents that run in the background."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import CancelToken, FunctionTool, tool
from nailong_agent_sdk.starter.skill_files import parse_frontmatter

from .approvals import subject_of
from .config import PROJECT_DIRECTORY, SUBAGENT_TOOLS
from .live import LiveWork
from .modes import parse_rule
from .rules import PermissionRules, Rule

MAX_BACKGROUND = 4
MAX_WAIT_SECONDS = 600.0
_EDIT_WORDS = {"write", "edit", "notebookedit", "write_file", "edit_file", "notebook_edit"}
_COMMAND_WORDS = {"bash", "run_command"}


def agent_roots(project: Path, home: Path) -> list[tuple[str, Path]]:
    return [
        ("user (~/.claude)", home / ".claude" / "agents"),
        ("user", home / ".config" / "nailong" / "agents"),
        ("project (.claude)", project / ".claude" / "agents"),
        ("project", project / PROJECT_DIRECTORY / "agents"),
    ]


def tool_level(value: Any, label: str) -> str:
    if value in (None, ""):
        return "read-only"
    if isinstance(value, str) and value in SUBAGENT_TOOLS:
        return value
    words = (
        [str(item) for item in value]
        if isinstance(value, list)
        else [part for part in str(value).replace(",", " ").split() if part]
    )
    lowered = {word.split("(")[0].lower() for word in words}
    if lowered & _COMMAND_WORDS:
        return "all"
    if lowered & _EDIT_WORDS:
        return "edit"
    return "read-only"


def load_agent_files(project: Path, home: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for source, root in agent_roots(project, home):
        for path in sorted(root.glob("*.md")) if root.is_dir() else []:
            label = f"Agent file {path}"
            meta, body = parse_frontmatter(path.read_text(encoding="utf-8"), label)
            name = str(meta.get("name") or path.stem)
            description = str(meta.get("description") or "").strip()
            if not description or not body.strip():
                raise ValueError(
                    f'{label} needs a "description:" line in its --- header and instructions '
                    "after it."
                )
            if not name.replace("-", "_").replace("_", "").isalnum():
                raise ValueError(f'{label}: name "{name}" must be letters, digits, "-" or "_".')
            spec: dict[str, Any] = {
                "description": description,
                "instructions": body.strip(),
                "tools": tool_level(meta.get("tools"), label),
                "source": f"{source}: {path}",
            }
            if meta.get("model"):
                spec["model"] = str(meta["model"])
            for key in ("temperature", "steps"):
                if meta.get(key) not in (None, ""):
                    try:
                        spec[key] = float(meta[key]) if key == "temperature" else int(meta[key])
                    except (TypeError, ValueError):
                        raise ValueError(
                            f"{label}: {key} must be a number, not {meta[key]!r}."
                        ) from None
            for key in ("deny", "allow"):
                if not meta.get(key) or (key == "allow" and not source.startswith("user")):
                    continue
                words = meta[key] if isinstance(meta[key], list) else str(meta[key]).split(",")
                spec[key] = [word.strip() for word in words if word.strip()]
            found[name.replace("-", "_")] = spec
    return found


class ScopedApprover:
    """A background subagent's approvals come from its own allow list; it never asks anyone."""

    def __init__(self, name: str, allowed: list[str]) -> None:
        self._rules = PermissionRules(
            [
                Rule("allow", (tool,), pattern, f"agent {name}")
                for tool, pattern in (parse_rule(text) for text in allowed)
            ]
        )

    async def __call__(self, request: Any) -> bool:
        verdict = self._rules.decide(request.tool, subject_of(request))
        return verdict is not None and verdict.action == "allow"


@dataclass
class BackgroundRun:
    id: str
    agent: str
    task: str
    started: float
    status: str = "running"
    text: str = ""
    reason: str = ""
    steps: int = 0


class BackgroundAgents:
    def __init__(self, make_helper: Callable[[str], Any], live: LiveWork, readonly: set[str]):
        self._make_helper = make_helper
        self._live = live
        self._readonly = readonly
        self._runs: dict[str, BackgroundRun] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def tools(self) -> list[FunctionTool]:
        if not self._readonly:
            return []

        def agent_start(agent: str, task: str) -> dict[str, Any]:
            """Start a read-only subagent on a task in the background and return its id at once."""
            return self.start(agent, task)

        def agent_result(run: str, wait_seconds: float = 0.0) -> dict[str, Any]:
            """Get a background subagent's status and, once it is done, its answer."""
            return self.result(run, wait_seconds)

        return [tool(agent_start), tool(agent_result, read_only=True)]

    def start(self, agent: str, task: str) -> dict[str, Any]:
        if agent not in self._readonly:
            raise ValueError(
                f'"{agent}" cannot run in the background; only read-only subagents, or ones '
                f"with an allow list in a user-level agent file, can: {sorted(self._readonly)}."
            )
        if not task.strip():
            raise ValueError("task must not be empty.")
        with self._lock:
            active = [run for run in self._runs.values() if run.status == "running"]
            if len(active) >= MAX_BACKGROUND:
                raise ValueError(
                    f"{MAX_BACKGROUND} background subagents are already running; wait for one "
                    "with agent_result."
                )
            self._counter += 1
            run = BackgroundRun(f"agent-{self._counter}", agent, task, time.monotonic())
            self._runs[run.id] = run
        helper = self._make_helper(agent)
        token = CancelToken()
        self._live.add(run.id, f"{run.id} [{agent}] (background)", token, helper)
        thread = threading.Thread(
            target=self._work, args=(run, helper, token), name=run.id, daemon=True
        )
        self._threads[run.id] = thread
        thread.start()
        return {"run": run.id, "agent": agent, "note": "Use agent_result to collect the answer."}

    def _work(self, run: BackgroundRun, helper: Any, token: CancelToken) -> None:
        try:
            answer = asyncio.run(helper.run(run.task, cancellation=token))
            run.steps = answer.steps
            if answer.ok:
                run.status, run.text = "done", answer.text
            else:
                run.status = "stopped" if token.is_cancelled() else "failed"
                run.reason = str(answer.reason)
        except Exception as error:
            run.status, run.reason = "failed", f"{type(error).__name__}: {error}"
        finally:
            self._live.remove(run.id)
            helper.close()

    def result(self, run_id: str, wait_seconds: float) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError(f'There is no background subagent "{run_id}"; runs: {self.ids()}.')
        thread = self._threads.get(run_id)
        wait = min(max(wait_seconds, 0.0), MAX_WAIT_SECONDS)
        if thread is not None and wait > 0 and run.status == "running":
            thread.join(timeout=wait)
        out: dict[str, Any] = {
            "run": run.id,
            "agent": run.agent,
            "status": run.status,
            "seconds": round(time.monotonic() - run.started, 1),
        }
        if run.status == "done":
            out["answer"] = run.text
        elif run.status != "running":
            out["reason"] = run.reason
        return out

    def ids(self) -> list[str]:
        return sorted(self._runs)

    def listing(self) -> list[str]:
        return [
            f"{run.id}: {run.status}  [{run.agent}]  {run.task[:60]}" for run in self._runs.values()
        ]

    def close(self) -> None:
        for run_id in self.ids():
            if self._runs[run_id].status == "running":
                self._live.cancel(run_id)
        for thread in self._threads.values():
            thread.join(timeout=5)
