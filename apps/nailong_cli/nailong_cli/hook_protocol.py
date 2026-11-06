from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

CLAUDE_EVENTS = {
    "PreToolUse": "pre_tool",
    "PostToolUse": "post_tool",
    "UserPromptSubmit": "prompt_submit",
    "SessionStart": "session_start",
    "SessionEnd": "session_end",
    "Stop": "turn_end",
    "Notification": "permission_asked",
    "PreCompact": "pre_compact",
    "SubagentStop": "subagent_stop",
}
CLAUDE_NAMES = {value: key for key, value in CLAUDE_EVENTS.items()}
CLAUDE_TOOLS = {
    "run_command": "Bash",
    "write_file": "Write",
    "edit_file": "Edit",
    "apply_patch": "Edit",
    "read_file": "Read",
    "glob": "Glob",
    "grep": "Grep",
    "notebook_edit": "NotebookEdit",
    "todo_write": "TodoWrite",
}
PROTOCOLS = ("env", "claude")
BLOCKING_EXIT_CODE = 2


def normalise(hook: dict[str, Any]) -> dict[str, Any]:
    result = dict(hook)
    event = result.get("event")
    if event in CLAUDE_EVENTS:
        result["event"] = CLAUDE_EVENTS[event]
        result.setdefault("protocol", "claude")
    if "matcher" in result:
        result.setdefault("protocol", "claude")
    return result


def uses_claude_protocol(hook: dict[str, Any]) -> bool:
    return hook.get("protocol") == "claude"


def tool_matches(hook: dict[str, Any], tool: str) -> bool:
    if "matcher" in hook:
        matcher = str(hook["matcher"])
        if matcher in {"", "*"}:
            return True
        return any(re.fullmatch(matcher, name) for name in (tool, CLAUDE_TOOLS.get(tool, tool)))
    from fnmatch import fnmatchcase

    return fnmatchcase(tool, str(hook.get("tool", "*")))


def tool_payload(
    event: str,
    session_id: str,
    cwd: str,
    tool: str,
    arguments: dict[str, Any],
    response: Any = None,
) -> str:
    tool_input = dict(arguments)
    if "path" in arguments:
        tool_input.setdefault("file_path", arguments["path"])
    body: dict[str, Any] = {
        "session_id": session_id,
        "hook_event_name": CLAUDE_NAMES.get(event, event),
        "cwd": cwd,
        "tool_name": CLAUDE_TOOLS.get(tool, tool),
        "tool_input": tool_input,
    }
    if response is not None:
        body["tool_response"] = response
    return json.dumps(body, default=str)


def lifecycle_payload(event: str, session_id: str, cwd: str, values: dict[str, str]) -> str:
    body: dict[str, Any] = {
        "session_id": session_id,
        "hook_event_name": CLAUDE_NAMES.get(event, event),
        "cwd": cwd,
    }
    if "prompt" in values:
        body["prompt"] = values["prompt"]
    return json.dumps(body, default=str)


@dataclass(frozen=True)
class Verdict:
    blocked: bool = False
    reason: str = ""
    warning: str = ""
    context: str = ""
    allowed: bool = False
    updated: dict[str, Any] | None = None


def interpret(return_code: int, output: str, timed_out: bool = False) -> Verdict:
    text = output.strip()
    if timed_out:
        return Verdict(warning=f"timed out ({text[-200:] or 'no output'})")
    if return_code == BLOCKING_EXIT_CODE:
        return Verdict(blocked=True, reason=text[-600:] or "the hook exited with code 2")
    if return_code != 0:
        return Verdict(warning=text[-300:] or f"exit code {return_code}")
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return Verdict(context=text)
        if isinstance(data, dict):
            specific = data.get("hookSpecificOutput")
            decision = data.get("decision")
            reason = str(data.get("reason") or "")
            if isinstance(specific, dict) and specific.get("permissionDecision") == "deny":
                return Verdict(
                    blocked=True, reason=str(specific.get("permissionDecisionReason") or reason)
                )
            if isinstance(specific, dict):
                changed = specific.get("updatedInput")
                allowed = specific.get("permissionDecision") == "allow"
                if isinstance(changed, dict) or allowed:
                    return Verdict(
                        allowed=allowed,
                        updated=changed if isinstance(changed, dict) else None,
                        context=str(specific.get("additionalContext") or ""),
                    )
            if decision in {"block", "deny"}:
                return Verdict(blocked=True, reason=reason or "the hook blocked it")
            if data.get("continue") is False:
                return Verdict(
                    blocked=True, reason=str(data.get("stopReason") or "the hook stopped")
                )
            extra = specific.get("additionalContext") if isinstance(specific, dict) else None
            return Verdict(context=str(extra or ""))
    return Verdict(context=text)
