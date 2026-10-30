# Copyright (c) 2026 David Michael Indraputra

"""Pairwise logistic ranking loss, its derivatives, and explicit trainers for utility models."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from .utility_models import LinearRankingModel, MlpRankingModel, RankingModel, require_finite


@dataclass(frozen=True)
class RankingPair:
    """One preference over item-index tuples: the ``plus`` side beats the ``minus`` side."""

    plus: tuple[int, ...]
    minus: tuple[int, ...]
    weight: float


@dataclass(frozen=True)
class RankingDataset:
    items: tuple[tuple[float, ...], ...]
    pairs: tuple[RankingPair, ...]
    item_keys: tuple[str, ...] = field(default=())


class RankingTrainingConfig(StrictModel):
    model_type: Literal["linear", "mlp"] = "linear"
    tau: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    l2: float = Field(default=1e-2, ge=0, allow_inf_nan=False)
    seed: int = 0
    max_iterations: int = Field(default=100, ge=1)
    tolerance: float = Field(default=1e-9, gt=0)
    learning_rate: float = Field(default=0.01, gt=0)
    hidden_units: int = Field(default=8, ge=1)
    patience: int = Field(default=25, ge=1)


@dataclass(frozen=True)
class TrainingReport:
    iterations: int
    final_loss: float
    gradient_norm: float
    train_accuracy: float
    validation_accuracy: float | None
    converged: bool


def build_ranking_dataset(
    features: Mapping[str, Mapping[str, tuple[float, ...]]],
    preferences: Sequence[tuple[str, Sequence[str], Sequence[str], float]],
) -> RankingDataset:
    """Build a dataset from ``(snapshot_id, S+, S-, weight)`` preferences.

    Items common to both sets cancel in an additive model, so only the symmetric difference is kept.
    """

    index: dict[tuple[str, str], int] = {}
    items: list[tuple[float, ...]] = []
    keys: list[str] = []
    pairs: list[RankingPair] = []
    for snapshot_id, positive, negative, weight in preferences:
        if snapshot_id not in features:
            raise ValueError(f'Preference references unknown snapshot "{snapshot_id}".')
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(
                f'Preference on snapshot "{snapshot_id}" has weight {weight!r}; weights must '
                "be finite and positive."
            )
        plus_only = sorted(set(positive) - set(negative))
        minus_only = sorted(set(negative) - set(positive))
        if not plus_only and not minus_only:
            continue
        sides: list[tuple[int, ...]] = []
        for side in (plus_only, minus_only):
            indices = []
            for episode_id in side:
                if episode_id not in features[snapshot_id]:
                    raise ValueError(
                        f'Preference on snapshot "{snapshot_id}" names episode "{episode_id}" '
                        "that has no features."
                    )
                key = (snapshot_id, episode_id)
                if key not in index:
                    index[key] = len(items)
                    items.append(features[snapshot_id][episode_id])
                    keys.append(f"{snapshot_id}/{episode_id}")
                indices.append(index[key])
            sides.append(tuple(indices))
        pairs.append(RankingPair(sides[0], sides[1], float(weight)))
    return RankingDataset(tuple(items), tuple(pairs), tuple(keys))


def softplus(value: float) -> float:
    return max(value, 0.0) + math.log1p(math.exp(-abs(value)))


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def pair_margins(model: RankingModel, dataset: RankingDataset) -> list[float]:
    scores = [model.score(item) for item in dataset.items]
    return [
        sum(scores[i] for i in pair.plus) - sum(scores[i] for i in pair.minus)
        for pair in dataset.pairs
    ]


def ranking_loss(model: RankingModel, dataset: RankingDataset, *, tau: float, l2: float) -> float:
    margins = pair_margins(model, dataset)
    data_term = sum(
        pair.weight * softplus(-margin / tau)
        for pair, margin in zip(dataset.pairs, margins, strict=True)
    )
    return data_term + l2 * sum(value * value for value in model.parameters)


def ranking_gradient(
    model: RankingModel, dataset: RankingDataset, *, tau: float, l2: float
) -> list[float]:
    margins = pair_margins(model, dataset)
    item_coefficients = [0.0] * len(dataset.items)
    for pair, margin in zip(dataset.pairs, margins, strict=True):
        coefficient = -pair.weight * sigmoid(-margin / tau) / tau
        for item in pair.plus:
            item_coefficients[item] += coefficient
        for item in pair.minus:
            item_coefficients[item] -= coefficient
    gradient = [2.0 * l2 * value for value in model.parameters]
    for features, coefficient in zip(dataset.items, item_coefficients, strict=True):
        if coefficient:
            model.accumulate_gradient(features, coefficient, gradient)
    return gradient


def linear_ranking_hessian(
    model: LinearRankingModel, dataset: RankingDataset, *, tau: float, l2: float
) -> list[list[float]]:
    d = model.feature_dim
    hessian = [[2.0 * l2 if i == j else 0.0 for j in range(d)] for i in range(d)]
    margins = pair_margins(model, dataset)
    for pair, margin in zip(dataset.pairs, margins, strict=True):
        delta = _feature_difference(dataset, pair, d)
        curvature = pair.weight * sigmoid(margin / tau) * sigmoid(-margin / tau) / (tau * tau)
        for i in range(d):
            if delta[i]:
                for j in range(d):
                    hessian[i][j] += curvature * delta[i] * delta[j]
    return hessian


def pairwise_accuracy(model: RankingModel, dataset: RankingDataset) -> float:
    if not dataset.pairs:
        return float("nan")
    score = 0.0
    for margin in pair_margins(model, dataset):
        score += 1.0 if margin > 0 else 0.5 if margin == 0 else 0.0
    return score / len(dataset.pairs)


def train_ranking_model(
    train: RankingDataset,
    config: RankingTrainingConfig,
    *,
    feature_dim: int,
    validation: RankingDataset | None = None,
) -> tuple[RankingModel, TrainingReport]:
    """Fit a utility model. The only training entry point; nothing calls it implicitly."""

    if not train.pairs:
        raise ValueError("Training needs at least one preference pair; the dataset has none.")
    if config.model_type == "linear":
        return _train_linear(train, config, feature_dim, validation)
    return _train_mlp(train, config, feature_dim, validation)


def _train_linear(
    train: RankingDataset,
    config: RankingTrainingConfig,
    feature_dim: int,
    validation: RankingDataset | None,
) -> tuple[RankingModel, TrainingReport]:
    model = LinearRankingModel([0.0] * feature_dim)
    loss = ranking_loss(model, train, tau=config.tau, l2=config.l2)
    gradient = ranking_gradient(model, train, tau=config.tau, l2=config.l2)
    iterations = 0
    converged = False
    while iterations < config.max_iterations:
        if math.sqrt(sum(g * g for g in gradient)) <= config.tolerance:
            converged = True
            break
        iterations += 1
        hessian = linear_ranking_hessian(model, train, tau=config.tau, l2=config.l2)
        step = _solve_positive_definite(hessian, gradient, config.l2)
        directional = sum(g * s for g, s in zip(gradient, step, strict=True))
        parameters = model.parameters
        scale = 1.0
        while True:
            candidate = model.with_parameters(
                [p - scale * s for p, s in zip(parameters, step, strict=True)]
            )
            candidate_loss = ranking_loss(candidate, train, tau=config.tau, l2=config.l2)
            if candidate_loss <= loss - 1e-4 * scale * directional or scale < 1e-12:
                break
            scale *= 0.5
        if candidate_loss > loss:
            converged = True
            break
        model, loss = candidate, candidate_loss
        gradient = ranking_gradient(model, train, tau=config.tau, l2=config.l2)
    require_finite(model.parameters, "trained linear parameters")
    return model, _report(model, train, validation, loss, gradient, iterations, converged)


def _train_mlp(
    train: RankingDataset,
    config: RankingTrainingConfig,
    feature_dim: int,
    validation: RankingDataset | None,
) -> tuple[RankingModel, TrainingReport]:
    model = MlpRankingModel.initialize(feature_dim, config.hidden_units, config.seed)
    first = [0.0] * len(model.parameters)
    second = [0.0] * len(model.parameters)
    best_model = model
    best_score = _selection_loss(model, train, validation, config)
    stale = 0
    iterations = 0
    converged = False
    beta1, beta2, epsilon = 0.9, 0.999, 1e-8
    while iterations < config.max_iterations:
        iterations += 1
        gradient = ranking_gradient(model, train, tau=config.tau, l2=config.l2)
        parameters = model.parameters
        for index, value in enumerate(gradient):
            first[index] = beta1 * first[index] + (1 - beta1) * value
            second[index] = beta2 * second[index] + (1 - beta2) * value * value
            corrected_first = first[index] / (1 - beta1**iterations)
            corrected_second = second[index] / (1 - beta2**iterations)
            parameters[index] -= (
                config.learning_rate * corrected_first / (math.sqrt(corrected_second) + epsilon)
            )
        model = model.with_parameters(parameters)
        require_finite(model.parameters, f"MLP parameters at iteration {iterations}")
        score = _selection_loss(model, train, validation, config)
        if score < best_score - config.tolerance:
            best_model, best_score, stale = model, score, 0
        else:
            stale += 1
            if stale >= config.patience:
                converged = True
                break
    gradient = ranking_gradient(best_model, train, tau=config.tau, l2=config.l2)
    loss = ranking_loss(best_model, train, tau=config.tau, l2=config.l2)
    return best_model, _report(best_model, train, validation, loss, gradient, iterations, converged)


def _selection_loss(
    model: RankingModel,
    train: RankingDataset,
    validation: RankingDataset | None,
    config: RankingTrainingConfig,
) -> float:
    target = validation if validation is not None and validation.pairs else train
    return ranking_loss(model, target, tau=config.tau, l2=config.l2)


def _report(
    model: RankingModel,
    train: RankingDataset,
    validation: RankingDataset | None,
    loss: float,
    gradient: list[float],
    iterations: int,
    converged: bool,
) -> TrainingReport:
    return TrainingReport(
        iterations=iterations,
        final_loss=loss,
        gradient_norm=math.sqrt(sum(g * g for g in gradient)),
        train_accuracy=pairwise_accuracy(model, train),
        validation_accuracy=(
            pairwise_accuracy(model, validation)
            if validation is not None and validation.pairs
            else None
        ),
        converged=converged,
    )


def _feature_difference(dataset: RankingDataset, pair: RankingPair, dimension: int) -> list[float]:
    delta = [0.0] * dimension
    for item in pair.plus:
        for k, value in enumerate(dataset.items[item]):
            delta[k] += value
    for item in pair.minus:
        for k, value in enumerate(dataset.items[item]):
            delta[k] -= value
    return delta


def _solve_positive_definite(matrix: list[list[float]], rhs: list[float], l2: float) -> list[float]:
    n = len(rhs)
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            total = matrix[i][j] - sum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                if total <= 1e-14:
                    raise AgentSdkError(
                        "UTILITY_TRAINING_NOT_STRONGLY_CONVEX",
                        f"The Hessian of the linear ranking objective is not positive definite "
                        f"(pivot {total:.3e} at row {i}) with l2={l2}; set l2 > 0 or supply "
                        "preference pairs whose feature differences span every feature.",
                        {"row": i, "l2": l2},
                    )
                lower[i][j] = math.sqrt(total)
            else:
                lower[i][j] = total / lower[j][j]
    forward = [0.0] * n
    for i in range(n):
        forward[i] = (rhs[i] - sum(lower[i][k] * forward[k] for k in range(i))) / lower[i][i]
    solution = [0.0] * n
    for i in reversed(range(n)):
        solution[i] = (
            forward[i] - sum(lower[k][i] * solution[k] for k in range(i + 1, n))
        ) / lower[i][i]
    return solution
