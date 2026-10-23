# Copyright (c) 2026 David Michael Indraputra

"""Opt-in learned utility coefficients for dependency-closed episode retention.

The predictor supplies only the additive objective coefficients. Budget, mandatory retention,
prerequisite closure and the optimality certificate stay with the exact PCKP solver.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import sha256_hex, strict_canonical_json
from .utility_features import FEATURE_SCHEMA_VERSION, RetentionContext
from .utility_features_v2 import FEATURE_SCHEMAS
from .utility_models import RankingModel, load_utility_model


class PredictorIdentity(StrictModel):
    predictor_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    feature_schema_version: str = Field(min_length=1)
    artifact_digest: str = Field(min_length=1)


class QuantizationPolicy(StrictModel):
    """Maps utilities to PCKP integers: ``max(floor, round(scale * clip(u, 0, ceiling)))``.

    ``floor`` defaults to 0 (clipped episodes are worth nothing and the solver prefers the cheaper
    of equal-utility selections, so a model that rates many episodes at or below zero leaves
    budget unspent). A positive ``floor`` makes every live episode worth at least that many
    quantized units, which breaks such ties toward using the budget.
    """

    scale: int = Field(default=1_000_000, ge=1)
    ceiling: float = Field(default=1_000.0, gt=0, allow_inf_nan=False)
    floor: int = Field(default=0, ge=0)


class EpisodeUtilityPredictor(Protocol):
    @property
    def identity(self) -> PredictorIdentity: ...

    def predict(self, context: RetentionContext) -> Mapping[str, float]: ...


class LearnedUtilityPredictor:
    """Scores live episodes with a trained, immutable ranking model."""

    def __init__(
        self,
        model: RankingModel,
        *,
        predictor_id: str,
        version: str,
        artifact_digest: str,
        disabled_groups: Sequence[str] = (),
        feature_schema: str = FEATURE_SCHEMA_VERSION,
    ) -> None:
        if feature_schema not in FEATURE_SCHEMAS:
            raise ValueError(
                f'Unknown feature schema "{feature_schema}"; known: {sorted(FEATURE_SCHEMAS)}.'
            )
        _, extractor = FEATURE_SCHEMAS[feature_schema]
        self._model = model
        self._disabled_groups = tuple(disabled_groups)
        self._extractor = extractor
        self._identity = PredictorIdentity(
            predictor_id=predictor_id,
            version=version,
            feature_schema_version=feature_schema,
            artifact_digest=artifact_digest,
        )

    @classmethod
    def from_artifact(
        cls, path: Path, *, predictor_id: str, version: str
    ) -> LearnedUtilityPredictor:
        loaded = load_utility_model(path)
        return cls(
            loaded.model,
            predictor_id=predictor_id,
            version=version,
            artifact_digest=loaded.digest,
            disabled_groups=tuple(loaded.training.get("disabled_feature_groups", ())),
            feature_schema=loaded.training.get("feature_schema_version", FEATURE_SCHEMA_VERSION),
        )

    @property
    def identity(self) -> PredictorIdentity:
        return self._identity

    def predict(self, context: RetentionContext) -> Mapping[str, float]:
        features = self._extractor(context, self._disabled_groups)
        return {episode_id: self._model.score(vector) for episode_id, vector in features.items()}


class FixedUtilityPredictor:
    """Serves per-episode utilities computed outside the SDK; it performs no inference."""

    def __init__(self, utilities: Mapping[str, float], *, predictor_id: str, version: str) -> None:
        table: dict[str, float] = {}
        for episode_id, value in utilities.items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(
                    f'Fixed utility for episode "{episode_id}" is {value!r}; expected a number.'
                )
            table[str(episode_id)] = float(value)
        self._table = table
        self._predictions = 0
        self._identity = PredictorIdentity(
            predictor_id=predictor_id,
            version=version,
            feature_schema_version="fixed-utility-table-v1",
            artifact_digest=sha256_hex(strict_canonical_json(dict(sorted(table.items())))),
        )

    @property
    def identity(self) -> PredictorIdentity:
        return self._identity

    @property
    def prediction_count(self) -> int:
        return self._predictions

    def predict(self, context: RetentionContext) -> Mapping[str, float]:
        self._predictions += 1
        return {
            record.id: self._table[record.id]
            for record in context.records
            if record.id in self._table
        }


@dataclass(frozen=True)
class LearnedCoefficients:
    identity: PredictorIdentity
    raw: dict[str, float]
    quantized: dict[str, int]
    clipped_below: list[str]
    clipped_above: list[str]
    policy: QuantizationPolicy


def quantize_utilities(
    raw: Mapping[str, float], policy: QuantizationPolicy
) -> tuple[dict[str, int], list[str], list[str]]:
    quantized: dict[str, int] = {}
    below: list[str] = []
    above: list[str] = []
    for episode_id in sorted(raw):
        value = raw[episode_id]
        if value < 0:
            below.append(episode_id)
            value = 0.0
        elif value > policy.ceiling:
            above.append(episode_id)
            value = policy.ceiling
        quantized[episode_id] = max(policy.floor, round(value * policy.scale))
    return quantized, below, above


def resolve_learned_coefficients(
    predictor: EpisodeUtilityPredictor,
    context: RetentionContext,
    policy: QuantizationPolicy,
) -> LearnedCoefficients:
    """Predict, validate and quantize; every failure names the predictor and the episode."""

    identity = predictor.identity
    try:
        predicted = dict(predictor.predict(context))
    except AgentSdkError:
        raise
    except Exception as error:
        raise AgentSdkError(
            "UTILITY_PREDICTOR_FAILED",
            f'Predictor "{identity.predictor_id}" version {identity.version} raised '
            f"{type(error).__name__}: {error}",
            {"predictor_id": identity.predictor_id},
        ) from error
    live = {record.id for record in context.records}
    missing = sorted(live - set(predicted))
    if missing:
        raise AgentSdkError(
            "UTILITY_PREDICTION_INCOMPLETE",
            f'Predictor "{identity.predictor_id}" returned no utility for live episodes '
            f"{missing[:5]}{' ...' if len(missing) > 5 else ''}.",
            {"predictor_id": identity.predictor_id, "missing_count": len(missing)},
        )
    raw: dict[str, float] = {}
    for episode_id in sorted(live):
        value = predicted[episode_id]
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise AgentSdkError(
                "UTILITY_PREDICTION_NONFINITE",
                f'Predictor "{identity.predictor_id}" returned {value!r} for episode '
                f'"{episode_id}"; utilities must be finite numbers.',
                {"predictor_id": identity.predictor_id, "episode_id": episode_id},
            )
        raw[episode_id] = float(value)
    quantized, below, above = quantize_utilities(raw, policy)
    return LearnedCoefficients(identity, raw, quantized, below, above, policy)
