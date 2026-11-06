"""Commands you configure to run around tool calls and the session (trusted settings only)."""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import AfterTool, BeforeTool, ToolEvent

from .console import Console
from .shell import run_shell

DEFAULT_TIMEOUT_SECONDS = 10.0
LIFECYCLE_EVENTS = (
    "session_start",
    "session_end",
    "prompt_submit",
    "turn_end",
    "assistant_message",
    "permission_asked",
    "compacted",
)
EDIT_TOOL_NAMES = ("write_file", "edit_file", "notebook_edit", "apply_patch")
_INPUT_CHARS = 8_000


def build_hooks(
    hooks: tuple[dict[str, Any], ...], project: Path, console: Console
) -> tuple[list[BeforeTool], list[AfterTool]]:
    before: list[BeforeTool] = []
    after: list[AfterTool] = []
    for hook in hooks:
        if hook["event"] == "pre_tool":
            before.append(_Hook(hook, project, console).before)
        elif hook["event"] in {"post_tool", "tool_failed"}:
            after.append(_Hook(hook, project, console).after)
        elif hook["event"] == "file_edited":
            for name in EDIT_TOOL_NAMES:
                edited = {**hook, "event": "file_edited", "tool": name}
                after.append(_Hook(edited, project, console).after)
    return before, after


class LifecycleHooks:
    def __init__(self, hooks: tuple[dict[str, Any], ...], project: Path) -> None:
        self._hooks = [hook for hook in hooks if hook["event"] in LIFECYCLE_EVENTS]
        self._project = project

    def has(self, event: str) -> bool:
        return any(hook["event"] == event for hook in self._hooks)

    async def run(self, event: str, **values: str) -> list[tuple[bool, str]]:
        results: list[tuple[bool, str]] = []
        env = {
            **os.environ,
            "NAILONG_HOOK_EVENT": event,
            "NAILONG_PROJECT": str(self._project),
            **{f"NAILONG_{key.upper()}": value[:_INPUT_CHARS] for key, value in values.items()},
        }
        for hook in self._hooks:
            if hook["event"] != event:
                continue
            record = await run_shell(
                hook["command"],
                self._project,
                env=env,
                timeout_seconds=float(hook.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
            )
            results.append((record.successful, record.output.strip()))
        return results


class _Hook:
    def __init__(self, hook: dict[str, Any], project: Path, console: Console) -> None:
        self._pattern = hook.get("tool", "*")
        self._command = hook["command"]
        self._timeout = float(hook.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self._event = hook["event"]
        self._project = project
        self._console = console

    async def before(self, event: ToolEvent) -> str | None:
        if not fnmatch.fnmatchcase(event.tool, self._pattern):
            return None
        record = await self._run(event)
        if record.successful:
            return None
        detail = record.output.strip()[-300:] or f"exit code {record.return_code}"
        return f"Hook refused {event.tool}: {detail}"

    async def after(self, event: ToolEvent) -> None:
        if not fnmatch.fnmatchcase(event.tool, self._pattern):
            return
        if self._event == "tool_failed" and (
            event.result is None or event.result.status == "succeeded"
        ):
            return
        record = await self._run(event)
        if not record.successful:
            self._console.line(f"Hook for {event.tool} failed: {record.output.strip()[-200:]}")

    async def _run(self, event: ToolEvent):
        env = {
            **os.environ,
            "NAILONG_HOOK_EVENT": self._event,
            "NAILONG_TOOL": event.tool,
            "NAILONG_TOOL_INPUT": json.dumps(event.arguments, default=str)[:_INPUT_CHARS],
            "NAILONG_PROJECT": str(self._project),
        }
        if event.result is not None:
            env["NAILONG_TOOL_STATUS"] = event.result.status
        return await run_shell(self._command, self._project, env=env, timeout_seconds=self._timeout)
