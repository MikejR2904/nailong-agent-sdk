"""Solver scale check and latency micro-benchmark (descriptive; no oracle involved).

``python -m dfl_bench.perf scale|latency [--out DIR]``. Run ``latency`` on an idle machine.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import sys
import time
from fractions import Fraction
from pathlib import Path

from nailong_agent_sdk.memory.learned_retention import (
    FixedUtilityPredictor,
    LearnedUtilityPredictor,
)
from nailong_agent_sdk.memory.utility_features import FEATURE_NAMES
from nailong_agent_sdk.memory.utility_models import LinearRankingModel, MlpRankingModel

from .instances import generate_instance, replay
from .selection import (
    LEARNED,
    PASK,
    STATIC,
    is_usable,
    perturbed_values,
    retention_context,
    select_via_store,
    static_utilities,
)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def _usable_instances(size: int, count: int, seed: int, regime: str = "aligned"):
    found = []
    index = 0
    while len(found) < count and index < count * 20:
        explore = max(2, int(size * 0.6))
        instance = generate_instance(
            f"S{size}",
            regime,
            index,
            seed,
            n_explore=explore,
            n_action=max(1, size - explore),
            max_dependencies=3,
        )
        index += 1
        if is_usable(instance):
            found.append(instance)
    return found


def run_scale(
    sizes: tuple[int, ...] = (12, 24, 48, 96, 160), per_size: int = 8, seed: int = 7
) -> dict:
    """Static and learned PCKP on growing general DAGs, reporting the solver's own certificate."""

    report: dict = {"seed": seed, "per_size": per_size, "sizes": {}}
    for size in sizes:
        rows = []
        for instance in _usable_instances(size, per_size, seed):
            rng = random.Random(f"{seed}/{instance.instance_id}")
            values = perturbed_values(static_utilities(instance), rng, 0.5)
            for name, policy, predictor in (
                ("static-pckp", STATIC, None),
                (
                    "learned-pckp",
                    LEARNED,
                    FixedUtilityPredictor(values, predictor_id="scale-probe", version="1"),
                ),
            ):
                selection, _, dossier = select_via_store(instance, policy, predictor)
                solver = dossier["solver"]
                gap = solver["optimality_gap"]
                rows.append(
                    {
                        "method": name,
                        "episodes": instance.size,
                        "status": solver["status"],
                        "solver": solver["solver"],
                        "branch_nodes": solver["branch_nodes"],
                        "gap_over_utility": (
                            float(Fraction(gap)) / max(1, solver["utility"])
                            if solver["status"] == "best-effort"
                            else 0.0
                        ),
                        "latency_ms": selection.latency_ms,
                    }
                )
        per_method: dict = {}
        for name in ("static-pckp", "learned-pckp"):
            chosen = [r for r in rows if r["method"] == name]
            if not chosen:
                continue
            per_method[name] = {
                "instances": len(chosen),
                "status_counts": {
                    s: sum(1 for r in chosen if r["status"] == s)
                    for s in sorted({r["status"] for r in chosen})
                },
                "solvers": sorted({r["solver"] for r in chosen}),
                "max_branch_nodes": max(r["branch_nodes"] for r in chosen),
                "median_latency_ms": statistics.median(r["latency_ms"] for r in chosen),
                "max_latency_ms": max(r["latency_ms"] for r in chosen),
                "max_relative_gap": max(r["gap_over_utility"] for r in chosen),
            }
        report["sizes"][str(size)] = per_method
    return report


def _random_predictors(seed: int) -> dict[str, LearnedUtilityPredictor]:
    rng = random.Random(seed)
    linear = LinearRankingModel([rng.uniform(0.0, 1.0) for _ in FEATURE_NAMES])
    mlp = MlpRankingModel.initialize(len(FEATURE_NAMES), 16, seed)
    return {
        "linear-dfl": LearnedUtilityPredictor(
            linear, predictor_id="bench-linear", version="1", artifact_digest="0" * 64
        ),
        "mlp-dfl": LearnedUtilityPredictor(
            mlp, predictor_id="bench-mlp", version="1", artifact_digest="1" * 64
        ),
    }


def run_latency(
    size: int = 12, instances: int = 6, repeats: int = 30, warmup: int = 5, seed: int = 11
) -> dict:
    """Median and spread of ``compact()`` wall time; store construction is excluded."""

    predictors = _random_predictors(seed)
    cases = [
        ("static-pckp", STATIC, None),
        ("greedy-pask", PASK, None),
        ("linear-dfl", LEARNED, predictors["linear-dfl"]),
        ("mlp-dfl", LEARNED, predictors["mlp-dfl"]),
    ]
    workload = _usable_instances(size, instances, seed)
    report: dict = {"size": size, "instances": len(workload), "repeats": repeats, "methods": {}}
    for name, policy, predictor in cases:
        samples: list[float] = []
        for instance in workload:
            for repeat in range(warmup + repeats):
                built = replay(instance, utility_predictor=predictor)
                started = time.perf_counter_ns()
                built.store.compact(
                    instance.budget,
                    protected_episode_ids=built.protected,
                    relevance_query=instance.query,
                    policy=policy,
                )
                elapsed = (time.perf_counter_ns() - started) / 1e6
                if repeat >= warmup:
                    samples.append(elapsed)
        report["methods"][name] = {
            "median_ms": statistics.median(samples),
            "p10_ms": _percentile(samples, 0.10),
            "p90_ms": _percentile(samples, 0.90),
            "samples": len(samples),
        }
    predict_only: dict[str, dict] = {}
    for name, predictor in predictors.items():
        samples = []
        for instance in workload:
            built = replay(instance)
            context = retention_context(built, instance)
            for repeat in range(warmup + repeats):
                started = time.perf_counter_ns()
                predictor.predict(context)
                elapsed = (time.perf_counter_ns() - started) / 1e6
                if repeat >= warmup:
                    samples.append(elapsed)
        predict_only[name] = {
            "median_ms": statistics.median(samples),
            "p10_ms": _percentile(samples, 0.10),
            "p90_ms": _percentile(samples, 0.90),
        }
    report["predictor_only"] = predict_only
    return report


def render_scale(report: dict) -> str:
    lines = [
        "### Solver scale check (descriptive)",
        "",
        "General DAGs (actions depend on 1-3 exploratory episodes), budget 45-65% of total tokens, "
        "default solver limits (`exact_max_branch_nodes = 50000`). Learned coefficients here are "
        "perturbed static utilities, because the point is the solver, not the model.",
        "",
        "| Episodes | Method | Status counts | Solver | Max branch nodes | Median ms | Max ms | "
        "Max relative gap |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for size, methods in report["sizes"].items():
        for name, row in methods.items():
            lines.append(
                f"| ~{size} | {name} | {row['status_counts']} | {', '.join(row['solvers'])} | "
                f"{row['max_branch_nodes']} | {row['median_latency_ms']:.1f} | "
                f"{row['max_latency_ms']:.1f} | {row['max_relative_gap']:.4f} |"
            )
    return "\n".join(lines) + "\n"


def render_latency(report: dict) -> str:
    lines = [
        "### Latency micro-benchmark (descriptive)",
        "",
        f"`compact()` wall time on {report['instances']} instances of about {report['size']} "
        f"episodes, {report['repeats']} timed repeats each after warm-up; store construction is "
        "excluded. Model weights are random: only cost is measured, not quality.",
        "",
        "| Method | Median ms | p10 | p90 |",
        "|---|---|---|---|",
    ]
    for name, row in report["methods"].items():
        lines.append(
            f"| {name} | {row['median_ms']:.2f} | {row['p10_ms']:.2f} | {row['p90_ms']:.2f} |"
        )
    lines += [
        "",
        "Predictor alone (feature extraction plus scoring):",
        "",
        "| Model | Median ms | p10 | p90 |",
        "|---|---|---|---|",
    ]
    for name, row in report["predictor_only"].items():
        lines.append(
            f"| {name} | {row['median_ms']:.2f} | {row['p10_ms']:.2f} | {row['p90_ms']:.2f} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dfl_bench.perf")
    parser.add_argument("mode", choices=["scale", "latency"])
    parser.add_argument("--out", type=Path, default=Path("results/perf"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    report = run_scale() if args.mode == "scale" else run_latency()
    report["environment"] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "wall_seconds": time.perf_counter() - started,
        "command": " ".join(sys.argv),
    }
    text = render_scale(report) if args.mode == "scale" else render_latency(report)
    (args.out / f"{args.mode}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (args.out / f"{args.mode}.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
