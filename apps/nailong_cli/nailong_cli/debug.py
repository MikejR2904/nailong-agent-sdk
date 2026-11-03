"""--debug-file: a JSON line for each model stream chunk, tool call and result."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from nailong_agent_sdk.agent.model import ModelStreamEvent
from nailong_agent_sdk.starter import ToolEvent


class DebugLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def on_stream(self, event: ModelStreamEvent) -> None:
        self._write({"event": "stream", "kind": type(event).__name__, "detail": repr(event)[:300]})

    def before_tool(self, event: ToolEvent) -> None:
        self._write({"event": "tool_call", "tool": event.tool, "arguments": event.arguments})
        return None

    def after_tool(self, event: ToolEvent) -> None:
        result = event.result
        self._write(
            {
                "event": "tool_result",
                "tool": event.tool,
                "status": result.status if result else None,
                "output": str(
                    result.output
                    if result and result.status == "succeeded"
                    else result and result.error
                )[:2000],
            }
        )

    def note(self, message: str) -> None:
        self._write({"event": "note", "message": message})

    def _write(self, record: dict[str, Any]) -> None:
        line = json.dumps({"at": time.time(), **record}, default=str, sort_keys=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
