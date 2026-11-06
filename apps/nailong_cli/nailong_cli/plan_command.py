"""/plan: split a request into tasks, then run it as a team or keep it in one agent."""

from __future__ import annotations

import threading

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.starter import (
    Answer,
    CancelToken,
    Planner,
    PlannerError,
    PlanProposal,
    Role,
)
from nailong_agent_sdk.starter.planner import PlannedTask
from nailong_agent_sdk.tools.supervisor import listening_to_command_output

from .config import CliConfig
from .cost import BudgetReached
from .keys import EscapeWatcher
from .live_commands import handle_typed
from .state import ReplState
from .turns import PromptRefused, keep_unread_messages, run_turn

EXPLORER = Role("explorer", "reads files, searches and fetches pages; changes nothing")
WORKER = Role(
    "worker", "does the work, including editing files and running commands", writable=True
)
TEAM = "team"
SINGLE = "single"


def cli_roles(config: CliConfig) -> tuple[Role, ...]:
    roles = {EXPLORER.name: EXPLORER, WORKER.name: WORKER}
    for name, spec in config.agents.items():
        writable = spec.get("tools", "read-only") != "read-only"
        roles[name] = Role(name, spec["description"], writable=writable)
    return tuple(roles.values())


def _ask_architecture(state: ReplState, proposal: PlanProposal) -> str | None:
    console = state.console
    count = len(proposal.tasks)
    roles = ", ".join(sorted({task.role for task in proposal.tasks}))
    labels = [f"Run as a team ({count} tasks: {roles})", "Keep everything in one agent", "Cancel"]
    if console.chooser is not None:
        with console.reading_input():
            picked = console.chooser("How should this run?", labels)
        return {0: TEAM, 1: SINGLE}.get(picked)
    while True:
        answer = console.ask("Run as a [t]eam, keep it in one [a]gent, or [c]ancel? > ").lower()
        if answer in {"t", "team"}:
            return TEAM
        if answer in {"a", "agent", "s", "single"}:
            return SINGLE
        if answer in {"c", "cancel", "n", "no", ""}:
            return None
        console.line("Please answer t, a or c.")


def _plan(state: ReplState, request: str) -> PlanProposal | None:
    console = state.console
    planner = Planner(
        model=state.setup,
        roles=cli_roles(state.config),
        workspace=state.config.workspace / "planner",
    )
    console.start_spinner("Planning…")
    try:
        return state.runner.run(planner.plan(request))
    except (PlannerError, ValueError, AgentSdkError) as error:
        console.error(str(error))
        return None
    finally:
        console.stop_spinner()


class _Tracked:
    def __init__(self, agent, token: CancelToken, state: ReplState, task_id: str) -> None:
        self._agent = agent
        self._token = token
        self._state = state
        self._task_id = task_id

    async def run(self, prompt: str) -> Answer:
        try:
            with listening_to_command_output(self._state.parts.renderer.command_output):
                return await self._agent.run(prompt, cancellation=self._token)
        finally:
            self._state.live.remove(self._task_id)
            self._state.live.enqueue_first(self._agent.inbox.drain())

    def close(self) -> None:
        self._agent.close()


def _run_team(state: ReplState, proposal: PlanProposal) -> None:
    console = state.console
    parts = state.parts
    parts.spending.check()
    parts.checkpoints.begin_turn()
    total = len(proposal.tasks)
    finished = 0

    halted = threading.Event()

    def stop_everything() -> None:
        halted.set()
        state.live.cancel_all()

    def build(role: Role, task: PlannedTask):
        parts.repeat_guard.new_turn()
        agent = state.builder.build(
            state.setup, "explore" if not role.writable else parts.mode.value
        )
        token = CancelToken()
        if halted.is_set():
            token.cancel()
        state.live.add(task.id, f"{task.id} [{task.role}]", token, agent)
        return _Tracked(agent, token, state, task.id)

    def on_task(event: str, task: PlannedTask, answer: Answer | None) -> None:
        nonlocal finished
        if event == "start":
            console.line(console.style(f"▸ {task.id} [{task.role}]", "bold"))
            parts.renderer.start_turn()
            return
        if answer is not None:
            parts.renderer.finish_turn(answer)
            parts.spending.add(answer)
        if event == "done":
            finished += 1
        else:
            console.line(console.style(f"  {task.id} {event}", "yellow"))

    watcher = EscapeWatcher(
        console,
        stop_everything,
        state.keys if console.fancy else None,
        on_line=lambda line: handle_typed(state, line),
    )
    try:
        with watcher:
            result = state.runner.run(
                proposal.run_team(build, max_parallel=1, on_task=on_task, close=False)
            )
    finally:
        console.stop_spinner()
        keep_unread_messages(state, watcher.leftover)
    if halted.is_set():
        console.line(console.style("Interrupted (Esc).", "yellow"))
    console.line(console.style(f"Team finished: {finished} of {total} task(s) completed.", "bold"))
    for task_id, outcome in result.outcomes.items():
        if outcome.status != "completed":
            console.line(f"  {task_id} {outcome.status}: {outcome.reason}")


def plan_command(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    request = " ".join(arguments).strip()
    if not request:
        console.line("Usage: /plan <what you want done>")
        return True
    try:
        proposal = _plan(state, request)
        if proposal is None:
            return True
        console.line(proposal.summary())
        decision = SINGLE if not proposal.choice.team else _ask_architecture(state, proposal)
        if decision is None:
            console.line("  Cancelled.")
        elif decision == SINGLE:
            run_turn(state, proposal.solo_prompt())
        else:
            _run_team(state, proposal)
    except KeyboardInterrupt:
        console.stop_spinner()
        console.line("\nCancelled.")
    except (BudgetReached, PromptRefused, AgentSdkError, ValueError) as error:
        console.error(str(error))
    return True
