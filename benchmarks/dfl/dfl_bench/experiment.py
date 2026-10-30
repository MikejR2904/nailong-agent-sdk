"""One synthetic-ground-truth replicate: split, candidates, outcomes, training, test evaluation.

Everything here is a solver and learning sanity check against a known-by-construction oracle. It
is not a downstream agent evaluation, and every output is stamped ``evidence_kind``.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

from nailong_agent_sdk.memory.learned_retention import QuantizationPolicy
from nailong_agent_sdk.memory.utility_features import FEATURE_GROUPS

from .instances import Instance, generate_instance, replay
from .methods import (
    FeatureCache,
    TrainedMethod,
    pair_accuracy_for_scores,
    top_k_with_repair,
    train_ranker,
    train_set_regression,
)
from .oracle import (
    DownstreamExecutor,
    SyntheticOracleExecutor,
    optimum_quality,
    quality_from_indices,
    satisfied_fraction,
    to_indices,
)
from .preferences import (
    CandidateOutcome,
    PreferencePolicy,
    assert_single_evidence_kind,
    build_preferences,
)
from .selection import (
    LEARNED,
    PASK,
    STATIC,
    Selection,
    assert_feasible,
    cost_of,
    generate_candidates,
    is_usable,
    random_feasible,
    select_via_store,
    static_utilities,
)
from .splits import assert_no_leakage, split_instances

METHODS = (
    "static-pckp",
    "greedy-pask",
    "random-feasible",
    "linear-dfl",
    "mlp-dfl",
    "regression-pckp",
    "topk-repair",
)


@dataclass
class ExperimentConfig:
    regime: str = "aligned"
    seed: int = 0
    templates: int = 14
    per_template: int = 6
    train_trials: int = 8
    eval_trials: int = 40
    rounds: int = 2
    fractions: tuple[float, float, float] = (0.6, 0.15, 0.25)
    run_mlp: bool = True
    run_ablations: bool = True
    preference_policy: PreferencePolicy = field(default_factory=PreferencePolicy)


def evaluate_candidates(
    instances: list[Instance],
    executor: DownstreamExecutor,
    trials: int,
    seed: int,
    learned: dict[str, object] | None = None,
) -> list[CandidateOutcome]:
    outcomes: list[CandidateOutcome] = []
    for instance in instances:
        rng = random.Random(f"{seed}/{instance.instance_id}/candidates")
        for candidate in generate_candidates(instance, rng, learned_predictors=learned):
            results = tuple(executor.run(instance, candidate.ids, t) for t in range(trials))
            sources = (candidate.source, *candidate.meta.get("also", []))
            outcomes.append(
                CandidateOutcome(
                    instance.instance_id,
                    candidate.ids,
                    tuple(sources),
                    results,
                    executor.evidence_kind,
                )
            )
    return outcomes


def build_world(config: ExperimentConfig) -> list[Instance]:
    instances = []
    for template in range(config.templates):
        template_id = f"T{config.seed}x{template}"
        for index in range(config.per_template):
            instance = generate_instance(template_id, config.regime, index, config.seed)
            if is_usable(instance):
                instances.append(instance)
    return instances


def _method_selection(
    name: str,
    instance: Instance,
    methods: dict[str, TrainedMethod],
    seed: int,
    quantization: QuantizationPolicy | None = None,
):
    if name == "static-pckp":
        return select_via_store(instance, STATIC, source=name)[0]
    if name == "greedy-pask":
        return select_via_store(instance, PASK, source=name)[0]
    if name == "random-feasible":
        built = replay(instance)
        started = time.perf_counter()
        ids = random_feasible(instance, built, random.Random(f"{seed}/{instance.instance_id}/rf"))
        return Selection(ids, name, latency_ms=(time.perf_counter() - started) * 1000.0)
    if name == "topk-repair":
        return top_k_with_repair(instance, methods["linear-dfl"], name)
    return select_via_store(
        instance, LEARNED, methods[name].predictor, source=name, quantization=quantization
    )[0]


def score_selection(instance: Instance, selection: Selection, optimum: float, trials: int) -> dict:
    built = replay(instance)
    assert_feasible(instance, built, selection.ids, selection.source)
    indices = to_indices(built, selection.ids)
    quality = quality_from_indices(instance, indices)
    members = {i for alt in instance.requirements for i in alt}
    failed_members = [i for i in members if instance.recipe[i]["status"] == "failed"]
    draws = [
        1.0 if random.Random(f"{instance.instance_id}/eval/{t}").random() < quality else 0.0
        for t in range(trials)
    ]
    return {
        "instance_id": instance.instance_id,
        "quality": quality,
        "success_rate": sum(draws) / trials,
        "regret": optimum - quality,
        "evidence_coverage": satisfied_fraction(instance, indices),
        "failure_critical_recall": (
            sum(1 for i in failed_members if i in indices) / len(failed_members)
            if failed_members
            else float("nan")
        ),
        "token_fraction": cost_of(built, selection.ids) / instance.budget,
        "solver_status": selection.solver_status,
        "optimality_gap": selection.optimality_gap,
        "learned_status": selection.learned_status,
        "latency_ms": selection.latency_ms,
        "selected": len(selection.ids),
    }


def run_replicate(config: ExperimentConfig, executor: DownstreamExecutor | None = None) -> dict:
    executor = executor or SyntheticOracleExecutor()
    instances = build_world(config)
    train_i, val_i, test_i = split_instances(instances, config.fractions, config.seed)
    assert_no_leakage(train_i, val_i, test_i)
    cache = FeatureCache(instances)
    timings = {"candidates_s": 0.0, "training_s": 0.0}

    started = time.perf_counter()
    outcomes = evaluate_candidates(train_i + val_i, executor, config.train_trials, config.seed)
    timings["candidates_s"] += time.perf_counter() - started
    kind = assert_single_evidence_kind(outcomes)
    train_ids = {i.instance_id for i in train_i}

    def split_prefs(items):
        prefs = build_preferences(items, config.preference_policy)
        return (
            [p for p in prefs if p.instance_id in train_ids],
            [p for p in prefs if p.instance_id not in train_ids],
        )

    train_prefs, val_prefs = split_prefs(outcomes)
    methods: dict[str, TrainedMethod] = {}
    for round_index in range(config.rounds):
        methods["linear-dfl"] = train_ranker(
            "linear-dfl", "linear", cache, train_prefs, val_prefs, seed=config.seed
        )
        if config.run_mlp:
            methods["mlp-dfl"] = train_ranker(
                "mlp-dfl", "mlp", cache, train_prefs, val_prefs, seed=config.seed
            )
        timings["training_s"] += sum(m.train_seconds for m in methods.values())
        if round_index == config.rounds - 1:
            break
        started = time.perf_counter()
        policy_outcomes = evaluate_candidates(
            train_i + val_i,
            executor,
            config.train_trials,
            config.seed + round_index + 1,
            {name: m.predictor for name, m in methods.items()},
        )
        timings["candidates_s"] += time.perf_counter() - started
        merged = {(o.instance_id, o.selection): o for o in outcomes}
        for item in policy_outcomes:
            key = (item.instance_id, item.selection)
            if key in merged:
                old = merged[key]
                merged[key] = CandidateOutcome(
                    old.instance_id,
                    old.selection,
                    tuple(dict.fromkeys((*old.sources, *item.sources))),
                    old.outcomes,
                    old.evidence_kind,
                )
            else:
                merged[key] = item
        outcomes = list(merged.values())
        train_prefs, val_prefs = split_prefs(outcomes)
    train_outcomes = [o for o in outcomes if o.instance_id in train_ids]
    val_outcomes = [o for o in outcomes if o.instance_id not in train_ids]
    methods["regression-pckp"] = train_set_regression(
        "regression-pckp", cache, train_outcomes, val_outcomes
    )
    timings["training_s"] += methods["regression-pckp"].train_seconds

    optimum = {i.instance_id: optimum_quality(i)[0] for i in test_i}
    trained_only = ("linear-dfl", "mlp-dfl", "regression-pckp")
    names = [n for n in METHODS if n in methods or n not in trained_only]
    per_method: dict[str, list[dict]] = {name: [] for name in names}
    for instance in test_i:
        for name in names:
            selection = _method_selection(name, instance, methods, config.seed)
            per_method[name].append(
                score_selection(
                    instance, selection, optimum[instance.instance_id], config.eval_trials
                )
            )

    test_outcomes = evaluate_candidates(test_i, executor, config.train_trials, config.seed + 99)
    test_prefs = build_preferences(test_outcomes, config.preference_policy)
    pair_accuracy = _pair_accuracies(test_i, test_prefs, methods, cache)

    ablations = (
        _ablations(config, cache, train_i, test_i, train_prefs, val_prefs, optimum, methods)
        if config.run_ablations
        else {}
    )
    return {
        "evidence_kind": kind,
        "config": {
            "regime": config.regime,
            "seed": config.seed,
            "templates": config.templates,
            "per_template": config.per_template,
            "train_trials": config.train_trials,
            "eval_trials": config.eval_trials,
            "rounds": config.rounds,
        },
        "sizes": {
            "train_instances": len(train_i),
            "val_instances": len(val_i),
            "test_instances": len(test_i),
            "train_pairs": len(train_prefs),
            "val_pairs": len(val_prefs),
            "test_pairs": len(test_prefs),
        },
        "per_method": per_method,
        "pair_accuracy": pair_accuracy,
        "training": {name: {"seconds": m.train_seconds, **m.detail} for name, m in methods.items()},
        "timings": timings,
        "ablations": ablations,
    }


def _pair_accuracies(test_i, test_prefs, methods, cache) -> dict[str, float]:
    result: dict[str, float] = {}
    static = {i.instance_id: static_utilities(i) for i in test_i}
    result["static-pckp"] = pair_accuracy_for_scores(static, test_prefs)
    for name, method in methods.items():
        scores = {}
        for instance in test_i:
            features = cache.get(instance.instance_id, method.disabled)
            scores[instance.instance_id] = {
                episode_id: method.model.score(vector) for episode_id, vector in features.items()
            }
        result[name] = pair_accuracy_for_scores(scores, test_prefs)
    result["n_test_pairs"] = len(test_prefs)
    return result


def _mean_quality(rows) -> float:
    return sum(r["quality"] for r in rows) / len(rows)


def _ablations(config, cache, train_i, test_i, train_prefs, val_prefs, optimum, methods) -> dict:
    report: dict[str, dict] = {"feature_groups": {}, "training_size": {}}

    def test_quality(method: TrainedMethod) -> float:
        rows = [
            score_selection(
                i,
                select_via_store(i, LEARNED, method.predictor, source=method.name)[0],
                optimum[i.instance_id],
                config.eval_trials,
            )
            for i in test_i
        ]
        return _mean_quality(rows)

    report["full_linear_test_quality"] = test_quality(methods["linear-dfl"])
    for group in FEATURE_GROUPS:
        if group == "bias":
            continue
        method = train_ranker(
            f"linear-without-{group}",
            "linear",
            cache,
            train_prefs,
            val_prefs,
            disabled=(group,),
            seed=config.seed,
        )
        report["feature_groups"][group] = test_quality(method)
    train_order = sorted({i.instance_id for i in train_i})
    random.Random(config.seed).shuffle(train_order)
    for fraction in (0.25, 0.5, 1.0):
        keep = set(train_order[: max(1, math.ceil(fraction * len(train_order)))])
        subset = [p for p in train_prefs if p.instance_id in keep]
        if not subset:
            report["training_size"][str(fraction)] = None
            continue
        method = train_ranker(
            f"linear-{fraction}", "linear", cache, subset, val_prefs, seed=config.seed
        )
        report["training_size"][str(fraction)] = {
            "train_instances": len(keep),
            "train_pairs": len(subset),
            "test_quality": test_quality(method),
        }
    return report
