# Copyright (c) 2026 David Michael Indraputra

"""Retention decisions and their later outcomes, recorded for offline learning.

A decision carries everything a retrain needs and nothing that identifies the task beyond its
digest: the per-episode features at decision time, costs, prerequisite graph, the utilities that
drove the choice and the selection made. Outcomes are attached afterwards, by whatever scores the
run.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import sha256_hex, strict_canonical_json
from .episode_models import CompactionResult
from .learned_retention import PredictorIdentity
from .utility_features import FEATURE_SCHEMA_VERSION, RetentionContext
from .utility_features_v2 import FEATURE_SCHEMAS


class RetentionDecision(StrictModel):
    decision_id: str = Field(min_length=1)
    task_digest: str = Field(min_length=1)
    task_group: str = "unspecified"
    strategy: str = Field(min_length=1)
    token_budget: int = Field(ge=0)
    predictor: PredictorIdentity | None = None
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    episode_ids: list[str]
    token_costs: dict[str, int]
    depends_on: dict[str, list[str]]
    features: dict[str, list[float]]
    utilities: dict[str, float] = Field(default_factory=dict)
    mandatory_ids: list[str] = Field(default_factory=list)
    selected_ids: list[str]
    solver_status: str | None = None
    optimality_gap: str | None = None


class RetentionOutcome(StrictModel):
    decision_id: str = Field(min_length=1)
    trial: int = Field(default=0, ge=0)
    score: float = Field(ge=0.0, le=1.0)
    source: str = Field(min_length=1)


def build_decision(
    context: RetentionContext,
    result: CompactionResult,
    feature_schema: str = FEATURE_SCHEMA_VERSION,
) -> RetentionDecision | None:
    if feature_schema not in FEATURE_SCHEMAS:
        raise ValueError(
            f'Unknown feature schema "{feature_schema}"; known: {sorted(FEATURE_SCHEMAS)}.'
        )
    selected = result.dossier.get("retained_episode_ids")
    if selected is None:
        return None
    learned = result.dossier.get("learned_ranking") or {}
    if learned.get("status") == "applied":
        utilities = {key: float(value) for key, value in learned["predicted_utilities"].items()}
        predictor = PredictorIdentity.model_validate(learned["predictor"])
    else:
        utilities = {
            row["episode_id"]: float(row["utility"])
            for row in result.dossier.get("static_utilities", [])
        }
        predictor = None
    solver = result.dossier.get("solver") or {}
    task_digest = sha256_hex(context.query)
    selected_ids = sorted(selected)
    decision_id = sha256_hex(
        strict_canonical_json(
            {
                "task": task_digest,
                "strategy": result.strategy.value,
                "predictor": predictor.artifact_digest if predictor else None,
                "budget": context.token_budget,
                "selected": selected_ids,
            }
        )
    )[:24]
    return RetentionDecision(
        decision_id=decision_id,
        task_digest=task_digest,
        strategy=result.strategy.value,
        token_budget=context.token_budget,
        predictor=predictor,
        episode_ids=sorted(record.id for record in context.records),
        token_costs={key: int(value) for key, value in context.token_costs.items()},
        depends_on={record.id: list(record.depends_on) for record in context.records},
        feature_schema_version=feature_schema,
        features={
            key: list(value)
            for key, value in FEATURE_SCHEMAS[feature_schema][1](context, ()).items()
        },
        utilities=utilities,
        mandatory_ids=sorted(result.dossier.get("mandatory_episode_ids", [])),
        selected_ids=selected_ids,
        solver_status=solver.get("status"),
        optimality_gap=solver.get("optimality_gap"),
    )


class RetentionOutcomeLog:
    """Append-only JSON-lines log of decisions and outcomes; safe to share across threads."""

    def __init__(self, path: Path, *, task_group: str = "unspecified") -> None:
        self._path = Path(path)
        self._task_group = task_group
        self._lock = threading.Lock()
        self._decisions: dict[str, RetentionDecision] = {}
        self._outcomes: dict[tuple[str, int], RetentionOutcome] = {}
        if self._path.exists():
            for number, line in enumerate(
                self._path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if line.strip():
                    self._ingest(line, number)
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch()

    def _ingest(self, line: str, number: int) -> None:
        try:
            row = json.loads(line)
            kind = row["type"]
            if kind == "decision":
                decision = RetentionDecision.model_validate(row["decision"])
                self._decisions.setdefault(decision.decision_id, decision)
            elif kind == "outcome":
                outcome = RetentionOutcome.model_validate(row["outcome"])
                if outcome.decision_id not in self._decisions:
                    raise ValueError(f'unknown decision "{outcome.decision_id}"')
                key = (outcome.decision_id, outcome.trial)
                existing = self._outcomes.get(key)
                if existing is not None and not math.isclose(existing.score, outcome.score):
                    raise ValueError(
                        f'decision "{outcome.decision_id}" trial {outcome.trial} already has '
                        f"score {existing.score}, not {outcome.score}"
                    )
                self._outcomes[key] = outcome
            else:
                raise ValueError(f'unknown row type "{kind}"')
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise AgentSdkError(
                "RETENTION_LOG_CORRUPT",
                f'Retention log "{self._path}" line {number} is invalid: '
                f"{type(error).__name__}: {error}",
                {"path": str(self._path), "line": number},
            ) from error

    def _append(self, row: dict[str, Any]) -> None:
        with self._path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")

    def observe(self, decision: RetentionDecision) -> None:
        stamped = decision.model_copy(update={"task_group": self._task_group})
        with self._lock:
            if stamped.decision_id in self._decisions:
                return
            self._decisions[stamped.decision_id] = stamped
            self._append({"type": "decision", "decision": stamped.model_dump(mode="json")})

    def record_outcome(
        self, decision_id: str, score: float, *, source: str, trial: int = 0
    ) -> None:
        with self._lock:
            if decision_id not in self._decisions:
                raise AgentSdkError(
                    "RETENTION_DECISION_UNKNOWN",
                    f'Cannot record an outcome for unknown retention decision "{decision_id}".',
                    {"decision_id": decision_id},
                )
            outcome = RetentionOutcome(
                decision_id=decision_id, trial=trial, score=score, source=source
            )
            row = {"type": "outcome", "outcome": outcome.model_dump(mode="json")}
            self._ingest(json.dumps(row), len(self._decisions) + len(self._outcomes) + 1)
            self._append(row)

    def decisions(self) -> list[RetentionDecision]:
        with self._lock:
            return list(self._decisions.values())

    def outcomes(self, decision_id: str) -> list[RetentionOutcome]:
        with self._lock:
            return sorted(
                (o for (d, _), o in self._outcomes.items() if d == decision_id),
                key=lambda item: item.trial,
            )
