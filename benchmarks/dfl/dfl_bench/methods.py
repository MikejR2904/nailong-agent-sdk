"""Training of the compared utility models and the non-PCKP baselines."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from nailong_agent_sdk.memory.learned_retention import LearnedUtilityPredictor
from nailong_agent_sdk.memory.utility_features import FEATURE_NAMES, extract_features
from nailong_agent_sdk.memory.utility_models import (
    LinearRankingModel,
    RankingModel,
    model_to_artifact,
)
from nailong_agent_sdk.memory.utility_training import (
    RankingTrainingConfig,
    _solve_positive_definite,
    build_ranking_dataset,
    pairwise_accuracy,
    train_ranking_model,
)

from .instances import Instance, replay
from .preferences import CandidateOutcome, Preference
from .selection import (
    Selection,
    _closure,
    assert_feasible,
    cost_of,
    mandatory_closure,
    retention_context,
)

BENCH_VERSION = "dfl-bench-1"


class FeatureCache:
    def __init__(self, instances: Sequence[Instance]) -> None:
        self._instances = {i.instance_id: i for i in instances}
        self._cache: dict[tuple[str, tuple[str, ...]], dict[str, tuple[float, ...]]] = {}

    def get(self, instance_id: str, disabled: Sequence[str] = ()) -> dict[str, tuple[float, ...]]:
        key = (instance_id, tuple(disabled))
        if key not in self._cache:
            instance = self._instances[instance_id]
            built = replay(instance)
            self._cache[key] = extract_features(retention_context(built, instance), disabled)
        return self._cache[key]


def ranking_dataset(
    cache: FeatureCache, preferences: Sequence[Preference], disabled: Sequence[str] = ()
):
    ids = sorted({p.instance_id for p in preferences})
    features = {i: cache.get(i, disabled) for i in ids}
    return build_ranking_dataset(
        features,
        [(p.instance_id, sorted(p.positive), sorted(p.negative), p.weight) for p in preferences],
    )


@dataclass
class TrainedMethod:
    name: str
    predictor: LearnedUtilityPredictor
    model: RankingModel
    train_seconds: float
    detail: dict
    disabled: tuple[str, ...] = ()


def _wrap(name, model, disabled, seconds, detail) -> TrainedMethod:
    digest = model_to_artifact(model, {"name": name})["digest"]
    predictor = LearnedUtilityPredictor(
        model,
        predictor_id=name,
        version=BENCH_VERSION,
        artifact_digest=digest,
        disabled_groups=disabled,
    )
    return TrainedMethod(name, predictor, model, seconds, detail, tuple(disabled))


def train_ranker(
    name: str,
    model_type: str,
    cache: FeatureCache,
    train_prefs: Sequence[Preference],
    val_prefs: Sequence[Preference],
    *,
    disabled: Sequence[str] = (),
    seed: int = 0,
    tau: float = 1.0,
) -> TrainedMethod:
    train = ranking_dataset(cache, train_prefs, disabled)
    validation = ranking_dataset(cache, val_prefs, disabled) if val_prefs else None
    grid = (
        [{"l2": value} for value in (1e-3, 1e-2, 1e-1, 1.0)]
        if model_type == "linear"
        else [{"l2": l2, "hidden_units": h} for h in (8, 16) for l2 in (1e-3, 1e-2)]
    )
    best = None
    started = time.perf_counter()
    for params in grid:
        config = RankingTrainingConfig(
            model_type=model_type,
            tau=tau,
            seed=seed,
            max_iterations=60 if model_type == "linear" else 120,
            learning_rate=0.03,
            patience=20,
            **{"hidden_units": 8, **params},
        )
        model, report = train_ranking_model(
            train, config, feature_dim=len(FEATURE_NAMES), validation=validation
        )
        score = (
            pairwise_accuracy(model, validation)
            if validation is not None and validation.pairs
            else report.train_accuracy
        )
        if best is None or score > best[0]:
            best = (score, model, config, report)
    seconds = time.perf_counter() - started
    assert best is not None
    score, model, config, report = best
    detail = {
        "validation_pair_accuracy": score,
        "train_pair_accuracy": report.train_accuracy,
        "config": config.model_dump(),
        "iterations": report.iterations,
        "converged": report.converged,
        "n_train_pairs": len(train.pairs),
    }
    return _wrap(name, model, tuple(disabled), seconds, detail)


def train_set_regression(
    name: str,
    cache: FeatureCache,
    train_outcomes: Sequence[CandidateOutcome],
    val_outcomes: Sequence[CandidateOutcome],
    *,
    disabled: Sequence[str] = (),
) -> TrainedMethod:
    """Ridge regression of a set's mean measured outcome on the sum of its episode features."""

    def design(outcomes):
        rows, targets = [], []
        for outcome in outcomes:
            features = cache.get(outcome.instance_id, disabled)
            row = [0.0] * len(FEATURE_NAMES)
            for episode_id in outcome.selection:
                for k, value in enumerate(features[episode_id]):
                    row[k] += value
            rows.append(row)
            targets.append(outcome.mean_outcome)
        return rows, targets

    started = time.perf_counter()
    rows, targets = design(train_outcomes)
    val_rows, val_targets = design(val_outcomes)
    d = len(FEATURE_NAMES)
    best = None
    for l2 in (0.01, 0.1, 1.0, 10.0):
        gram = [[sum(r[i] * r[j] for r in rows) for j in range(d)] for i in range(d)]
        for i in range(d):
            gram[i][i] += l2
        rhs = [sum(r[i] * y for r, y in zip(rows, targets, strict=True)) for i in range(d)]
        theta = _solve_positive_definite(gram, rhs, l2)
        evaluation = val_rows or rows
        reference = val_targets or targets
        mse = sum(
            (sum(t * x for t, x in zip(theta, r, strict=True)) - y) ** 2
            for r, y in zip(evaluation, reference, strict=True)
        ) / len(evaluation)
        if best is None or mse < best[0]:
            best = (mse, theta, l2)
    seconds = time.perf_counter() - started
    assert best is not None
    return _wrap(
        name,
        LinearRankingModel(best[1]),
        tuple(disabled),
        seconds,
        {"validation_mse": best[0], "l2": best[2], "n_rows": len(rows)},
    )


def top_k_with_repair(
    instance: Instance, method: TrainedMethod, source: str = "topk-repair"
) -> Selection:
    """Rank episodes by predicted utility; add each (with its prerequisites) while it fits."""

    built = replay(instance)
    started = time.perf_counter()
    scores = method.predictor.predict(retention_context(built, instance))
    chosen = set(mandatory_closure(built))
    for episode_id in sorted(scores, key=lambda i: (-scores[i], i)):
        if scores[episode_id] <= 0 and episode_id not in chosen:
            continue
        addition = _closure(built, {episode_id}) - chosen
        if addition and cost_of(built, chosen | addition) <= instance.budget:
            chosen |= addition
    elapsed = (time.perf_counter() - started) * 1000.0
    ids = frozenset(chosen)
    assert_feasible(instance, built, ids, source)
    return Selection(ids, source, latency_ms=elapsed)


def pair_accuracy_for_scores(
    scorer: Mapping[str, Mapping[str, float]], preferences: Sequence[Preference]
) -> float:
    if not preferences:
        return float("nan")
    total = 0.0
    for p in preferences:
        values = scorer[p.instance_id]
        margin = sum(values[i] for i in p.positive) - sum(values[i] for i in p.negative)
        total += 1.0 if margin > 0 else 0.5 if margin == 0 else 0.0
    return total / len(preferences)
