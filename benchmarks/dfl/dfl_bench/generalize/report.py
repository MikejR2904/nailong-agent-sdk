"""Aggregation of fold results into tables with paired, template-clustered intervals."""

from __future__ import annotations

import math
from collections import defaultdict

from ..stats import cluster_bootstrap, holm_adjust, mean

BANNER = (
    "SYNTHETIC ORACLE EVIDENCE ONLY (evidence_kind=synthetic). Outcomes are Bernoulli draws from a "
    "closed-form quality model; nothing here measures a real agent."
)
REFERENCES = ("static-pckp", "tuned-static-pckp", "linear-dfl", "regression-pckp")
CANDIDATES = (
    "regret-pairwise-v1",
    "listwise-v1",
    "dro-pairwise-v1",
    "regression-v1",
    "regression-v2",
    "listwise-v2",
    "calibrated-pckp",
    "combined-validation",
    "shrunk-regression-v1",
    "shrunk-listwise-v1",
    "shrunk-regret-pairwise-v1",
    "shrunk-dro-pairwise-v1",
    "shrunk-regression-v2",
    "shrunk-listwise-v2",
    "shrunk-selected",
)


def _rows(results: list[dict], method: str, fold_prefix: str | None = None):
    for result in results:
        if fold_prefix is not None and not result["fold"].startswith(fold_prefix):
            continue
        for row in result["per_method"].get(method, []):
            yield result, row


def fold_means(results: list[dict], method: str, fold_prefix: str) -> dict[tuple[int, str], float]:
    grouped: dict[tuple[int, str], list[float]] = defaultdict(list)
    for result, row in _rows(results, method, fold_prefix):
        grouped[(result["seed"], result["fold"])].append(row["quality"])
    return {key: mean(values) for key, values in grouped.items()}


def paired_clusters(
    results: list[dict], candidate: str, reference: str, fold_prefix: str, key: str = "quality"
) -> dict[str, list[float]]:
    reference_rows = {
        (r["seed"], r["fold"], row["instance_id"]): row[key]
        for r, row in _rows(results, reference, fold_prefix)
    }
    clusters: dict[str, list[float]] = defaultdict(list)
    for r, row in _rows(results, candidate, fold_prefix):
        other = reference_rows.get((r["seed"], r["fold"], row["instance_id"]))
        if other is not None:
            clusters[f"{r['seed']}/{row['template']}"].append(row[key] - other)
    return clusters


def comparison_table(results: list[dict], fold_prefix: str, seed: int = 0) -> list[dict]:
    rows = []
    pvalues: dict[str, float] = {}
    for candidate in CANDIDATES:
        for reference in REFERENCES:
            clusters = paired_clusters(results, candidate, reference, fold_prefix)
            if not clusters:
                continue
            summary = cluster_bootstrap(clusters, seed=seed)
            name = f"{candidate} vs {reference}"
            pvalues[name] = summary["p"]
            rows.append({"comparison": name, **summary})
    adjusted = holm_adjust(pvalues)
    for row in rows:
        row["holm_p"] = adjusted[row["comparison"]]
    return rows


def method_table(results: list[dict], fold_prefix: str) -> list[dict]:
    names = sorted({m for r in results for m in r["per_method"]})
    table = []
    for name in names:
        per_fold = fold_means(results, name, fold_prefix)
        if not per_fold:
            continue
        rows = [row for _, row in _rows(results, name, fold_prefix)]
        by_seed: dict[int, list[float]] = defaultdict(list)
        for (seed, _), value in per_fold.items():
            by_seed[seed].append(value)
        statuses: dict[str, int] = defaultdict(int)
        for row in rows:
            statuses[str(row["solver_status"])] += 1
        gaps = [
            float(row["optimality_gap"]) for row in rows if row["optimality_gap"] not in (None, "0")
        ]
        modes = [row.get("calibration_mode") for row in rows if row.get("calibration_mode")]
        table.append(
            {
                "method": name,
                "mean_quality": mean([row["quality"] for row in rows]),
                "mean_regret": mean([row["regret"] for row in rows]),
                "worst_fold": min(per_fold.values()),
                "mean_worst_fold_per_seed": mean([min(v) for v in by_seed.values()]),
                "token_fraction": mean([row["token_fraction"] for row in rows]),
                "failure_recall": mean(
                    [
                        row["failure_critical_recall"]
                        for row in rows
                        if not math.isnan(row["failure_critical_recall"])
                    ]
                ),
                "evidence_coverage": mean([row["evidence_coverage"] for row in rows]),
                "latency_ms": mean([row["latency_ms"] for row in rows]),
                "solver_status": dict(statuses),
                "max_gap": max(gaps) if gaps else 0.0,
                "fallback_rate": (
                    sum(1 for m in modes if m == "static-fallback") / len(modes) if modes else None
                ),
                "n": len(rows),
            }
        )
    return sorted(table, key=lambda r: -r["mean_worst_fold_per_seed"])


def per_fold_table(results: list[dict], fold_prefix: str) -> dict[str, dict[str, float]]:
    table: dict[str, dict[str, float]] = defaultdict(dict)
    names = sorted({m for r in results for m in r["per_method"]})
    for name in names:
        grouped: dict[str, list[float]] = defaultdict(list)
        for (_, fold), value in fold_means(results, name, fold_prefix).items():
            grouped[fold].append(value)
        for fold, values in grouped.items():
            table[name][fold] = mean(values)
    return table


def training_costs(results: list[dict]) -> dict[str, float]:
    totals: dict[str, list[float]] = defaultdict(list)
    for r in results:
        for name, info in r["training"].items():
            totals[name].append(info["seconds"])
    return {name: mean(values) for name, values in sorted(totals.items())}


def degenerate_rate(results: list[dict]) -> dict[str, float]:
    counts: dict[str, list[float]] = defaultdict(list)
    for r in results:
        for name, info in r["training"].items():
            if "degenerate" in info:
                counts[name].append(1.0 if info["degenerate"] else 0.0)
    return {name: mean(values) for name, values in sorted(counts.items())}


def pair_accuracy_table(results: list[dict]) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for r in results:
        for name, value in r["pair_accuracy"].items():
            if name != "n_test_pairs" and isinstance(value, float) and not math.isnan(value):
                values[name].append(value)
    return {name: mean(v) for name, v in sorted(values.items())}


def _f(value, digits=3):
    return (
        "n/a"
        if value is None or (isinstance(value, float) and math.isnan(value))
        else f"{value:.{digits}f}"
    )


def render(results: list[dict], protocol_hash: str, label: str) -> str:
    lines = [f"# Generalization results: {label}", "", f"> {BANNER}", ""]
    seeds = sorted({r["seed"] for r in results})
    lines += [
        f"Protocol file SHA-256: `{protocol_hash}`. Seeds: {seeds}. Fold results: {len(results)}.",
        "",
    ]
    for title, prefix in (
        ("Leave-one-family-out (primary)", "lofo:"),
        ("Held-out long histories", "holdout-long"),
        ("Unseen low budget", "budget-low"),
        ("Unseen high budget", "budget-high"),
        ("Unseen templates, same families", "iid-template"),
    ):
        table = method_table(results, prefix)
        if not table:
            continue
        lines += [
            f"## {title}",
            "",
            "| method | mean quality | mean regret | worst fold | worst fold/seed | token use | "
            "failure recall | solver status | fallback | n |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for row in table:
            lines.append(
                f"| {row['method']} | {_f(row['mean_quality'])} | {_f(row['mean_regret'])} | "
                f"{_f(row['worst_fold'])} | {_f(row['mean_worst_fold_per_seed'])} | "
                f"{_f(row['token_fraction'], 2)} | {_f(row['failure_recall'], 2)} | "
                f"{row['solver_status']} (max gap {_f(row['max_gap'], 4)}) | "
                f"{_f(row['fallback_rate'], 2)} | {row['n']} |"
            )
        lines.append("")
        if prefix in ("lofo:", "budget-low", "budget-high", "holdout-long"):
            lines += [
                f"Paired comparisons, {title.lower()} (template-clustered bootstrap, Holm over "
                f"{len(CANDIDATES) * len(REFERENCES)} comparisons):",
                "",
                "| comparison | mean diff | 95% CI | p | Holm p | clusters |",
                "|---|---|---|---|---|---|",
            ]
            for row in comparison_table(results, prefix):
                lines.append(
                    f"| {row['comparison']} | {row['mean']:+.3f} | [{row['ci_low']:+.3f}, "
                    f"{row['ci_high']:+.3f}] | {row['p']:.4f} | {row['holm_p']:.4f} | "
                    f"{row['clusters']} |"
                )
            lines.append("")
        folds = per_fold_table(results, prefix)
        if prefix == "lofo:" and folds:
            fold_names = sorted({f for v in folds.values() for f in v})
            lines += [
                "Held-out family quality by method:",
                "",
                "| method | " + " | ".join(f.split(":", 1)[1] for f in fold_names) + " |",
                "|---|" + "---|" * len(fold_names),
            ]
            for name, row in folds.items():
                lines.append(f"| {name} | " + " | ".join(_f(row.get(f)) for f in fold_names) + " |")
            lines.append("")
    lines += ["## Pairwise ranking accuracy on held-out candidate pools", ""]
    for name, value in pair_accuracy_table(results).items():
        lines.append(f"- {name}: {_f(value)}")
    calibration = defaultdict(list)
    for r in results:
        for name, value in r["calibration_error"].items():
            calibration[name].append(value)
    lines += [
        "",
        "## Set-utility calibration error (mean |U(S) - outcome|, regression methods)",
        "",
    ]
    for name, values in sorted(calibration.items()):
        lines.append(f"- {name}: {_f(mean(values))}")
    lines += ["", "## Degenerate (all-zero) trained models, share of folds", ""]
    for name, value in degenerate_rate(results).items():
        lines.append(f"- {name}: {_f(value, 2)}")
    lines += ["", "## Mean training seconds per fold (model-call cost: none, synthetic)", ""]
    for name, value in training_costs(results).items():
        lines.append(f"- {name}: {_f(value, 1)}")
    return "\n".join(lines) + "\n"
