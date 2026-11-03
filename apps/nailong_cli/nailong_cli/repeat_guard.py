"""Stopping the agent from doing the same thing twice when nothing has changed in between."""

from __future__ import annotations

import json

from nailong_agent_sdk.starter import ToolEvent

from .console import Console

READ_ONLY = frozenset({"read_file", "glob", "grep"})
IDENTICAL_WRITES = frozenset({"write_file"})
CHANGES_THINGS = frozenset(
    {"write_file", "edit_file", "run_command", "start_background", "background_stop"}
)


class RepeatGuard:
    def __init__(self, console: Console | None = None) -> None:
        self._console = console
        self._done: dict[str, int] = {}
        self._step = 0

    def attach(self, console: Console) -> None:
        self._console = console

    def new_turn(self) -> None:
        self._done.clear()
        self._step = 0

    def before_tool(self, event: ToolEvent) -> str | None:
        if event.tool not in READ_ONLY | IDENTICAL_WRITES:
            return None
        earlier = self._done.get(_key(event))
        if earlier is None:
            return None
        if self._console is not None:
            self._console.line(
                self._console.style(
                    f"  ↺ skipped a repeated {event.tool} (same as step {earlier})", "dim"
                )
            )
        what = (
            "wrote exactly this file" if event.tool in IDENTICAL_WRITES else "made this exact call"
        )
        return (
            f"You already {what} at step {earlier} and it succeeded, and nothing has changed since "
            "then. Use that result instead of repeating it. If you need more of a file, read a "
            "different range with offset and limit, or search it with grep."
        )

    def after_tool(self, event: ToolEvent) -> None:
        self._step += 1
        result = event.result
        if result is None or result.status != "succeeded":
            return
        if event.tool in CHANGES_THINGS:
            self._done.clear()
        if event.tool in READ_ONLY | IDENTICAL_WRITES:
            self._done[_key(event)] = self._step


def _key(event: ToolEvent) -> str:
    return event.tool + json.dumps(event.arguments, sort_keys=True, default=str)
