"""Model-free semantic check of three-level retention on the live scenario histories.

For every generated task the retained representation (full episode text plus extractive summaries)
is searched for the verifier's expected values. This is a necessary condition for a correct answer,
not a measure of answer quality: it cannot see distractors or reasoning errors. No model is called.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from nailong_agent_sdk.memory.semantic_compaction import record_corpus
from nailong_agent_sdk.memory.tiered_retention import (
    FidelityCalibration,
    TierPolicy,
    conservative_fidelity,
    document_frequency,
    plan_tiered_retention,
    summarize_episode,
)

from .instances import replay
from .live.experiment import LiveConfig, build_live_world
from .live.scenarios import LiveTask, contains_value, normalize
from .selection import mandatory_closure, retention_context, static_utilities
from .stats import cluster_bootstrap, mean

BANNER = (
    "MODEL-FREE PROXY: fact survival is a necessary condition for answering correctly, not answer "
    "quality. No model was called."
)


def oracle_utilities(task: LiveTask, ids: list[str]) -> dict[str, float]:
    sources = {i for indices in task.fact_sources.values() for i in indices}
    return {e: (1.0 if n in sources else 0.1) for n, e in enumerate(ids)}


def evaluate_task(
    task: LiveTask,
    utilities_name: str,
    policy: TierPolicy,
    calibration: FidelityCalibration | None = None,
) -> dict[str, dict]:
    instance = task.instance
    built = replay(instance)
    context = retention_context(built, instance)
    mandatory = frozenset(mandatory_closure(built))
    if utilities_name == "static":
        utilities = static_utilities(instance)
    else:
        utilities = oracle_utilities(task, built.ids)
    texts = {r.id: record_corpus(r) for r in context.records}
    rows = {}
    variants = [("binary", False, None), ("tiered", True, None)]
    if calibration is not None:
        variants.append(("tiered-calibrated", True, calibration))
    for name, enabled, calib in variants:
        plan = plan_tiered_retention(
            context,
            utilities,
            policy=policy,
            mandatory_ids=mandatory,
            enable_summaries=enabled,
            calibration=calib,
        )
        retained = [texts[e] for e in plan.full_ids] + [s.text for s in plan.summaries.values()]
        corpus = " ".join(retained).casefold()
        present = [contains_value(corpus, normalize(v)) for v in task.expected.values()]
        rows[name] = {
            "fact_survival": sum(present) / len(present),
            "all_facts": float(all(present)),
            "tokens_used": plan.token_cost / instance.budget,
            "represented": len(plan.full_ids) + len(plan.summary_ids),
            "full": len(plan.full_ids),
            "summary": len(plan.summary_ids),
            "expected_fidelity": plan.expected_fidelity,
            "status": plan.solver_status,
            "template": instance.template_id,
            "family": task.family,
            "instance_id": instance.instance_id,
        }
    return rows


def survival_pairs(task: LiveTask, policy: TierPolicy) -> list[tuple[float, float]]:
    """(conservative fidelity, fact survived in the summary) for every fact-bearing episode."""

    built = replay(task.instance)
    records = [built.store.get(e) for e in built.ids]
    frequency = document_frequency(records)
    pairs = []
    for key, indices in task.fact_sources.items():
        value = normalize(task.expected[key])
        for index in indices:
            record = records[index]
            if not contains_value(record_corpus(record), value):
                continue
            summary = summarize_episode(
                record,
                frequency,
                len(records),
                policy.summary_ratio,
                0.5,
                policy.short_value_chars,
            )
            pairs.append(
                (conservative_fidelity(summary), float(contains_value(summary.text, value)))
            )
    return pairs


def collect_pairs(seeds: list[int], policy: TierPolicy, args) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for seed in seeds:
        tasks = build_live_world(
            LiveConfig(
                seed=seed,
                variants_per_family=args.variants_per_family,
                per_template=args.per_template,
            )
        )
        for task in tasks:
            pairs.extend(survival_pairs(task, policy))
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dfl_bench.tiered", description=BANNER)
    parser.add_argument("--seeds", type=int, nargs="+", default=[4100, 4101, 4102])
    parser.add_argument("--variants-per-family", type=int, default=6)
    parser.add_argument("--per-template", type=int, default=6)
    parser.add_argument("--summary-ratio", type=float, default=0.3)
    parser.add_argument("--out", type=Path, default=Path("results/tiered"))
    parser.add_argument("--fit-calibration", type=int, nargs="*", default=None)
    parser.add_argument("--heldout-calibration", type=int, nargs="*", default=[])
    parser.add_argument("--calibration", type=Path, default=None)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    policy = TierPolicy(summary_ratio=args.summary_ratio)
    if args.fit_calibration:
        return fit_calibration(args, policy)
    calibration = (
        FidelityCalibration.from_json(json.loads(args.calibration.read_text(encoding="utf-8")))
        if args.calibration
        else None
    )
    results: dict[str, list[dict]] = defaultdict(list)
    for seed in args.seeds:
        tasks = build_live_world(
            LiveConfig(
                seed=seed,
                variants_per_family=args.variants_per_family,
                per_template=args.per_template,
            )
        )
        for task in tasks:
            for utilities_name in ("static", "oracle"):
                for variant, row in evaluate_task(
                    task, utilities_name, policy, calibration
                ).items():
                    results[f"{utilities_name}/{variant}"].append(row)
    (args.out / "tiered_rows.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    text = render(results)
    (args.out / "tiered_tables.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


def fit_calibration(args, policy: TierPolicy) -> int:
    train = collect_pairs(args.fit_calibration, policy, args)
    calibration = FidelityCalibration.fit(train)
    (args.out / "fidelity_calibration.json").write_text(
        json.dumps(calibration.to_json(), indent=1), encoding="utf-8"
    )
    lines = [
        "# Fidelity calibration (model-free)\n",
        f"Fitted on {len(train)} fact-bearing episodes from seeds {args.fit_calibration}.\n",
        "| conservative fidelity | measured survival |",
        "|---|---|",
    ]
    for x, y in zip(calibration.knots_x, calibration.knots_y, strict=True):
        lines.append(f"| {x:.3f} | {y:.3f} |")
    identity = FidelityCalibration((0.0, 1.0), (0.0, 1.0), 0)
    lines.append(
        f"\nIn-sample ECE: calibrated {calibration.expected_calibration_error(train):.3f}, "
        f"uncalibrated {identity.expected_calibration_error(train):.3f}."
    )
    if args.heldout_calibration:
        held = collect_pairs(args.heldout_calibration, policy, args)
        lines.append(
            f"\nHeld-out ECE on {len(held)} episodes from seeds {args.heldout_calibration}: "
            f"calibrated {calibration.expected_calibration_error(held):.3f}, "
            f"uncalibrated {identity.expected_calibration_error(held):.3f}; "
            f"mean fidelity {mean([x for x, _ in held]):.3f} vs mean survival "
            f"{mean([y for _, y in held]):.3f}."
        )
    text = "\n".join(lines) + "\n"
    (args.out / "calibration_report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


def render(results: dict[str, list[dict]]) -> str:
    lines = [f"# Three-level retention, model-free semantic check\n\n> {BANNER}\n"]
    lines += [
        "| utilities / variant | family | fact survival | all facts kept | tokens used | "
        "episodes represented | full / summary | expected fidelity |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key in sorted(results):
        by_family = defaultdict(list)
        for row in results[key]:
            by_family[row["family"]].append(row)
        for family in sorted(by_family) + ["all"]:
            rows = results[key] if family == "all" else by_family[family]
            lines.append(
                f"| {key} | {family} | {mean([r['fact_survival'] for r in rows]):.3f} | "
                f"{mean([r['all_facts'] for r in rows]):.3f} | "
                f"{mean([r['tokens_used'] for r in rows]):.2f} | "
                f"{mean([r['represented'] for r in rows]):.1f} | "
                f"{mean([r['full'] for r in rows]):.1f} / "
                f"{mean([r['summary'] for r in rows]):.1f} | "
                f"{mean([r['expected_fidelity'] for r in rows]):.3f} |"
            )
    lines += ["", "## Paired difference, tiered minus binary (template-clustered bootstrap)", ""]
    lines += [
        "| utilities | measure | mean diff | 95% CI | p | clusters |",
        "|---|---|---|---|---|---|",
    ]
    for utilities_name in ("static", "oracle"):
        binary = {r["instance_id"]: r for r in results.get(f"{utilities_name}/binary", [])}
        for variant in ("tiered", "tiered-calibrated"):
            rows = results.get(f"{utilities_name}/{variant}", [])
            for measure in ("fact_survival", "all_facts", "represented"):
                clusters: dict[str, list[float]] = defaultdict(list)
                for row in rows:
                    clusters[row["template"]].append(
                        row[measure] - binary[row["instance_id"]][measure]
                    )
                if not clusters:
                    continue
                s = cluster_bootstrap(clusters, seed=0)
                lines.append(
                    f"| {utilities_name} / {variant} | {measure} | {s['mean']:+.3f} | "
                    f"[{s['ci_low']:+.3f}, {s['ci_high']:+.3f}] | {s['p']:.4f} | "
                    f"{s['clusters']} |"
                )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
