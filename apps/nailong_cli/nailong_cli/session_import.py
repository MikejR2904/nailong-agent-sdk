"""/sessions import: turn a Claude Code or Codex transcript into a session of this CLI."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter.session_store import SessionStore, validate_session_id

MAX_BYTES = 200 * 1024 * 1024
MAX_TURNS = 500
TEXT_CHARS = 4_000
TOOL_CHARS = 160
TOOLS_PER_TURN = 40
CODEX_CONTEXT_MARKERS = (
    "<environment_context>",
    "# AGENTS.md",
    "<user_instructions>",
    "<INSTRUCTIONS>",
)


class TranscriptError(ValueError):
    pass


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else f"{text[:limit]}...(truncated)"


def _brief(name: str, arguments: Any) -> str:
    if isinstance(arguments, str):
        shown = arguments
    else:
        shown = json.dumps(arguments, separators=(",", ":"), ensure_ascii=False)
    return _clip(f"{name} {shown}", TOOL_CHARS)


def _records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise TranscriptError(f"{path} is not a file.")
    if path.stat().st_size > MAX_BYTES:
        raise TranscriptError(f"{path} is over {MAX_BYTES // 1_000_000} MB.")
    found: list[dict[str, Any]] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise TranscriptError(f"{path} line {number} is not JSON ({error.msg}).") from None
        if isinstance(record, dict):
            found.append(record)
    return found


def detect(records: list[dict[str, Any]]) -> str:
    kinds = {record.get("type") for record in records[:50]}
    if "session_meta" in kinds or "response_item" in kinds:
        return "codex"
    if {"user", "assistant"} & kinds:
        return "claude"
    raise TranscriptError(
        "This is not a Claude Code or Codex transcript: no user/assistant or response_item "
        "lines in its first 50 records."
    )


def _claude_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _claude_turns(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for record in records:
        if record.get("isSidechain") or record.get("isMeta"):
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        if record.get("type") == "user":
            text = _claude_text(message.get("content")).strip()
            if text and not text.startswith(("<command-", "<local-command", "<system-reminder")):
                turns.append({"prompt": text, "answer": "", "tools": []})
        elif record.get("type") == "assistant" and turns:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and str(block.get("text", "")).strip():
                    turns[-1]["answer"] = str(block["text"])
                elif block.get("type") == "tool_use":
                    turns[-1]["tools"].append(_brief(str(block.get("name")), block.get("input")))
    return turns


def _codex_turns(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for record in records:
        if record.get("type") != "response_item":
            continue
        payload = record.get("payload") or {}
        kind = payload.get("type")
        if kind == "message":
            text = "\n".join(
                str(block.get("text", ""))
                for block in payload.get("content") or []
                if isinstance(block, dict)
            ).strip()
            if payload.get("role") == "user":
                if text and not text.startswith(CODEX_CONTEXT_MARKERS):
                    turns.append({"prompt": text, "answer": "", "tools": []})
            elif payload.get("role") == "assistant" and turns and text:
                turns[-1]["answer"] = text
        elif kind in {"function_call", "custom_tool_call"} and turns:
            arguments = payload.get("arguments", payload.get("input", ""))
            turns[-1]["tools"].append(_brief(str(payload.get("name")), arguments))
    return turns


def read_transcript(path: Path) -> tuple[str, list[dict[str, Any]]]:
    records = _records(path)
    source = detect(records)
    turns = _claude_turns(records) if source == "claude" else _codex_turns(records)
    if not turns:
        raise TranscriptError(f"{path} has no user prompts to import.")
    return source, turns


def default_name(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]", "-", path.stem)[:40].strip("-.") or "transcript"
    return f"imported-{stem}"[:64]


def import_transcript(
    store: SessionStore, agent: str, path: Path, name: str | None = None
) -> tuple[str, str, int, int]:
    source, turns = read_transcript(path)
    session_id = validate_session_id(name or default_name(path))
    if store.exists(session_id):
        raise TranscriptError(f'Session "{session_id}" already exists; pass another name.')
    skipped = max(0, len(turns) - MAX_TURNS)
    store.create(session_id, agent)
    for turn in turns[-MAX_TURNS:]:
        tools = [_clip(item, TOOL_CHARS) for item in turn["tools"][:TOOLS_PER_TURN]]
        store.append(
            session_id,
            {
                "prompt": _clip(turn["prompt"], TEXT_CHARS),
                "status": "completed" if turn["answer"] else "imported-without-answer",
                "answer": _clip(turn["answer"], TEXT_CHARS),
                "steps": len(turn["tools"]),
                "tools": tools,
            },
        )
    return session_id, source, len(turns) - skipped, skipped
