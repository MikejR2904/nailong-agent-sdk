"""Running one turn: hooks, mentions, budget, checkpoint, the agent call, and the result."""

from __future__ import annotations

import re
import threading
import time

from nailong_agent_sdk.starter import Answer, CancelToken, ModelSetup
from nailong_agent_sdk.tools.supervisor import listening_to_command_output

from .cost import BudgetReached
from .keys import EscapeWatcher
from .live import MAIN
from .live_commands import handle_typed, release_pause
from .mentions import expand_mentions
from .state import ReplState

NOTIFY_AFTER_SECONDS = 10.0


class PromptRefused(Exception):
    pass


def _watch_remote_cancel(console, token) -> threading.Event:
    done = threading.Event()
    asked = console.remote_cancel
    if asked is None:
        return done

    def watch() -> None:
        while not done.is_set():
            if asked.wait(0.1):
                token.cancel()
                return

    threading.Thread(target=watch, daemon=True).start()
    return done


async def _run_listening(state: ReplState, text: str, images, token) -> Answer:
    with listening_to_command_output(state.parts.renderer.command_output):
        return await state.session.run(text, images=images, cancellation=token)


def delegate_to_mentioned_agent(state: ReplState, prompt: str) -> str:
    if not prompt.startswith("@"):
        return prompt
    word, _, rest = prompt[1:].partition(" ")
    name = word.replace("-", "_")
    if name not in state.config.agents or not rest.strip() or (state.project / word).exists():
        return prompt
    return f"Hand this to the agent_{name} tool and report what it returns:\n\n{rest.strip()}"


def keep_unread_messages(state: ReplState, leftover: str) -> None:
    unread = state.agent.inbox.drain()
    if unread:
        state.live.enqueue_first(unread)
        state.console.line(
            state.console.style(
                f"  {len(unread)} message(s) the agent did not read before it finished are queued.",
                "dim",
            )
        )
    state.typed_leftover = leftover


def run_turn(state: ReplState, prompt: str) -> Answer | None:
    console = state.console
    parts = state.parts
    parts.spending.check()
    if state.mode_dirty:
        switch_agent(state)
        state.mode_dirty = False
    if state.config.autocompact and state.session.history()["omitted_turns"] > 0:
        _autocompact(state)
    prompt = delegate_to_mentioned_agent(state, _after_prompt_hooks(state, prompt))
    expanded = expand_mentions(prompt, state.project)
    if not state.config.web and re.search(r"https?://", expanded.text):
        console.line(
            console.style(
                "  This message has a link and web access is off; /web on lets the agent fetch it.",
                "dim",
            )
        )
    images = [*state.first_images, *expanded.images]
    state.first_images = []
    if expanded.attached:
        console.line(console.style("  attached: " + ", ".join(expanded.attached), "dim"))
    token = CancelToken()
    parts.checkpoints.begin_turn()
    parts.repeat_guard.new_turn()
    parts.renderer.start_turn()
    started = time.monotonic()
    state.live.add(MAIN, "main agent", token, state.agent)
    stop_watching = _watch_remote_cancel(console, token)
    watcher = EscapeWatcher(
        console,
        token.cancel,
        state.keys if console.fancy else None,
        on_line=lambda line: handle_typed(state, line),
    )
    try:
        with watcher:
            answer = state.runner.run(_run_listening(state, expanded.text, images, token))
    except KeyboardInterrupt:
        token.cancel()
        console.stop_spinner()
        console.line("\nCancelled.")
        return None
    finally:
        stop_watching.set()
        console.stop_spinner()
        state.live.remove(MAIN)
        state.parts.skill_scope.clear()
        release_pause(state)
        keep_unread_messages(state, watcher.leftover)
    if token.is_cancelled():
        console.line(console.style("Interrupted (Esc).", "yellow"))
    parts.renderer.finish_turn(answer)
    parts.spending.add(answer)
    state.last_answer = answer
    state.turns_run += 1
    _after_turn_hooks(state, answer)
    if parts.exporter is not None:
        parts.exporter.after_turn()
    if (
        state.config.notify
        and console.interactive
        and time.monotonic() - started > NOTIFY_AFTER_SECONDS
    ):
        console.write("\a")
    return answer


def small_setup(state: ReplState) -> ModelSetup:
    name = state.config.small_model
    if name and state.parts.model_for is not None:
        return state.parts.model_for(name)
    return state.setup


def ask_read_only(state: ReplState, prompt: str, *, small: bool = False) -> Answer:
    agent = state.builder.build(small_setup(state) if small else state.setup, "plan")
    try:
        state.parts.renderer.start_turn()
        return state.runner.run(agent.run(prompt))
    finally:
        agent.close()


def switch_agent(
    state: ReplState, *, setup: ModelSetup | None = None, mode: str | None = None
) -> None:
    if mode is not None:
        state.parts.mode.set(mode)
    if setup is not None:
        state.setup = setup
    previous = state.agent
    state.agent = state.builder.build(state.setup, state.parts.mode.value)
    state.session = state.agent.resume(state.session.id)
    previous.close()


def _after_prompt_hooks(state: ReplState, prompt: str) -> str:
    lifecycle = state.parts.lifecycle
    if lifecycle is None or not lifecycle.has("prompt_submit"):
        return prompt
    results = state.runner.run(lifecycle.run("prompt_submit", prompt=prompt))
    extra = []
    for ok, output in results:
        if not ok:
            raise PromptRefused(f"A prompt_submit hook refused the message: {output[-300:]}")
        if output:
            extra.append(output)
    return prompt + ("\n\nFrom your hooks:\n" + "\n".join(extra) if extra else "")


def _after_turn_hooks(state: ReplState, answer: Answer) -> None:
    lifecycle = state.parts.lifecycle
    if lifecycle is not None and lifecycle.has("assistant_message") and answer.ok:
        state.runner.run(
            lifecycle.run("assistant_message", message=answer.text, session=state.session.id)
        )
    if lifecycle is not None and lifecycle.has("turn_end"):
        state.runner.run(
            lifecycle.run(
                "turn_end",
                turn_status=answer.status,
                session=state.session.id,
                steps=str(answer.steps),
            )
        )


def _autocompact(state: ReplState) -> None:
    from .compaction import compact_session

    try:
        covered, size = compact_session(state)
    except ValueError as error:
        state.console.line(f"  autocompact skipped: {error}")
        return
    state.console.line(f"  autocompacted {covered} turn(s) into a {size}-character summary")


__all__ = ["BudgetReached", "PromptRefused", "ask_read_only", "run_turn", "switch_agent"]
