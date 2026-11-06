"""--output stream-json and claude-stream-json: one JSON object per line while the turn runs."""

from __future__ import annotations

import json
import time
from typing import Any

from nailong_agent_sdk.agent.model import ModelReasoningDelta, ModelStreamEvent, ModelTextDelta
from nailong_agent_sdk.starter import Answer, ToolEvent

from .console import Console
from .render import AnswerStreamer


class JsonStream:
    def __init__(self, console: Console) -> None:
        self.rationale = None
        self._console = console
        self._streamer = AnswerStreamer()

    def announce(self, session_id: str) -> None:
        self._emit({"type": "session", "id": session_id})

    def start_turn(self) -> None:
        self._streamer = AnswerStreamer()

    def on_stream(self, event: ModelStreamEvent) -> None:
        if isinstance(event, ModelReasoningDelta):
            self._emit({"type": "reasoning", "delta": event.text})
        elif isinstance(event, ModelTextDelta):
            piece = self._streamer.feed(event.text)
            if piece:
                self._emit({"type": "text", "delta": piece})

    def before_tool(self, event: ToolEvent) -> None:
        self._emit({"type": "tool_use", "tool": event.tool, "arguments": event.arguments})

    def command_output(self, text: str) -> None:
        return None

    def after_tool(self, event: ToolEvent) -> None:
        result = event.result
        if result is not None:
            self._emit(
                {
                    "type": "tool_result",
                    "tool": event.tool,
                    "status": result.status,
                    "output": result.output if result.status == "succeeded" else result.error,
                }
            )

    def finish_turn(self, answer: Answer) -> None:
        self._emit(
            {
                "type": "result",
                "status": answer.status,
                "answer": answer.text if answer.ok else None,
                "reason": answer.reason,
                "steps": answer.steps,
                "cost": answer.cost,
            }
        )

    def _emit(self, payload: dict[str, Any]) -> None:
        self._console.line(json.dumps(payload, default=str, sort_keys=True))


class ClaudeJsonStream(JsonStream):
    def __init__(self, console: Console, *, model: str, cwd: str, mode: str) -> None:
        super().__init__(console)
        self._model = model
        self._cwd = cwd
        self._mode = mode
        self._session = ""
        self._text = ""
        self._started = time.monotonic()
        self._pending: list[str] = []
        self._tool_calls = 0
        self.tools: list[str] = []

    def announce(self, session_id: str) -> None:
        self._session = session_id
        self._emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": session_id,
                "cwd": self._cwd,
                "model": self._model,
                "permissionMode": self._mode,
                "tools": self.tools,
            }
        )

    def start_turn(self) -> None:
        super().start_turn()
        self._started = time.monotonic()
        self._text = ""

    def on_stream(self, event: ModelStreamEvent) -> None:
        if isinstance(event, ModelTextDelta):
            self._text += self._streamer.feed(event.text)

    def before_tool(self, event: ToolEvent) -> None:
        self._flush_text()
        self._tool_calls += 1
        call_id = f"toolu_{self._tool_calls:04d}"
        self._pending.append(call_id)
        self._message(
            "assistant",
            [{"type": "tool_use", "id": call_id, "name": event.tool, "input": event.arguments}],
        )

    def after_tool(self, event: ToolEvent) -> None:
        result = event.result
        if result is None:
            return
        failed = result.status != "succeeded"
        body = result.error if failed else result.output
        self._message(
            "user",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": self._pending.pop(0) if self._pending else "",
                    "content": body if isinstance(body, str) else json.dumps(body, default=str),
                    "is_error": failed,
                }
            ],
        )

    def finish_turn(self, answer: Answer) -> None:
        if not self._text and answer.ok:
            self._text = answer.text or ""
        self._flush_text()
        cost = answer.cost or {}
        subtype = "success"
        if not answer.ok:
            limit = "Maximum iteration limit" in (answer.reason or "")
            subtype = "error_max_turns" if limit else "error_during_execution"
        self._emit(
            {
                "type": "result",
                "subtype": subtype,
                "is_error": not answer.ok,
                "duration_ms": int((time.monotonic() - self._started) * 1000),
                "num_turns": answer.steps,
                "result": answer.text if answer.ok else (answer.reason or ""),
                "session_id": self._session,
                "total_cost_usd": _usd(cost.get("total")),
                "usage": {
                    "input_tokens": cost.get("input_tokens", 0),
                    "cache_read_input_tokens": cost.get("cached_input_tokens", 0),
                    "output_tokens": cost.get("output_tokens", 0),
                },
            }
        )

    def _flush_text(self) -> None:
        if self._text.strip():
            self._message("assistant", [{"type": "text", "text": self._text}])
        self._text = ""

    def _message(self, role: str, content: list[dict[str, Any]]) -> None:
        self._emit(
            {
                "type": role,
                "message": {"role": role, "content": content, "model": self._model}
                if role == "assistant"
                else {"role": role, "content": content},
                "session_id": self._session,
            }
        )


def _usd(value: object) -> float | None:
    try:
        return float(str(value))
    except ValueError:
        return None
