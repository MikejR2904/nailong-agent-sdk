"""Run every attribution method over a split of the corpus and report the accuracy.

python -m corpus.evaluate --split dev --out results/corpus_eval_dev.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from corpus.build import short
from corpus.llm import USAGE
from corpus.methods import (
    Prediction,
    first_step,
    last_step,
    llm_all_at_once,
    llm_step_by_step,
    load_case_steps,
    load_label,
    sdk_method,
)

ROOT = Path("results/corpus")
MODEL_PATH = "accounts/fireworks/models/"


def wilson(hits: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 0.0, 1.0
    z = 1.96
    p = hits / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    return max(0.0, centre - spread), min(1.0, centre + spread)


def eligible(label: dict[str, Any]) -> bool:
    return bool(
        label.get("kind") in {"read", "write"}
        and label.get("pipeline_complete")
        and label.get("outcome_failed")
        and label.get("fault_call")
    )


def methods(judges: list[str], stepwise: str | None, sdk_judge: str) -> list[str]:
    names = ["last-step", "first-step"]
    names += [f"llm-all:{judge}" for judge in judges]
    if stepwise:
        names.append(f"llm-step:{stepwise}")
    names += [
        "sdk-structural",
        "sdk-integrity",
        "sdk-oracle",
        f"sdk-llmcheck:{sdk_judge}",
        f"sdk-llmcheck+integrity:{sdk_judge}",
    ]
    return names


def predict(name: str, case_dir: Path, label: dict[str, Any], key: str) -> Prediction:
    steps = load_case_steps(case_dir)
    family, _, judge = name.partition(":")
    judge_id = MODEL_PATH + judge if judge else ""
    if name == "last-step":
        return last_step(steps)
    if name == "first-step":
        return first_step(steps)
    if family == "llm-all":
        return llm_all_at_once(steps, judge_id, key)
    if family == "llm-step":
        return llm_step_by_step(steps, judge_id, key)
    return sdk_method(family, case_dir, label["seed"], judge_id, key)


def score_one(name: str, case_dir: Path, label: dict[str, Any], key: str) -> dict[str, Any]:
    try:
        prediction = predict(name, case_dir, label, key)
        error = None
    except Exception as failure:
        prediction = Prediction(None, None, "error")
        error = f"{type(failure).__name__}: {str(failure)[:200]}"
    truth = label["fault_call"]
    return {
        "case_id": label["case_id"],
        "method": name,
        "model": short(label["model"]),
        "kind": label["kind"],
        "node": label["node"],
        "predicted_node": prediction.node,
        "predicted_call": prediction.call_id,
        "note": prediction.note,
        "error": error,
        "agent_ok": prediction.node == truth["node"],
        "step_ok": prediction.call_id == truth["call_id"],
    }


def table(rows: list[dict[str, Any]], names: list[str], key_of) -> list[str]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(key_of(row), row["method"])].append(row)
    labels = sorted({key for key, _ in groups})
    lines = ["| group | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for label in labels:
        cells = []
        for name in names:
            cell = groups.get((label, name), [])
            if not cell:
                cells.append("-")
                continue
            agent = sum(1 for r in cell if r["agent_ok"])
            step = sum(1 for r in cell if r["step_ok"])
            cells.append(f"{agent}/{len(cell)} · {step}/{len(cell)}")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return lines


def render(rows: list[dict[str, Any]], names: list[str], split: str, skipped: int) -> str:
    cases = len({r["case_id"] for r in rows})
    lines = [
        f"# Root-cause attribution on the corpus, {split} split",
        "",
        f"{cases} failed runs with a planted tool fault ({skipped} cases in the split were "
        "left out: no fault, an incomplete pipeline, or a correct final answer). Each cell is "
        "agent-correct / cases · step-correct / cases.",
        "",
        "## All cases",
        "",
        "| method | agent | step | agent 95% | step 95% | errors |",
        "|---|---|---|---|---|---|",
    ]
    for name in names:
        cell = [r for r in rows if r["method"] == name]
        if not cell:
            continue
        agent = sum(1 for r in cell if r["agent_ok"])
        step = sum(1 for r in cell if r["step_ok"])
        a_low, a_high = wilson(agent, len(cell))
        s_low, s_high = wilson(step, len(cell))
        errors = sum(1 for r in cell if r["error"])
        lines.append(
            f"| {name} | {agent}/{len(cell)} | {step}/{len(cell)} "
            f"| [{a_low:.2f}, {a_high:.2f}] | [{s_low:.2f}, {s_high:.2f}] | {errors} |"
        )
    for title, key_of in (
        ("By fault kind", lambda r: r["kind"]),
        ("By node of the fault", lambda r: r["node"]),
        ("By model that ran the pipeline", lambda r: r["model"]),
    ):
        lines += ["", f"## {title}", ""] + table(rows, names, key_of)
    lines += [
        "",
        f"LLM calls made: {USAGE.calls} ({USAGE.cached} answered from the cache); "
        f"tokens sent {USAGE.prompt:,} and received {USAGE.completion:,}.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="dev")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--judges", default="glm-5p3-flash,deepseek-v4p1-flash")
    parser.add_argument("--stepwise", default="glm-5p3-flash")
    parser.add_argument("--sdk-judge", default="glm-5p3-flash")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--only", default="")
    arguments = parser.parse_args(argv)
    from corpus.build import api_key

    key = api_key()
    labels = [
        load_label(path.parent)
        for path in sorted(arguments.root.glob("*/label.json"))
        if load_label(path.parent).get("split") == arguments.split
    ]
    chosen = [label for label in labels if eligible(label)]
    names = methods(
        [item for item in arguments.judges.split(",") if item],
        arguments.stepwise or None,
        arguments.sdk_judge,
    )
    if arguments.only:
        names = [name for name in names if name.split(":")[0] in arguments.only.split(",")]
    jobs = [(name, label) for label in chosen for name in names]
    rows: list[dict[str, Any]] = []
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=arguments.workers) as pool:
        futures = [
            pool.submit(score_one, name, arguments.root / label["case_id"], label, key)
            for name, label in jobs
        ]
        for count, future in enumerate(futures, start=1):
            rows.append(future.result())
            if count % 25 == 0:
                print(f"{count}/{len(jobs)}", flush=True)
    arguments.out.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    text = render(rows, names, arguments.split, len(labels) - len(chosen))
    report = arguments.report or Path(f"reports/corpus_eval_{arguments.split}.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
