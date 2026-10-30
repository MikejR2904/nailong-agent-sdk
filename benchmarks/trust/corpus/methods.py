"""The attribution methods compared on the corpus, behind one name grammar.

    last-step | first-step                      the shape of the pipeline only
    ww-all | ww-step | ww-binary [+gt] : judge   Who&When's three LLM methods (corpus/whowhen.py),
                                                 +gt gives the correct final answer
    falat : judge                                FALAT, reimplemented (corpus/falat.py), with the
                                                 correct final answer as the method defines it
    sdk-structural | sdk-integrity | sdk-oracle | sdk-llmcheck[+integrity] [: judge]
                                                 attribute_root_cause with the stated checks

Every method gets the same case: the pipeline, the symptom (summary.txt is wrong) and the SDK's
ledgers. The LLM methods read the numbered trace built from the audit ledger; the SDK methods read
the journal, the artifact store and the same ledgers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from corpus import falat, whowhen
from corpus.llm import ask, json_object
from corpus.pipeline import OUTPUT_FILE, artifact_defective, truth
from corpus.prediction import Prediction
from corpus.trace import Step, load_steps, pipeline_preamble
from nailong_agent_sdk.developer_tools.attribute_root_cause import (
    CauseKind,
    RootCauseAnalysis,
    attribute_root_cause,
)
from nailong_agent_sdk.tools.artifacts import ArtifactStore

MODEL_PATH = "accounts/fireworks/models/"
SDK_FAMILIES = (
    "sdk-structural",
    "sdk-integrity",
    "sdk-oracle",
    "sdk-llmcheck",
    "sdk-llmcheck+integrity",
)
WHOWHEN = {
    "ww-all": whowhen.all_at_once,
    "ww-step": whowhen.step_by_step,
    "ww-binary": whowhen.binary_search,
}


def node_of(run_id: str | None) -> str | None:
    return run_id.removeprefix("node-") if run_id and run_id.startswith("node-") else None


def expected_answer(seed: int) -> str:
    expected = truth(seed)
    return f"TOTAL={expected['total']};TOP={expected['top']}"


def last_step(steps: list[Step]) -> Prediction:
    return Prediction(steps[-1].node, steps[-1].call_id, "last step")


def first_step(steps: list[Step]) -> Prediction:
    return Prediction(steps[0].node, steps[0].call_id, "first step")


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
        decide = LlmChecker(workspace, judge, key).defective

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


def _call_of(candidate: Any) -> str | None:
    if candidate.writer is not None:
        return candidate.writer.tool_call_id
    if candidate.kind in {CauseKind.DEFECTIVE_RESULT, CauseKind.RECORD_INCONSISTENCY}:
        return candidate.subject.rsplit(" call ", 1)[-1]
    return None


def prediction_of(analysis: RootCauseAnalysis) -> Prediction:
    ranked: list[str] = []
    for candidate in analysis.candidates:
        call = _call_of(candidate)
        if call and call not in ranked:
            ranked.append(call)
    primary = analysis.primary
    if primary is None:
        return Prediction(None, None, "no primary", tuple(ranked[:3]))
    call = _call_of(primary)
    node = node_of(primary.writer.agent_run_id if primary.writer else primary.run_id)
    return Prediction(node, call, primary.kind.value, tuple(ranked[:3]))


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


def split_name(name: str) -> tuple[str, bool, str]:
    family, _, judge = name.partition(":")
    ground_truth = family.endswith("+gt")
    return family.removesuffix("+gt"), ground_truth, judge


def predict(name: str, case_dir: Path, label: dict[str, Any], key: str) -> Prediction:
    steps = load_steps(case_dir)
    family, ground_truth, judge = split_name(name)
    judge_id = MODEL_PATH + judge if judge else ""
    seed = label["seed"]
    if family == "last-step":
        return last_step(steps)
    if family == "first-step":
        return first_step(steps)

    def chat(prompt: str) -> str:
        return ask(judge_id, prompt, key)

    preamble = pipeline_preamble(seed)
    if family in WHOWHEN:
        answer = expected_answer(seed) if ground_truth else None
        return WHOWHEN[family](steps, chat, answer, preamble)
    if family == "falat":
        return falat.falat(steps, chat, expected_answer(seed), preamble)
    if family in SDK_FAMILIES:
        return sdk_method(family, case_dir, seed, judge_id, key)
    raise ValueError(f'unknown method "{name}".')


def load_label(case_dir: Path) -> dict[str, Any]:
    return json.loads((case_dir / "label.json").read_text("utf-8"))
