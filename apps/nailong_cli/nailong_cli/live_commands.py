"""Commands that work while an agent is running, and what happens to other typed lines."""

from __future__ import annotations

import time

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.tools.process_freeze import FREEZER

from .state import ReplState

QUEUE_PREVIEW_CHARS = 70


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _why(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    note = state.parts.renderer.rationale
    running = bool(state.live.agents())
    if note is None:
        console.line("No tool call has been made yet, so there is nothing to explain.")
        return True
    console.line(console.style("Right now" if running else "Last step", "bold"))
    console.line(f"  doing   step {note.step}: {note.doing}")
    if note.reasoning:
        console.line("  because " + _clip(" ".join(note.reasoning.split()), 600))
    else:
        console.line("  because the model gave no reasoning before this call.")
    items = state.parts.todos.items
    if items:
        console.line("  plan")
        state.parts.todos.render(console)
    return True


def _ps(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    agents = state.live.agents()
    queued = state.live.queued()
    if not agents and not queued:
        console.line("Nothing is running and nothing is queued.")
        return True
    for live in agents:
        seconds = int(time.monotonic() - live.started)
        waiting = getattr(getattr(live.agent, "inbox", None), "pending", 0)
        note = f", {waiting} message(s) not yet read" if waiting else ""
        mark = " · paused" if getattr(getattr(live.agent, "pause", None), "paused", False) else ""
        console.line(f"  {live.id:<10} {live.label} · {seconds}s{mark}{note}")
    for number, text in enumerate(queued, 1):
        console.line(f"  queued {number}: {_clip(text, QUEUE_PREVIEW_CHARS)}")
    return True


def _pause(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    if arguments:
        frozen = FREEZER.freeze(label=arguments[0])
        if not frozen:
            names = ", ".join(entry.label for entry in FREEZER.entries()) or "none running"
            console.line(f'There is no running command "{arguments[0]}" to pause ({names}).')
        else:
            console.line(console.style(f"  paused {arguments[0]} where it is", "yellow"))
        return True
    agents = state.live.agents()
    if not agents:
        console.line("Nothing is running to pause.")
        return True
    stopped = [live.id for live in agents if live.agent is not None and live.agent.pause.pause()]
    frozen = FREEZER.freeze()
    if not stopped and not frozen:
        console.line("Everything running is already paused.")
        return True
    parts = []
    if stopped:
        parts.append(f"{', '.join(stopped)} will wait at its next step")
    if frozen:
        parts.append(f"froze {', '.join(frozen)} in place")
    console.set_spinner_label("Paused (/unpause to continue)")
    console.line(console.style("  " + "; ".join(parts) + ". /unpause continues.", "yellow"))
    return True


def _unpause(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    if arguments:
        thawed = FREEZER.thaw(label=arguments[0])
        if not thawed:
            console.line(f'"{arguments[0]}" is not paused.')
        else:
            console.line(console.style(f"  resumed {arguments[0]}", "dim"))
        return True
    woken = release_pause(state)
    if not woken:
        console.line("Nothing is paused.")
        return True
    console.set_spinner_label("Thinking…")
    console.line(console.style(f"  resumed {', '.join(woken)}", "dim"))
    return True


def release_pause(state: ReplState) -> list[str]:
    woken = [
        live.id
        for live in state.live.agents()
        if live.agent is not None and live.agent.pause.resume()
    ]
    if state.agent.pause.resume() and "main" not in woken:
        woken.append("main")
    woken += FREEZER.thaw(label=None)
    return woken


def _interrupt(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    ids = state.live.cancel_all()
    if not ids:
        console.line("Nothing is running to interrupt.")
        return True
    text = " ".join(arguments)
    if text:
        state.live.enqueue_first([text])
    tail = f"; then: {_clip(text, QUEUE_PREVIEW_CHARS)}" if text else ""
    console.line(console.style(f"  interrupting {', '.join(ids)}{tail}", "yellow"))
    return True


def _stop(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    running = [live.id for live in state.live.agents()]
    if len(arguments) != 1:
        listed = ", ".join(running) if running else "nothing is running"
        console.line(f"Usage: /stop <agent-id>  (running: {listed})")
        return True
    if not state.live.cancel(arguments[0]):
        listed = ", ".join(running) if running else "nothing is running"
        console.line(f'There is no running agent "{arguments[0]}" ({listed}).')
        return True
    console.line(console.style(f"  stopping {arguments[0]}", "yellow"))
    return True


def _broadcast(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    message = " ".join(arguments)
    if not message:
        console.line("Usage: /broadcast <message>  (every running agent reads it at its next step)")
        return True
    if not state.live.agents():
        console.line("Nothing is running to tell. Type the message as a normal prompt instead.")
        return True
    delivered, refused = state.live.tell(message)
    if delivered:
        console.line(console.style(f"  told {', '.join(delivered)}", "dim"))
    for agent_id, reason in refused.items():
        console.error(f"  {agent_id} did not take the message: {reason}")
    return True


LIVE_HELP = {
    "why": "what the agent is doing and the reasoning it gave for it",
    "ps": "running agents and prompts waiting in the queue",
    "pause": "/pause [job-id]: freeze the running work in place (or one background job)",
    "unpause": "/unpause [job-id]: continue from exactly where it paused",
    "interrupt": "/interrupt [text]: stop the running work, then run text next",
    "stop": "/stop <agent-id>: stop one running agent",
    "broadcast": "/broadcast <message>: tell every running agent something now",
}
LIVE_COMMANDS = {
    "why": _why,
    "ps": _ps,
    "pause": _pause,
    "unpause": _unpause,
    "interrupt": _interrupt,
    "stop": _stop,
    "broadcast": _broadcast,
}


def handle_typed(state: ReplState, line: str) -> None:
    console = state.console
    if line.startswith("/"):
        name, _, rest = line[1:].partition(" ")
        handler = LIVE_COMMANDS.get(name)
        if handler is not None:
            try:
                handler(state, rest.split())
            except (ValueError, AgentSdkError) as error:
                console.error(str(error))
            return
    position = state.live.enqueue(line)
    console.line(
        console.style(
            f"  queued {position}: {_clip(line, QUEUE_PREVIEW_CHARS)} (runs after the agent)",
            "dim",
        )
    )
