"""/memory, /remember and /forget: what the agent is carrying, and the rules you pin for it."""

from __future__ import annotations

from .memory import memory_text
from .pins import MAX_PIN_CHARS
from .state import ReplState
from .token_commands import estimate


def _loaded_text(state: ReplState) -> str:
    pieces = []
    if state.parts.pins is not None and state.parts.pins.text():
        pieces.append(state.parts.pins.text())
    if state.parts.memory:
        pieces.append(memory_text(state.parts.memory))
    return "\n\n".join(pieces)


def overview(state: ReplState) -> list[str]:
    parts = state.parts
    lines: list[str] = []
    pins = parts.pins.listing() if parts.pins is not None else []
    lines.append(f"Always in the prompt (put first, never summarised): {len(pins)} pinned rule(s)")
    for pin in pins:
        lines.append(f"  {pin.id} [{pin.scope}] {pin.text}")
    if not pins:
        lines.append("  none yet; /remember <rule> pins one")
    lines.append("Instruction files (loaded in full each session):")
    for file in parts.memory:
        cut = " (cut at 20,000 characters)" if file.text.endswith("(truncated)") else ""
        lines.append(f"  {file.label}: {file.path} ({len(file.text)} characters){cut}")
    if not parts.memory:
        lines.append("  No NAILONG.md, AGENTS.md or CLAUDE.md found. /init can draft one.")
    loaded = _loaded_text(state)
    lines.append(
        f"  together with the pins: {len(loaded)} characters, about {estimate(loaded)} tokens"
    )
    history = state.session.history()
    summary = history.get("summary")
    lines.append("Conversation (recent turns only; older ones only if summarised):")
    covered = (
        f"; a summary covers {summary['covers_turns']} turn(s), {len(summary['text'])} characters"
        if summary
        else "; no summary yet (/compact)"
    )
    sent, left_out = len(history["turns"]), history["omitted_turns"]
    lines.append(f"  {sent} recent turn(s) sent, {left_out} left out{covered}")
    lines.append("Looked up on demand (in the prompt only when the agent searches):")
    notes = parts.notes.titles() if parts.notes is not None else []
    lines.append(f"  notes: {len(notes)}   (/notes)")
    if parts.wiki is not None:
        stale = parts.wiki.stale_pages()
        lines.append(f"  wiki: {len(parts.wiki.names())} page(s), {len(stale)} stale   (/wiki)")
    if parts.discoveries is not None:
        rows = parts.discoveries.listing("all")
        accepted = [row for row in rows if row["status"] == "accepted"]
        lines.append(
            f"  discoveries: {len(rows)} ({len(accepted)} accepted; accepted ones that still hold "
            "are added to the prompt)   (/discoveries)"
        )
    if parts.knowledge is not None:
        lines.append(f"  knowledge base: {len(parts.knowledge.trees())} source(s)   (/kb list)")
    lines.append("Edit: instruction files and the pinned files are plain Markdown you can open;")
    lines.append("  /remember adds a pin, /forget ID removes one, /memory show prints it all.")
    return lines


def _memory(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    action = arguments[0] if arguments else ""
    if action == "show":
        console.line(_loaded_text(state) or "  Nothing is loaded.")
    elif action == "pins":
        for pin in state.parts.pins.listing():
            console.line(f"  {pin.id} [{pin.scope}] {pin.text}")
        console.line(
            f"  files: {state.parts.pins.path('project')} and {state.parts.pins.path('user')}"
        )
    elif action:
        raise ValueError("Usage: /memory   or   /memory pins   or   /memory show")
    else:
        for line in overview(state):
            console.line("  " + line if not line.startswith("  ") else line)
    return True


def _rebuild(state: ReplState) -> None:
    from .turns import switch_agent

    switch_agent(state)


def _remember(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    scope = "project"
    words = list(arguments)
    if words[:1] in (["--user"], ["-u"]):
        scope, words = "user", words[1:]
    if not words:
        console.line(
            f"Usage: /remember [--user] <one short rule, up to {MAX_PIN_CHARS} characters>"
        )
        return True
    pin = state.parts.pins.add(" ".join(words), scope)
    _rebuild(state)
    console.line(f"  pinned {pin.id} ({scope}): {pin.text}")
    console.line(
        "  It is at the top of every request from now on. /forget " + pin.id + " removes it."
    )
    return True


def _forget(state: ReplState, arguments: list[str]) -> bool:
    if len(arguments) != 1:
        state.console.line("Usage: /forget <pin id>   (/memory pins lists them)")
        return True
    pin = state.parts.pins.remove(arguments[0])
    _rebuild(state)
    state.console.line(f"  unpinned {pin.id}: {pin.text}")
    return True


def _paste(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    store = state.pastes
    if not arguments:
        for item in store.listing():
            console.line(
                f"  #{item.number}  +{item.lines} lines  {len(item.text):,} chars  {item.preview}"
            )
        if not store.listing():
            console.line("  No long pastes yet. A paste over 10 lines or 1,000 characters shows as")
            console.line("  [Pasted text #N +L lines]; the whole text is still sent.")
        else:
            console.line(
                "  /paste N shows one; Alt+E in the prompt opens the label under the cursor."
            )
        return True
    if len(arguments) != 1 or not arguments[0].lstrip("#").isdigit():
        raise ValueError("Usage: /paste   or   /paste N")
    console.line(store.get(int(arguments[0].lstrip("#"))).text)
    return True


MEMORY_HELP = {
    "paste": "/paste [N]: long pasted text kept behind a label; list them or show one",
    "memory": "/memory [pins|show]: everything the agent carries, what is pinned and what is not",
    "remember": "/remember [--user] RULE: pin a short standing rule into every request",
    "forget": "/forget ID: remove a pinned rule",
}
MEMORY_COMMANDS = {
    "memory": _memory,
    "remember": _remember,
    "forget": _forget,
    "paste": _paste,
}
