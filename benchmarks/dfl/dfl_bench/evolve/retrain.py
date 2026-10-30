"""Train a candidate utility model from logged outcomes. An explicit act, never run implicitly."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nailong_agent_sdk.memory.utility_features_v2 import FEATURE_SCHEMAS
from nailong_agent_sdk.memory.utility_models import RankingModel, save_utility_model
from nailong_agent_sdk.memory.utility_objectives import (
    OBJECTIVES,
    ObjectiveConfig,
    build_objective_dataset,
    effect_weights,
    train_objective,
)
from nailong_agent_sdk.memory.utility_training import (
    RankingTrainingConfig,
    build_ranking_dataset,
    pairwise_accuracy,
    train_ranking_model,
)

from ..preferences import PreferencePolicy, build_preferences
from .dataset import TaskRecord, dataset_digest, split_of


@dataclass
class Candidate:
    model: RankingModel
    training: dict[str, Any] = field(default_factory=dict)


def _pairs(tasks: list[TaskRecord], policy: PreferencePolicy):
    outcomes = [o for t in tasks for o in t.outcomes]
    return build_preferences(outcomes, policy) if outcomes else []


def _dataset(tasks: list[TaskRecord], preferences):
    features = {t.task_digest: t.features for t in tasks}
    return build_ranking_dataset(
        features,
        [(p.instance_id, sorted(p.positive), sorted(p.negative), p.weight) for p in preferences],
    )


def _objective_data(
    tasks: list[TaskRecord], objective: str, policy: PreferencePolicy, gamma: float
):
    features = {t.task_digest: t.features for t in tasks}
    groups = {t.task_digest: t.task_group for t in tasks}
    if objective in ("pairwise", "regret-pairwise"):
        preferences = _pairs(tasks, policy)
        weights = (
            effect_weights(
                [p.effect for p in preferences],
                [p.weight for p in preferences],
                gamma=gamma,
            )
            if objective == "regret-pairwise" and preferences
            else [p.weight for p in preferences]
        )
        pairs = [
            (p.instance_id, sorted(p.positive), sorted(p.negative), w)
            for p, w in zip(preferences, weights, strict=True)
        ]
        return build_objective_dataset(features, groups, pairs=pairs)
    pools = [
        (t.task_digest, [o for o in t.outcomes if len(o.outcomes) >= policy.min_trials])
        for t in tasks
    ]
    if objective == "listwise":
        lists = [
            (d, [sorted(o.selection) for o in pool], [o.mean_outcome for o in pool], 1.0)
            for d, pool in pools
            if len(pool) >= 2
            and max(o.mean_outcome for o in pool) - min(o.mean_outcome for o in pool)
            >= policy.min_effect
        ]
        return build_objective_dataset(features, groups, lists=lists)
    points = [
        (d, sorted(o.selection), o.mean_outcome, 1.0 / len(pool)) for d, pool in pools for o in pool
    ]
    return build_objective_dataset(features, groups, points=points)


def retrain(
    tasks: list[TaskRecord],
    *,
    model_type: str = "linear",
    seed: int = 0,
    preference_policy: PreferencePolicy | None = None,
    l2_grid: tuple[float, ...] = (1e-3, 1e-2, 1e-1, 1.0),
    objective: str = "pairwise",
    group_dro: bool = False,
    gamma: float = 1.0,
) -> Candidate:
    if objective not in OBJECTIVES:
        raise ValueError(f"Unknown objective {objective!r}; expected one of {OBJECTIVES}.")
    if objective != "pairwise" or group_dro:
        return _retrain_objective(
            tasks, objective, model_type, seed, preference_policy, l2_grid, group_dro, gamma
        )
    policy = preference_policy or PreferencePolicy(min_trials=1)
    train_tasks = [t for t in tasks if split_of(t.task_digest) == "train"]
    validation_tasks = [t for t in tasks if split_of(t.task_digest) == "validation"]
    schema = tasks[0].feature_schema if tasks else ""
    feature_names = FEATURE_SCHEMAS[schema][0] if schema in FEATURE_SCHEMAS else ()
    train_pairs = _pairs(train_tasks, policy)
    if not train_pairs:
        raise ValueError(
            f"No preference pairs in the {len(train_tasks)} training tasks: retraining needs "
            "decisions that differ within a task and have outcomes (a task logged under a single "
            "retention set teaches nothing). Log shadow or exploratory decisions with outcomes."
        )
    validation_pairs = _pairs(validation_tasks, policy)
    train = _dataset(train_tasks, train_pairs)
    validation = _dataset(validation_tasks, validation_pairs) if validation_pairs else None
    best = None
    grid = l2_grid if model_type == "linear" else (l2_grid[1],)
    for l2 in grid:
        config = RankingTrainingConfig(
            model_type=model_type,
            seed=seed,
            l2=l2,
            max_iterations=60 if model_type == "linear" else 120,
            learning_rate=0.03,
            patience=20,
        )
        model, report = train_ranking_model(
            train, config, feature_dim=len(feature_names), validation=validation
        )
        score = (
            pairwise_accuracy(model, validation)
            if validation is not None and validation.pairs
            else report.train_accuracy
        )
        if best is None or score > best[0]:
            best = (score, model, config, report)
    assert best is not None
    score, model, config, report = best
    return Candidate(
        model=model,
        training={
            "model_type": model_type,
            "objective": "pairwise",
            "group_dro": False,
            "feature_schema_version": schema,
            "seed": seed,
            "l2": config.l2,
            "tasks": {"train": len(train_tasks), "validation": len(validation_tasks)},
            "train_pairs": len(train_pairs),
            "validation_pairs": len(validation_pairs),
            "selection_accuracy": score,
            "train_accuracy": report.train_accuracy,
            "converged": report.converged,
            "dataset_digest": dataset_digest(train_tasks + validation_tasks),
            "evidence_kind": train_tasks[0].outcomes[0].evidence_kind
            if train_tasks and train_tasks[0].outcomes
            else "unknown",
            "disabled_feature_groups": [],
        },
    )


def write_candidate(candidate: Candidate, path: Path) -> str:
    return save_utility_model(path, candidate.model, candidate.training)


def _retrain_objective(
    tasks, objective, model_type, seed, preference_policy, l2_grid, group_dro, gamma
) -> Candidate:
    policy = preference_policy or PreferencePolicy(min_trials=1)
    schema = tasks[0].feature_schema if tasks else ""
    if schema not in FEATURE_SCHEMAS:
        raise ValueError(
            f'Logged feature schema "{schema}" is unknown; known: {sorted(FEATURE_SCHEMAS)}.'
        )
    train_tasks = [t for t in tasks if split_of(t.task_digest) == "train"]
    validation_tasks = [t for t in tasks if split_of(t.task_digest) == "validation"]
    train = _objective_data(train_tasks, objective, policy, gamma)
    if not train.terms:
        raise ValueError(
            f"No {objective} terms in the {len(train_tasks)} training tasks: retraining needs "
            "decisions that differ within a task and have outcomes. Log shadow or exploratory "
            "decisions with outcomes."
        )
    validation = _objective_data(validation_tasks, objective, policy, gamma)
    best = None
    grid = l2_grid if model_type == "linear" else (l2_grid[1],)
    for l2 in grid:
        config = ObjectiveConfig(
            objective=objective,
            model_type=model_type,
            seed=seed,
            l2=l2,
            group_dro=group_dro,
            max_iterations=300,
            learning_rate=0.02,
            patience=60,
        )
        model, report = train_objective(
            train,
            config,
            feature_dim=len(FEATURE_SCHEMAS[schema][0]),
            validation=validation if validation.terms else None,
        )
        selection = report.training.validation_accuracy
        if selection is None or selection != selection:
            selection = report.training.train_accuracy
        if best is None or selection > best[0]:
            best = (selection, model, config, report)
    assert best is not None
    score, model, config, report = best
    return Candidate(
        model=model,
        training={
            "model_type": model_type,
            "objective": objective,
            "group_dro": group_dro,
            "gamma": gamma,
            "feature_schema_version": schema,
            "seed": seed,
            "l2": config.l2,
            "tasks": {"train": len(train_tasks), "validation": len(validation_tasks)},
            "train_terms": len(train.terms),
            "validation_terms": len(validation.terms),
            "selection_accuracy": score,
            "train_accuracy": report.training.train_accuracy,
            "converged": report.training.converged,
            "group_weights": report.group_weights,
            "dataset_digest": dataset_digest(train_tasks + validation_tasks),
            "evidence_kind": train_tasks[0].outcomes[0].evidence_kind
            if train_tasks and train_tasks[0].outcomes
            else "unknown",
            "disabled_feature_groups": [],
        },
    )
