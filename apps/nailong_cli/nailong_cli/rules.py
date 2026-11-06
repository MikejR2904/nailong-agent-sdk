"""Permission rules: allow, ask or deny a tool for a pattern; the last rule that matches wins."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from .apply_patch import patch_paths
from .commandline import split_command

ACTIONS = ("allow", "ask", "deny")
PATH_TOOLS = (
    "read_file",
    "write_file",
    "edit_file",
    "notebook_edit",
    "apply_patch",
    "grep",
    "glob",
)
ASK_IN_HOOK = frozenset({"read_file", "grep", "glob"})


@dataclass(frozen=True)
class Rule:
    action: str
    tools: tuple[str, ...]
    pattern: str
    source: str = "settings"


DEFAULT_RULES: tuple[Rule, ...] = ()


def rule_from_table(entry: Any, source: str, position: int) -> Rule:
    if not isinstance(entry, dict):
        raise ValueError(f"rules entry {position} must be a table with action, tool and pattern.")
    action, tool, pattern = entry.get("action"), entry.get("tool"), entry.get("pattern", "*")
    if action not in ACTIONS:
        raise ValueError(
            f"rules entry {position}: action must be one of {list(ACTIONS)}, not {action!r}."
        )
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError(f'rules entry {position} needs a tool name, for example "run_command".')
    if not isinstance(pattern, str) or not pattern:
        raise ValueError(f"rules entry {position}: pattern must be text, not {pattern!r}.")
    tools = tuple(part.strip() for part in tool.split(",") if part.strip())
    return Rule(action, tools, pattern, source)


class PermissionRules:
    def __init__(self, rules: list[Rule]) -> None:
        self._rules = list(rules)

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    def decide(self, tool: str, subject: str) -> Rule | None:
        if tool == "apply_patch":
            return self._decide_files(tool, subject.splitlines())
        found: Rule | None = None
        for rule in self._rules:
            if self._matches(rule, tool, subject):
                found = rule
        return found

    def _decide_files(self, tool: str, paths: list[str]) -> Rule | None:
        verdicts = [self._decide_one(tool, path) for path in paths] or [None]
        for action in ("deny", "ask"):
            for verdict in verdicts:
                if verdict is not None and verdict.action == action:
                    return verdict
        return verdicts[0] if all(v is not None for v in verdicts) else None

    def _decide_one(self, tool: str, subject: str) -> Rule | None:
        found: Rule | None = None
        for rule in self._rules:
            if self._matches(rule, tool, subject):
                found = rule
        return found

    @staticmethod
    def _matches(rule: Rule, tool: str, subject: str) -> bool:
        if not any(fnmatch.fnmatchcase(tool, name) for name in rule.tools):
            return False
        if tool != "run_command":
            return fnmatch.fnmatchcase(subject, rule.pattern)
        segments = split_command(subject) or []
        if rule.action == "allow":
            if subject == rule.pattern:
                return True
            return bool(segments) and all(
                fnmatch.fnmatchcase(segment, rule.pattern) for segment in segments
            )
        return any(fnmatch.fnmatchcase(text, rule.pattern) for text in [subject, *segments])


def subject_for(tool: str, arguments: dict[str, Any]) -> str:
    if tool in {"run_command", "start_background", "shell_run"}:
        return str(arguments.get("command", ""))
    if tool == "apply_patch":
        return "\n".join(patch_paths(str(arguments.get("patch", ""))))
    if tool in {"web_fetch", "fetch_document"}:
        return str(arguments.get("url", ""))
    if tool == "web_search":
        return str(arguments.get("query", ""))
    return str(arguments.get("path", ""))


def rules_hook(rules: PermissionRules, approver: Any):
    async def before_tool(event: Any) -> str | None:
        subject = subject_for(event.tool, event.arguments)
        rule = rules.decide(event.tool, subject)
        if rule is None:
            return None
        if rule.action == "deny":
            return (
                f"{event.tool} {subject!r} is refused by a deny rule "
                f"({rule.pattern!r} from {rule.source})."
            )
        if rule.action == "ask" and event.tool in ASK_IN_HOOK:
            request = SimpleNamespace(tool=event.tool, arguments=event.arguments)
            if not await approver(request):
                return f"{event.tool} {subject!r} was not approved ({rule.source} ask rule)."
        return None

    return before_tool
