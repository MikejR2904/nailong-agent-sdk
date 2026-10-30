"""Pooled statistics and Markdown tables for synthetic-ground-truth replicates."""

from __future__ import annotations

import math
from collections import Counter

from .stats import (
    bootstrap_ci,
    cluster_bootstrap,
    holm_adjust,
    mean,
    paired_summary,
    template_of,
)

MEASURED_BANNER = (
    "MEASURED DOWNSTREAM PERFORMANCE. Every outcome is the score of a real model call, on the "
    "context the SDK assembled for the retention set under test, checked by a deterministic "
    "verifier against ground truth. The tasks are generated tool-call histories, not production "
    "traffic; see the report for what that does and does not support."
)


def banner_for(evidence_kind: str) -> str:
    return MEASURED_BANNER if evidence_kind == "measured" else BANNER


BANNER = (
    "SYNTHETIC GROUND TRUTH. These numbers come from a closed-form oracle with known structure. "
    "They check that the solver and the learning pipeline behave as the mathematics says; they are "
    "not downstream agent performance and must not be quoted as such."
)


def pooled(replicates: list[dict], method: str, key: str) -> list[float]:
    return [row[key] for rep in replicates for row in rep["per_method"][method]]


def cluster_groups(
    replicates: list[dict], method: str, reference: str, key: str
) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = {}
    for rep in replicates:
        base = {row["instance_id"]: row[key] for row in rep["per_method"][reference]}
        for row in rep["per_method"][method]:
            groups.setdefault(template_of(row["instance_id"]), []).append(
                row[key] - base[row["instance_id"]]
            )
    return groups


LEARNED_PAIRS = (
    ("linear-dfl", "mlp-dfl"),
    ("linear-dfl", "regression-pckp"),
    ("linear-dfl", "topk-repair"),
    ("mlp-dfl", "regression-pckp"),
)


def _finite(values: list[float]) -> list[float]:
    return [v for v in values if not (isinstance(v, float) and math.isnan(v))]


def _ci_text(values: list[float], seed: int = 0, digits: int = 3) -> str:
    values = _finite(values)
    if not values:
        return "n/a"
    low, high = bootstrap_ci(values, seed=seed)
    return f"{mean(values):.{digits}f} [{low:.{digits}f}, {high:.{digits}f}]"


def _effect(values: list[float], full: list[float]) -> dict:
    diffs = [v - f for v, f in zip(values, full, strict=True)]
    low, high = bootstrap_ci(diffs, seed=0)
    return {"test_q": mean(values), "diff_vs_full": mean(diffs), "ci_low": low, "ci_high": high}


def summarize(replicates: list[dict]) -> dict:
    methods = list(replicates[0]["per_method"])
    summary: dict = {"methods": {}, "paired_vs_static": {}}
    for method in methods:
        rows = [r for rep in replicates for r in rep["per_method"][method]]
        statuses = Counter(r["solver_status"] or "n/a" for r in rows)
        gaps = [
            r["optimality_gap"] for r in rows if r["optimality_gap"] not in (None, "unavailable")
        ]
        summary["methods"][method] = {
            "n_instances": len(rows),
            "quality": _ci_text([r["quality"] for r in rows]),
            "success_rate": _ci_text([r["success_rate"] for r in rows]),
            "regret": _ci_text([r["regret"] for r in rows]),
            "failure_critical_recall": _ci_text([r["failure_critical_recall"] for r in rows]),
            "evidence_coverage": _ci_text([r["evidence_coverage"] for r in rows]),
            "token_fraction": _ci_text([r["token_fraction"] for r in rows]),
            "solver_status": dict(statuses),
            "nonzero_gaps": sum(1 for g in gaps if g != "0"),
            "learned_status": dict(Counter(r["learned_status"] or "n/a" for r in rows)),
            "latency_ms": mean([r["latency_ms"] for r in rows]),
        }
    base = pooled(replicates, "static-pckp", "quality")
    raw_p: dict[str, float] = {}
    for method in methods:
        if method == "static-pckp":
            continue
        result = paired_summary(pooled(replicates, method, "quality"), base)
        summary["paired_vs_static"][method] = result
        raw_p[method] = result["sign_test_p"]
    for method, adjusted in holm_adjust(raw_p).items():
        summary["paired_vs_static"][method]["holm_p"] = adjusted
    cluster_p: dict[str, float] = {}
    summary["paired_regret_vs_static"] = {}
    has_regret = any(not math.isnan(v) for v in pooled(replicates, "static-pckp", "regret"))
    for method in summary["paired_vs_static"]:
        stat = cluster_bootstrap(cluster_groups(replicates, method, "static-pckp", "quality"))
        summary["paired_vs_static"][method].update(
            {
                "cluster_ci_low": stat["ci_low"],
                "cluster_ci_high": stat["ci_high"],
                "cluster_p": stat["p"],
            }
        )
        cluster_p[method] = stat["p"]
        if has_regret:
            summary["paired_regret_vs_static"][method] = cluster_bootstrap(
                cluster_groups(replicates, method, "static-pckp", "regret")
            )
    for method, adjusted in holm_adjust(cluster_p).items():
        summary["paired_vs_static"][method]["cluster_holm_p"] = adjusted
    summary["paired_among_learned"] = {}
    learned_p: dict[str, float] = {}
    for first, second in LEARNED_PAIRS:
        if first in methods and second in methods:
            stat = cluster_bootstrap(cluster_groups(replicates, first, second, "quality"))
            summary["paired_among_learned"][f"{first} - {second}"] = stat
            learned_p[f"{first} - {second}"] = stat["p"]
    for name, adjusted in holm_adjust(learned_p).items():
        summary["paired_among_learned"][name]["holm_p"] = adjusted
    summary["pair_accuracy"] = {
        name: mean(_finite([rep["pair_accuracy"].get(name, float("nan")) for rep in replicates]))
        for name in replicates[0]["pair_accuracy"]
        if name != "n_test_pairs"
    }
    summary["test_pairs"] = sum(rep["pair_accuracy"]["n_test_pairs"] for rep in replicates)
    summary["training_seconds"] = {
        name: mean([rep["training"][name]["seconds"] for rep in replicates])
        for name in replicates[0]["training"]
    }
    summary["total_seconds"] = {
        key: mean([rep["timings"][key] for rep in replicates]) for key in replicates[0]["timings"]
    }
    summary["training_detail"] = {
        name: mean(
            _finite(
                [
                    rep["training"][name].get("validation_pair_accuracy", float("nan"))
                    for rep in replicates
                ]
            )
        )
        for name in replicates[0]["training"]
    }
    if replicates[0]["ablations"]:
        ab = [rep["ablations"] for rep in replicates]
        full = [a["full_linear_test_quality"] for a in ab]
        summary["ablations"] = {
            "full_linear": mean(full),
            "replicates": len(ab),
            "feature_groups": {
                g: _effect([a["feature_groups"][g] for a in ab], full)
                for g in ab[0]["feature_groups"]
            },
            "training_size": {
                f: _effect([a["training_size"][f]["test_quality"] for a in ab], full)
                for f in ab[0]["training_size"]
                if all(a["training_size"][f] is not None for a in ab)
            },
        }
    return summary


def render(regime: str, replicates: list[dict], summary: dict) -> str:
    sizes = replicates[0]["sizes"]
    lines = [
        f"### Regime `{regime}`",
        "",
        f"> {banner_for(replicates[0]['evidence_kind'])}",
        "",
        f"{len(replicates)} replicates; per replicate about {sizes['train_instances']} train / "
        f"{sizes['val_instances']} validation / {sizes['test_instances']} test instances "
        f"(template-disjoint). Intervals are 95% bootstrap over test instances pooled across "
        "replicates.",
        "",
        "| Method | Q (expected success) | Success rate | Regret vs exhaustive optimum | "
        "Failure-critical recall | Evidence coverage | Tokens / budget |",
        "|---|---|---|---|---|---|---|",
    ]
    for method, row in summary["methods"].items():
        lines.append(
            f"| {method} | {row['quality']} | {row['success_rate']} | {row['regret']} | "
            f"{row['failure_critical_recall']} | {row['evidence_coverage']} | "
            f"{row['token_fraction']} |"
        )
    lines += [
        "",
        "Solver certificates, learned-policy status and latency (mean ms per selection):",
        "",
        "| Method | Solver status | Non-zero gaps | Learned status | Latency ms |",
        "|---|---|---|---|---|",
    ]
    for method, row in summary["methods"].items():
        lines.append(
            f"| {method} | {row['solver_status']} | {row['nonzero_gaps']} | "
            f"{row['learned_status']} | {row['latency_ms']:.2f} |"
        )
    lines += [
        "",
        "Paired difference in Q against `static-pckp` (same test instances):",
        "",
        "| Method | Mean difference [instance 95% CI] | Template-clustered 95% CI | Clustered p "
        "(Holm) | Wins / losses / ties | Sign-test p (Holm) |",
        "|---|---|---|---|---|---|",
    ]
    for method, r in summary["paired_vs_static"].items():
        lines.append(
            f"| {method} | {r['mean_difference']:+.4f} [{r['ci_low']:+.4f}, {r['ci_high']:+.4f}] | "
            f"[{r['cluster_ci_low']:+.4f}, {r['cluster_ci_high']:+.4f}] | "
            f"{r['cluster_p']:.4f} ({r['cluster_holm_p']:.4f}) | "
            f"{r['wins']} / {r['losses']} / {r['ties']} | "
            f"{r['sign_test_p']:.4f} ({r['holm_p']:.4f}) |"
        )
    if summary["paired_regret_vs_static"]:
        lines += [
            "",
            "Paired difference in regret against `static-pckp` (negative is better, clustered):",
            "",
            "| Method | Mean regret difference [95% CI] |",
            "|---|---|",
        ]
        for method, r in summary["paired_regret_vs_static"].items():
            lines.append(
                f"| {method} | {r['mean']:+.4f} [{r['ci_low']:+.4f}, {r['ci_high']:+.4f}] |"
            )
    if summary["paired_among_learned"]:
        lines += [
            "",
            "Paired difference in Q between learned methods (template-clustered, Holm over these):",
            "",
            "| Comparison | Mean difference [95% CI] | Clustered p | Holm p |",
            "|---|---|---|---|",
        ]
        for name, r in summary["paired_among_learned"].items():
            lines.append(
                f"| {name} | {r['mean']:+.4f} [{r['ci_low']:+.4f}, {r['ci_high']:+.4f}] | "
                f"{r['p']:.4f} | {r['holm_p']:.4f} |"
            )
    lines += [
        "",
        f"Held-out pairwise preference accuracy ({summary['test_pairs']} significance-filtered "
        "pairs pooled; 0.5 is chance):",
        "",
        "| Scorer | Accuracy |",
        "|---|---|",
    ]
    for name, value in summary["pair_accuracy"].items():
        lines.append(f"| {name} | {value:.3f} |")
    lines += [
        "",
        "Training cost, mean seconds for the final-round fit (includes the hyperparameter grid):",
        "",
        "| Model | Seconds | Validation pair accuracy |",
        "|---|---|---|",
    ]
    for name, seconds in summary["training_seconds"].items():
        accuracy = summary["training_detail"][name]
        lines.append(f"| {name} | {seconds:.2f} | {accuracy:.3f} |")
    totals = summary["total_seconds"]
    lines += [
        "",
        f"Mean per replicate over all training rounds: {totals['training_s']:.2f} s of model "
        f"fitting and {totals['candidates_s']:.2f} s of candidate generation and outcome "
        "evaluation.",
    ]
    if "ablations" in summary:
        ab = summary["ablations"]
        lines += [
            "",
            f"Ablations (linear Ranking-DFL; mean test Q, full model {ab['full_linear']:.4f}; "
            f"difference from the full model with a 95% bootstrap interval over "
            f"{ab['replicates']} replicates):",
            "",
            "| Removed feature group | Test Q | Difference from full |",
            "|---|---|---|",
        ]
        for group, v in ab["feature_groups"].items():
            lines.append(
                f"| {group} | {v['test_q']:.4f} | {v['diff_vs_full']:+.4f} "
                f"[{v['ci_low']:+.4f}, {v['ci_high']:+.4f}] |"
            )
        lines += ["", "| Training fraction | Test Q | Difference from full |", "|---|---|---|"]
        for fraction, v in ab["training_size"].items():
            lines.append(
                f"| {fraction} | {v['test_q']:.4f} | {v['diff_vs_full']:+.4f} "
                f"[{v['ci_low']:+.4f}, {v['ci_high']:+.4f}] |"
            )
    return "\n".join(lines) + "\n"


PRIMARY_CELLS = (
    ("shifted", "linear-dfl"),
    ("shifted", "mlp-dfl"),
    ("aligned", "linear-dfl"),
    ("aligned", "mlp-dfl"),
)


def primary_hypotheses(
    regimes: dict[str, dict], primary_cells: tuple[tuple[str, str], ...] = PRIMARY_CELLS
) -> dict:
    """Evaluate the pre-registered primary family: clustered p, Holm over the available cells."""

    cells = {}
    for regime, method in primary_cells:
        if regime in regimes:
            stats = regimes[regime]["summary"]["paired_vs_static"].get(method)
            if stats is not None:
                cells[f"{regime}/{method}"] = stats
    adjusted = holm_adjust({name: stat["cluster_p"] for name, stat in cells.items()})
    return {
        name: {
            "mean_difference": stat["mean_difference"],
            "cluster_ci": [stat["cluster_ci_low"], stat["cluster_ci_high"]],
            "cluster_p": stat["cluster_p"],
            "holm_p": adjusted[name],
            "supported": adjusted[name] < 0.05 and stat["mean_difference"] > 0,
        }
        for name, stat in cells.items()
    }


def render_primary(primary: dict) -> str:
    lines = [
        "### Pre-registered primary hypotheses",
        "",
        "| Cell | Mean difference in Q | Clustered 95% CI | Clustered p | Holm p | Supported |",
        "|---|---|---|---|---|---|",
    ]
    for name, r in primary.items():
        lines.append(
            f"| {name} | {r['mean_difference']:+.4f} | [{r['cluster_ci'][0]:+.4f}, "
            f"{r['cluster_ci'][1]:+.4f}] | {r['cluster_p']:.4f} | {r['holm_p']:.4f} | "
            f"{'yes' if r['supported'] else 'no'} |"
        )
    return "\n".join(lines) + "\n"
