"""Commands you configure to run around tool calls and the session (trusted settings only)."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import AfterTool, BeforeTool, ToolEvent

from .console import Console
from .events import current as events_current
from .hook_protocol import (
    interpret,
    lifecycle_payload,
    tool_matches,
    tool_payload,
    uses_claude_protocol,
)
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
    "pre_compact",
)
SAFE_ENVIRONMENT = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "COMSPEC", "WINDIR", "TEMP", "TMP",
    "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "USER", "USERNAME", "LANG",
    "LC_ALL", "TERM", "TZ",
)  # fmt: skip


def hook_environment(extra: tuple[str, ...] = ()) -> dict[str, str]:
    """Only the variables a command needs to run, plus the ones the user listed in hook_env."""
    return {name: os.environ[name] for name in (*SAFE_ENVIRONMENT, *extra) if name in os.environ}


async def run_hook_command(command, cwd, env, timeout, payload):
    if payload is None:
        return await run_shell(command, cwd, env=env, timeout_seconds=timeout)
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8", prefix="nailong-hook-"
    )
    try:
        handle.write(payload)
        handle.close()
        return await run_shell(
            f'( {command} ) < "{handle.name}"', cwd, env=env, timeout_seconds=timeout
        )
    finally:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)


EDIT_TOOL_NAMES = ("write_file", "edit_file", "notebook_edit", "apply_patch")
_INPUT_CHARS = 8_000


def build_hooks(
    hooks: tuple[dict[str, Any], ...],
    project: Path,
    console: Console,
    env_names: tuple[str, ...] = (),
    allow: Callable[[str, dict[str, Any]], None] | None = None,
) -> tuple[list[BeforeTool], list[AfterTool]]:
    before: list[BeforeTool] = []
    after: list[AfterTool] = []
    for hook in hooks:
        if hook["event"] == "pre_tool":
            before.append(_Hook(hook, project, console, env_names, allow).before)
        elif hook["event"] == "subagent_stop":
            stopped = {key: value for key, value in hook.items() if key != "matcher"}
            after.append(_Hook({**stopped, "tool": "agent_*"}, project, console, env_names).after)
        elif hook["event"] in {"post_tool", "tool_failed"}:
            after.append(_Hook(hook, project, console, env_names).after)
        elif hook["event"] == "file_edited":
            for name in EDIT_TOOL_NAMES:
                edited = {**hook, "event": "file_edited", "tool": name}
                after.append(_Hook(edited, project, console, env_names).after)
    return before, after


class LifecycleHooks:
    def __init__(
        self, hooks: tuple[dict[str, Any], ...], project: Path, env_names: tuple[str, ...] = ()
    ) -> None:
        self._hooks = [hook for hook in hooks if hook["event"] in LIFECYCLE_EVENTS]
        self._project = project
        self._env_names = env_names

    def has(self, event: str) -> bool:
        return any(hook["event"] == event for hook in self._hooks)

    async def run(self, event: str, **values: str) -> list[tuple[bool, str]]:
        results: list[tuple[bool, str]] = []
        span = events_current.get()
        env = {
            **hook_environment(self._env_names),
            **(
                {
                    "NAILONG_TRACE_ID": span.trace_id,
                    "NAILONG_SPAN_ID": span.span_id,
                    "NAILONG_PARENT_EVENT_ID": span.event_id,
                }
                if span
                else {}
            ),
            "NAILONG_HOOK_EVENT": event,
            "NAILONG_PROJECT": str(self._project),
            **{f"NAILONG_{key.upper()}": value[:_INPUT_CHARS] for key, value in values.items()},
        }
        for hook in self._hooks:
            if hook["event"] != event:
                continue
            claude = uses_claude_protocol(hook)
            record = await run_hook_command(
                hook["command"],
                self._project,
                env,
                float(hook.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
                lifecycle_payload(event, values.get("session", ""), str(self._project), values)
                if claude
                else None,
            )
            if claude:
                verdict = interpret(record.return_code, record.output, record.timed_out)
                if verdict.blocked:
                    results.append((False, verdict.reason))
                else:
                    results.append((True, verdict.context or verdict.warning))
                continue
            ok = record.successful or hook.get("on_failure") == "warn"
            results.append(
                (ok, _failure_text(record) if not record.successful else record.output.strip())
            )
        return results


class _Hook:
    def __init__(
        self,
        hook: dict[str, Any],
        project: Path,
        console: Console,
        env_names: tuple[str, ...] = (),
        allow: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._allow = allow
        self._env_names = env_names
        self._on_failure = hook.get("on_failure", "block")
        self._hook = hook
        self._claude = uses_claude_protocol(hook)
        self._command = hook["command"]
        self._timeout = float(hook.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self._event = hook["event"]
        self._project = project
        self._console = console

    async def before(self, event: ToolEvent) -> str | None:
        if not tool_matches(self._hook, event.tool):
            return None
        record = await self._run(event)
        if self._claude:
            verdict = interpret(record.return_code, record.output, record.timed_out)
            if verdict.blocked:
                return f"Hook refused {event.tool}: {verdict.reason}"
            if verdict.updated is not None:
                changed = dict(verdict.updated)
                if "file_path" in changed and "path" in event.arguments:
                    changed["path"] = changed.pop("file_path")
                event.arguments.clear()
                event.arguments.update(changed)
            if verdict.allowed and self._allow is not None:
                self._allow(event.tool, event.arguments)
            if verdict.warning:
                self._console.line(f"Hook for {event.tool} failed (continuing): {verdict.warning}")
            return None
        if record.successful:
            return None
        detail = _failure_text(record)
        if self._on_failure == "warn":
            self._console.line(f"Hook for {event.tool} failed (continuing): {detail}")
            return None
        return f"Hook refused {event.tool}: {detail}"

    async def after(self, event: ToolEvent) -> None:
        if not tool_matches(self._hook, event.tool):
            return
        if self._event == "tool_failed" and (
            event.result is None or event.result.status == "succeeded"
        ):
            return
        record = await self._run(event)
        if self._claude:
            verdict = interpret(record.return_code, record.output, record.timed_out)
            problem = verdict.reason if verdict.blocked else verdict.warning
            if problem:
                self._console.line(f"Hook for {event.tool} failed: {problem[-200:]}")
            return
        if not record.successful:
            self._console.line(f"Hook for {event.tool} failed: {_failure_text(record)[-200:]}")

    async def _run(self, event: ToolEvent):
        env = {
            **hook_environment(self._env_names),
            "NAILONG_HOOK_EVENT": self._event,
            "NAILONG_TOOL": event.tool,
            "NAILONG_TOOL_INPUT": json.dumps(event.arguments, default=str)[:_INPUT_CHARS],
            "NAILONG_PROJECT": str(self._project),
        }
        if event.result is not None:
            env["NAILONG_TOOL_STATUS"] = event.result.status
        payload = None
        if self._claude:
            span = events_current.get()
            payload = tool_payload(
                self._event,
                span.trace_id if span else "",
                str(self._project),
                event.tool,
                event.arguments,
                getattr(event.result, "output", None) if event.result is not None else None,
            )
        return await run_hook_command(self._command, self._project, env, self._timeout, payload)


def _failure_text(record: Any) -> str:
    if getattr(record, "timed_out", False):
        return f"timed out ({record.output.strip()[-200:] or 'no output'})"
    return record.output.strip()[-300:] or f"exit code {record.return_code}"
