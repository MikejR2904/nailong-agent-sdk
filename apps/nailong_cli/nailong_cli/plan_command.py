"""/plan: split a request into tasks, then run it as a team or keep it in one agent."""

from __future__ import annotations

import re
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

from .claims import current_owner
from .config import CliConfig
from .cost import BudgetReached
from .events import Span
from .events import current as events_current
from .keys import EscapeWatcher
from .live_commands import handle_typed
from .state import ReplState
from .taskstate import PlanState, describe, task_keys
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
        return {0: TEAM, 1: SINGLE}.get(picked) if picked is not None else None
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
        owner = current_owner.set(self._task_id)
        opened = self._state.parts.events.open_spans() if self._state.parts.events else []
        mine = next(
            (
                e
                for e in reversed(opened)
                if e["event_type"] == "task.start" and e["task_id"] == self._task_id
            ),
            None,
        )
        marker = (
            events_current.set(
                Span(
                    mine["trace_id"],
                    mine["span_id"],
                    mine["event_id"],
                    mine["plan_id"],
                    self._task_id,
                    self._task_id,
                )
            )
            if mine
            else None
        )
        try:
            with listening_to_command_output(self._state.parts.renderer.command_output):
                return await self._agent.run(prompt, cancellation=self._token)
        finally:
            if marker is not None:
                events_current.reset(marker)
            current_owner.reset(owner)
            if self._state.parts.claims is not None:
                self._state.parts.claims.release(self._task_id)
            self._state.live.remove(self._task_id)
            self._state.live.enqueue_first(self._agent.inbox.drain())

    def close(self) -> None:
        self._agent.close()


INTERRUPTED_NOTE = (
    "A earlier run was cut off while it was working on this task. Some of its changes may be "
    "on disk already: look at what exists before you change or repeat anything."
)
PLAN_COMMANDS = {"list", "show", "resume", "abandon"}
_PLAN_ID = re.compile(r"^plan-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")


def _run_team(state: ReplState, proposal: PlanProposal, resume: PlanState | None = None) -> None:
    parts = state.parts
    journal = parts.plans
    parts.spending.check()
    keys = task_keys(proposal)
    plan_id = resume.plan_id if resume is not None else journal.create(proposal)
    lock = journal.acquire(plan_id)
    try:
        completed = resume.committed(keys) if resume is not None else {}
        notes = {
            task_id: INTERRUPTED_NOTE
            for task_id in (resume.interrupted() if resume is not None else [])
            if task_id not in completed
        }
        if resume is not None:
            journal.record(plan_id, "resumed")
        _run_tasks(state, proposal, plan_id, keys, completed, notes)
    finally:
        journal.release(lock)


def _run_tasks(
    state: ReplState,
    proposal: PlanProposal,
    plan_id: str,
    keys: dict[str, str],
    completed: dict[str, str],
    notes: dict[str, str],
) -> None:
    console = state.console
    parts = state.parts
    journal = parts.plans
    parts.checkpoints.begin_turn()
    total = len(proposal.tasks)
    finished = 0
    attempts: dict[str, int] = {}
    log = parts.events
    plan_event = log.start("plan.start", {"tasks": total}, plan_id=plan_id) if log else None
    task_spans: dict[str, dict] = {}

    def open_task(task: PlannedTask) -> None:
        if log is None or plan_event is None:
            return
        task_spans[task.id] = log.start(
            "task.start",
            {"task": task.id, "attempt": attempts.get(task.id, 1)},
            plan_id=plan_id,
            task_id=task.id,
            agent_id=task.id,
            parent_event_id=plan_event["event_id"],
            parent_span_id=plan_event["span_id"],
        )

    def close_task(task_id: str, status: str, **extra: str) -> None:
        opened = task_spans.pop(task_id, None)
        if log is not None and opened is not None:
            log.end(opened, status, **extra)

    halted = threading.Event()
    config = state.config

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
            attempts[task.id] = attempts.get(task.id, 0) + 1
            open_task(task)
            journal.record(
                plan_id, "started", task.id, attempt=attempts[task.id], key=keys[task.id]
            )
            label = f"▸ {task.id} [{task.role}]"
            if attempts[task.id] > 1:
                label += f" (attempt {attempts[task.id]} of {config.task_attempts})"
            console.line(console.style(label, "bold"))
            parts.renderer.start_turn()
            return
        if event == "replayed":
            finished += 1
            open_task(task)
            close_task(task.id, "replayed")
            console.line(console.style(f"  {task.id} already done in an earlier run: kept", "dim"))
            return
        if answer is not None:
            parts.renderer.finish_turn(answer)
            parts.spending.add(answer)
        if event == "done":
            finished += 1
            journal.record(
                plan_id, "done", task.id, text=answer.text if answer else "", key=keys[task.id]
            )
            close_task(task.id, "completed")
        elif event == "retry":
            journal.record(plan_id, "retry", task.id, reason=str(answer.reason if answer else ""))
            close_task(task.id, "failed")
            if log is not None:
                log.emit("task.retry", {"task": task.id}, plan_id=plan_id, task_id=task.id)
            console.line(console.style(f"  {task.id} failed; trying again", "yellow"))
        else:
            close_task(task.id, event)
            console.line(console.style(f"  {task.id} {event}", "yellow"))

    watcher = EscapeWatcher(
        console,
        stop_everything,
        state.keys if console.fancy else None,
        on_line=lambda line: handle_typed(state, line),
    )
    result = None
    try:
        with watcher:
            result = state.runner.run(
                proposal.run_team(
                    build,
                    max_parallel=config.team_parallel,
                    on_task=on_task,
                    close=False,
                    completed=completed,
                    attempts=config.task_attempts,
                    notes=notes,
                    should_retry=lambda task: not halted.is_set(),
                )
            )
    finally:
        console.stop_spinner()
        keep_unread_messages(state, watcher.leftover)
        if result is None:
            journal.record(plan_id, "halted", reason="the run was cut short")
            for left in list(task_spans):
                close_task(left, "interrupted")
            if log is not None and plan_event is not None:
                log.end(plan_event, "halted")
    if halted.is_set():
        console.line(console.style("Interrupted (Esc).", "yellow"))
    console.line(console.style(f"Team finished: {finished} of {total} task(s) completed.", "bold"))
    problems = []
    for task_id, outcome in result.outcomes.items():
        if outcome.status == "failed":
            journal.record(
                plan_id,
                "escalated" if attempts.get(task_id, 1) >= config.task_attempts else "failed",
                task_id,
                reason=str(outcome.reason),
            )
        elif outcome.status == "skipped":
            journal.record(plan_id, "skipped", task_id, reason=str(outcome.reason))
        if outcome.status != "completed":
            problems.append(task_id)
            console.line(f"  {task_id} {outcome.status}: {outcome.reason}")
    if problems:
        journal.record(plan_id, "halted", reason=f"not completed: {problems}")
        console.line(
            console.style(
                f"  Needs you: fix what stopped {', '.join(problems)}, then /plan resume "
                f"{plan_id} runs only what is left.",
                "yellow",
            )
        )
    else:
        journal.record(plan_id, "finished")
    for left in list(task_spans):
        close_task(left, "interrupted")
    if log is not None and plan_event is not None:
        log.end(plan_event, "halted" if problems else "finished")


def _chosen_plan(state: ReplState, wanted: str | None) -> PlanState:
    journal = state.parts.plans
    if wanted is not None:
        return journal.load(wanted)
    pending = journal.unfinished()
    if not pending:
        raise ValueError("There is no unfinished plan to resume.")
    if len(pending) > 1:
        names = ", ".join(item.plan_id for item in pending)
        raise ValueError(f"Several plans are unfinished ({names}); name one: /plan resume ID.")
    return pending[0]


def _plan_admin(state: ReplState, action: str, wanted: str | None) -> None:
    console = state.console
    journal = state.parts.plans
    if action == "list":
        pending = journal.unfinished()
        for plan in pending:
            for line in describe(plan):
                console.line("  " + line)
        if not pending:
            console.line("  No unfinished plans.")
        return
    plan = _chosen_plan(state, wanted)
    if action == "show":
        for line in describe(plan):
            console.line("  " + line)
    elif action == "abandon":
        journal.record(plan.plan_id, "abandoned")
        console.line(f"  abandoned {plan.plan_id}")
    else:
        proposal = plan.proposal(cli_roles(state.config))
        for line in describe(plan):
            console.line("  " + line)
        _run_team(state, proposal, resume=plan)


def plan_command(state: ReplState, arguments: list[str]) -> bool:
    console = state.console
    request = " ".join(arguments).strip()
    if not request:
        console.line("Usage: /plan <what you want done>   or   /plan list|show|resume|abandon [ID]")
        return True
    try:
        action = arguments[0]
        second = arguments[1] if len(arguments) > 1 else None
        if (
            action in PLAN_COMMANDS
            and len(arguments) <= 2
            and (second is None or _PLAN_ID.match(second))
            and (action in {"list", "resume"} or second is not None or action == "show")
        ):
            _plan_admin(state, action, second)
            return True
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
