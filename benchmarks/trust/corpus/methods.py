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
import re
from pathlib import Path
from typing import Any

from corpus import falat, whowhen
from corpus.llm import ask
from corpus.pipeline import OUTPUT_FILE, answer_line, artifact_defective, file_spec
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
    "sdk-structural+trace",
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
    return answer_line(seed)


def last_step(steps: list[Step]) -> Prediction:
    return Prediction(steps[-1].node, steps[-1].call_id, "last step")


def first_step(steps: list[Step]) -> Prediction:
    return Prediction(steps[0].node, steps[0].call_id, "first step")


CHECK = """A sales table is the source of a three-step pipeline.

SOURCE TABLE (csv):
{source}

The pipeline writes files that must contain:
{spec}

FILE {path}:
{content}

Compute from the source table what this file should contain and compare. Is any number or the
top region in the file wrong, or is any line the description requires missing? Work out every
needed sum row by row before you judge; the last line of your reply must be one JSON object:
{{"defective": true or false}}"""
VOTES = 3


class LlmChecker:
    def __init__(self, workspace: Path, judge: str, key: str, seed: int) -> None:
        store = ArtifactStore(workspace, read_only=True)
        history = store.history("sales.csv")
        self.source = store.read_bytes(history[0].sha256).decode("utf-8")
        self.judge, self.key, self.spec = judge, key, file_spec(seed)

    def defective(self, path: str, content: str) -> bool:
        if path == "sales.csv":
            return False
        prompt = CHECK.format(source=self.source, spec=self.spec, path=path, content=content)
        votes = 0
        for vote in range(VOTES):
            reply = ask(self.judge, f"{prompt}\n(independent check {vote + 1})", self.key)
            verdicts = re.findall(r'"defective"\s*:\s*(true|false)', reply)
            votes += bool(verdicts and verdicts[-1] == "true")
        return votes * 2 > VOTES


def sdk_checks(mode: str, seed: int, workspace: Path, judge: str, key: str) -> dict[str, Any]:
    if mode == "sdk-structural":

        def symptom(content: bytes, version: Any) -> bool:
            return artifact_defective("summary.txt", content.decode("utf-8"), seed)

        return {"artifact_checks": {"summary.txt": symptom}}
    if mode == "sdk-oracle":

        def decide(path: str, content: str) -> bool:
            return artifact_defective(path, content, seed)
    else:
        decide = LlmChecker(workspace, judge, key, seed).defective

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
    base, *flags = mode.split("+")
    if base == "sdk-integrity":
        base, flags = "sdk-structural", [*flags, "integrity"]
    checks = sdk_checks(base, seed, workspace, judge, key)
    options: dict[str, Any] = {}
    if "integrity" in flags:
        options = {"verify_records": True, "related_runs": ["node-A", "node-B"]}
    if "trace" in flags:
        options["trace_unchecked"] = True
    options.setdefault("assume_fault", True)
    analysis = attribute_root_cause(
        case_dir / "service", "node-C", workspace=workspace, **checks, **options, **extra
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
