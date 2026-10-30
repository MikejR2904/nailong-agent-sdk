"""Run every attribution method over a split of the corpus and report the metrics.

python -m corpus.evaluate --dataset planted --split dev --out results/corpus_eval_dev.jsonl
python -m corpus.evaluate --dataset natural --split dev --out results/natural_eval_dev.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from corpus import stats
from corpus.build import api_key, short
from corpus.llm import REPEAT, USAGE, track
from corpus.methods import load_label, predict
from corpus.prediction import Prediction
from corpus.trace import load_steps

ROOTS = {"planted": Path("results/corpus"), "natural": Path("results/corpus_natural")}
LLM_FAMILIES = ("ww-all", "ww-step", "ww-binary", "falat")


class TokenCap(RuntimeError):
    pass


def eligible(label: dict[str, Any], dataset: str) -> bool:
    kinds = {"planted": {"read", "write"}, "natural": {"natural"}}[dataset]
    return bool(
        label.get("kind") in kinds
        and label.get("pipeline_complete")
        and label.get("outcome_failed")
        and label.get("fault_call")
    )


def methods(arguments: argparse.Namespace) -> list[str]:
    judges = [item for item in arguments.judges.split(",") if item]
    names = ["last-step", "first-step"]
    for judge in judges:
        for family in ("ww-all", "ww-binary", "falat"):
            names.append(f"{family}:{judge}")
        for family in ("ww-all+gt", "ww-binary+gt"):
            names.append(f"{family}:{judge}")
    if arguments.stepwise:
        names += [f"ww-step:{arguments.stepwise}", f"ww-step+gt:{arguments.stepwise}"]
    names += [
        "sdk-structural",
        "sdk-integrity",
        "sdk-oracle",
        f"sdk-llmcheck:{arguments.sdk_judge}",
        f"sdk-llmcheck+integrity:{arguments.sdk_judge}",
    ]
    if arguments.only:
        wanted = set(arguments.only.split(","))
        names = [name for name in names if name.split(":")[0] in wanted]
    return names


def is_llm(name: str) -> bool:
    return name.split(":")[0].removesuffix("+gt") in LLM_FAMILIES


def score_one(
    name: str, case_dir: Path, label: dict[str, Any], key: str, repeat: int, cap: int
) -> dict[str, Any]:
    truth = label["fault_call"]
    order = [step.call_id for step in load_steps(case_dir)]
    REPEAT.set(repeat)
    error = None
    with track() as cost:
        try:
            if cap and USAGE.prompt + USAGE.completion > cap:
                raise TokenCap(f"the {cap:,}-token cap on new model calls was reached")
            prediction = predict(name, case_dir, label, key)
        except Exception as failure:
            prediction = Prediction(None, None, "error")
            error = f"{type(failure).__name__}: {str(failure)[:200]}"
    distance = None
    if prediction.call_id in order and truth["call_id"] in order:
        distance = abs(order.index(prediction.call_id) - order.index(truth["call_id"]))
    return {
        "case_id": label["case_id"],
        "method": name,
        "repeat": repeat,
        "model": short(label["model"]),
        "kind": label["kind"],
        "node": label["node"],
        "steps": len(order),
        "predicted_node": prediction.node,
        "predicted_call": prediction.call_id,
        "ranked": list(prediction.ranked),
        "note": prediction.note,
        "error": error,
        "answered": prediction.call_id is not None,
        "agent_ok": prediction.node == truth["node"],
        "step_ok": prediction.call_id == truth["call_id"],
        "near1": distance is not None and distance <= 1,
        "near2": distance is not None and distance <= 2,
        "top3_ok": truth["call_id"] in (prediction.ranked or (prediction.call_id,)),
        "calls": cost.calls,
        "prompt_tokens": cost.prompt_tokens,
        "completion_tokens": cost.completion_tokens,
        "seconds": round(cost.seconds, 2),
    }


def first_repeat(rows: list[dict[str, Any]], name: str) -> dict[str, dict[str, Any]]:
    return {r["case_id"]: r for r in rows if r["method"] == name and r["repeat"] == 0}


def share(rows: list[dict[str, Any]], field: str) -> str:
    return f"{sum(1 for r in rows if r[field])}/{len(rows)}"


def summary_table(rows: list[dict[str, Any]], names: list[str]) -> list[str]:
    lines = [
        "| method | agent | step | step 95% | ±1 | ±2 | top-3 | answered | step if answered "
        "| calls | tokens | seconds | errors |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in names:
        cell = list(first_repeat(rows, name).values())
        if not cell:
            continue
        step = sum(1 for r in cell if r["step_ok"])
        low, high = stats.wilson(step, len(cell))
        answered = [r for r in cell if r["answered"]]
        when = f"{sum(1 for r in answered if r['step_ok'])}/{len(answered)}" if answered else "-"
        count = len(cell)
        calls = sum(r["calls"] for r in cell) / count
        tokens = sum(r["prompt_tokens"] + r["completion_tokens"] for r in cell) / count
        seconds = sum(r["seconds"] for r in cell) / count
        lines.append(
            f"| {name} | {share(cell, 'agent_ok')} | {step}/{count} | [{low:.2f}, {high:.2f}] "
            f"| {share(cell, 'near1')} | {share(cell, 'near2')} | {share(cell, 'top3_ok')} "
            f"| {share(cell, 'answered')} | {when} | {calls:.1f} | {tokens:,.0f} | {seconds:.0f} "
            f"| {sum(1 for r in cell if r['error'])} |"
        )
    return lines


def breakdown(
    rows: list[dict[str, Any]], names: list[str], key_of: Any, field: str = "step_ok"
) -> list[str]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for name in names:
        for row in first_repeat(rows, name).values():
            groups[(str(key_of(row)), name)].append(row)
    labels = sorted({label for label, _ in groups})
    lines = ["| group | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for label in labels:
        cells = []
        for name in names:
            cell = groups.get((label, name), [])
            cells.append(share(cell, field) if cell else "-")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return lines


def paired(rows: list[dict[str, Any]], names: list[str], reference: str) -> list[str]:
    base = first_repeat(rows, reference)
    lines = [
        f"Step-level accuracy of each method minus that of {reference}, on the same cases.",
        "",
        "| method | reference only right | method only right | difference [95% bootstrap] "
        "| McNemar p |",
        "|---|---|---|---|---|",
    ]
    for name in names:
        if name == reference:
            continue
        other = first_repeat(rows, name)
        shared = sorted(set(base) & set(other))
        if not shared:
            continue
        a = [bool(other[case]["step_ok"]) for case in shared]
        b = [bool(base[case]["step_ok"]) for case in shared]
        only_a, only_b, p = stats.mcnemar_exact(a, b)
        delta, low, high = stats.paired_difference_interval(a, b)
        lines.append(
            f"| {name} | {only_b} | {only_a} | {delta:+.2f} [{low:+.2f}, {high:+.2f}] | {p:.3f} |"
        )
    return lines


def stability(rows: list[dict[str, Any]], names: list[str]) -> list[str]:
    lines = [
        "| method | repeats | cases | same answer on every repeat | step accuracy per repeat |"
    ]
    lines += ["|---|---|---|---|---|"]
    for name in names:
        by_repeat: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in rows:
            if row["method"] == name:
                by_repeat[row["repeat"]][row["case_id"]] = row
        if len(by_repeat) < 2:
            continue
        shared = sorted(set.intersection(*(set(items) for items in by_repeat.values())))
        runs = [[by_repeat[r][c]["predicted_call"] for c in shared] for r in sorted(by_repeat)]
        accuracy = ", ".join(
            f"{sum(1 for c in shared if by_repeat[r][c]['step_ok'])}/{len(shared)}"
            for r in sorted(by_repeat)
        )
        lines.append(
            f"| {name} | {len(by_repeat)} | {len(shared)} | {stats.agreement(runs):.2f} "
            f"| {accuracy} |"
        )
    return lines


def trace_length(row: dict[str, Any]) -> str:
    return "≤8 steps" if row["steps"] <= 8 else "9-12 steps" if row["steps"] <= 12 else ">12 steps"


def render(
    rows: list[dict[str, Any]], names: list[str], arguments: argparse.Namespace, skipped: int
) -> str:
    cases = len({r["case_id"] for r in rows})
    lines = [
        f"# Root-cause attribution on the {arguments.dataset} corpus, {arguments.split} split",
        "",
        f"{cases} failed runs ({skipped} cases in the split were left out: no recorded fault, an "
        "incomplete pipeline, or a correct final answer). Agent and step are exact matches; ±1 and "
        "±2 count a step within that many calls of the labelled one; top-3 counts the labelled "
        "step among the first three a method ranks (one for methods that rank nothing); 'answered' "
        "is how often a method named a step at all; calls, tokens and seconds are per case.",
        "",
        "## All cases",
        "",
        *summary_table(rows, names),
        "",
        f"## Differences from {arguments.reference}",
        "",
        *paired(rows, names, arguments.reference),
    ]
    for title, key_of in (
        ("By fault kind", lambda r: r["kind"]),
        ("By node of the fault", lambda r: r["node"]),
        ("By model that ran the pipeline", lambda r: r["model"]),
        ("By length of the trace", trace_length),
    ):
        lines += ["", f"## {title} (step correct / cases)", ""]
        lines += breakdown(rows, names, key_of)
    repeated = stability(rows, names)
    if len(repeated) > 2:
        lines += ["", "## Stability across repeats", "", *repeated]
    lines += [
        "",
        f"Model calls made: {USAGE.calls} ({USAGE.cached} answered from the cache); new tokens "
        f"sent {USAGE.prompt:,} and received {USAGE.completion:,}; {USAGE.truncated} reply(ies) "
        "ran out of the token budget and were asked again with a larger one.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=sorted(ROOTS), default="planted")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--judges", default="glm-5p3-flash,deepseek-v4p1-flash")
    parser.add_argument("--stepwise", default="glm-5p3-flash")
    parser.add_argument("--sdk-judge", default="glm-5p3-flash")
    parser.add_argument("--reference", default="sdk-structural")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--only", default="")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--repeat-methods", default="falat,ww-all")
    parser.add_argument("--token-cap", type=int, default=0)
    arguments = parser.parse_args(argv)
    root = arguments.root or ROOTS[arguments.dataset]
    key = api_key()
    labels = [
        label
        for label in (load_label(path.parent) for path in sorted(root.glob("*/label.json")))
        if label.get("split") == arguments.split
        and label.get("kind") in {"read", "write", "natural"}
        and (label["kind"] == "natural") == (arguments.dataset == "natural")
    ]
    chosen = [label for label in labels if eligible(label, arguments.dataset)]
    names = methods(arguments)
    repeating = set(arguments.repeat_methods.split(","))
    jobs = []
    for label in chosen:
        for name in names:
            count = arguments.repeats if is_llm(name) and name.split(":")[0] in repeating else 1
            jobs += [(name, label, repeat) for repeat in range(count)]
    rows: list[dict[str, Any]] = []
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=arguments.workers) as pool:
        futures = [
            pool.submit(
                score_one,
                name,
                root / label["case_id"],
                label,
                key,
                repeat,
                arguments.token_cap,
            )
            for name, label, repeat in jobs
        ]
        for count, future in enumerate(futures, start=1):
            rows.append(future.result())
            if count % 25 == 0:
                print(f"{count}/{len(jobs)}", flush=True)
    arguments.out.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    text = render(rows, names, arguments, len(labels) - len(chosen))
    report = arguments.report or Path(
        f"reports/corpus_eval_{arguments.dataset}_{arguments.split}.md"
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
