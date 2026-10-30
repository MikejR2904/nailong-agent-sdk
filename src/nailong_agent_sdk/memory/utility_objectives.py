# Copyright (c) 2026 David Michael Indraputra

"""Generalized training objectives for additive episode-utility models.

Every objective scores a candidate selection ``S`` by ``U(S) = sum_i f(h_i)``. A term compares or
fits the set utilities of one task: a pair (logistic), a list (cross-entropy against a softmax of
measured outcomes) or a regression point. Terms carry an environment label so the same data can be
trained by pooled empirical risk minimisation or by Group-DRO. The PCKP solver is never
differentiated through.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from ..foundations.contracts import StrictModel
from .utility_models import LinearRankingModel, MlpRankingModel, RankingModel, require_finite
from .utility_training import TrainingReport, sigmoid, softplus

TermKind = Literal["pair", "list", "reg"]
OBJECTIVES: tuple[str, ...] = ("pairwise", "regret-pairwise", "listwise", "regression")


@dataclass(frozen=True)
class ObjectiveTerm:
    kind: TermKind
    candidates: tuple[tuple[int, ...], ...]
    targets: tuple[float, ...]
    weight: float
    group: str


@dataclass(frozen=True)
class ObjectiveDataset:
    items: tuple[tuple[float, ...], ...]
    terms: tuple[ObjectiveTerm, ...]
    item_keys: tuple[str, ...] = field(default=())

    def groups(self) -> tuple[str, ...]:
        return tuple(sorted({term.group for term in self.terms}))


class ObjectiveConfig(StrictModel):
    objective: Literal["pairwise", "regret-pairwise", "listwise", "regression"] = "pairwise"
    model_type: Literal["linear", "mlp"] = "linear"
    tau: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    tau_quality: float = Field(default=0.1, gt=0, allow_inf_nan=False)
    l2: float = Field(default=1e-2, ge=0, allow_inf_nan=False)
    seed: int = 0
    max_iterations: int = Field(default=300, ge=1)
    tolerance: float = Field(default=1e-9, gt=0)
    learning_rate: float = Field(default=0.05, gt=0)
    hidden_units: int = Field(default=8, ge=1)
    patience: int = Field(default=40, ge=1)
    group_dro: bool = False
    dro_step: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    keep_initial: bool = False
    prior: tuple[float, ...] | None = None


@dataclass(frozen=True)
class ObjectiveReport:
    training: TrainingReport
    group_losses: dict[str, float]
    group_weights: dict[str, float]
    objective: str
    group_dro: bool


def effect_weights(
    effects: Sequence[float],
    confidences: Sequence[float],
    *,
    gamma: float,
    normalize: bool = True,
    cap_ratio: float | None = 5.0,
) -> list[float]:
    """Weights ``c * |dQ|**gamma``, optionally capped at ``cap_ratio`` x the median and rescaled
    to mean one, so a few extreme comparisons cannot dominate."""

    if len(effects) != len(confidences):
        raise ValueError(
            f"effects has {len(effects)} entries but confidences has {len(confidences)}."
        )
    if gamma < 0 or not math.isfinite(gamma):
        raise ValueError(f"gamma must be a finite number >= 0, got {gamma!r}.")
    raw = []
    for effect, confidence in zip(effects, confidences, strict=True):
        if not math.isfinite(effect) or not math.isfinite(confidence) or confidence <= 0:
            raise ValueError(
                f"effect {effect!r} and confidence {confidence!r} must be finite, confidence > 0."
            )
        raw.append(confidence * abs(effect) ** gamma)
    if not raw:
        return []
    if cap_ratio is not None:
        ceiling = cap_ratio * statistics.median(raw)
        if ceiling > 0:
            raw = [min(value, ceiling) for value in raw]
    if any(value <= 0 for value in raw):
        raise ValueError("A preference has zero effect; omit indistinguishable outcomes instead.")
    if normalize:
        mean = sum(raw) / len(raw)
        raw = [value / mean for value in raw]
    return raw


def build_objective_dataset(
    features: Mapping[str, Mapping[str, tuple[float, ...]]],
    groups: Mapping[str, str],
    *,
    pairs: Sequence[tuple[str, Sequence[str], Sequence[str], float]] = (),
    lists: Sequence[tuple[str, Sequence[Sequence[str]], Sequence[float], float]] = (),
    points: Sequence[tuple[str, Sequence[str], float, float]] = (),
) -> ObjectiveDataset:
    """Assemble terms from ``(snapshot, S+, S-, w)``, ``(snapshot, [S...], [Q...], w)`` and
    ``(snapshot, S, Q, w)`` rows. Items common to every candidate of a pair or list cancel and are
    dropped; regression keeps whole sets."""

    index: dict[tuple[str, str], int] = {}
    items: list[tuple[float, ...]] = []
    keys: list[str] = []

    def locate(snapshot: str, episode_id: str) -> int:
        if snapshot not in features:
            raise ValueError(f'Row references unknown snapshot "{snapshot}".')
        if snapshot not in groups:
            raise ValueError(f'Snapshot "{snapshot}" has no environment label.')
        if episode_id not in features[snapshot]:
            raise ValueError(
                f'Snapshot "{snapshot}" names episode "{episode_id}" with no features.'
            )
        key = (snapshot, episode_id)
        if key not in index:
            index[key] = len(items)
            items.append(features[snapshot][episode_id])
            keys.append(f"{snapshot}/{episode_id}")
        return index[key]

    def positive(weight: float, label: str) -> float:
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"{label} has weight {weight!r}; weights must be finite and positive.")
        return float(weight)

    terms: list[ObjectiveTerm] = []
    for snapshot, plus, minus, weight in pairs:
        plus_only, minus_only = sorted(set(plus) - set(minus)), sorted(set(minus) - set(plus))
        if not plus_only and not minus_only:
            continue
        terms.append(
            ObjectiveTerm(
                "pair",
                (
                    tuple(locate(snapshot, i) for i in plus_only),
                    tuple(locate(snapshot, i) for i in minus_only),
                ),
                (),
                positive(weight, f'Pair on "{snapshot}"'),
                groups[snapshot],
            )
        )
    for snapshot, candidates, qualities, weight in lists:
        if len(candidates) != len(qualities) or len(candidates) < 2:
            raise ValueError(
                f'List on "{snapshot}" needs >= 2 candidates with one quality each; got '
                f"{len(candidates)} candidates and {len(qualities)} qualities."
            )
        shared = set.intersection(*(set(c) for c in candidates))
        terms.append(
            ObjectiveTerm(
                "list",
                tuple(
                    tuple(locate(snapshot, i) for i in sorted(set(c) - shared)) for c in candidates
                ),
                tuple(float(q) for q in qualities),
                positive(weight, f'List on "{snapshot}"'),
                groups[snapshot],
            )
        )
    for snapshot, selection, quality, weight in points:
        terms.append(
            ObjectiveTerm(
                "reg",
                (tuple(locate(snapshot, i) for i in sorted(set(selection))),),
                (float(quality),),
                positive(weight, f'Point on "{snapshot}"'),
                groups[snapshot],
            )
        )
    return ObjectiveDataset(tuple(items), tuple(terms), tuple(keys))


def log_softmax(values: Sequence[float], temperature: float) -> list[float]:
    scaled = [value / temperature for value in values]
    peak = max(scaled)
    total = peak + math.log(sum(math.exp(value - peak) for value in scaled))
    return [value - total for value in scaled]


def _term_loss_and_slopes(
    term: ObjectiveTerm, utilities: list[float], config: ObjectiveConfig
) -> tuple[float, list[float]]:
    if term.kind == "pair":
        margin = utilities[0] - utilities[1]
        slope = -term.weight * sigmoid(-margin / config.tau) / config.tau
        return term.weight * softplus(-margin / config.tau), [slope, -slope]
    if term.kind == "list":
        target = [math.exp(v) for v in log_softmax(term.targets, config.tau_quality)]
        predicted = log_softmax(utilities, config.tau)
        loss = -sum(p * lq for p, lq in zip(target, predicted, strict=True))
        slopes = [
            term.weight * (math.exp(lq) - p) / config.tau
            for p, lq in zip(target, predicted, strict=True)
        ]
        return term.weight * loss, slopes
    residual = utilities[0] - term.targets[0]
    return term.weight * residual * residual, [2.0 * term.weight * residual]


def _evaluate(
    model: RankingModel,
    dataset: ObjectiveDataset,
    config: ObjectiveConfig,
    group_weights: Mapping[str, float] | None,
    *,
    want_gradient: bool,
) -> tuple[float, dict[str, float], list[float]]:
    scores = [model.score(item) for item in dataset.items]
    counts: dict[str, int] = defaultdict(int)
    for term in dataset.terms:
        counts[term.group] += 1
    total = len(dataset.terms)
    weights = (
        {g: counts[g] / total for g in counts} if group_weights is None else dict(group_weights)
    )
    group_loss: dict[str, float] = defaultdict(float)
    coefficients = [0.0] * len(dataset.items)
    for term in dataset.terms:
        utilities = [sum(scores[i] for i in candidate) for candidate in term.candidates]
        loss, slopes = _term_loss_and_slopes(term, utilities, config)
        group_loss[term.group] += loss / counts[term.group]
        if want_gradient:
            scale = weights[term.group] / counts[term.group]
            for candidate, slope in zip(term.candidates, slopes, strict=True):
                for item in candidate:
                    coefficients[item] += scale * slope
    center = config.prior or (0.0,) * len(model.parameters)
    if len(center) != len(model.parameters):
        raise ValueError(
            f"prior has {len(center)} entries but the model has {len(model.parameters)} parameters."
        )
    penalty = config.l2 * sum((v - c) ** 2 for v, c in zip(model.parameters, center, strict=True))
    objective = sum(weights[g] * group_loss[g] for g in group_loss) + penalty
    gradient = [2.0 * config.l2 * (v - c) for v, c in zip(model.parameters, center, strict=True)]
    if want_gradient:
        for features, coefficient in zip(dataset.items, coefficients, strict=True):
            if coefficient:
                model.accumulate_gradient(features, coefficient, gradient)
    return objective, dict(group_loss), gradient


def objective_loss(
    model: RankingModel,
    dataset: ObjectiveDataset,
    config: ObjectiveConfig,
    group_weights: Mapping[str, float] | None = None,
) -> float:
    return _evaluate(model, dataset, config, group_weights, want_gradient=False)[0]


def objective_group_losses(
    model: RankingModel, dataset: ObjectiveDataset, config: ObjectiveConfig
) -> dict[str, float]:
    return _evaluate(model, dataset, config, None, want_gradient=False)[1]


def objective_gradient(
    model: RankingModel,
    dataset: ObjectiveDataset,
    config: ObjectiveConfig,
    group_weights: Mapping[str, float] | None = None,
) -> list[float]:
    return _evaluate(model, dataset, config, group_weights, want_gradient=True)[2]


def dro_update(
    weights: Mapping[str, float], group_losses: Mapping[str, float], step: float
) -> dict[str, float]:
    """Exponentiated-gradient ascent on the simplex: ``q_g <- q_g exp(step * L_g) / Z``."""

    peak = max(group_losses.values())
    raw = {g: weights[g] * math.exp(step * (group_losses[g] - peak)) for g in weights}
    total = sum(raw.values())
    return {g: value / total for g, value in raw.items()}


def term_accuracy(model: RankingModel, dataset: ObjectiveDataset) -> float:
    """Fraction of pair terms ordered correctly, or of list terms whose best-measured candidate
    gets the highest utility; nan when there are neither."""

    scores = [model.score(item) for item in dataset.items]
    correct, count = 0.0, 0
    for term in dataset.terms:
        if term.kind == "reg":
            continue
        utilities = [sum(scores[i] for i in c) for c in term.candidates]
        if term.kind == "pair":
            margin = utilities[0] - utilities[1]
            correct += 1.0 if margin > 0 else 0.5 if margin == 0 else 0.0
        else:
            top_target = max(term.targets)
            bests = {j for j, q in enumerate(term.targets) if q == top_target}
            top = max(utilities)
            winners = {j for j, u in enumerate(utilities) if u == top}
            correct += len(winners & bests) / len(winners)
        count += 1
    return correct / count if count else float("nan")


def train_objective(
    train: ObjectiveDataset,
    config: ObjectiveConfig,
    *,
    feature_dim: int,
    validation: ObjectiveDataset | None = None,
) -> tuple[RankingModel, ObjectiveReport]:
    """Full-batch Adam on the configured objective, with early stopping on validation loss.

    Linear models are convex under every objective here; Adam is used for both model types so one
    code path serves them, and ``gradient_norm`` in the report states how close it got.
    """

    if not train.terms:
        raise ValueError("Training needs at least one term; the dataset has none.")
    if config.prior is not None and config.model_type != "linear":
        raise ValueError("A prior centre is only defined for linear models.")
    model: RankingModel = (
        LinearRankingModel(list(config.prior) if config.prior else [0.0] * feature_dim)
        if config.model_type == "linear"
        else MlpRankingModel.initialize(feature_dim, config.hidden_units, config.seed)
    )
    groups = train.groups()
    weights = {g: 1.0 / len(groups) for g in groups}
    first = [0.0] * len(model.parameters)
    second = [0.0] * len(model.parameters)
    target = validation if validation is not None and validation.terms else train
    best_model = model
    best_score = objective_loss(model, target, config) if config.keep_initial else math.inf
    stale, iterations, converged = 0, 0, False
    beta1, beta2, epsilon = 0.9, 0.999, 1e-8
    while iterations < config.max_iterations:
        iterations += 1
        _, losses, gradient = _evaluate(
            model, train, config, weights if config.group_dro else None, want_gradient=True
        )
        if config.group_dro:
            weights = dro_update(weights, losses, config.dro_step)
        if math.sqrt(sum(g * g for g in gradient)) <= config.tolerance:
            converged = True
            break
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
        require_finite(model.parameters, f"parameters at iteration {iterations}")
        score = objective_loss(model, target, config)
        if score < best_score - config.tolerance:
            best_model, best_score, stale = model, score, 0
        else:
            stale += 1
            if stale >= config.patience:
                converged = True
                break
    final_loss, final_groups, final_gradient = _evaluate(
        best_model, train, config, weights if config.group_dro else None, want_gradient=True
    )
    report = TrainingReport(
        iterations=iterations,
        final_loss=final_loss,
        gradient_norm=math.sqrt(sum(g * g for g in final_gradient)),
        train_accuracy=term_accuracy(best_model, train),
        validation_accuracy=(
            term_accuracy(best_model, validation)
            if validation is not None and validation.terms
            else None
        ),
        converged=converged,
    )
    return best_model, ObjectiveReport(
        report, final_groups, weights, config.objective, config.group_dro
    )
