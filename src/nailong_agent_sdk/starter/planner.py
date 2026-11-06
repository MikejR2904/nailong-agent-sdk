# Copyright (c) 2026 David Michael Indraputra

"""Turn a plain request into a validated plan, then run it as one agent or as a team."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from ..state.orchestration_models import GapMetadata
from ..state.planning import (
    DependencyProof,
    DependencyRule,
    ModelTier,
    Plan,
    PlanTask,
    PlanValidator,
    SignalRole,
    TaskSignalUse,
)
from .agent import DEFAULT_WORKSPACE, Agent, Answer
from .models import ModelSetup
from .toolbox import ToolGroup
from .tools import FunctionTool

IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{0,39}")
SIGNAL = re.compile(r"[a-z][a-z0-9_]{0,59}")
QUOTE_CHARS = 60
DEFAULT_MAX_TASKS = 6
DEFAULT_ATTEMPTS = 3
DEFAULT_PARALLEL = 3
GENERIC_ROLE = "worker"


@dataclass(frozen=True)
class Role:
    name: str
    description: str
    tools: tuple[FunctionTool | ToolGroup, ...] = ()
    writable: bool = False


class DraftTask(BaseModel):
    id: str = Field(description="Short kebab-case name, unique in the plan.")
    role: str = Field(description="One of the available roles.")
    instructions: str = Field(min_length=1, description="What this task must do.")
    done_when: str = Field(min_length=1, description="How to tell this task is finished.")
    produces: list[str] = Field(
        default_factory=list, description="snake_case names of results this task hands on."
    )
    needs: list[str] = Field(
        default_factory=list, description="Names of results other tasks produce that this needs."
    )


class PlanDraft(BaseModel):
    rationale: str = Field(min_length=1, description="Why the work is split this way.")
    tasks: list[DraftTask] = Field(min_length=1)


class PlanDraftError(ValueError):
    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class PlannerError(ValueError):
    pass


@dataclass(frozen=True)
class PlannedTask:
    id: str
    role: str
    instructions: str
    done_when: str
    produces: tuple[str, ...]
    needs: tuple[str, ...]
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class ArchitectureChoice:
    team: bool
    reasons: tuple[str, ...]

    @property
    def architecture(self) -> str:
        return "team" if self.team else "single"


@dataclass(frozen=True)
class TaskOutcome:
    status: str
    text: str = ""
    reason: str | None = None


@dataclass(frozen=True)
class TeamResult:
    ok: bool
    text: str
    outcomes: dict[str, TaskOutcome]
    order: tuple[str, ...]


WorkerFactory = Callable[[Role, PlannedTask], Agent]
SoloFactory = Callable[[Sequence[Role]], Agent]
TaskEvent = Callable[[str, PlannedTask, "Answer | None"], None]


def _slug(text: str, joiner: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", joiner, text.strip().lower()).strip(joiner)
    return cleaned if not cleaned or cleaned[0].isalpha() else f"t{joiner}{cleaned}"


def _normalised(draft: PlanDraft, roles: Sequence[Role]) -> PlanDraft:
    known = {role.name.lower(): role.name for role in roles}
    tasks = [
        task.model_copy(
            update={
                "id": _slug(task.id, "-") or task.id,
                "role": known.get(task.role.strip().lower(), task.role),
                "produces": [_slug(name, "_") or name for name in task.produces],
                "needs": [_slug(name, "_") or name for name in task.needs],
            }
        )
        for task in draft.tasks
    ]
    return draft.model_copy(update={"tasks": tasks})


def compile_draft(
    draft: PlanDraft, request: str, roles: Sequence[Role], max_tasks: int = DEFAULT_MAX_TASKS
) -> tuple[Plan, tuple[PlannedTask, ...]]:
    problems: list[str] = []
    by_role = {role.name: role for role in roles}
    draft = _normalised(draft, roles)
    tasks = draft.tasks
    if len(tasks) > max_tasks:
        problems.append(f"The plan has {len(tasks)} tasks; the most allowed is {max_tasks}.")
    ids = [task.id for task in tasks]
    for task in tasks:
        if not IDENTIFIER.fullmatch(task.id):
            problems.append(
                f'Task id "{task.id}" must be kebab-case: lowercase letters, digits and hyphens.'
            )
        if task.role not in by_role:
            problems.append(
                f'Task "{task.id}" uses role "{task.role}"; the roles are {sorted(by_role)}.'
            )
        for name in (*task.produces, *task.needs):
            if not SIGNAL.fullmatch(name):
                problems.append(
                    f'Task "{task.id}" names the result "{name}"; use snake_case letters, '
                    "digits and underscores."
                )
    for duplicate in sorted({item for item in ids if ids.count(item) > 1}):
        problems.append(f'Task id "{duplicate}" is used more than once.')
    producers: dict[str, str] = {}
    for task in tasks:
        for name in task.produces:
            if name in producers and producers[name] != task.id:
                problems.append(
                    f'The result "{name}" is produced by both "{producers[name]}" and "{task.id}".'
                )
            producers[name] = task.id
    for task in tasks:
        for name in task.needs:
            if name not in producers:
                problems.append(
                    f'Task "{task.id}" needs "{name}", which no task produces; add it to the '
                    'task that makes it, or remove it from "needs".'
                )
            elif producers[name] == task.id:
                problems.append(f'Task "{task.id}" needs "{name}", which it produces itself.')
    if problems:
        raise PlanDraftError(problems)
    order = _order(tasks, producers)
    chain = _writer_chain(tasks, order, by_role)
    planned = tuple(_planned(task, producers, chain) for task in tasks)
    plan = _plan(planned, request)
    report = PlanValidator().validate(plan)
    if not report.valid:
        raise PlanDraftError([error.message for error in report.errors])
    return plan, planned


def _order(tasks: Sequence[DraftTask], producers: dict[str, str]) -> list[str]:
    waiting = {
        task.id: {producers[name] for name in task.needs if name in producers} for task in tasks
    }
    ordered: list[str] = []
    while waiting:
        ready = [task.id for task in tasks if task.id in waiting and not waiting[task.id]]
        if not ready:
            raise PlanDraftError(
                [f"The tasks {sorted(waiting)} each wait on another of them, so none can start."]
            )
        for item in ready:
            ordered.append(item)
            del waiting[item]
        for pending in waiting.values():
            pending.difference_update(ready)
    return ordered


def _writer_chain(
    tasks: Sequence[DraftTask], order: list[str], by_role: dict[str, Role]
) -> dict[str, str | None]:
    writers = [item for item in order if by_role[_task(tasks, item).role].writable]
    return {item: (writers[index - 1] if index else None) for index, item in enumerate(writers)}


def _task(tasks: Sequence[DraftTask], task_id: str) -> DraftTask:
    return next(task for task in tasks if task.id == task_id)


def _turn_signal(task_id: str) -> str:
    return "turn_after_" + task_id.replace("-", "_")


def _planned(
    task: DraftTask, producers: dict[str, str], chain: dict[str, str | None]
) -> PlannedTask:
    produces = list(task.produces)
    needs = list(task.needs)
    if task.id in chain:
        produces.append(_turn_signal(task.id))
        previous = chain[task.id]
        if previous is not None:
            needs.append(_turn_signal(previous))
    parents = {producers[name] for name in task.needs}
    if chain.get(task.id):
        parents.add(chain[task.id])
    return PlannedTask(
        id=task.id,
        role=task.role,
        instructions=task.instructions,
        done_when=task.done_when,
        produces=tuple(dict.fromkeys(produces)),
        needs=tuple(dict.fromkeys(needs)),
        depends_on=tuple(sorted(parents)),
    )


def _plan(planned: Sequence[PlannedTask], request: str) -> Plan:
    quote = f"request:{' '.join(request.split())[:QUOTE_CHARS]}"
    producer = {name: task.id for task in planned for name in task.produces}
    shared: dict[tuple[str, str], list[str]] = {}
    tasks = []
    for task in planned:
        for name in task.needs:
            shared.setdefault((producer[name], task.id), []).append(name)
        signals = sorted({*task.produces, *task.needs})
        uses = [
            TaskSignalUse(
                task_id=task.id,
                signal_id=name,
                role=SignalRole.PRODUCE if name in task.produces else SignalRole.CONSUME,
                source_spans=[quote],
                scope_pointer=task.id,
            )
            for name in signals
        ]
        tasks.append(
            PlanTask(
                task_id=task.id,
                scope=task.role,
                locked_interface={"signals": [{"id": name} for name in signals]},
                instructions=task.instructions,
                dependencies=list(task.depends_on),
                acceptance_criteria=task.done_when,
                model_tier=ModelTier.STANDARD,
                signal_uses=uses,
            )
        )
    proofs = [
        DependencyProof(
            parent_task_id=parent,
            child_task_id=child,
            shared_signal_ids=sorted(names),
            applied_rule=DependencyRule.PRODUCER_TO_CONSUMER,
            source_spans=[quote],
        )
        for (parent, child), names in sorted(shared.items())
    ]
    digest = hashlib.sha256(request.encode("utf-8")).hexdigest()[:10]
    return Plan(plan_id=f"plan-{digest}", tasks=tasks, dependency_proofs=proofs)


def choose_architecture(tasks: Sequence[PlannedTask]) -> ArchitectureChoice:
    reasons: list[str] = []
    roles = sorted({task.role for task in tasks})
    if len(roles) >= 2:
        reasons.append(f"{len(roles)} roles are involved ({', '.join(roles)})")
    ancestors = _ancestors(tasks)
    for index, first in enumerate(tasks):
        for second in tasks[index + 1 :]:
            if first.id not in ancestors[second.id] and second.id not in ancestors[first.id]:
                reasons.append(f"{first.id} and {second.id} can run at the same time")
                break
        else:
            continue
        break
    if reasons:
        return ArchitectureChoice(True, tuple(reasons))
    if len(tasks) == 1:
        return ArchitectureChoice(False, ("the job is a single task",))
    return ArchitectureChoice(False, ("one role, and the tasks run one after another",))


def _ancestors(tasks: Sequence[PlannedTask]) -> dict[str, set[str]]:
    direct = {task.id: set(task.depends_on) for task in tasks}
    result: dict[str, set[str]] = {}

    def visit(task_id: str) -> set[str]:
        if task_id not in result:
            found: set[str] = set()
            for parent in direct[task_id]:
                found |= {parent} | visit(parent)
            result[task_id] = found
        return result[task_id]

    for task in tasks:
        visit(task.id)
    return result


def topological(tasks: Sequence[PlannedTask]) -> tuple[PlannedTask, ...]:
    ancestors = _ancestors(tasks)
    return tuple(sorted(tasks, key=lambda task: (len(ancestors[task.id]), task.id)))


@dataclass(frozen=True)
class PlanProposal:
    request: str
    rationale: str
    tasks: tuple[PlannedTask, ...]
    plan: Plan
    choice: ArchitectureChoice
    roles: tuple[Role, ...]

    @property
    def gap_metadata(self) -> GapMetadata:
        return GapMetadata(
            categories_touched=sorted({task.role for task in self.tasks}),
            blast_radius=len(self.tasks),
        )

    def summary(self) -> str:
        lines = [f"Plan: {self.rationale}"]
        for task in topological(self.tasks):
            after = f" (after {', '.join(task.depends_on)})" if task.depends_on else ""
            lines.append(f"  {task.id} [{task.role}]{after}: {task.instructions}")
        verdict = "a team" if self.choice.team else "one agent"
        lines.append(f"Suggested: {verdict} - {'; '.join(self.choice.reasons)}.")
        return "\n".join(lines)

    def solo_prompt(self) -> str:
        steps = "\n".join(
            f"{number}. {task.instructions} (done when: {task.done_when})"
            for number, task in enumerate(topological(self.tasks), 1)
        )
        return f"{self.request}\n\nWork through these steps in order:\n{steps}"

    async def run_solo(self, factory: SoloFactory, *, close: bool = True) -> Answer:
        agent = factory(self.roles)
        try:
            return await agent.run(self.solo_prompt())
        finally:
            if close:
                agent.close()

    async def run_team(
        self,
        factory: WorkerFactory,
        *,
        max_parallel: int = DEFAULT_PARALLEL,
        on_task: TaskEvent | None = None,
        close: bool = True,
    ) -> TeamResult:
        by_role = {role.name: role for role in self.roles}
        by_id = {task.id: task for task in self.tasks}
        outcomes: dict[str, TaskOutcome] = {}
        started: list[str] = []
        gate = asyncio.Semaphore(max_parallel)
        notify = on_task or (lambda event, task, answer: None)

        async def work(task: PlannedTask) -> None:
            async with gate:
                notify("start", task, None)
                agent = None
                try:
                    agent = factory(by_role[task.role], task)
                    answer = await agent.run(self._worker_prompt(task, outcomes, by_id))
                except Exception as error:
                    reason = f"{type(error).__name__}: {error}"
                    outcomes[task.id] = TaskOutcome("failed", reason=reason)
                    notify("failed", task, None)
                    return
                finally:
                    if close and agent is not None:
                        agent.close()
                if answer.ok:
                    outcomes[task.id] = TaskOutcome("completed", answer.text)
                    notify("done", task, answer)
                else:
                    outcomes[task.id] = TaskOutcome("failed", reason=answer.reason)
                    notify("failed", task, answer)

        remaining = {task.id for task in self.tasks}
        while remaining:
            ready = [
                by_id[item]
                for item in sorted(remaining)
                if all(
                    outcomes.get(parent, TaskOutcome("")).status == "completed"
                    for parent in by_id[item].depends_on
                )
            ]
            if not ready:
                for item in sorted(remaining):
                    outcomes[item] = TaskOutcome(
                        "skipped", reason=_skip_reason(by_id[item], outcomes)
                    )
                    notify("skipped", by_id[item], None)
                break
            for task in ready:
                remaining.discard(task.id)
                started.append(task.id)
            await asyncio.gather(*(work(task) for task in ready))
        ok = all(outcome.status == "completed" for outcome in outcomes.values())
        return TeamResult(ok, self._final_text(outcomes), outcomes, tuple(started))

    def _worker_prompt(
        self, task: PlannedTask, outcomes: dict[str, TaskOutcome], by_id: dict[str, PlannedTask]
    ) -> str:
        parts = [
            f"Original request: {self.request}",
            f"Your task ({task.id}): {task.instructions}",
            f"Done when: {task.done_when}",
        ]
        upstream = [parent for parent in task.depends_on if outcomes[parent].text]
        if upstream:
            parts.append("Results from earlier tasks:")
            parts.extend(f"## {parent}\n{outcomes[parent].text}" for parent in upstream)
        return "\n\n".join(parts)

    def _final_text(self, outcomes: dict[str, TaskOutcome]) -> str:
        parents = {parent for task in self.tasks for parent in task.depends_on}
        sinks = [task.id for task in topological(self.tasks) if task.id not in parents]
        done = [item for item in sinks if outcomes.get(item, TaskOutcome("")).status == "completed"]
        if len(done) == 1 and len(sinks) == 1:
            return outcomes[done[0]].text
        return "\n\n".join(f"## {item}\n{outcomes[item].text}" for item in done)


def _skip_reason(task: PlannedTask, outcomes: dict[str, TaskOutcome]) -> str:
    blocked = [
        parent
        for parent in task.depends_on
        if outcomes.get(parent, TaskOutcome("")).status != "completed"
    ]
    return f"it depends on {', '.join(blocked)}, which did not complete"


def planner_instructions(roles: Sequence[Role], max_tasks: int) -> str:
    listing = "\n".join(f"- {role.name}: {role.description}" for role in roles)
    return (
        "You turn a request into a small plan of tasks. Reply with the plan only.\n"
        f"Use at most {max_tasks} tasks. Use one task when the job is simple or its steps "
        "must happen in order by one kind of worker; split only when parts are independent or "
        "need different roles.\n"
        "Each task has an id (kebab-case), a role, instructions, a done_when, and two lists: "
        "produces (snake_case names of results it hands on) and needs (results other tasks "
        "produce that it must wait for). Every name in needs must be produced by exactly one "
        "other task. Do not write dependencies; they follow from produces and needs.\n"
        f"Available roles:\n{listing}"
    )


class Planner:
    def __init__(
        self,
        *,
        model: ModelSetup,
        roles: Sequence[Role] = (),
        max_tasks: int = DEFAULT_MAX_TASKS,
        max_attempts: int = DEFAULT_ATTEMPTS,
        workspace: Path = DEFAULT_WORKSPACE / "planner",
    ) -> None:
        if max_tasks < 1 or max_attempts < 1:
            raise ValueError("max_tasks and max_attempts must be at least 1.")
        self._model = model
        self.roles = tuple(roles) or (Role(GENERIC_ROLE, "does any part of the job"),)
        self._max_tasks = max_tasks
        self._max_attempts = max_attempts
        self._workspace = workspace
        names = [role.name for role in self.roles]
        if len(set(names)) != len(names):
            raise ValueError(f"Role names must be unique; got {names}.")

    async def plan(self, request: str) -> PlanProposal:
        if not request.strip():
            raise ValueError("The request to plan is empty.")
        problems: list[str] = []
        previous = ""
        for _ in range(self._max_attempts):
            prompt = request if not problems else _retry_prompt(request, previous, problems)
            draft = await self._ask(prompt, problems)
            if draft is None:
                continue
            previous = draft.model_dump_json()
            try:
                plan, tasks = compile_draft(draft, request, self.roles, self._max_tasks)
            except PlanDraftError as error:
                problems = error.problems
                continue
            return PlanProposal(
                request=request,
                rationale=draft.rationale,
                tasks=tasks,
                plan=plan,
                choice=choose_architecture(tasks),
                roles=self.roles,
            )
        raise PlannerError(
            f"No valid plan after {self._max_attempts} attempts. Last problems: "
            + "; ".join(problems[:5])
        )

    def plan_sync(self, request: str) -> PlanProposal:
        return asyncio.run(self.plan(request))

    async def _ask(self, prompt: str, problems: list[str]) -> PlanDraft | None:
        agent = Agent(
            model=self._model,
            instructions=planner_instructions(self.roles, self._max_tasks),
            output=PlanDraft,
            max_steps=2,
            workspace=self._workspace,
            name="planner",
        )
        try:
            answer = await agent.run(prompt)
        finally:
            agent.close()
        if not answer.ok:
            problems[:] = [f"The planner model did not return a plan: {answer.reason}"]
            return None
        try:
            output = answer.output
            return output if isinstance(output, PlanDraft) else PlanDraft.model_validate(output)
        except ValidationError as error:
            problems[:] = [
                f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
                for issue in error.errors()[:5]
            ]
            return None


def _retry_prompt(request: str, previous: str, problems: Sequence[str]) -> str:
    listing = "\n".join(f"- {problem}" for problem in problems)
    return (
        f"{request}\n\nYour previous plan was:\n{previous}\n\n"
        f"It had these problems:\n{listing}\nReturn a corrected plan."
    )


__all__ = [
    "ArchitectureChoice",
    "PlanDraft",
    "PlanDraftError",
    "PlanProposal",
    "PlannedTask",
    "Planner",
    "PlannerError",
    "Role",
    "TaskOutcome",
    "TeamResult",
    "choose_architecture",
    "compile_draft",
    "topological",
]
