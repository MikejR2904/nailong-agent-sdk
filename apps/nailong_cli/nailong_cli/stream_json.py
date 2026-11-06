"""--output stream-json: one JSON object per line while the turn runs, for other programs."""

from __future__ import annotations

import json
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
