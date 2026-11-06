import sys
import time
from pathlib import Path
from types import SimpleNamespace

from nailong_agent_sdk.starter.planner import (
    DraftTask,
    PlanDraft,
    PlanProposal,
    choose_architecture,
    compile_draft,
)

from nailong_cli.plan_command import cli_roles
from nailong_cli.taskstate import PlanJournal

project = Path(sys.argv[1])
journal = PlanJournal(project / ".nailong" / "tasks")
rows = [
    {
        "id": "first",
        "role": "explorer",
        "instructions": "do first",
        "done_when": "done",
        "produces": ["a"],
        "needs": [],
    },
    {
        "id": "second",
        "role": "worker",
        "instructions": "do second",
        "done_when": "done",
        "produces": [],
        "needs": ["a"],
    },
]

roles = cli_roles(SimpleNamespace(agents={}))
draft = PlanDraft(rationale="r", tasks=[DraftTask(**row) for row in rows])
plan, tasks = compile_draft(draft, "the request", roles)
proposal = PlanProposal("the request", "r", tasks, plan, choose_architecture(tasks), tuple(roles))
plan_id = journal.create(proposal)
journal.acquire(plan_id)
journal.record(plan_id, "started", "first", attempt=1, key=journal.load(plan_id).keys["first"])
journal.record(
    plan_id, "done", "first", text="first result", key=journal.load(plan_id).keys["first"]
)
journal.record(plan_id, "started", "second", attempt=1, key=journal.load(plan_id).keys["second"])
print(plan_id, flush=True)
time.sleep(120)
