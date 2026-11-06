"""A plan that survives the process: what was planned, what finished, and what a crash cut short.

A team plan writes one append-only journal under `.nailong/tasks/`. Each task has an idempotency
key built from what it was asked to do and from the keys of the tasks it depends on. A task whose
result was committed under the same key is not run again when the plan is resumed; a task that was
started and never finished is run again with a note that its files may be half-changed; a task
whose instructions changed (or whose upstream result changed) has a different key and runs afresh.
Only one process runs a plan at a time.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter.planner import (
    DraftTask,
    PlanDraft,
    PlannedTask,
    PlanProposal,
    Role,
    choose_architecture,
    compile_draft,
)

from .storage import append_durably, pid_alive

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_RESULT_CHARS = 20_000


def task_keys(proposal: PlanProposal) -> dict[str, str]:
    keys: dict[str, str] = {}
    by_id = {task.id: task for task in proposal.tasks}

    def key(task: PlannedTask) -> str:
        if task.id not in keys:
            parents = [key(by_id[parent]) for parent in sorted(task.depends_on)]
            material = json.dumps(
                [
                    proposal.request,
                    task.id,
                    task.role,
                    task.instructions,
                    task.done_when,
                    sorted(task.produces),
                    sorted(task.needs),
                    parents,
                ]
            )
            keys[task.id] = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
        return keys[task.id]

    for task in proposal.tasks:
        key(task)
    return keys


@dataclass
class TaskRecord:
    status: str = "pending"
    attempts: int = 0
    text: str = ""
    reason: str = ""
    key: str = ""


@dataclass
class PlanState:
    plan_id: str
    request: str
    rationale: str
    draft: list[dict[str, Any]]
    roles: list[dict[str, Any]]
    keys: dict[str, str]
    created: str
    tasks: dict[str, TaskRecord] = field(default_factory=dict)
    finished: bool = False
    abandoned: bool = False

    @property
    def unfinished(self) -> bool:
        return not self.finished and not self.abandoned

    def committed(self, current: dict[str, str] | None = None) -> dict[str, str]:
        """Results still valid: committed under the key the task has now (current, if given)."""
        wanted = self.keys if current is None else current
        return {
            task_id: record.text
            for task_id, record in self.tasks.items()
            if record.status == "completed" and record.key == wanted.get(task_id)
        }

    def interrupted(self) -> list[str]:
        return [
            task_id
            for task_id, record in self.tasks.items()
            if record.status in {"running", "interrupted"}
        ]

    def proposal(self, roles: tuple[Role, ...]) -> PlanProposal:
        known = {role.name: role for role in roles}
        missing = sorted({item["role"] for item in self.draft} - set(known))
        if missing:
            raise ValueError(
                f"Plan {self.plan_id} needs the role(s) {missing}, which are not defined now; "
                "restore them or /plan abandon it."
            )
        draft = PlanDraft(
            rationale=self.rationale, tasks=[DraftTask(**item) for item in self.draft]
        )
        plan, tasks = compile_draft(draft, self.request, roles)
        return PlanProposal(
            self.request, self.rationale, tasks, plan, choose_architecture(tasks), tuple(roles)
        )


class PlanJournal:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def _path(self, plan_id: str) -> Path:
        if not _ID.match(plan_id):
            raise ValueError(f'"{plan_id}" is not a plan id.')
        return self._directory / f"{plan_id}.jsonl"

    def _append(self, plan_id: str, event: dict[str, Any]) -> None:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        append_durably(
            self._path(plan_id), json.dumps({"at": stamp, **event}, separators=(",", ":"))
        )

    def create(self, proposal: PlanProposal) -> str:
        plan_id = f"plan-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        draft = [
            {
                "id": task.id,
                "role": task.role,
                "instructions": task.instructions,
                "done_when": task.done_when,
                "produces": list(task.produces),
                "needs": list(task.needs),
            }
            for task in proposal.tasks
        ]
        roles = [
            {"name": role.name, "description": role.description, "writable": role.writable}
            for role in proposal.roles
        ]
        self._append(
            plan_id,
            {
                "event": "plan",
                "request": proposal.request,
                "rationale": proposal.rationale,
                "draft": draft,
                "roles": roles,
                "keys": task_keys(proposal),
            },
        )
        return plan_id

    def record(self, plan_id: str, event: str, task_id: str | None = None, **values: Any) -> None:
        body: dict[str, Any] = {"event": event, **values}
        if task_id is not None:
            body["task"] = task_id
        if "text" in body:
            body["text"] = str(body["text"])[:MAX_RESULT_CHARS]
        self._append(plan_id, body)

    def load(self, plan_id: str) -> PlanState:
        path = self._path(plan_id)
        if not path.is_file():
            raise ValueError(f'There is no plan "{plan_id}"; plans: {self.ids()}.')
        lines = path.read_text(encoding="utf-8").splitlines()
        state: PlanState | None = None
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if number == len(lines):
                    continue
                raise ValueError(
                    f"Line {number} of {path} is not JSON and is not the last line, so a crash "
                    "did not cut it off; the plan journal needs repair by hand."
                ) from None
            kind = event.get("event")
            if kind == "plan":
                state = PlanState(
                    plan_id,
                    event["request"],
                    event["rationale"],
                    event["draft"],
                    event["roles"],
                    event["keys"],
                    event["at"],
                    {
                        item["id"]: TaskRecord(key=event["keys"][item["id"]])
                        for item in event["draft"]
                    },
                )
            elif state is None:
                raise ValueError(f"{path} does not start with a plan event.")
            elif kind == "finished":
                state.finished = True
            elif kind == "abandoned":
                state.abandoned = True
            elif kind == "resumed":
                state.finished = False
                for task_id, record in state.tasks.items():
                    if record.status == "running":
                        record.status = "interrupted"
            elif event.get("task") in state.tasks:
                record = state.tasks[event["task"]]
                if kind == "started":
                    record.status, record.attempts = "running", event.get("attempt", 1)
                    record.key = event.get("key", record.key)
                elif kind == "done":
                    record.status, record.text = "completed", event.get("text", "")
                    record.key = event.get("key", record.key)
                elif kind in {"failed", "escalated"}:
                    record.status, record.reason = kind, event.get("reason", "")
                elif kind == "skipped":
                    record.status, record.reason = "skipped", event.get("reason", "")
        if state is None:
            raise ValueError(f"{path} holds no plan.")
        return state

    def ids(self) -> list[str]:
        if not self._directory.is_dir():
            return []
        return sorted(path.stem for path in self._directory.glob("plan-*.jsonl"))

    def unfinished(self) -> list[PlanState]:
        found = []
        for plan_id in self.ids():
            try:
                state = self.load(plan_id)
            except ValueError:
                continue
            if state.unfinished:
                found.append(state)
        return found

    def acquire(self, plan_id: str) -> Path:
        """Take the plan for this process; refuse while another live process runs it."""
        lock = self._directory / f"{plan_id}.running"
        self._directory.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    holder = int(lock.read_text(encoding="utf-8").strip() or 0)
                except (OSError, ValueError):
                    holder = 0
                if holder and holder != os.getpid() and pid_alive(holder):
                    raise ValueError(
                        f"Plan {plan_id} is being run by process {holder}; wait for it to finish."
                    ) from None
                lock.unlink(missing_ok=True)
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            return lock
        raise ValueError(f"Could not take plan {plan_id}: {lock} keeps reappearing.")

    def release(self, lock: Path) -> None:
        lock.unlink(missing_ok=True)


def describe(state: PlanState) -> list[str]:
    counts: dict[str, int] = {}
    for record in state.tasks.values():
        counts[record.status] = counts.get(record.status, 0) + 1
    summary = ", ".join(f"{n} {name}" for name, n in sorted(counts.items()))
    lines = [f"{state.plan_id}  {state.request[:70]}  ({summary})"]
    committed = state.committed()
    for task_id, record in state.tasks.items():
        mark = "kept" if task_id in committed else record.status
        detail = f": {record.reason[:80]}" if record.reason else ""
        lines.append(f"    {task_id} {mark}{detail}")
    return lines


__all__ = ["PlanJournal", "PlanState", "TaskRecord", "describe", "pid_alive", "task_keys"]
