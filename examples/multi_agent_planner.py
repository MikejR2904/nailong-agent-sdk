# Copyright (c) 2026 David Michael Indraputra

"""Let a model split a request into tasks, then choose to run it as a team or in one agent.

No specification is needed: the request is the plan's source. The models are scripted so this
runs offline; replace `scripted(...)` with `openai_chat(...)` or `anthropic_messages(...)`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import Agent, ModelSetup, Planner, Role

REQUEST = "Compare vendors A and B on pricing and security, then write a one-page brief."
ROLES = [
    Role("researcher", "finds facts and reports only what sources say"),
    Role("writer", "writes the final document", writable=True),
]
PLAN = {
    "rationale": "Pricing and security are independent; the brief needs both.",
    "tasks": [
        {
            "id": "pricing",
            "role": "researcher",
            "instructions": "Compare the two vendors' pricing.",
            "done_when": "A short comparison exists.",
            "produces": ["pricing_notes"],
            "needs": [],
        },
        {
            "id": "security",
            "role": "researcher",
            "instructions": "Compare the two vendors' security.",
            "done_when": "A short comparison exists.",
            "produces": ["security_notes"],
            "needs": [],
        },
        {
            "id": "brief",
            "role": "writer",
            "instructions": "Write the one-page brief from the notes.",
            "done_when": "The brief covers pricing and security.",
            "produces": [],
            "needs": ["pricing_notes", "security_notes"],
        },
    ],
}


class Sequence:
    def __init__(self, *turns: dict[str, Any]) -> None:
        self.turns = [ScriptedModel([turn]) for turn in turns]
        self.calls: list[Any] = []

    async def next_turn(self, context: Any) -> Any:
        self.calls.append(context)
        return await self.turns[len(self.calls) - 1].next_turn(context)


def scripted(*answers: str) -> ModelSetup:
    turns = [{"type": "final", "output": {"status": "complete", "answer": a}} for a in answers]
    return ModelSetup(Sequence(*turns), "scripted", "scripted-1")


async def run(workspace: Path, keep_in_one_agent: bool = False) -> dict[str, Any]:
    planner_model = ModelSetup(
        Sequence({"type": "final", "output": PLAN}), "scripted", "scripted-1"
    )
    planner = Planner(model=planner_model, roles=ROLES, workspace=workspace / "planner")
    proposal = await planner.plan(REQUEST)
    result: dict[str, Any] = {
        "suggested": proposal.choice.architecture,
        "reasons": list(proposal.choice.reasons),
        "summary": proposal.summary(),
    }

    if keep_in_one_agent or not proposal.choice.team:

        def solo(roles) -> Agent:
            return Agent(
                model=scripted("Brief: A is cheaper, B has SOC 2."),
                instructions="Do the whole job.",
                workspace=workspace / "solo",
            )

        answer = await proposal.run_solo(solo)
        result.update(ran="single", text=answer.text)
        return result

    replies = {
        "pricing": "A costs less per seat.",
        "security": "Only B has SOC 2.",
        "brief": "Brief: A is cheaper, B has SOC 2.",
    }

    def worker(role: Role, task) -> Agent:
        return Agent(
            model=scripted(replies[task.id]),
            instructions=role.description,
            workspace=workspace / task.id,
            name=task.id,
        )

    team = await proposal.run_team(worker, max_parallel=2)
    result.update(ran="team", text=team.text, order=list(team.order), ok=team.ok)
    return result


async def main() -> None:
    result = await run(Path(".agent-runs/planner"))
    print(result["summary"])
    print(f"Ran as: {result['ran']}\n{result['text']}")


if __name__ == "__main__":
    asyncio.run(main())
