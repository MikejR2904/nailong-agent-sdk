"""One measured replicate: candidates, real model outcomes, training, held-out evaluation."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from nailong_agent_sdk.memory.learned_retention import QuantizationPolicy

from ..experiment import _method_selection, _pair_accuracies
from ..instances import Instance, replay
from ..methods import FeatureCache, TrainedMethod, train_ranker, train_set_regression
from ..preferences import (
    CandidateOutcome,
    PreferencePolicy,
    assert_single_evidence_kind,
    build_preferences,
)
from ..selection import Selection, cost_of, generate_candidates, is_usable
from ..splits import CONTENT_KEYS, assert_no_leakage, split_instances
from .executor import LiveExecutor
from .scenarios import FAMILIES, LiveTask, evidence_free_ids, generate_task, validate_task

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
class LiveConfig:
    seed: int = 4000
    variants_per_family: int = 6
    per_template: int = 6
    train_trials: int = 3
    eval_trials: int = 3
    pair_trials: int = 3
    rounds: int = 2
    fractions: tuple[float, float, float] = (0.6, 0.15, 0.25)
    heldout_family: str | None = None
    run_mlp: bool = True
    quantization_floor: int = 0
    preference_policy: PreferencePolicy = field(
        default_factory=lambda: PreferencePolicy(min_trials=2)
    )


def build_live_world(config: LiveConfig) -> list[LiveTask]:
    tasks = []
    for family in FAMILIES:
        for variant in range(config.variants_per_family):
            for index in range(config.per_template):
                task = generate_task(family, variant, index, config.seed)
                validate_task(task)
                if is_usable(task.instance):
                    tasks.append(task)
    return tasks


def _split(tasks: Sequence[LiveTask], config: LiveConfig):
    instances = [t.instance for t in tasks]
    if config.heldout_family is None:
        return split_instances(instances, config.fractions, config.seed, CONTENT_KEYS)
    family_of = {t.instance.instance_id: t.family for t in tasks}
    test = [i for i in instances if family_of[i.instance_id] == config.heldout_family]
    rest = [i for i in instances if family_of[i.instance_id] != config.heldout_family]
    train, validation, _ = split_instances(
        rest,
        (
            config.fractions[0] / (1 - config.fractions[2]),
            1 - config.fractions[0] / (1 - config.fractions[2]),
            0.0,
        ),
        config.seed,
        CONTENT_KEYS,
    )
    return train, validation, test


def evaluate_candidates(
    executor: LiveExecutor,
    instances: Sequence[Instance],
    candidates: dict[str, list[Selection]],
    trials: int,
) -> list[CandidateOutcome]:
    requests = [
        (instance, candidate.ids, trial)
        for instance in instances
        for candidate in candidates[instance.instance_id]
        for trial in range(trials)
    ]
    scores = iter(executor.run_batch(requests))
    outcomes = []
    for instance in instances:
        for candidate in candidates[instance.instance_id]:
            outcomes.append(
                CandidateOutcome(
                    instance.instance_id,
                    candidate.ids,
                    (candidate.source, *candidate.meta.get("also", [])),
                    tuple(next(scores) for _ in range(trials)),
                    executor.evidence_kind,
                )
            )
    return outcomes


def make_candidates(
    instances: Sequence[Instance],
    seed: str,
    learned: dict | None = None,
    quantization: QuantizationPolicy | None = None,
) -> dict[str, list[Selection]]:
    return {
        i.instance_id: generate_candidates(
            i,
            random.Random(f"{seed}/{i.instance_id}"),
            learned_predictors=learned,
            quantization=quantization,
        )
        for i in instances
    }


def _merge(
    outcomes: list[CandidateOutcome], more: list[CandidateOutcome]
) -> list[CandidateOutcome]:
    merged = {(o.instance_id, o.selection): o for o in outcomes}
    for item in more:
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
    return list(merged.values())


def score_rows(
    tasks: dict[str, LiveTask],
    instances: Sequence[Instance],
    selections: dict[tuple[str, str], Selection],
    scores: dict[tuple[str, str], list[float]],
    method: str,
) -> list[dict]:
    rows = []
    for instance in instances:
        task = tasks[instance.instance_id]
        selection = selections[(method, instance.instance_id)]
        built = replay(instance)
        position = {episode_id: n for n, episode_id in enumerate(built.ids)}
        retained = {position[i] for i in selection.ids}
        trial_scores = scores[(method, instance.instance_id)]
        facts_present = sum(1 for sources in task.fact_sources.values() if retained & set(sources))
        rows.append(
            {
                "instance_id": instance.instance_id,
                "family": task.family,
                "quality": sum(trial_scores) / len(trial_scores),
                "success_rate": sum(1 for s in trial_scores if s == 1.0) / len(trial_scores),
                "regret": float("nan"),
                "evidence_coverage": facts_present / len(task.fact_sources),
                "failure_critical_recall": float(
                    bool(retained & set(task.fact_sources["error_code"]))
                ),
                "token_fraction": cost_of(built, selection.ids) / instance.budget,
                "solver_status": selection.solver_status,
                "optimality_gap": selection.optimality_gap,
                "learned_status": selection.learned_status,
                "latency_ms": selection.latency_ms,
                "trial_scores": trial_scores,
            }
        )
    return rows


def measurement_validity(
    executor: LiveExecutor, tasks: dict[str, LiveTask], instances: Sequence[Instance]
) -> dict[str, float]:
    """Positive and negative controls: every fact retained vs every fact-bearing episode removed."""

    if not instances:
        raise ValueError("Measurement validity needs at least one test task; the split was empty.")
    requests_all = [(i, set(replay(i).ids), 0) for i in instances]
    requests_none = [(i, evidence_free_ids(tasks[i.instance_id]), 0) for i in instances]
    all_scores = executor.run_batch(requests_all)
    none_scores = executor.run_batch(requests_none)
    return {
        "all_evidence_mean": sum(all_scores) / len(all_scores),
        "no_fact_evidence_mean": sum(none_scores) / len(none_scores),
        "tasks": len(all_scores),
    }


def run_live_replicate(
    config: LiveConfig,
    executor: LiveExecutor,
    relaxed_executor: LiveExecutor,
    tasks: Sequence[LiveTask],
    progress: Callable[[str], None] = print,
    cross_executors: Mapping[str, LiveExecutor] | None = None,
) -> dict:
    by_id = {t.instance.instance_id: t for t in tasks}
    train_i, val_i, test_i = _split(tasks, config)
    assert_no_leakage(train_i, val_i, test_i, keys=(*CONTENT_KEYS, "instance"))
    instances = [t.instance for t in tasks]
    cache = FeatureCache(instances)
    train_ids = {i.instance_id for i in train_i}
    quantization = (
        QuantizationPolicy(floor=config.quantization_floor) if config.quantization_floor else None
    )
    timings = {"candidates_s": 0.0, "training_s": 0.0, "evaluation_s": 0.0}

    def split_prefs(outcomes):
        prefs = build_preferences(outcomes, config.preference_policy)
        return (
            [p for p in prefs if p.instance_id in train_ids],
            [p for p in prefs if p.instance_id not in train_ids],
        )

    started = time.perf_counter()
    candidates = make_candidates(train_i + val_i, f"{config.seed}/round0")
    outcomes = evaluate_candidates(executor, train_i + val_i, candidates, config.train_trials)
    timings["candidates_s"] += time.perf_counter() - started
    kind = assert_single_evidence_kind(outcomes)
    progress(f"  round 0: {len(outcomes)} candidate sets measured; {executor.usage}")
    train_prefs, val_prefs = split_prefs(outcomes)
    methods: dict[str, TrainedMethod] = {}
    for round_index in range(config.rounds):
        started = time.perf_counter()
        methods["linear-dfl"] = train_ranker(
            "linear-dfl", "linear", cache, train_prefs, val_prefs, seed=config.seed
        )
        if config.run_mlp:
            methods["mlp-dfl"] = train_ranker(
                "mlp-dfl", "mlp", cache, train_prefs, val_prefs, seed=config.seed
            )
        timings["training_s"] += time.perf_counter() - started
        if round_index == config.rounds - 1:
            break
        started = time.perf_counter()
        learned = {name: m.predictor for name, m in methods.items()}
        more_candidates = make_candidates(
            train_i + val_i, f"{config.seed}/round{round_index + 1}", learned, quantization
        )
        more = evaluate_candidates(executor, train_i + val_i, more_candidates, config.train_trials)
        timings["candidates_s"] += time.perf_counter() - started
        outcomes = _merge(outcomes, more)
        train_prefs, val_prefs = split_prefs(outcomes)
        progress(f"  round {round_index + 1}: {len(outcomes)} sets; {executor.usage}")
    train_outcomes = [o for o in outcomes if o.instance_id in train_ids]
    val_outcomes = [o for o in outcomes if o.instance_id not in train_ids]
    started = time.perf_counter()
    methods["regression-pckp"] = train_set_regression(
        "regression-pckp", cache, train_outcomes, val_outcomes
    )
    timings["training_s"] += time.perf_counter() - started

    names = [
        n for n in METHODS if n in methods or n not in ("linear-dfl", "mlp-dfl", "regression-pckp")
    ]
    selections: dict[tuple[str, str], Selection] = {}
    for instance in test_i:
        for name in names:
            selections[(name, instance.instance_id)] = _method_selection(
                name, instance, methods, config.seed, quantization
            )
    requests = [
        (instance, selections[(name, instance.instance_id)].ids, trial)
        for name in names
        for instance in test_i
        for trial in range(config.eval_trials)
    ]
    started = time.perf_counter()
    flat = iter(executor.run_batch(requests))
    scores = {
        (name, instance.instance_id): [next(flat) for _ in range(config.eval_trials)]
        for name in names
        for instance in test_i
    }
    timings["evaluation_s"] += time.perf_counter() - started
    per_method = {name: score_rows(by_id, test_i, selections, scores, name) for name in names}
    progress(f"  test selections measured; {executor.usage}")
    cross_model: dict[str, dict[str, list[dict]]] = {}
    for model_name, other in (cross_executors or {}).items():
        other_scores = iter(other.run_batch(requests))
        measured = {
            (name, instance.instance_id): [next(other_scores) for _ in range(config.eval_trials)]
            for name in names
            for instance in test_i
        }
        cross_model[model_name] = {
            name: score_rows(by_id, test_i, selections, measured, name) for name in names
        }
        progress(f"  cross-model {model_name}: {other.usage}")

    test_candidates = make_candidates(test_i, f"{config.seed}/test")
    test_outcomes = evaluate_candidates(executor, test_i, test_candidates, config.pair_trials)
    test_prefs = build_preferences(test_outcomes, config.preference_policy)
    pair_accuracy = _pair_accuracies(test_i, test_prefs, methods, cache)
    validity = measurement_validity(relaxed_executor, by_id, test_i)
    progress(f"  validity {validity}; {executor.usage} / {relaxed_executor.usage}")
    return {
        "evidence_kind": kind,
        "config": {
            "seed": config.seed,
            "variants_per_family": config.variants_per_family,
            "per_template": config.per_template,
            "train_trials": config.train_trials,
            "eval_trials": config.eval_trials,
            "pair_trials": config.pair_trials,
            "rounds": config.rounds,
            "heldout_family": config.heldout_family,
            "quantization_floor": config.quantization_floor,
            "regime": config.heldout_family or "live",
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
        "ablations": {},
        "measurement_validity": validity,
        "cross_model": cross_model,
    }
