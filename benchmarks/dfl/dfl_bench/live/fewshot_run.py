"""``python -m dfl_bench.live.fewshot_run``: measured few-shot adaptation on held-out families.

Real model calls (paid). For each held-out family a support set of tasks is measured and used by
every adapting method, including the tuned static baseline; methods are scored on the family's
remaining tasks by the deterministic verifier. Run from ``benchmarks/dfl``.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shutil
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from nailong_agent_sdk.memory.episode_models import PaskCompactionPolicy
from nailong_agent_sdk.memory.learned_retention import QuantizationPolicy
from nailong_agent_sdk.memory.tiered_retention import (
    FidelityCalibration,
    TierPolicy,
    document_frequency,
    plan_tiered_retention,
    summarize_episode,
)
from nailong_agent_sdk.memory.utility_calibration import static_prior_weights
from nailong_agent_sdk.memory.utility_features_v2 import (
    FEATURE_SCHEMA_VERSION_V3 as V3,
)
from nailong_agent_sdk.memory.utility_features_v2 import FEATURE_SCHEMAS

from ..generalize.fewshot import fit_adapted, split_support
from ..generalize.learners import Method, SchemaCache, run_method, tune_static
from ..generalize.loo import loo_dataset, measure_loo, retention_scorecard
from ..generalize.protocol import V1
from ..instances import replay
from ..ledger import OutcomeLedger
from ..live.scenarios import contains_value, normalize
from ..preferences import PreferencePolicy
from ..selection import (
    STATIC,
    Selection,
    mandatory_closure,
    retention_context,
    select_via_store,
    static_utilities,
)
from ..stats import cluster_bootstrap, holm_adjust, mean
from .__main__ import DEFAULT_MODEL, ROOT, _git_commit
from .executor import (
    SUMMARY_SUFFIX,
    LiveExecutor,
    LiveSettings,
    Throttle,
    UsageTotals,
    api_key_from_environment,
)
from .experiment import (
    LiveConfig,
    build_live_world,
    evaluate_candidates,
    make_candidates,
    measurement_validity,
    score_rows,
)
from .scenarios import FAMILIES

BANNER = (
    "MEASURED EVIDENCE: outcomes are real model answers scored by a deterministic verifier on "
    "generated tool-call histories."
)


def make_quality_many(executor: LiveExecutor, trials: int):
    def evaluate(jobs) -> list[float]:
        plan, requests = [], []
        for method, instances in jobs:
            chosen = [run_method(method, i).ids for i in instances]
            plan.append((instances, chosen))
            for instance, ids in zip(instances, chosen, strict=True):
                requests.extend((instance, ids, t) for t in range(trials))
        scores = iter(executor.run_batch(requests))
        results = []
        for instances, _ in plan:
            total = 0.0
            for _ in instances:
                total += sum(next(scores) for _ in range(trials)) / trials
            results.append(total / len(instances))
        return results

    return evaluate


def tiered_selection(
    name: str,
    instance,
    utilities_from,
    calibration: FidelityCalibration,
    executors,
) -> Selection:
    """Plan full/summary/drop for one task and register the summaries with the executors."""

    built = replay(instance)
    context = retention_context(built, instance)
    utilities = (
        static_utilities(instance)
        if utilities_from is None
        else dict(utilities_from.predictor.predict(context))
    )
    plan = plan_tiered_retention(
        context,
        utilities,
        QuantizationPolicy(floor=1),
        TierPolicy(),
        frozenset(mandatory_closure(built)),
        calibration=calibration,
    )
    for episode_id, summary in plan.summaries.items():
        for executor in executors:
            executor.summaries[(instance.instance_id, episode_id)] = (summary.text, summary.tokens)
    selection = Selection(
        frozenset(plan.full_ids) | frozenset(plan.summary_ids),
        name,
        plan.solver_status,
        plan.optimality_gap,
    )
    selection.meta["live_ids"] = frozenset(plan.full_ids) | frozenset(
        f"{e}{SUMMARY_SUFFIX}" for e in plan.summary_ids
    )
    selection.meta["token_fraction"] = plan.token_cost / instance.budget
    selection.meta["full"] = len(plan.full_ids)
    selection.meta["summary"] = len(plan.summary_ids)
    return selection


def summary_control(relaxed: LiveExecutor, query, calibration: FidelityCalibration) -> dict:
    """Every episode replaced by its summary (budget ignored): can the model use summaries at
    all, and how does a real model compare with the model-free fact-survival proxy?"""

    requests, survival = [], []
    for instance in query:
        built = replay(instance)
        records = [built.store.get(e) for e in built.ids]
        frequency = document_frequency(records)
        live_ids = set()
        texts = []
        for record in records:
            summary = summarize_episode(record, frequency, len(records), TierPolicy().summary_ratio)
            relaxed.summaries[(instance.instance_id, record.id)] = (summary.text, summary.tokens)
            live_ids.add(f"{record.id}{SUMMARY_SUFFIX}")
            texts.append(summary.text)
        requests.append((instance, live_ids, 0))
        expected = relaxed._tasks[instance.instance_id].expected
        corpus = " ".join(texts)
        survival.append(
            mean([float(contains_value(corpus, normalize(v))) for v in expected.values()])
        )
    scores = relaxed.run_batch(requests)
    return {
        "all_summaries_live_score": mean(scores),
        "all_summaries_fact_survival_proxy": mean(survival),
        "tasks": len(scores),
    }


def run_family(family: str, seed: int, args, executor: LiveExecutor, relaxed: LiveExecutor) -> dict:
    config = LiveConfig(
        seed=seed,
        variants_per_family=args.variants_per_family,
        per_template=args.per_template,
        train_trials=args.train_trials,
        eval_trials=args.eval_trials,
        heldout_family=family,
        preference_policy=PreferencePolicy(min_trials=2),
    )
    tasks = build_live_world(config)
    by_id = {t.instance.instance_id: t for t in tasks}
    executor._tasks.update(by_id)
    relaxed._tasks.update(by_id)
    members = [t.instance for t in tasks if t.family == family]
    support, query = split_support(members, args.support_templates, seed)
    cache = SchemaCache(support + query, V1)
    cache_v3 = SchemaCache(support + query, V3)
    quality_many = make_quality_many(executor, args.train_trials)
    before = dataclasses.asdict(executor.usage)
    print(f"{family}: {len(support)} support tasks, {len(query)} query tasks", flush=True)

    methods: dict[str, Method] = {"static-pckp": Method("static-pckp", "static", STATIC)}
    tuned = tune_static(
        support, executor, args.train_trials, seed + 1, args.tune_draws, quality_many
    )
    tuned.name = "tuned-static-support"
    methods["tuned-static-support"] = tuned
    print(f"  tuned static on support: {executor.usage}", flush=True)

    candidates = make_candidates(support, f"{seed}/support")
    support_outcomes = evaluate_candidates(executor, support, candidates, args.train_trials)
    print(f"  support candidates measured: {executor.usage}", flush=True)
    prior = static_prior_weights(PaskCompactionPolicy(), FEATURE_SCHEMAS[V1][0])
    for objective in ("listwise", "regression"):
        name = f"adapt-{objective}-static"
        methods[name] = fit_adapted(
            name,
            objective,
            prior,
            support,
            support_outcomes,
            cache,
            executor,
            preference_policy=PreferencePolicy(min_trials=2),
            seed=seed,
            trials=args.train_trials,
            quality_many=quality_many,
        )
    loo_support: dict[str, dict[str, float]] = {}
    loo_query: dict[str, dict[str, float]] = {}
    if args.loo:
        loo_support = measure_loo(relaxed, support, args.train_trials, batch=relaxed.run_batch)
        print(f"  support criticality measured: {executor.usage}", flush=True)
        prior_v3 = static_prior_weights(PaskCompactionPolicy(), FEATURE_SCHEMAS[V3][0])
        variants = (
            ("adapt-listwise-static-v3", cache_v3, prior_v3, None),
            ("adapt-loo-listwise-static-v1", cache, prior, loo_support),
            ("adapt-loo-listwise-static-v3", cache_v3, prior_v3, loo_support),
        )
        for name, variant_cache, variant_prior, labels in variants:
            methods[name] = fit_adapted(
                name,
                "listwise",
                variant_prior,
                support,
                support_outcomes,
                variant_cache,
                executor,
                preference_policy=PreferencePolicy(min_trials=2),
                seed=seed,
                trials=args.train_trials,
                quality_many=quality_many,
                extra_data=(
                    None
                    if labels is None
                    else lambda ids, c=variant_cache, lab=labels: loo_dataset(c, lab, ids)
                ),
            )
    print(f"  adapted: {executor.usage}", flush=True)

    selections: dict[tuple[str, str], Selection] = {}
    tiered_names: list[str] = []
    calibration = None
    if args.tiered:
        calibration = FidelityCalibration.from_json(
            json.loads(args.calibration.read_text(encoding="utf-8"))
        )
        sources = {"tiered-static": None, "tiered-adapt-listwise": methods["adapt-listwise-static"]}
        tiered_names = list(sources)
    for instance in query:
        for name, method in methods.items():
            selections[(name, instance.instance_id)] = run_method(method, instance)
        for name in tiered_names:
            selections[(name, instance.instance_id)] = tiered_selection(
                name, instance, sources[name], calibration, (executor, relaxed)
            )
    all_names = [*methods, *tiered_names]

    def live_ids(name, instance_id):
        selection = selections[(name, instance_id)]
        return selection.meta.get("live_ids", selection.ids)

    requests = [
        (instance, live_ids(name, instance.instance_id), trial)
        for name in all_names
        for instance in query
        for trial in range(args.eval_trials)
    ]
    flat = iter(executor.run_batch(requests))
    scores = {
        (name, instance.instance_id): [next(flat) for _ in range(args.eval_trials)]
        for name in all_names
        for instance in query
    }
    static_ids = {i.instance_id: select_via_store(i, STATIC)[0].ids for i in query}
    per_method = {}
    for name in all_names:
        rows = score_rows(by_id, query, selections, scores, name)
        for row in rows:
            row["template"] = by_id[row["instance_id"]].instance.template_id
            meta = selections[(name, row["instance_id"])].meta
            if "token_fraction" in meta:
                row["token_fraction"] = meta["token_fraction"]
                row["tier_full"] = meta["full"]
                row["tier_summary"] = meta["summary"]
            row["differs_from_static"] = (
                selections[(name, row["instance_id"])].ids != static_ids[row["instance_id"]]
            )
        per_method[name] = rows
    if args.loo:
        loo_query = measure_loo(relaxed, query, args.train_trials, batch=relaxed.run_batch)
        for name in all_names:
            for row in per_method[name]:
                row.update(
                    retention_scorecard(
                        by_id[row["instance_id"]].instance,
                        selections[(name, row["instance_id"])].ids,
                        loo_query[row["instance_id"]],
                    )
                )
    validity = measurement_validity(relaxed, by_id, query)
    if args.tiered and calibration is not None:
        validity.update(summary_control(relaxed, query, calibration))
    after = dataclasses.asdict(executor.usage)
    print(f"  evaluated; validity {validity}; {executor.usage}", flush=True)
    return {
        "evidence_kind": "measured",
        "family": family,
        "seed": seed,
        "sizes": {"support": len(support), "query": len(query)},
        "per_method": per_method,
        "validity": validity,
        "criticality": {"support": loo_support, "query": loo_query},
        "training": {
            n: {"seconds": m.train_seconds, **{k: v for k, v in m.detail.items() if k != "config"}}
            for n, m in methods.items()
        },
        "usage_for_family": {k: after[k] - before[k] for k in after},
    }


def summarize(results: Sequence[dict]) -> str:
    names = sorted({m for r in results for m in r["per_method"]})
    lines = [f"# Live few-shot adaptation\n\n> {BANNER}\n"]
    lines += [
        "## Held-out family quality (verifier score, query tasks)\n",
        "| method | " + " | ".join(r["family"] for r in results) + " | mean | worst family |",
        "|---|" + "---|" * (len(results) + 2),
    ]
    per_family = {
        n: [mean([row["quality"] for row in r["per_method"][n]]) for r in results] for n in names
    }
    for n in sorted(names, key=lambda x: -mean(per_family[x])):
        values = per_family[n]
        cells = " | ".join(f"{v:.3f}" for v in values)
        lines.append(f"| {n} | {cells} | {mean(values):.3f} | {min(values):.3f} |")
    lines += ["", "## Change against static on the same task\n"]
    lines += [
        "| method | mean change | worst-family change | negative-transfer | deviation rate |",
        "|---|---|---|---|---|",
    ]
    static = {
        (r["family"], row["instance_id"]): row["quality"]
        for r in results
        for row in r["per_method"]["static-pckp"]
    }
    for n in names:
        if n == "static-pckp":
            continue
        deltas, fam, dev = [], defaultdict(list), 0
        for r in results:
            for row in r["per_method"][n]:
                d = row["quality"] - static[(r["family"], row["instance_id"])]
                deltas.append(d)
                fam[r["family"]].append(d)
                dev += bool(row["differs_from_static"])
        lines.append(
            f"| {n} | {mean(deltas):+.3f} | {min(mean(v) for v in fam.values()):+.3f} | "
            f"{sum(1 for d in deltas if d < 0) / len(deltas):.2f} | {dev / len(deltas):.2f} |"
        )
    if any("critical_recall" in row for r in results for row in r["per_method"]["static-pckp"]):
        lines += ["", "## Retention scorecard (leave-one-out criticality on the query tasks)\n"]
        lines += [
            "| method | quality | evidence coverage | critical recall | critical missed / total | "
            "distraction load | token use |",
            "|---|---|---|---|---|---|---|",
        ]

        def avg(name, key, numeric_only=True):
            values = [
                row[key]
                for r in results
                for row in r["per_method"][name]
                if key in row and row[key] == row[key]
            ]
            return mean(values) if values else float("nan")

        for n in sorted(names, key=lambda x: -avg(x, "quality")):
            missed = sum(row["critical_missed"] for r in results for row in r["per_method"][n])
            total = sum(row["critical_total"] for r in results for row in r["per_method"][n])
            lines.append(
                f"| {n} | {avg(n, 'quality'):.3f} | {avg(n, 'evidence_coverage'):.3f} | "
                f"{avg(n, 'critical_recall'):.3f} | {missed} / {total} | "
                f"{avg(n, 'distraction_load'):.3f} | {avg(n, 'token_fraction'):.2f} |"
            )
    lines += ["", "## Paired comparisons (template-clustered bootstrap)\n"]
    lines += [
        "| comparison | mean diff | 95% CI | p | Holm p | clusters |",
        "|---|---|---|---|---|---|",
    ]
    comparisons = {}
    candidates = [n for n in names if n.startswith(("adapt-", "tiered-"))]
    references = ["tuned-static-support", "static-pckp"]
    if "adapt-loo-listwise-static-v3" in names or "tiered-adapt-listwise" in names:
        references.append("adapt-listwise-static")
    for cand in candidates:
        for ref in references:
            if cand == ref:
                continue
            base = {
                (r["family"], row["instance_id"]): row["quality"]
                for r in results
                for row in r["per_method"][ref]
            }
            clusters: dict[str, list[float]] = defaultdict(list)
            for r in results:
                for row in r["per_method"][cand]:
                    clusters[f"{r['family']}/{row['template']}"].append(
                        row["quality"] - base[(r["family"], row["instance_id"])]
                    )
            comparisons[f"{cand} vs {ref}"] = cluster_bootstrap(clusters, seed=0)
    adjusted = holm_adjust({k: v["p"] for k, v in comparisons.items()})
    for k, v in comparisons.items():
        lines.append(
            f"| {k} | {v['mean']:+.3f} | [{v['ci_low']:+.3f}, {v['ci_high']:+.3f}] | "
            f"{v['p']:.4f} | {adjusted[k]:.4f} | {v['clusters']} |"
        )
    lines += ["", "## Measurement validity (relaxed budget; positive and negative controls)\n"]
    for r in results:
        lines.append(f"- {r['family']}: {r['validity']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dfl_bench.live.fewshot_run", description=BANNER)
    parser.add_argument("--families", nargs="*", default=list(FAMILIES), choices=FAMILIES)
    parser.add_argument("--heldout-seed", type=int, default=4100)
    parser.add_argument("--support-templates", type=int, default=2)
    parser.add_argument("--variants-per-family", type=int, default=6)
    parser.add_argument("--per-template", type=int, default=6)
    parser.add_argument("--train-trials", type=int, default=3)
    parser.add_argument("--eval-trials", type=int, default=3)
    parser.add_argument("--loo", action="store_true")
    parser.add_argument("--tiered", action="store_true")
    parser.add_argument(
        "--calibration",
        type=Path,
        default=Path("results/tiered_calibration/fidelity_calibration.json"),
    )
    parser.add_argument("--tune-draws", type=int, default=20)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--input-token-cap", type=int, default=30_000_000)
    parser.add_argument("--reuse-ledger", type=Path, default=Path("results/live_heldout"))
    parser.add_argument("--key-env", default="FIREWORKS_API_KEY")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--out", type=Path, default=Path("results/live_fewshot"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    api_key = api_key_from_environment(args.key_env, args.env_file)
    settings = LiveSettings(
        model=args.model,
        concurrency=args.concurrency,
        input_token_cap=args.input_token_cap,
        parameters={
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            **(
                {"reasoning_effort": args.reasoning_effort}
                if args.reasoning_effort != "none"
                else {}
            ),
        },
    )
    digest = hashlib.sha256(
        json.dumps(
            [settings.model, settings.provider, dict(settings.parameters)], sort_keys=True
        ).encode()
    ).hexdigest()[:10]
    target = args.out / f"outcomes-{digest}.jsonl"
    source = args.reuse_ledger / f"outcomes-{digest}.jsonl"
    if not target.exists() and source.exists():
        shutil.copyfile(source, target)
    ledger = OutcomeLedger(target, create=True)
    usage = UsageTotals()
    throttle = Throttle(args.concurrency)
    executor = LiveExecutor({}, settings, ledger, api_key, usage, throttle)
    relaxed = LiveExecutor(
        {}, dataclasses.replace(settings, enforce_budget=False), ledger, api_key, usage, throttle
    )
    results = []
    started = time.perf_counter()
    for n, family in enumerate(args.families):
        results.append(run_family(family, args.heldout_seed + n, args, executor, relaxed))
        text = summarize(results)
        (args.out / "live_fewshot_tables.md").write_text(text, encoding="utf-8")
        (args.out / "live_fewshot_results.json").write_text(
            json.dumps(
                {
                    "evidence_kind": "measured",
                    "results": results,
                    "usage": dataclasses.asdict(usage),
                    "environment": {
                        "git_commit": _git_commit(),
                        "model": settings.model,
                        "parameters": dict(settings.parameters),
                        "wall_seconds": time.perf_counter() - started,
                        "command": " ".join(sys.argv),
                    },
                },
                indent=1,
                default=str,
            ),
            encoding="utf-8",
        )
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
