# Copyright (c) 2026 David Michael Indraputra

"""Typed episode records and PASK/PCKP compaction contracts.

The baseline compactor follows the framework's safe episode lifecycle (updated
systems-design PDF, pp. 62-64). ``PaskCompactionPolicy`` weights are deterministic;
relevance itself is supplied by a host-selectable scorer such as the lexical
default in :mod:`episode_scoring`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field, model_validator

from ..foundations.contracts import EpisodeKind, StrictModel


class EpisodeState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    COMPACTED = "compacted"


class CompactionStrategy(StrEnum):
    """Selectable deterministic compaction strategies.

    ``GREEDY_BASELINE`` preserves the original fixed-priority eviction rule for
    historical comparisons. ``PASK`` remains the dynamic-diversity heuristic.
    ``EXACT_PCKP`` is the default additive-utility exact mode.
    """

    GREEDY_BASELINE = "greedy-baseline"
    PASK = "provenance-aware-submodular-knapsack"
    EXACT_PCKP = "exact-precedence-constrained-knapsack"


class EpisodeRecord(StrictModel):
    id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    kind: EpisodeKind
    state: EpisodeState = EpisodeState.OPEN
    substrate_backed: bool = False
    snapshot_version: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    depended_on_by: list[str] = Field(default_factory=list)
    description: str | None = None
    content: dict[str, Any] | None = None
    tombstone: str | None = None
    access_count: int = Field(default=0, ge=0)
    last_access_sequence: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_record(self) -> EpisodeRecord:
        if self.kind is EpisodeKind.EXPLORATORY and self.depends_on:
            raise ValueError("exploratory episodes may not declare dependencies")
        if self.substrate_backed and not self.snapshot_version:
            raise ValueError("substrate-backed episodes require snapshot_version")
        if self.state is not EpisodeState.OPEN and self.kind is EpisodeKind.EXPLORATORY:
            if not self.description or not self.description.strip():
                raise ValueError("closed exploratory episodes require a description")
        return self


class PaskCompactionPolicy(StrictModel):
    """Non-negative weights for deterministic PASK utility scoring.

    Relevance is supplied by a host-selectable deterministic scorer.  The
    standard scorer is lexical, so the default requires no embedding model or
    external service.  A host may inject a local, deterministic embedding scorer
    when it can reproduce the score from versioned model assets.

    ``exact_max_branch_nodes`` bounds only general-DAG branch-and-bound search.
    A capped search returns an auditable feasible ``BEST_EFFORT`` certificate;
    the rooted-forest dynamic-program path remains exact.
    """

    strategy: CompactionStrategy = CompactionStrategy.EXACT_PCKP
    task_relevance_weight: float = Field(default=0.40, ge=0)
    dependency_centrality_weight: float = Field(default=0.20, ge=0)
    provenance_weight: float = Field(default=0.20, ge=0)
    recency_weight: float = Field(default=0.10, ge=0)
    frequency_weight: float = Field(default=0.05, ge=0)
    diversity_weight: float = Field(default=0.05, ge=0)
    exact_max_branch_nodes: int = Field(default=50_000, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def has_positive_weight(self) -> PaskCompactionPolicy:
        static_weight = sum(
            (
                self.task_relevance_weight,
                self.dependency_centrality_weight,
                self.provenance_weight,
                self.recency_weight,
                self.frequency_weight,
            )
        )
        if static_weight + self.diversity_weight <= 0:
            raise ValueError("At least one PASK utility weight must be positive.")
        if self.strategy is CompactionStrategy.EXACT_PCKP and static_weight <= 0:
            raise ValueError("EXACT_PCKP requires a positive additive utility weight.")
        return self


class EpisodeRelevanceScorer(Protocol):
    """Host extension point for deterministic episode/task relevance values."""

    def score(self, query: str, episode: EpisodeRecord) -> float: ...


class CompactionStatus(StrEnum):
    COMPACTED = "compacted"
    PROTECTED_OVER_BUDGET = "protected-over-budget"
    CONTEXT_DEADLOCK = "context-deadlock"
    WITHIN_BUDGET = "within-budget"


class EpisodeUtility(StrictModel):
    """Auditable score record for a PASK retention decision."""

    episode_id: str
    utility: float = Field(ge=0)
    utility_to_cost: float = Field(ge=0)
    marginal_token_cost: int = Field(ge=0)
    task_relevance: float = Field(ge=0, le=1)
    dependency_centrality: float = Field(ge=0, le=1)
    provenance: float = Field(ge=0, le=1)
    recency: float = Field(ge=0, le=1)
    frequency: float = Field(ge=0, le=1)
    diversity: float = Field(ge=0, le=1)
    dependency_closure: list[str] = Field(default_factory=list)


class CompactionResult(StrictModel):
    status: CompactionStatus
    before_tokens: int = Field(ge=0)
    after_tokens: int = Field(ge=0)
    compacted_episode_ids: list[str] = Field(default_factory=list)
    blocked_episode_ids: list[str] = Field(default_factory=list)
    strategy: CompactionStrategy = CompactionStrategy.PASK
    dossier: dict[str, Any] = Field(default_factory=dict)


class EpisodeCheckpoint(StrictModel):
    schema_version: str = "episode-checkpoint-v2"
    graph_hash: str = Field(min_length=1)
    episodes: list[dict[str, Any]]


class EpisodeStore(Protocol):
    def open_exploratory(
        self,
        owner_id: str,
        *,
        substrate_backed: bool = False,
        snapshot_version: str | None = None,
        content: dict[str, Any] | None = None,
    ) -> EpisodeRecord: ...

    def open_action(
        self,
        owner_id: str,
        dependencies: list[str],
        *,
        content: dict[str, Any] | None = None,
    ) -> EpisodeRecord: ...

    def close(self, episode_id: str, *, description: str | None = None) -> EpisodeRecord: ...

    def list(self) -> list[EpisodeRecord]: ...
