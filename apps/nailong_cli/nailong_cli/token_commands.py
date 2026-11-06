"""/tokens: where the tokens of the next request go, and what the provider's cache saved."""

from __future__ import annotations

import json

from .state import ReplState

CHARS_PER_TOKEN = 4
TOP_TOOLS = 8


def estimate(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def composition(state: ReplState) -> list[tuple[str, int]]:
    definition = state.agent.definition
    tools = [
        (tool.name, estimate(json.dumps(tool.model_dump(mode="json"), separators=(",", ":"))))
        for tool in definition.tools
    ]
    history = state.session.history()
    rows = [
        ("instructions", estimate(definition.instructions.text)),
        (f"tool definitions ({len(tools)})", sum(size for _, size in tools)),
        ("session history", estimate(json.dumps(history, separators=(",", ":")))),
    ]
    return rows


def largest_tools(state: ReplState) -> list[tuple[str, int]]:
    sized = [
        (tool.name, estimate(json.dumps(tool.model_dump(mode="json"), separators=(",", ":"))))
        for tool in state.agent.definition.tools
    ]
    return sorted(sized, key=lambda item: -item[1])[:TOP_TOOLS]


def _tokens(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    rows = composition(state)
    total = sum(size for _, size in rows)
    console.line(
        "  The next request carries, before your prompt (estimated, 4 characters a token):"
    )
    for label, size in rows:
        console.line(f"    {label:<28} {size:>7,}  {size * 100 // max(total, 1):>3}%")
    console.line(f"    {'total':<28} {total:>7,}")
    console.line("  Largest tool definitions:")
    for name, size in largest_tools(state):
        console.line(f"    {name:<28} {size:>7,}")
    cost = state.last_answer.cost if state.last_answer is not None else None
    if cost is not None:
        console.line(f"  last turn, as the provider counted it: {cost}")
    else:
        console.line("  No provider-counted usage yet; send a prompt, then run /tokens again.")
    if not state.config.autocompact:
        console.line("  autocompact is off: set autocompact = true to summarise old turns.")
    return True


TOKEN_HELP = {
    "tokens": "where the next request's tokens go (instructions, tools, history) and the last cost",
}
TOKEN_COMMANDS = {"tokens": _tokens}
