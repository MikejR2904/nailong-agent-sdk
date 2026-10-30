# Copyright (c) 2026 David Michael Indraputra

"""Additive episode-utility models and their digest-checked serialized artifacts."""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..foundations.atomic_io import replace_atomic, unique_temporary_path
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import sha256_hex, strict_canonical_json
from .utility_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION

ARTIFACT_VERSION = "episode-utility-model-v1"


class LinearRankingModel:
    """``u_i = theta . h_i``."""

    model_type = "linear"

    def __init__(self, weights: list[float]) -> None:
        if not weights:
            raise ValueError("A linear ranking model needs at least one weight.")
        self._weights = list(weights)

    @property
    def feature_dim(self) -> int:
        return len(self._weights)

    @property
    def parameters(self) -> list[float]:
        return list(self._weights)

    def with_parameters(self, parameters: list[float]) -> LinearRankingModel:
        return LinearRankingModel(parameters)

    def architecture(self) -> dict[str, Any]:
        return {"feature_dim": self.feature_dim}

    def score(self, features: tuple[float, ...]) -> float:
        return sum(w * x for w, x in zip(self._weights, features, strict=True))

    def accumulate_gradient(
        self, features: tuple[float, ...], coefficient: float, out: list[float]
    ) -> None:
        for index, value in enumerate(features):
            out[index] += coefficient * value


class MlpRankingModel:
    """``u_i = w2 . tanh(W1 h_i + b1) + b2`` with a flat, seed-reproducible parameter vector."""

    model_type = "mlp"

    def __init__(self, feature_dim: int, hidden_units: int, parameters: list[float]) -> None:
        if feature_dim < 1 or hidden_units < 1:
            raise ValueError("An MLP ranking model needs feature_dim >= 1 and hidden_units >= 1.")
        expected = hidden_units * feature_dim + 2 * hidden_units + 1
        if len(parameters) != expected:
            raise ValueError(
                f"An MLP with feature_dim={feature_dim} and hidden_units={hidden_units} has "
                f"{expected} parameters, got {len(parameters)}."
            )
        self._feature_dim = feature_dim
        self._hidden = hidden_units
        self._parameters = list(parameters)

    @classmethod
    def initialize(cls, feature_dim: int, hidden_units: int, seed: int) -> MlpRankingModel:
        rng = random.Random(seed)
        scale_in = 1.0 / math.sqrt(feature_dim)
        scale_out = 1.0 / math.sqrt(hidden_units)
        parameters = [rng.uniform(-scale_in, scale_in) for _ in range(hidden_units * feature_dim)]
        parameters += [0.0] * hidden_units
        parameters += [rng.uniform(-scale_out, scale_out) for _ in range(hidden_units)]
        parameters.append(0.0)
        return cls(feature_dim, hidden_units, parameters)

    @property
    def feature_dim(self) -> int:
        return self._feature_dim

    @property
    def parameters(self) -> list[float]:
        return list(self._parameters)

    def with_parameters(self, parameters: list[float]) -> MlpRankingModel:
        return MlpRankingModel(self._feature_dim, self._hidden, parameters)

    def architecture(self) -> dict[str, Any]:
        return {"feature_dim": self._feature_dim, "hidden_units": self._hidden}

    def _hidden_activations(self, features: tuple[float, ...]) -> list[float]:
        d = self._feature_dim
        offset = self._hidden * d
        return [
            math.tanh(
                self._parameters[offset + j]
                + sum(self._parameters[j * d + k] * features[k] for k in range(d))
            )
            for j in range(self._hidden)
        ]

    def score(self, features: tuple[float, ...]) -> float:
        h = self._hidden
        activations = self._hidden_activations(features)
        start = h * self._feature_dim + h
        return (
            sum(self._parameters[start + j] * activations[j] for j in range(h))
            + self._parameters[-1]
        )

    def accumulate_gradient(
        self, features: tuple[float, ...], coefficient: float, out: list[float]
    ) -> None:
        d, h = self._feature_dim, self._hidden
        activations = self._hidden_activations(features)
        start = h * d + h
        for j in range(h):
            delta = coefficient * self._parameters[start + j] * (1.0 - activations[j] ** 2)
            for k in range(d):
                out[j * d + k] += delta * features[k]
            out[h * d + j] += delta
            out[start + j] += coefficient * activations[j]
        out[-1] += coefficient


RankingModel = LinearRankingModel | MlpRankingModel


@dataclass(frozen=True)
class LoadedUtilityModel:
    model: RankingModel
    training: dict[str, Any]
    digest: str
    feature_schema_version: str


def model_to_artifact(model: RankingModel, training: dict[str, Any]) -> dict[str, Any]:
    require_finite(model.parameters, "model parameters")
    body = {
        "artifact_version": ARTIFACT_VERSION,
        "model_type": model.model_type,
        "architecture": model.architecture(),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "parameters": model.parameters,
        "training": training,
    }
    return {**body, "digest": sha256_hex(strict_canonical_json(body))}


def model_from_artifact(
    artifact: dict[str, Any], *, source: str = "artifact"
) -> LoadedUtilityModel:
    digest = artifact.get("digest")
    body = {key: value for key, value in artifact.items() if key != "digest"}
    expected = sha256_hex(strict_canonical_json(body))
    if digest != expected:
        raise AgentSdkError(
            "UTILITY_MODEL_ARTIFACT_CORRUPT",
            f"Utility model {source} digest {digest!r} does not match its content digest "
            f"{expected!r}; the file was modified or truncated after it was written.",
            {"source": source},
        )
    if body.get("artifact_version") != ARTIFACT_VERSION:
        raise AgentSdkError(
            "UTILITY_MODEL_ARTIFACT_INCOMPATIBLE",
            f"Utility model {source} has artifact_version {body.get('artifact_version')!r}; "
            f"this SDK reads {ARTIFACT_VERSION!r}.",
            {"source": source},
        )
    if body.get("feature_schema_version") != FEATURE_SCHEMA_VERSION or list(
        body.get("feature_names", [])
    ) != list(FEATURE_NAMES):
        raise AgentSdkError(
            "UTILITY_MODEL_FEATURE_SCHEMA_MISMATCH",
            f"Utility model {source} was trained on feature schema "
            f"{body.get('feature_schema_version')!r} with {len(body.get('feature_names', []))} "
            f"features; the running SDK extracts schema {FEATURE_SCHEMA_VERSION!r} with "
            f"{len(FEATURE_NAMES)} features.",
            {"source": source},
        )
    parameters = body.get("parameters")
    if not isinstance(parameters, list) or not all(
        isinstance(value, int | float) and not isinstance(value, bool) for value in parameters
    ):
        raise AgentSdkError(
            "UTILITY_MODEL_ARTIFACT_CORRUPT",
            f'Utility model {source} field "parameters" is not a list of numbers.',
            {"source": source},
        )
    require_finite(parameters, f"{source} parameters")
    architecture = body.get("architecture", {})
    try:
        if body.get("model_type") == "linear":
            model: RankingModel = LinearRankingModel([float(v) for v in parameters])
        elif body.get("model_type") == "mlp":
            model = MlpRankingModel(
                int(architecture["feature_dim"]),
                int(architecture["hidden_units"]),
                [float(v) for v in parameters],
            )
        else:
            raise ValueError(f"unknown model_type {body.get('model_type')!r}")
    except (KeyError, TypeError, ValueError) as error:
        raise AgentSdkError(
            "UTILITY_MODEL_ARTIFACT_CORRUPT",
            f"Utility model {source} has an inconsistent architecture: "
            f"{type(error).__name__}: {error}",
            {"source": source},
        ) from error
    if model.feature_dim != len(FEATURE_NAMES):
        raise AgentSdkError(
            "UTILITY_MODEL_FEATURE_SCHEMA_MISMATCH",
            f"Utility model {source} takes {model.feature_dim} inputs; the schema has "
            f"{len(FEATURE_NAMES)}.",
            {"source": source},
        )
    return LoadedUtilityModel(
        model=model,
        training=dict(body.get("training", {})),
        digest=expected,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
    )


def save_utility_model(path: Path, model: RankingModel, training: dict[str, Any]) -> str:
    artifact = model_to_artifact(model, training)
    target = Path(path)
    temporary = unique_temporary_path(target)
    try:
        temporary.write_text(json.dumps(artifact, sort_keys=True, indent=2), encoding="utf-8")
        replace_atomic(temporary, target)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise AgentSdkError(
            "UTILITY_MODEL_ARTIFACT_WRITE_FAILED",
            f'Could not write utility model to "{target}": {type(error).__name__}: {error}',
            {"path": str(target)},
        ) from error
    return str(artifact["digest"])


def load_utility_model(path: Path) -> LoadedUtilityModel:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise AgentSdkError(
            "UTILITY_MODEL_ARTIFACT_MISSING",
            f'Utility model artifact "{target}" does not exist.',
            {"path": str(target)},
        ) from error
    except OSError as error:
        raise AgentSdkError(
            "UTILITY_MODEL_ARTIFACT_UNREADABLE",
            f'Could not read utility model artifact "{target}": {type(error).__name__}: {error}',
            {"path": str(target)},
        ) from error
    try:
        artifact = json.loads(text)
    except json.JSONDecodeError as error:
        raise AgentSdkError(
            "UTILITY_MODEL_ARTIFACT_CORRUPT",
            f'Utility model artifact "{target}" is not valid JSON: {error.msg} at '
            f"line {error.lineno} column {error.colno}.",
            {"path": str(target)},
        ) from error
    if not isinstance(artifact, dict):
        raise AgentSdkError(
            "UTILITY_MODEL_ARTIFACT_CORRUPT",
            f'Utility model artifact "{target}" holds a JSON {type(artifact).__name__}, '
            "not an object.",
            {"path": str(target)},
        )
    return model_from_artifact(artifact, source=f'"{target}"')


def require_finite(values: list[float], label: str) -> None:
    for index, value in enumerate(values):
        if not math.isfinite(value):
            raise AgentSdkError(
                "UTILITY_MODEL_NONFINITE",
                f"{label} contain a non-finite value {value!r} at index {index}.",
                {"index": index},
            )
