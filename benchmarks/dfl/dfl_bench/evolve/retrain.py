"""Train a candidate utility model from logged outcomes. An explicit act, never run implicitly."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nailong_agent_sdk.memory.utility_features import FEATURE_NAMES
from nailong_agent_sdk.memory.utility_models import RankingModel, save_utility_model
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


def retrain(
    tasks: list[TaskRecord],
    *,
    model_type: str = "linear",
    seed: int = 0,
    preference_policy: PreferencePolicy | None = None,
    l2_grid: tuple[float, ...] = (1e-3, 1e-2, 1e-1, 1.0),
) -> Candidate:
    policy = preference_policy or PreferencePolicy(min_trials=1)
    train_tasks = [t for t in tasks if split_of(t.task_digest) == "train"]
    validation_tasks = [t for t in tasks if split_of(t.task_digest) == "validation"]
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
            train, config, feature_dim=len(FEATURE_NAMES), validation=validation
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
