"""The attribution methods compared on the corpus.

Every method gets the same case: the pipeline, the final symptom (summary.txt is wrong) and the
SDK's ledgers. The trivial baselines use only the shape of the pipeline. The LLM baselines are the
two Who&When methods (all at once, step by step) over the flat trace. The SDK methods use the
structure the SDK recorded, with the defect checks stated per method.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from corpus.llm import ask, json_object
from corpus.pipeline import OUTPUT_FILE, artifact_defective
from corpus.trace import Step, load_steps, render_step, render_trace
from nailong_agent_sdk.developer_tools.attribute_root_cause import (
    CauseKind,
    RootCauseAnalysis,
    attribute_root_cause,
)
from nailong_agent_sdk.tools.artifacts import ArtifactStore

SDK_MODES = (
    "sdk-structural",
    "sdk-integrity",
    "sdk-oracle",
    "sdk-llmcheck",
    "sdk-llmcheck+integrity",
)


@dataclass(frozen=True)
class Prediction:
    node: str | None
    call_id: str | None
    note: str = ""


def node_of(run_id: str | None) -> str | None:
    return run_id.removeprefix("node-") if run_id and run_id.startswith("node-") else None


def last_step(steps: list[Step]) -> Prediction:
    return Prediction(steps[-1].node, steps[-1].call_id, "last step")


def first_step(steps: list[Step]) -> Prediction:
    return Prediction(steps[0].node, steps[0].call_id, "first step")


ALL_AT_ONCE = """{trace}

The pipeline FAILED: its final output summary.txt does not contain the correct answer.
Find the decisive error: the earliest step whose outcome made the final output wrong. Steps are
numbered in the log. Answer with one JSON object and nothing else:
{{"agent": "A" or "B" or "C", "step": <step number>, "reason": "<one sentence>"}}"""

STEP_BY_STEP = """{trace}

The pipeline FAILED: its final output summary.txt does not contain the correct answer. You are
looking at the log up to step {step} only. Is step {step} the decisive error, the earliest step
whose outcome made the final output wrong? Answer with one JSON object and nothing else:
{{"decisive": true or false, "reason": "<one sentence>"}}"""


def llm_all_at_once(steps: list[Step], judge: str, key: str) -> Prediction:
    reply = ask(judge, ALL_AT_ONCE.format(trace=render_trace(steps)), key)
    found = json_object(reply)
    if not found:
        return Prediction(None, None, "unparseable")
    try:
        index = int(found["step"])
    except (KeyError, TypeError, ValueError):
        return Prediction(None, None, "no step")
    chosen = next((step for step in steps if step.index == index), None)
    if chosen is None:
        return Prediction(str(found.get("agent") or "") or None, None, f"step {index} not in log")
    return Prediction(chosen.node, chosen.call_id, "all-at-once")


def llm_step_by_step(steps: list[Step], judge: str, key: str) -> Prediction:
    for step in steps:
        reply = ask(
            judge, STEP_BY_STEP.format(trace=render_trace(steps, step.index), step=step.index), key
        )
        found = json_object(reply)
        if found and found.get("decisive") is True:
            return Prediction(step.node, step.call_id, "step-by-step")
    return Prediction(steps[-1].node, steps[-1].call_id, "step-by-step, none flagged")


CHECK = """A sales table is the source of a three-step pipeline.

SOURCE TABLE (csv):
{source}

The pipeline writes files that must contain:
- facts.json: a JSON object with the sum of the amount column for each region
- analysis.md: a line 'TOTAL: <grand total of all amounts>', a line 'TOP: <region with the
  largest total>' and one line per region with its total
- summary.txt: the single line TOTAL=<grand total>;TOP=<top region>

FILE {path}:
{content}

Compute from the source table what this file should contain and compare. Is any number or the
top region in the file wrong? Answer with one JSON object and nothing else:
{{"defective": true or false}}"""


class LlmChecker:
    def __init__(self, workspace: Path, judge: str, key: str) -> None:
        store = ArtifactStore(workspace, read_only=True)
        history = store.history("sales.csv")
        self.source = store.read_bytes(history[0].sha256).decode("utf-8")
        self.judge, self.key = judge, key

    def defective(self, path: str, content: str) -> bool:
        if path == "sales.csv":
            return False
        reply = ask(
            self.judge, CHECK.format(source=self.source, path=path, content=content), self.key
        )
        found = json_object(reply)
        return bool(found and found.get("defective") is True)


def sdk_checks(mode: str, seed: int, workspace: Path, judge: str, key: str) -> dict[str, Any]:
    if mode == "sdk-structural":

        def symptom(content: bytes, version: Any) -> bool:
            return artifact_defective("summary.txt", content.decode("utf-8"), seed)

        return {"artifact_checks": {"summary.txt": symptom}}
    if mode == "sdk-oracle":

        def decide(path: str, content: str) -> bool:
            return artifact_defective(path, content, seed)
    else:
        checker = LlmChecker(workspace, judge, key)
        decide = checker.defective

    def artifact(path: str):
        return lambda content, version: decide(path, content.decode("utf-8"))

    def lineage(path: str, content: bytes) -> bool:
        return decide(path, content.decode("utf-8"))

    def result(tool: str, arguments: dict[str, Any], outcome: dict[str, Any]) -> bool:
        output = outcome.get("output") if isinstance(outcome, dict) else None
        if tool != "read_artifact" or not isinstance(output, dict):
            return False
        return decide(str(output.get("path")), str(output.get("content")))

    return {
        "artifact_checks": {path: artifact(path) for path in OUTPUT_FILE.values()},
        "content_check": lineage,
        "result_check": result,
    }


def prediction_of(analysis: RootCauseAnalysis) -> Prediction:
    primary = analysis.primary
    if primary is None:
        return Prediction(None, None, "no primary")
    if primary.writer is not None:
        return Prediction(
            node_of(primary.writer.agent_run_id), primary.writer.tool_call_id, primary.kind.value
        )
    if primary.kind in {CauseKind.DEFECTIVE_RESULT, CauseKind.RECORD_INCONSISTENCY}:
        call_id = primary.subject.rsplit(" call ", 1)[-1]
        return Prediction(node_of(primary.run_id), call_id, primary.kind.value)
    return Prediction(node_of(primary.run_id), None, primary.kind.value)


def sdk_method(
    mode: str, case_dir: Path, seed: int, judge: str, key: str, **extra: Any
) -> Prediction:
    workspace = case_dir / "workspace"
    integrity = mode == "sdk-integrity" or mode.endswith("+integrity")
    base = "sdk-structural" if mode == "sdk-integrity" else mode.split("+")[0]
    checks = sdk_checks(base, seed, workspace, judge, key)
    options: dict[str, Any] = {}
    if integrity:
        options = {"verify_records": True, "related_runs": ["node-A", "node-B"]}
    analysis = attribute_root_cause(
        case_dir / "service",
        "node-C",
        workspace=workspace,
        assume_fault=True,
        **checks,
        **options,
        **extra,
    )
    return prediction_of(analysis)


def load_label(case_dir: Path) -> dict[str, Any]:
    return json.loads((case_dir / "label.json").read_text("utf-8"))


def load_case_steps(case_dir: Path) -> list[Step]:
    return load_steps(case_dir)


__all__ = [
    "SDK_MODES",
    "Prediction",
    "first_step",
    "last_step",
    "llm_all_at_once",
    "llm_step_by_step",
    "load_case_steps",
    "load_label",
    "render_step",
    "sdk_method",
]
