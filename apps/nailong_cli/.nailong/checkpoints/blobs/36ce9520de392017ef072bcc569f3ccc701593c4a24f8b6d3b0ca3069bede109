"""Asking before a write or a command, and remembering the answer only as the user chose."""

from __future__ import annotations

import asyncio
import difflib
from pathlib import Path

from nailong_agent_sdk.starter import ApprovalRequest

from .commandline import allowed_by, denied_by, is_read_only_command
from .console import Console
from .modes import EDIT_TOOLS, ModeRef
from .permissions import PermissionStore

NOT_SANDBOXED = "NOT SANDBOXED"
COMMAND_TOOLS = frozenset({"run_command", "start_background"})
_DIFF_LINES = 40


class Approver:
    def __init__(
        self,
        console: Console,
        store: PermissionStore,
        project: Path,
        sandbox_label: str,
        *,
        mode: ModeRef | None = None,
        allow_unsandboxed: bool = False,
        allow_rules: list[tuple[str, str]] | None = None,
        deny_rules: list[tuple[str, str]] | None = None,
    ) -> None:
        self._console = console
        self._store = store
        self._project = project
        self._sandbox = sandbox_label
        self._mode = mode or ModeRef()
        self._allow_unsandboxed = allow_unsandboxed
        self._allow = allow_rules or []
        self._deny = deny_rules or []
        self._session_tools: set[str] = set()

    @property
    def session_tools(self) -> set[str]:
        return self._session_tools

    async def __call__(self, request: ApprovalRequest) -> bool:
        subject = subject_of(request)
        unsandboxed = request.tool in COMMAND_TOOLS and self._sandbox == NOT_SANDBOXED
        attended = self._console.interactive
        if _matches(self._deny, request.tool, subject, denied_by):
            return self._refuse(request, "a --disallow rule forbids it")
        if unsandboxed and not attended and not self._allow_unsandboxed:
            return self._refuse(
                request, "no sandbox is active and --allow-unsandboxed was not given"
            )
        if self._mode.value == "bypass" and (not unsandboxed or self._allow_unsandboxed):
            return True
        if self._mode.value in {"accept-edits", "auto"} and request.tool in EDIT_TOOLS:
            return True
        if (
            self._mode.value == "auto"
            and request.tool == "run_command"
            and is_read_only_command(subject)
        ):
            return True
        if _matches(self._allow, request.tool, subject, allowed_by):
            return True
        if self._store.allows(self._project, request.tool, subject):
            return True
        if request.tool in self._session_tools and not unsandboxed:
            return True
        if not attended:
            return self._refuse(request, "it needs approval and there is no terminal")
        return await asyncio.to_thread(self._ask, request, subject, unsandboxed)

    def _choose(self, request: ApprovalRequest, subject: str, unsandboxed: bool) -> bool:
        console = self._console
        labels = [
            "Yes",
            "Yes, for this session" + (" (not offered: no sandbox)" if unsandboxed else ""),
            "Yes, and always for this",
            "No",
        ]
        while True:
            assert console.chooser is not None
            with console.reading_input():
                picked = console.chooser(f"Approve {request.tool}?", labels)
            if picked == 0:
                return True
            if picked == 1:
                if unsandboxed:
                    console.line(
                        "Commands are never allowed for a whole session without a sandbox."
                    )
                    continue
                self._session_tools.add(request.tool)
                return True
            if picked == 2:
                self._store.add(self._project, request.tool, subject)
                return True
            return False

    def _refuse(self, request: ApprovalRequest, reason: str) -> bool:
        self._console.line(f"Refused {request.tool}: {reason}.")
        return False

    def _ask(self, request: ApprovalRequest, subject: str, unsandboxed: bool) -> bool:
        console = self._console
        console.line()
        console.line(console.style(f"Approve {request.tool}?", "bold"))
        console.line(describe(request, self._project))
        if request.tool in COMMAND_TOOLS:
            label = self._sandbox
            console.line(console.style(f"sandbox: {label}", "red" if unsandboxed else "dim"))
        if console.chooser is not None:
            return self._choose(request, subject, unsandboxed)
        while True:
            prompt = (
                "1 Yes  2 Yes, for this session  3 Yes, always  4 No > "
                if console.fancy
                else "[y]es once  [s]ession  [a]lways  [n]o > "
            )
            answer = console.ask(prompt).lower()
            choice = _NUMBERED.get(answer, answer)
            if choice in {"y", "yes"}:
                return True
            if choice in {"n", "no", ""}:
                return False
            if choice in {"s", "session"}:
                if unsandboxed:
                    console.line(
                        "Commands are never allowed for a whole session without a sandbox."
                    )
                    continue
                self._session_tools.add(request.tool)
                return True
            if choice in {"a", "always"}:
                self._store.add(self._project, request.tool, subject)
                return True
            console.line("Please answer y, s, a or n.")


_NUMBERED = {"1": "y", "2": "s", "3": "a", "4": "n"}


def _matches(rules: list[tuple[str, str]], tool: str, subject: str, test) -> bool:
    patterns = [pattern for rule_tool, pattern in rules if rule_tool == tool]
    return bool(patterns) and test(patterns, tool, subject)


def subject_of(request: ApprovalRequest) -> str:
    arguments = request.arguments
    if request.tool in COMMAND_TOOLS:
        return str(arguments.get("command", ""))
    if request.tool == "web_fetch":
        return str(arguments.get("url", ""))
    if request.tool == "web_search":
        return str(arguments.get("query", ""))
    return str(arguments.get("path", ""))


def describe(request: ApprovalRequest, project: Path) -> str:
    arguments = request.arguments
    if request.tool in COMMAND_TOOLS:
        return f"  $ {arguments.get('command', '')}"
    if request.tool == "write_file":
        return _write_preview(
            project, str(arguments.get("path", "")), str(arguments.get("content", ""))
        )
    if request.tool == "edit_file":
        return _edit_preview(arguments)
    return "  " + ", ".join(f"{key}={value!r}" for key, value in arguments.items())


def _write_preview(project: Path, path: str, content: str) -> str:
    target = project / path
    old = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
    new = content.splitlines()
    diff = list(difflib.unified_diff(old, new, f"a/{path}", f"b/{path}", lineterm=""))
    header = f"  {'overwrite' if old else 'create'} {path}: {len(new)} line(s)"
    return "\n".join([header, *_clip(diff)])


def _edit_preview(arguments: dict) -> str:
    path = str(arguments.get("path", ""))
    old = str(arguments.get("old_text", "")).splitlines()
    new = str(arguments.get("new_text", "")).splitlines()
    lines = [f"- {line}" for line in old] + [f"+ {line}" for line in new]
    return "\n".join([f"  edit {path}", *_clip(lines)])


def _clip(lines: list[str]) -> list[str]:
    if len(lines) <= _DIFF_LINES:
        return lines
    return [*lines[:_DIFF_LINES], f"... {len(lines) - _DIFF_LINES} more line(s)"]
