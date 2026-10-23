# Copyright (c) 2026 David Michael Indraputra

"""Utility calibration, distribution-shift evidence and logged static fallback.

Everything here changes only the additive coefficients handed to the PCKP solver. Budget,
mandatory retention, prerequisite closure and the optimality certificate are untouched.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import sha256_hex, strict_canonical_json
from .episode_models import PaskCompactionPolicy
from .episode_scoring import _clamp_score, _provenance_weight
from .learned_retention import EpisodeUtilityPredictor, PredictorIdentity
from .utility_features import RetentionContext


def scale_normalize(utilities: Mapping[str, float]) -> dict[str, float]:
    """Divide by the largest magnitude. A positive rescaling only: PCKP is sensitive to additive
    offsets (they change how many items fit), so no centring is applied."""

    peak = max((abs(v) for v in utilities.values()), default=0.0)
    if peak == 0:
        return {k: 0.0 for k in utilities}
    return {k: v / peak for k, v in utilities.items()}


def mix_utilities(
    learned: Mapping[str, float], static: Mapping[str, float], alpha: float
) -> dict[str, float]:
    """``alpha * learned + (1 - alpha) * static`` after scale normalisation of each side."""

    if not 0.0 <= alpha <= 1.0 or not math.isfinite(alpha):
        raise ValueError(f"alpha must be in [0, 1], got {alpha!r}.")
    if set(learned) != set(static):
        raise ValueError(
            f"Learned and static utilities cover different episodes "
            f"({sorted(set(learned) ^ set(static))[:5]})."
        )
    left, right = scale_normalize(learned), scale_normalize(static)
    return {k: alpha * left[k] + (1.0 - alpha) * right[k] for k in learned}


class PolicyStaticPredictor:
    """The store's static PCKP utilities for a policy, as a predictor over a retention context."""

    def __init__(self, policy: PaskCompactionPolicy, *, version: str = "1") -> None:
        self._policy = policy
        self._identity = PredictorIdentity(
            predictor_id="static-policy",
            version=version,
            feature_schema_version="static-policy-v1",
            artifact_digest=sha256_hex(strict_canonical_json(policy.model_dump(mode="json"))),
        )

    @property
    def identity(self) -> PredictorIdentity:
        return self._identity

    def predict(self, context: RetentionContext) -> Mapping[str, float]:
        policy = self._policy
        records = {r.id: r for r in context.records}
        live = set(records)
        dependents = {
            i: len([c for c in r.depended_on_by if c in live]) for i, r in records.items()
        }
        max_dependents = max(dependents.values(), default=0)
        max_count = max((r.access_count for r in records.values()), default=0)
        max_sequence = max((r.last_access_sequence for r in records.values()), default=0)
        result: dict[str, float] = {}
        for episode_id, record in records.items():
            result[episode_id] = (
                policy.task_relevance_weight * _clamp_score(context.relevance[episode_id])
                + policy.dependency_centrality_weight
                * (dependents[episode_id] / max_dependents if max_dependents else 0.0)
                + policy.provenance_weight * _provenance_weight(record)
                + policy.recency_weight
                * (record.last_access_sequence / max_sequence if max_sequence else 0.0)
                + policy.frequency_weight * (record.access_count / max_count if max_count else 0.0)
            )
        return result


def static_prior_weights(policy: PaskCompactionPolicy, feature_names: Sequence[str]) -> list[float]:
    """The static PCKP utility written as a linear model over the feature columns.

    The static utility is linear in the schema's columns because provenance is
    ``0.40 + 0.25*exploratory + 0.35*substrate_backed`` (substrate-backed episodes are
    exploratory). A learned linear model regularised towards these weights equals the static
    policy at infinite regularisation.
    """

    weights = dict.fromkeys(feature_names, 0.0)
    weights["bias"] = policy.provenance_weight * 0.40
    weights["lexical_relevance"] = policy.task_relevance_weight
    weights["dependent_share"] = policy.dependency_centrality_weight
    weights["exploratory"] = policy.provenance_weight * 0.25
    weights["substrate_backed"] = policy.provenance_weight * 0.35
    weights["recency"] = policy.recency_weight
    weights["frequency"] = policy.frequency_weight
    missing = [name for name in weights if name not in feature_names]
    if missing:
        raise ValueError(f"Feature columns {missing} are required to express the static utility.")
    return [weights[name] for name in feature_names]


class FeatureShiftDetector:
    """Standardised distance of a context's mean feature vector from the training contexts.

    ``fit`` records, per feature, the mean and standard deviation of the per-context means over
    the training contexts. ``calibrate`` sets the alarm threshold from separate validation
    contexts, so the threshold is never fitted on the data that defined the reference.
    """

    def __init__(self) -> None:
        self._mean: list[float] = []
        self._scale: list[float] = []
        self.threshold: float | None = None

    @staticmethod
    def context_mean(features: Mapping[str, Sequence[float]]) -> list[float]:
        rows = list(features.values())
        return [sum(column) / len(rows) for column in zip(*rows, strict=True)]

    def fit(self, contexts: Sequence[Mapping[str, Sequence[float]]]) -> FeatureShiftDetector:
        if len(contexts) < 2:
            raise ValueError(f"Fitting needs at least 2 training contexts, got {len(contexts)}.")
        means = [self.context_mean(c) for c in contexts]
        self._mean = [statistics.fmean(col) for col in zip(*means, strict=True)]
        self._scale = [max(statistics.pstdev(col), 1e-6) for col in zip(*means, strict=True)]
        return self

    def score(self, features: Mapping[str, Sequence[float]]) -> float:
        if not self._mean:
            raise ValueError("The detector has not been fitted.")
        mean = self.context_mean(features)
        if len(mean) != len(self._mean):
            raise ValueError(
                f"Context has {len(mean)} features; detector was fit on {len(self._mean)}."
            )
        return math.sqrt(
            sum(((m - c) / s) ** 2 for m, c, s in zip(mean, self._mean, self._scale, strict=True))
            / len(mean)
        )

    def calibrate(
        self, contexts: Sequence[Mapping[str, Sequence[float]]], quantile: float = 0.95
    ) -> float:
        if not contexts:
            raise ValueError("Calibration needs at least one validation context.")
        scores = sorted(self.score(c) for c in contexts)
        position = min(len(scores) - 1, math.ceil(quantile * len(scores)) - 1)
        self.threshold = scores[max(position, 0)]
        return self.threshold


class FallbackPolicy(StrictModel):
    alpha: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    max_shift: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_clip_fraction: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    min_dispersion: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_disagreement: float | None = Field(default=None, ge=0, allow_inf_nan=False)


def dispersion(utilities: Mapping[str, float]) -> float:
    values = list(utilities.values())
    if len(values) < 2:
        return 0.0
    scale = max(abs(v) for v in values)
    return statistics.pstdev(values) / scale if scale else 0.0


def ensemble_disagreement(members: Sequence[Mapping[str, float]]) -> float:
    """Mean across episodes of the members' standard deviation, relative to the largest
    magnitude any member predicts."""

    if len(members) < 2:
        raise ValueError("Disagreement needs at least 2 ensemble members.")
    normalised = [scale_normalize(m) for m in members]
    return statistics.fmean(statistics.pstdev([m[i] for m in normalised]) for i in normalised[0])


class CalibratedUtilityPredictor:
    """Mixes a learned predictor with the static one and falls back to static when the evidence
    says the learned model is unreliable. Every decision is kept in ``last_audit``."""

    def __init__(
        self,
        learned: EpisodeUtilityPredictor,
        static: EpisodeUtilityPredictor,
        policy: FallbackPolicy,
        *,
        detector: FeatureShiftDetector | None = None,
        shift_features: Any = None,
        members: Sequence[EpisodeUtilityPredictor] = (),
        predictor_id: str = "calibrated",
        version: str = "1",
    ) -> None:
        if policy.max_shift is not None and (detector is None or shift_features is None):
            raise ValueError("max_shift needs both a fitted detector and shift_features.")
        if policy.max_disagreement is not None and len(members) < 2:
            raise ValueError("max_disagreement needs at least 2 ensemble members.")
        self._learned = learned
        self._static = static
        self._policy = policy
        self._detector = detector
        self._shift_features = shift_features
        self._members = tuple(members)
        self._identity = PredictorIdentity(
            predictor_id=predictor_id,
            version=version,
            feature_schema_version=learned.identity.feature_schema_version,
            artifact_digest=sha256_hex(
                strict_canonical_json(
                    {
                        "learned": learned.identity.artifact_digest,
                        "static": static.identity.artifact_digest,
                        "policy": policy.model_dump(mode="json"),
                        "threshold": detector.threshold if detector else None,
                    }
                )
            ),
        )
        self.last_audit: dict[str, Any] | None = None

    @property
    def identity(self) -> PredictorIdentity:
        return self._identity

    def predict(self, context: RetentionContext) -> Mapping[str, float]:
        learned = dict(self._learned.predict(context))
        static = dict(self._static.predict(context))
        evidence: dict[str, float] = {
            "clip_fraction": sum(1 for v in learned.values() if v < 0) / max(len(learned), 1),
            "dispersion": dispersion(learned),
        }
        reasons: list[str] = []
        policy = self._policy
        if policy.max_shift is not None:
            assert self._detector is not None
            evidence["shift"] = self._detector.score(self._shift_features(context))
            if evidence["shift"] > policy.max_shift:
                reasons.append(f"shift {evidence['shift']:.3f} > {policy.max_shift}")
        if policy.max_disagreement is not None:
            evidence["disagreement"] = ensemble_disagreement(
                [dict(m.predict(context)) for m in self._members]
            )
            if evidence["disagreement"] > policy.max_disagreement:
                reasons.append(
                    f"disagreement {evidence['disagreement']:.3f} > {policy.max_disagreement}"
                )
        if (
            policy.max_clip_fraction is not None
            and evidence["clip_fraction"] > policy.max_clip_fraction
        ):
            reasons.append(
                f"clip_fraction {evidence['clip_fraction']:.3f} > {policy.max_clip_fraction}"
            )
        if policy.min_dispersion is not None and evidence["dispersion"] < policy.min_dispersion:
            reasons.append(f"dispersion {evidence['dispersion']:.3f} < {policy.min_dispersion}")
        alpha = 0.0 if reasons else policy.alpha
        try:
            mixed = mix_utilities(learned, static, alpha)
        except ValueError as error:
            raise AgentSdkError(
                "UTILITY_CALIBRATION_INCOMPLETE",
                str(error),
                {"predictor_id": self._identity.predictor_id},
            ) from error
        self.last_audit = {
            "mode": "static-fallback" if reasons else "mixed",
            "alpha": alpha,
            "configured_alpha": policy.alpha,
            "evidence": evidence,
            "reasons": reasons,
            "learned_predictor": self._learned.identity.model_dump(mode="json"),
            "static_predictor": self._static.identity.model_dump(mode="json"),
        }
        return mixed
