"""Read a corpus case back out of the SDK's ledgers as a flat, numbered trace."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from corpus.pipeline import NODES, OUTPUT_FILE, instructions_for
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore

RESULT_CHARS = 1_200


@dataclass(frozen=True)
class Step:
    index: int
    node: str
    iteration: int | None
    tool: str
    call_id: str
    arguments: dict[str, Any]
    status: str
    output: str


def load_steps(case_dir: Path) -> list[Step]:
    store = AuditTranscriptStore(case_dir / "service", read_only=True)
    steps: list[Step] = []
    try:
        for node in NODES:
            for entry in store.iter_entries(f"node-{node}"):
                if entry.event_type != "tool-result":
                    continue
                payload = entry.payload
                result = payload.get("result") or {}
                output = result.get("output")
                text = result.get("error") if output is None else json.dumps(output, sort_keys=True)
                steps.append(
                    Step(
                        index=len(steps) + 1,
                        node=node,
                        iteration=entry.iteration,
                        tool=str(payload.get("tool")),
                        call_id=str(payload.get("tool_call_id")),
                        arguments=payload.get("arguments") or {},
                        status=str(result.get("status")),
                        output=str(text),
                    )
                )
    finally:
        store.close()
    return steps


def render_step(step: Step, *, chars: int = RESULT_CHARS) -> str:
    arguments = json.dumps(step.arguments, sort_keys=True)
    result = step.output if len(step.output) <= chars else step.output[:chars] + " ...[cut]"
    return (
        f"[Step {step.index}] agent {step.node}, turn {step.iteration}\n"
        f"  CALL {step.tool}({arguments})\n"
        f"  RESULT {step.status}: {result}"
    )


def pipeline_preamble(seed: int = 0) -> str:
    lines = [
        "A three-agent pipeline processes a sales table (month, region, amount). Agent A "
        "extracts per-region totals, agent B computes a grand total and the top region, and "
        "agent C writes the final one-line headline summary.txt. Each agent has two tools, "
        "read_artifact and write_draft. The instruction given to each agent was:",
    ]
    for node in NODES:
        text = instructions_for(node, seed).format(
            artifact_id="<id of the previous artifact>",
            FINAL="the final reply",
        )
        lines.append(f"- Agent {node} (writes {OUTPUT_FILE[node]}): {text}")
    return "\n".join(lines)


def render_trace(steps: list[Step], upto: int | None = None, seed: int = 0) -> str:
    chosen = steps if upto is None else [step for step in steps if step.index <= upto]
    return pipeline_preamble(seed) + "\n\nLOG\n" + "\n".join(render_step(step) for step in chosen)
