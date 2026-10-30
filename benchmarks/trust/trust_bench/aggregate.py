# Copyright (c) 2026 David Michael Indraputra

"""Turn per-run results into the endpoints of the pre-registration, with intervals."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from . import stats
from .splits import TEST_ONLY_OPERATORS

KINDS = ("model-call", "tool-call", "tool-result", "write")
TRUTH_CLASSES = ("F1", "F2", "F3")


def _clean(results: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [r for r in results if r["fault"] is None and r["status"] == "completed"]


def traceability(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    runs = _clean(results)
    out: dict[str, Any] = {"runs": len(runs)}
    if not runs:
        return out
    complete = sum(1 for r in runs if r["trace"]["calls_complete"])
    out["calls_complete"] = stats.proportion(complete, len(runs))
    for kind in KINDS:
        total = sum(r["trace"]["counts"][kind]["total"] for r in runs)
        present = sum(r["trace"]["counts"][kind]["present"] for r in runs)
        phantom = sum(r["trace"]["counts"][kind]["phantom"] for r in runs)
        comparable = sum(r["trace"]["counts"][kind]["comparable"] for r in runs)
        agree = sum(r["trace"]["counts"][kind]["agree"] for r in runs)
        recoverable = sum(r["trace"]["counts"][kind]["recoverable"] for r in runs)
        out[kind] = {
            "completeness": stats.proportion(present, total),
            "phantom": phantom,
            "agreement": stats.proportion(agree, comparable),
            "recoverability": stats.proportion(recoverable, present),
        }
    artifacts = [(r["case_id"], a) for r in runs for a in r["trace"]["artifacts"]]
    n = len(artifacts)
    correct = sum(1 for _, a in artifacts if a["outcome"] == "correct")
    wrong = sum(1 for _, a in artifacts if a["outcome"] == "wrong")
    abstain = sum(1 for _, a in artifacts if a["outcome"] == "abstain")
    per_case: dict[str, list[float]] = defaultdict(list)
    for case_id, a in artifacts:
        per_case[case_id].append(1.0 if a["outcome"] == "correct" else 0.0)
    cluster = {case: sum(v) / len(v) for case, v in per_case.items()}
    estimate, low, high = stats.cluster_bootstrap(cluster) if cluster else (0.0, 0.0, 0.0)
    out["resolution"] = {
        "artifacts": n,
        "darr": stats.proportion(correct, n),
        "darr_cluster": (estimate, low, high),
        "false_resolution": stats.proportion(wrong, n),
        "abstained": stats.proportion(abstain, n),
        "source_precision": stats.mean([a["source_precision"] for _, a in artifacts]) if n else 0.0,
        "source_recall": stats.mean([a["source_recall"] for _, a in artifacts]) if n else 0.0,
    }
    return out


def failure_completeness(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    runs = [r for r in results if r["fault"] is not None and r["status"] != "completed"]
    out: dict[str, Any] = {"runs": len(runs)}
    if not runs:
        return out
    complete = sum(1 for r in runs if r["trace"]["calls_complete"])
    out["calls_complete"] = stats.proportion(complete, len(runs))
    for kind in KINDS:
        counts = [r["trace"]["counts"][kind] for r in runs]
        out[kind] = {
            "completeness": stats.proportion(
                sum(c["present"] for c in counts), sum(c["total"] for c in counts)
            ),
            "phantom": sum(c["phantom"] for c in counts),
        }
    return out


def degradation_table(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    runs = _clean(results)
    table: dict[str, Any] = {}
    for run in runs:
        for condition, resolvers in run["degradation"].items():
            for label, counts in resolvers.items():
                bucket = table.setdefault(condition, {}).setdefault(
                    label, {"correct": 0, "wrong": 0, "abstain": 0}
                )
                for outcome, value in counts.items():
                    bucket[outcome] += value
    summary: dict[str, Any] = {}
    for condition, resolvers in table.items():
        summary[condition] = {}
        for label, counts in resolvers.items():
            total = sum(counts.values())
            summary[condition][label] = {
                "correct": stats.proportion(counts["correct"], total),
                "wrong": stats.proportion(counts["wrong"], total),
                "abstain": stats.proportion(counts["abstain"], total),
            }
    return summary


def _diagnosed(results: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [r for r in results if r["fault"] is not None and r["diagnoses"]]


def diagnosis(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cases = _diagnosed(results)
    methods = sorted({m for r in cases for m in r["diagnoses"]})
    out: dict[str, Any] = {"cases": len(cases), "methods": {}}
    for method in methods:
        rows = [(r, r["diagnoses"][method]) for r in cases if method in r["diagnoses"]]
        by_class: dict[str, list[float]] = defaultdict(list)
        for r, d in rows:
            if d["has_truth"]:
                by_class[r["fault"]["class"]].append(1.0 if d["top1"] else 0.0)
        truth_rows = [(r, d) for r, d in rows if d["has_truth"]]
        none_rows = [(r, d) for r, d in rows if not d["has_truth"]]
        entry: dict[str, Any] = {
            "top1": stats.proportion(sum(1 for _, d in truth_rows if d["top1"]), len(truth_rows)),
            "top3": stats.proportion(sum(1 for _, d in truth_rows if d["top3"]), len(truth_rows)),
            "mrr": stats.mean([d["reciprocal_rank"] for _, d in truth_rows]) if truth_rows else 0.0,
            "abstain_on_truth": stats.proportion(
                sum(1 for _, d in truth_rows if d["abstained"]), len(truth_rows)
            ),
            "unsupported": stats.proportion(
                sum(1 for _, d in none_rows if d["unsupported"]), len(none_rows)
            ),
            "by_class": {
                name: stats.proportion(int(sum(values)), len(values))
                for name, values in sorted(by_class.items())
            },
            "evaluations": stats.mean([d["evaluations"] for _, d in rows]) if rows else 0.0,
        }
        macro = {c: v for c, v in by_class.items() if c in TRUTH_CLASSES}
        if macro:
            entry["macro_top1"] = stats.stratified_macro(macro)
        out["methods"][method] = entry
    return out


def twin_table(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cases = [r for r in results if r.get("twins")]
    methods = sorted({m for r in cases for m in r["twins"]})
    out: dict[str, Any] = {"cases": len(cases), "methods": {}}
    for method in methods:
        rows = [r["twins"][method] for r in cases if method in r["twins"]]
        out["methods"][method] = {
            "unsupported": stats.proportion(sum(1 for x in rows if x["named"]), len(rows)),
            "abstained": stats.proportion(sum(1 for x in rows if not x["named"]), len(rows)),
            "named_truth": stats.proportion(sum(1 for x in rows if x["names_truth"]), len(rows)),
        }
    return out


def paired_top1(results: Sequence[Mapping[str, Any]], first: str, second: str) -> dict[str, Any]:
    both = only_first = only_second = neither = 0
    for r in _diagnosed(results):
        if first not in r["diagnoses"] or second not in r["diagnoses"]:
            continue
        a, b = r["diagnoses"][first], r["diagnoses"][second]
        if not a["has_truth"]:
            continue
        if a["top1"] and b["top1"]:
            both += 1
        elif a["top1"]:
            only_first += 1
        elif b["top1"]:
            only_second += 1
        else:
            neither += 1
    return {
        "both": both,
        "only_first": only_first,
        "only_second": only_second,
        "neither": neither,
        "p_exact": stats.mcnemar_exact(only_first, only_second),
    }


def split_by(results: Iterable[Mapping[str, Any]], key: str) -> dict[str, list[Mapping[str, Any]]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in results:
        groups[str(r[key])].append(r)
    return dict(groups)


STRATA = ("development-like", "test-only operators", "held-out family")


def stratum_of(row: Mapping[str, Any]) -> str:
    if row["family"] == "plan":
        return "held-out family"
    fault = row["fault"]
    if fault is not None and fault["operator"] in TEST_ONLY_OPERATORS:
        return "test-only operators"
    return "development-like"


def stratum_table(results: Sequence[Mapping[str, Any]], method: str) -> dict[str, Any]:
    groups: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(list)
    for row in results:
        entry = row["diagnoses"].get(method) if row["fault"] is not None else None
        if entry is not None and entry["has_truth"]:
            groups[stratum_of(row)].append((row, entry))
    out: dict[str, Any] = {"method": method, "strata": {}}
    for name in STRATA:
        pairs = groups.get(name, [])
        if pairs:
            out["strata"][name] = stats.proportion(
                sum(1 for _, e in pairs if e["top1"]), len(pairs)
            )
    base = out["strata"].get(STRATA[0])
    out["differences"] = {}
    for name in STRATA[1:]:
        other = out["strata"].get(name)
        if base is not None and other is not None:
            out["differences"][name] = stats.newcombe_difference(
                (other.successes, other.trials), (base.successes, base.trials)
            )
    return out


def resolution_strata(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[str]] = defaultdict(list)
    for row in _clean(results):
        groups["held-out family" if row["family"] == "plan" else "development-like"].extend(
            a["outcome"] for a in row["trace"]["artifacts"]
        )
    out: dict[str, Any] = {
        name: stats.proportion(sum(1 for o in outcomes if o == "correct"), len(outcomes))
        for name, outcomes in groups.items()
        if outcomes
    }
    return out


def compare_arms(
    results: Sequence[Mapping[str, Any]], first: str = "sdk", second: str = "langgraph"
) -> dict[str, Any]:
    by_arm = split_by(results, "arm")
    other = {(r["case_id"], (r["fault"] or {}).get("operator")): r for r in by_arm.get(second, [])}
    pairs = []
    for row in by_arm.get(first, []):
        match = other.get((row["case_id"], (row["fault"] or {}).get("operator")))
        if match is not None:
            pairs.append((row, match))

    def ledger_hit(row: Mapping[str, Any]) -> bool | None:
        entry = row["diagnoses"].get("ledger-scan+oracle")
        return None if entry is None or not entry["has_truth"] else bool(entry["top1"])

    extractors = {
        "every model call and tool call recorded (clean runs)": lambda r: (
            bool(r["trace"]["calls_complete"])
            if r["fault"] is None and r["status"] == "completed"
            else None
        ),
        "every artifact resolved (clean runs)": lambda r: (
            all(a["outcome"] == "correct" for a in r["trace"]["artifacts"])
            if r["fault"] is None and r["status"] == "completed"
            else None
        ),
        "generic ledger scan names the decisive step (single-cause faults)": ledger_hit,
    }
    tests: dict[str, Any] = {}
    for name, extract in extractors.items():
        both = only_first = only_second = neither = 0
        for a, b in pairs:
            x, y = extract(a), extract(b)
            if x is None or y is None:
                continue
            if x and y:
                both += 1
            elif x:
                only_first += 1
            elif y:
                only_second += 1
            else:
                neither += 1
        tests[name] = {
            "both": both,
            "only_first": only_first,
            "only_second": only_second,
            "neither": neither,
            "p_exact": stats.mcnemar_exact(only_first, only_second),
        }
    adjusted = stats.holm({name: t["p_exact"] for name, t in tests.items()})
    for name, value in adjusted.items():
        tests[name]["p_holm"] = value
    return {"first": first, "second": second, "pairs": len(pairs), "tests": tests}
