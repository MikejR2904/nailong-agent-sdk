# Copyright (c) 2026 David Michael Indraputra

"""Two ways to combine agents with the starter: a supervisor that calls specialists, and a fan-out.

The models are scripted so this runs offline. Replace each `scripted(...)` with a real
`openai_chat(...)` or `anthropic_messages(...)` setup and nothing else changes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import Agent, ModelSetup


def scripted(*turns: dict) -> ModelSetup:
    return ModelSetup(ScriptedModel(list(turns)), "scripted", "scripted-1")


def final(answer: str) -> dict:
    return {"type": "final", "output": {"status": "complete", "answer": answer}}


def call(call_id: str, tool: str, **arguments: object) -> dict:
    return {"type": "tool-call", "call": {"id": call_id, "name": tool, "arguments": arguments}}


async def supervisor_with_specialists(workspace: Path) -> str:
    researcher = Agent(
        model=scripted(final("Three sources agree the part is rated for 85 C.")),
        instructions="You research one question and report only what the sources say.",
        workspace=workspace / "researcher",
        name="researcher",
    )
    reviewer = Agent(
        model=scripted(final("The claim is supported; one source is a vendor datasheet.")),
        instructions="You check a claim and say how well supported it is.",
        workspace=workspace / "reviewer",
        name="reviewer",
    )
    supervisor = Agent(
        model=scripted(
            call("1", "research", task="What temperature is the part rated for?"),
            call("2", "review", task="Three sources agree the part is rated for 85 C."),
            final("The part is rated for 85 C (supported; one source is a vendor datasheet)."),
        ),
        instructions="Delegate research, then have the claim reviewed, then answer once.",
        tools=[
            researcher.as_tool(
                name="research",
                description="Ask the researcher one question; returns what the sources say.",
                read_only=True,
            ),
            reviewer.as_tool(
                name="review",
                description="Ask the reviewer how well a claim is supported.",
                read_only=True,
            ),
        ],
        workspace=workspace / "supervisor",
        name="supervisor",
    )
    try:
        answer = await supervisor.run("What is the part's temperature rating?")
        return answer.text
    finally:
        for agent in (researcher, reviewer, supervisor):
            agent.close()


async def fan_out_then_combine(workspace: Path) -> str:
    topics = {
        "pricing": "Pricing is per seat with a free tier.",
        "security": "SOC 2 report available; data stays in the region.",
        "limits": "Rate limit is 60 requests a minute.",
    }
    workers = {
        name: Agent(
            model=scripted(final(text)),
            instructions=f"Summarise the {name} of the product in one sentence.",
            workspace=workspace / name,
            name=name,
        )
        for name, text in topics.items()
    }
    editor = Agent(
        model=scripted(final("Pricing, security and limits are covered; see the notes above.")),
        instructions="Combine the notes into a short brief.",
        workspace=workspace / "editor",
        name="editor",
    )
    try:
        answers = await asyncio.gather(
            *(agent.run(f"Cover {name}.") for name, agent in workers.items())
        )
        notes = "\n".join(
            f"- {name}: {answer.text}" for name, answer in zip(workers, answers, strict=True)
        )
        brief = await editor.run(f"Write the brief from these notes:\n{notes}")
        return brief.text
    finally:
        for agent in (*workers.values(), editor):
            agent.close()


async def main() -> None:
    workspace = Path(".agent-runs/multi-agent-starter")
    print("supervisor:", await supervisor_with_specialists(workspace))
    print("fan-out:", await fan_out_then_combine(workspace))


if __name__ == "__main__":
    asyncio.run(main())
