# Copyright (c) 2026 David Michael Indraputra

"""In-memory and file-backed episode stores with auditable, deterministic retention.

PASK never compactifies an open, explicitly protected, active, manifest-
incomplete, or dependency-required episode.  The raw evidence remains in the
tool-result journal; compaction replaces only the in-memory episode payload
with a structural tombstone.  ``EXACT_PCKP`` composes the episode graph into a
:class:`~nailong_agent_sdk.foundations.optimization.PckpProblem` and solves it
exactly; it is deliberately an auditable heuristic-free mode, not a claim that
``PASK`` itself is globally optimal.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..foundations.atomic_io import fsync_directory, replace_atomic, unique_temporary_path
from ..foundations.contracts import EpisodeKind
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import sha256_hex, strict_canonical_json
from ..foundations.identifiers import require_unique
from ..foundations.optimization import ExactPckpSolver, PckpItem, PckpProblem, PckpStatus
from .episode_archive import (
    FileEpisodeArchive,
    InMemoryEpisodeArchive,
    snapshot_text,
)
from .episode_models import (
    CompactionResult,
    CompactionStatus,
    CompactionStrategy,
    EpisodeArchive,
    EpisodeCheckpoint,
    EpisodeRecord,
    EpisodeRelevanceScorer,
    EpisodeState,
    EpisodeUtility,
    EvictedEpisode,
    PaskCompactionPolicy,
    QuarantinedSummary,
    RehydrationResult,
    SemanticEpisodeSummary,
    SemanticSummaryView,
    SummaryLifecycle,
    SummaryRef,
)
from .episode_scoring import (
    LexicalEpisodeRelevanceScorer,
    _clamp_score,
    _episode_terms,
    _episode_tokens,
    _provenance_weight,
)
from .learned_retention import (
    EpisodeUtilityPredictor,
    LearnedCoefficients,
    QuantizationPolicy,
    resolve_learned_coefficients,
)
from .memory_snapshot import (
    EpisodeMemorySnapshot,
    RestoreReport,
    SnapshotRejection,
    snapshot_problems,
)
from .retention_outcomes import RetentionDecision, build_decision
from .semantic_compaction import (
    SemanticCompactor,
    SemanticPassReport,
    archivable,
    episode_order_key,
)
from .summarization_scheduler import SummarizationScheduler
from .summary_ledger import SummaryLedger
from .utility_features import FEATURE_SCHEMA_VERSION, RetentionContext

SNAPSHOT_DIRECTORY = "snapshots"
SNAPSHOT_PREFIX = "snapshot-"
SNAPSHOT_SUFFIX = ".json"


class CompactionGuard:
    """Lets the run that started a compaction abandon it without waiting for its thread."""

    def __init__(self, lock: threading.RLock) -> None:
        self._lock = lock
        self._abandoned = threading.Event()

    @property
    def abandoned(self) -> bool:
        return self._abandoned.is_set()

    def abandon(self) -> None:
        with self._lock:
            self._abandoned.set()


class InMemoryEpisodeStore:
    """Enforce typed episode lifecycle and deterministic, auditable retention.

    PASK never compactifies an open, explicitly protected, active, manifest-
    incomplete, or dependency-required episode.  The raw evidence remains in the
    tool-result journal; compaction replaces only the in-memory episode payload
    with a structural tombstone.
    """

    def __init__(
        self,
        *,
        compaction_policy: PaskCompactionPolicy | None = None,
        relevance_scorer: EpisodeRelevanceScorer | None = None,
        semantic: SemanticCompactor | None = None,
        utility_predictor: EpisodeUtilityPredictor | None = None,
        utility_quantization: QuantizationPolicy | None = None,
        retention_observer: Callable[[RetentionDecision], None] | None = None,
        retention_feature_schema: str = FEATURE_SCHEMA_VERSION,
    ) -> None:
        self._retention_observer = retention_observer
        self._retention_feature_schema = retention_feature_schema
        self._utility_predictor = utility_predictor
        self._utility_quantization = utility_quantization or QuantizationPolicy()
        self._records: dict[str, EpisodeRecord] = {}
        self._next_id = 1
        self._next_access_sequence = 1
        self._compaction_policy = compaction_policy or PaskCompactionPolicy()
        self._relevance_scorer = relevance_scorer or LexicalEpisodeRelevanceScorer()
        self._archive: EpisodeArchive | None = None
        self._semantic = semantic
        if semantic is not None:
            self._archive = semantic.archive or self._default_archive()
            self._semantic = semantic.with_archive(self._archive)
        summary_archive = (
            self._archive
            if self._archive is not None and hasattr(self._archive, "put_summary")
            else None
        )
        if semantic is not None and summary_archive is None:
            summary_archive = InMemoryEpisodeArchive()
        self._ledger = SummaryLedger(summary_archive)
        self._evicted: dict[str, EvictedEpisode] = {}
        self._group_attempts: dict[str, int] = {}
        self._next_summary_id = 1
        self._commit_lock = threading.RLock()
        self._guard: CompactionGuard | None = None
        self._scheduler: SummarizationScheduler | None = None
        self._jobs: dict[str, dict[str, Any]] = {}
        self._version = 0

    def _default_archive(self) -> EpisodeArchive:
        return InMemoryEpisodeArchive()

    def guard_compaction(self) -> CompactionGuard:
        """Arm a fresh guard; the next ``compact`` call stops committing once it is abandoned."""

        guard = CompactionGuard(self._commit_lock)
        self._guard = guard
        return guard

    @staticmethod
    def _ensure_active(guard: CompactionGuard | None) -> None:
        if guard is not None and guard.abandoned:
            raise AgentSdkError(
                "EPISODE_COMPACTION_ABANDONED",
                "The run that started this episode compaction abandoned it (cancelled or past "
                "its deadline); no episode, summary or archive reference was changed by it "
                "after the abandonment.",
            )

    @property
    def compaction_policy(self) -> PaskCompactionPolicy:
        return self._compaction_policy

    @property
    def relevance_scorer(self) -> EpisodeRelevanceScorer:
        return self._relevance_scorer

    @property
    def utility_predictor(self) -> EpisodeUtilityPredictor | None:
        return self._utility_predictor

    @property
    def semantic_compaction_enabled(self) -> bool:
        return self._semantic is not None

    @property
    def archive(self) -> EpisodeArchive | None:
        return self._archive

    def summaries(self) -> list[SemanticEpisodeSummary]:
        return self._ledger.resident()

    def active_summaries(self) -> list[SemanticEpisodeSummary]:
        return self._ledger.active()

    def summary_refs(self) -> list[SummaryRef]:
        return self._ledger.refs()

    def summary_lifecycle(self, summary_id: str) -> SummaryLifecycle | None:
        return self._ledger.lifecycle_of(summary_id)

    def load_summary(self, summary_id: str) -> SemanticEpisodeSummary:
        return self._ledger.load(summary_id)

    def quarantined_summaries(self) -> list[QuarantinedSummary]:
        return self._ledger.quarantined()

    def summary_for(
        self, episode_id: str, *, resolve_archived: bool = False
    ) -> SemanticEpisodeSummary | None:
        summary_id = self._ledger.covering(episode_id)
        if summary_id is None:
            return None
        top = self._ledger.top_for(summary_id)
        if self._ledger.is_resident(top):
            return self._ledger.load(top)
        return self._ledger.load(top) if resolve_archived else None

    def summary_views(self) -> list[SemanticSummaryView]:
        return [item.view() for item in self.active_summaries()]

    def evicted_episode_ids(self) -> list[str]:
        return sorted(self._evicted, key=episode_order_key)

    def pending_episode_ids(self) -> list[str]:
        return [record.id for record in self._pending()]

    def open_exploratory(
        self,
        owner_id: str,
        *,
        substrate_backed: bool = False,
        snapshot_version: str | None = None,
        content: dict[str, Any] | None = None,
    ) -> EpisodeRecord:
        return self._open(
            owner_id,
            EpisodeKind.EXPLORATORY,
            substrate_backed=substrate_backed,
            snapshot_version=snapshot_version,
            content=content,
        )

    def open_action(
        self,
        owner_id: str,
        dependencies: list[str],
        *,
        content: dict[str, Any] | None = None,
    ) -> EpisodeRecord:
        require_unique(dependencies, "action episode dependencies")
        for dependency_id in dependencies:
            dependency = self._records.get(dependency_id)
            if dependency is None:
                raise ValueError(f'Action episode references unknown episode "{dependency_id}".')
            if (
                dependency.kind is not EpisodeKind.EXPLORATORY
                or dependency.state is not EpisodeState.CLOSED
            ):
                raise ValueError("Action episodes may depend only on closed exploratory episodes.")
        record = self._open(
            owner_id,
            EpisodeKind.ACTION,
            dependencies=dependencies,
            content=content,
        )
        for dependency_id in dependencies:
            dependency = self._records[dependency_id]
            self._records[dependency_id] = dependency.model_copy(
                update={"depended_on_by": [*dependency.depended_on_by, record.id]}
            )
        return record

    def close(self, episode_id: str, *, description: str | None = None) -> EpisodeRecord:
        record = self._require(episode_id)
        if record.state is not EpisodeState.OPEN:
            raise ValueError(f'Episode "{episode_id}" is not open.')
        if record.kind is EpisodeKind.EXPLORATORY and (not description or not description.strip()):
            raise ValueError("Closing an exploratory episode requires a non-empty description.")
        closed = record.model_copy(
            update={"state": EpisodeState.CLOSED, "description": description or record.description}
        )
        self._records[episode_id] = closed
        return closed

    def attach_source_handle(self, episode_id: str, handle_id: str) -> None:
        """Record the journal handle that holds this episode's complete tool result."""

        record = self._require(episode_id)
        self._records[episode_id] = record.model_copy(update={"source_handle": handle_id})

    def mark_accessed(self, episode_ids: Iterable[str]) -> None:
        """Record explicit harness/tool use for recency and frequency scoring."""

        for episode_id in sorted(set(episode_ids)):
            record = self._require(episode_id)
            if record.state is EpisodeState.COMPACTED:
                raise ValueError(f'Compacted episode "{episode_id}" cannot be marked accessed.')
            self._records[episode_id] = record.model_copy(
                update={
                    "access_count": record.access_count + 1,
                    "last_access_sequence": self._next_access_sequence,
                }
            )
            self._next_access_sequence += 1

    def list(self) -> list[EpisodeRecord]:
        return [self._records[episode_id] for episode_id in sorted(self._records)]

    def get(self, episode_id: str) -> EpisodeRecord | None:
        record = self._records.get(episode_id)
        if record is not None:
            return record
        evicted = self._evicted.get(episode_id)
        return evicted.to_record() if evicted is not None else None

    def estimate_tokens(self) -> int:
        return sum(
            _episode_tokens(record)
            for record in self._records.values()
            if record.state is not EpisodeState.COMPACTED
        )

    def compact(
        self,
        token_budget: int,
        *,
        active_episode_id: str | None = None,
        protected_episode_ids: Iterable[str] = (),
        relevance_query: str = "",
        policy: PaskCompactionPolicy | None = None,
    ) -> CompactionResult:
        """Retain a safe bounded episode set, then tombstone other closed records.

        With a semantic compactor the tombstoned records keep their payloads until their
        group has been archived, summarized and verified; the dossier reports the pass.
        """

        guard = self._guard
        self._ensure_active(guard)
        observer = self._retention_observer
        context = (
            self._retention_context(token_budget, relevance_query) if observer is not None else None
        )
        result = self._compact_selection(
            token_budget,
            active_episode_id=active_episode_id,
            protected_episode_ids=protected_episode_ids,
            relevance_query=relevance_query,
            policy=policy,
        )
        if observer is not None and context is not None:
            decision = build_decision(context, result, self._retention_feature_schema)
            if decision is not None:
                try:
                    observer(decision)
                except Exception as error:
                    raise AgentSdkError(
                        "RETENTION_OBSERVER_FAILED",
                        f"The retention observer raised {type(error).__name__}: {error}. The "
                        f"compaction itself was applied ({len(result.compacted_episode_ids)} "
                        "episodes compacted).",
                    ) from error
        if self._semantic is None:
            return result
        report = self._semantic_pass(guard)
        return result.model_copy(
            update={
                "dossier": {**result.dossier, "semantic_compaction": report.model_dump(mode="json")}
            }
        )

    def _retention_context(self, token_budget: int, relevance_query: str) -> RetentionContext:
        live = [
            record
            for record in self._records.values()
            if record.state is not EpisodeState.COMPACTED
        ]
        return RetentionContext(
            records=tuple(sorted(live, key=lambda record: record.id)),
            token_costs={record.id: _episode_tokens(record) for record in live},
            relevance={
                record.id: _clamp_score(self._relevance_scorer.score(relevance_query, record))
                for record in live
            },
            query=relevance_query,
            token_budget=token_budget,
        )

    def retain_only(
        self,
        retained_episode_ids: Iterable[str],
        *,
        active_episode_id: str | None = None,
        protected_episode_ids: Iterable[str] = (),
    ) -> CompactionResult:
        """Apply an externally chosen retention set through the same tombstoning path."""

        guard = self._guard
        self._ensure_active(guard)
        retained = set(retained_episode_ids)
        protected = set(protected_episode_ids)
        named = retained | protected
        if active_episode_id is not None:
            named.add(active_episode_id)
        unknown = sorted(named - set(self._records))
        if unknown:
            raise ValueError(f"retain_only names unknown episodes {unknown}.")
        live_ids = {
            record.id
            for record in self._records.values()
            if record.state is not EpisodeState.COMPACTED
        }
        already_compacted = sorted(retained - live_ids)
        if already_compacted:
            raise ValueError(
                f"retain_only cannot retain episodes that are already compacted: "
                f"{already_compacted}."
            )
        mandatory = set(protected)
        if active_episode_id is not None:
            mandatory.add(active_episode_id)
        mandatory.update(
            record.id
            for record in self._records.values()
            if record.id in live_ids and record.state is EpisodeState.OPEN
        )
        mandatory = self._dependency_closure(mandatory, live_ids)
        dropped = sorted(mandatory - retained)
        if dropped:
            raise ValueError(
                f"retain_only would drop mandatory episodes {dropped} "
                "(protected, active, open, or required by one of them)."
            )
        for episode_id in sorted(retained):
            missing = [d for d in self._require(episode_id).depends_on if d not in retained]
            if missing:
                raise ValueError(
                    f'retain_only retains episode "{episode_id}" without its prerequisites '
                    f"{missing}."
                )
        before = self.estimate_tokens()
        compacted = self._compact_unretained(live_ids - retained)
        result = CompactionResult(
            status=CompactionStatus.COMPACTED if compacted else CompactionStatus.WITHIN_BUDGET,
            before_tokens=before,
            after_tokens=self.estimate_tokens(),
            compacted_episode_ids=compacted,
            strategy=CompactionStrategy.EXPLICIT,
            dossier={
                "strategy": CompactionStrategy.EXPLICIT.value,
                "mandatory_episode_ids": sorted(mandatory),
                "retained_episode_ids": sorted(retained),
            },
        )
        if self._semantic is None:
            return result
        report = self._semantic_pass(guard)
        return result.model_copy(
            update={
                "dossier": {**result.dossier, "semantic_compaction": report.model_dump(mode="json")}
            }
        )

    def _compact_selection(
        self,
        token_budget: int,
        *,
        active_episode_id: str | None,
        protected_episode_ids: Iterable[str],
        relevance_query: str,
        policy: PaskCompactionPolicy | None,
    ) -> CompactionResult:

        if token_budget < 0:
            raise ValueError("token budget may not be negative")
        if active_episode_id is not None:
            self._require(active_episode_id)
        protected = set(protected_episode_ids)
        for episode_id in protected:
            self._require(episode_id)
        effective_policy = policy or self._compaction_policy
        before = self.estimate_tokens()
        if before <= token_budget:
            return CompactionResult(
                status=CompactionStatus.WITHIN_BUDGET,
                before_tokens=before,
                after_tokens=before,
                strategy=effective_policy.strategy,
                dossier={"strategy": effective_policy.strategy.value},
            )
        if effective_policy.strategy is CompactionStrategy.GREEDY_BASELINE:
            return self._compact_greedy(token_budget, active_episode_id, protected, before)
        if effective_policy.strategy is CompactionStrategy.LEARNED_PCKP:
            return self._compact_learned_pckp(
                token_budget,
                active_episode_id=active_episode_id,
                protected_episode_ids=protected,
                relevance_query=relevance_query,
                policy=effective_policy,
                before=before,
            )
        if effective_policy.strategy is CompactionStrategy.EXACT_PCKP:
            return self._compact_exact_pckp(
                token_budget,
                active_episode_id=active_episode_id,
                protected_episode_ids=protected,
                relevance_query=relevance_query,
                policy=effective_policy,
                before=before,
            )
        return self._compact_pask(
            token_budget,
            active_episode_id=active_episode_id,
            protected_episode_ids=protected,
            relevance_query=relevance_query,
            policy=effective_policy,
            before=before,
        )

    def checkpoint(self) -> EpisodeCheckpoint:
        structural = [self._structural(record) for record in self.list()]
        structural.extend(
            {
                "id": evicted.episode_id,
                "owner_id": evicted.owner_id,
                "kind": evicted.kind.value,
                "state": "evicted",
                "depends_on": evicted.depends_on,
                "description": evicted.description,
                "tombstone": evicted.tombstone,
                "summary_id": evicted.summary_id,
                "archive_id": evicted.archive_id,
                "content_hash": evicted.content_hash,
            }
            for evicted in (self._evicted[key] for key in self.evicted_episode_ids())
        )
        return EpisodeCheckpoint(
            graph_hash=sha256_hex(strict_canonical_json(structural)),
            episodes=structural,
        )

    def export_snapshot(self, generation: int) -> EpisodeMemorySnapshot:
        with self._commit_lock:
            summaries, refs, quarantined = self._ledger.export()
            snapshot = EpisodeMemorySnapshot(
                generation=generation,
                created_at_utc=datetime.now(UTC).isoformat(),
                semantic_policy_hash=(
                    self._semantic.policy_hash if self._semantic is not None else None
                ),
                next_episode_number=self._next_id,
                next_access_sequence=self._next_access_sequence,
                next_summary_number=self._next_summary_id,
                records=self.list(),
                evicted=[self._evicted[key] for key in self.evicted_episode_ids()],
                summaries=summaries,
                summary_index=refs,
                group_attempts=dict(self._group_attempts),
                quarantined=quarantined,
                checkpoint_graph_hash=self.checkpoint().graph_hash,
            )
        return snapshot.sealed()

    def adopt_snapshot(
        self, snapshot: EpisodeMemorySnapshot, *, verify_archives: bool = True
    ) -> None:
        with self._commit_lock:
            if self._records or self._evicted or self._ledger.refs() or self._next_id != 1:
                raise AgentSdkError(
                    "EPISODE_MEMORY_NOT_EMPTY",
                    "A snapshot can only be restored into a store that holds no episodes, "
                    f"but this one holds {len(self._records)} record(s) and "
                    f"{len(self._evicted)} evicted entr(ies).",
                )
            problems = snapshot_problems(
                snapshot,
                archive=self._archive,
                summary_archive=self._ledger.archive,
                verify_archives=verify_archives,
            )
            if problems:
                raise AgentSdkError(
                    "EPISODE_MEMORY_SNAPSHOT_INVALID",
                    f"Memory snapshot generation {snapshot.generation} is not a consistent "
                    f"state ({len(problems)} problem(s)); first: {problems[0]}",
                    {"generation": snapshot.generation, "problems": problems},
                )
            covers = {
                record.id: record.summary_id
                for record in snapshot.records
                if record.summary_id is not None
            }
            covers.update(
                {
                    item.episode_id: item.summary_id
                    for item in snapshot.evicted
                    if item.summary_id is not None
                }
            )
            self._records = {record.id: record for record in snapshot.records}
            self._evicted = {item.episode_id: item for item in snapshot.evicted}
            self._ledger.adopt(
                snapshot.summaries, snapshot.summary_index, covers, snapshot.quarantined
            )
            self._group_attempts = dict(snapshot.group_attempts)
            self._next_id = snapshot.next_episode_number
            self._next_access_sequence = snapshot.next_access_sequence
            self._next_summary_id = snapshot.next_summary_number
            if self.checkpoint().graph_hash != snapshot.checkpoint_graph_hash:
                self._records, self._evicted = {}, {}
                self._ledger.adopt([], [], {}, [])
                self._group_attempts = {}
                self._next_id = self._next_access_sequence = self._next_summary_id = 1
                raise AgentSdkError(
                    "EPISODE_MEMORY_SNAPSHOT_INVALID",
                    f"Memory snapshot generation {snapshot.generation} restored to a graph "
                    "whose structural hash differs from the one recorded when it was written.",
                    {"generation": snapshot.generation},
                )

    def verify_checkpoint(self, checkpoint: EpisodeCheckpoint) -> bool:
        current = self.checkpoint()
        return (
            current.graph_hash == checkpoint.graph_hash and current.episodes == checkpoint.episodes
        )

    def _compact_greedy(
        self,
        token_budget: int,
        active_episode_id: str | None,
        protected_episode_ids: set[str],
        before: int,
    ) -> CompactionResult:
        compacted: list[str] = []
        while self.estimate_tokens() > token_budget:
            candidate = next(iter(self._eligible(active_episode_id, protected_episode_ids)), None)
            if candidate is None:
                if self._protected_tokens(protected_episode_ids, active_episode_id) > token_budget:
                    return CompactionResult(
                        status=CompactionStatus.PROTECTED_OVER_BUDGET,
                        before_tokens=before,
                        after_tokens=self.estimate_tokens(),
                        compacted_episode_ids=compacted,
                        blocked_episode_ids=sorted(protected_episode_ids),
                        strategy=CompactionStrategy.GREEDY_BASELINE,
                        dossier={
                            "strategy": CompactionStrategy.GREEDY_BASELINE.value,
                            "token_budget": token_budget,
                            "current_tokens": self.estimate_tokens(),
                            "reason": "Protected live episodes exceed the episode budget.",
                        },
                    )
                blocked = [
                    record.id
                    for record in self.list()
                    if record.state is EpisodeState.CLOSED and record.id != active_episode_id
                ]
                return CompactionResult(
                    status=CompactionStatus.CONTEXT_DEADLOCK,
                    before_tokens=before,
                    after_tokens=self.estimate_tokens(),
                    compacted_episode_ids=compacted,
                    blocked_episode_ids=blocked,
                    strategy=CompactionStrategy.GREEDY_BASELINE,
                    dossier={
                        "strategy": CompactionStrategy.GREEDY_BASELINE.value,
                        "token_budget": token_budget,
                        "current_tokens": self.estimate_tokens(),
                        "reason": ("No closed, dependency-free episode is eligible."),
                    },
                )
            self._compact_record(candidate.id)
            compacted.append(candidate.id)
        return CompactionResult(
            status=CompactionStatus.COMPACTED,
            before_tokens=before,
            after_tokens=self.estimate_tokens(),
            compacted_episode_ids=compacted,
            strategy=CompactionStrategy.GREEDY_BASELINE,
            dossier={"strategy": CompactionStrategy.GREEDY_BASELINE.value},
        )

    def _compact_exact_pckp(
        self,
        token_budget: int,
        *,
        active_episode_id: str | None,
        protected_episode_ids: set[str],
        relevance_query: str,
        policy: PaskCompactionPolicy,
        before: int,
        learned: LearnedCoefficients | None = None,
    ) -> CompactionResult:
        """Solve additive dependency-closed retention exactly for live episodes.

        The dynamic PASK diversity feature is deliberately absent here because
        exact PCKP requires an additive item objective. Every selected episode
        is charged once, including shared prerequisites.
        """

        live_ids = {
            record.id
            for record in self._records.values()
            if record.state is not EpisodeState.COMPACTED
        }
        mandatory = set(protected_episode_ids)
        if active_episode_id is not None:
            mandatory.add(active_episode_id)
        mandatory.update(
            record.id
            for record in self._records.values()
            if record.id in live_ids and record.state is EpisodeState.OPEN
        )
        mandatory = self._dependency_closure(mandatory, live_ids)
        live_records = [self._require(item_id) for item_id in live_ids]
        max_dependents = max((len(value.depended_on_by) for value in live_records), default=0)
        max_access_count = max((value.access_count for value in live_records), default=0)
        max_access_sequence = max((value.last_access_sequence for value in live_records), default=0)
        utilities = {
            episode_id: self._static_utility(
                episode_id,
                relevance_query,
                policy,
                live_ids,
                max_dependents,
                max_access_count,
                max_access_sequence,
            )
            for episode_id in sorted(live_ids)
        }
        problem = PckpProblem(
            token_budget=token_budget,
            items=[
                PckpItem(
                    item_id=episode_id,
                    token_cost=_episode_tokens(self._require(episode_id)),
                    utility=(
                        learned.quantized[episode_id]
                        if learned is not None
                        else round(utilities[episode_id].utility * 1_000_000)
                    ),
                    prerequisites=self._require(episode_id).depends_on,
                    mandatory=episode_id in mandatory,
                )
                for episode_id in sorted(live_ids)
            ],
        )
        solution = ExactPckpSolver(max_branch_nodes=policy.exact_max_branch_nodes).solve(problem)
        query_hash = sha256_hex(relevance_query)
        if solution.status is PckpStatus.INFEASIBLE_MANDATORY:
            compacted = self._compact_unretained(live_ids - mandatory)
            status = (
                CompactionStatus.PROTECTED_OVER_BUDGET
                if protected_episode_ids
                else CompactionStatus.CONTEXT_DEADLOCK
            )
            return CompactionResult(
                status=status,
                before_tokens=before,
                after_tokens=self.estimate_tokens(),
                compacted_episode_ids=compacted,
                blocked_episode_ids=solution.mandatory_item_ids,
                strategy=CompactionStrategy.EXACT_PCKP,
                dossier={
                    "strategy": CompactionStrategy.EXACT_PCKP.value,
                    "token_budget": token_budget,
                    "mandatory_episode_ids": solution.mandatory_item_ids,
                    "relevance_query_hash": query_hash,
                    "solver": solution.model_dump(mode="json"),
                    "reason": "Mandatory dependency closure exceeds the episode budget.",
                },
            )
        retained = set(solution.selected_item_ids)
        compacted = self._compact_unretained(live_ids - retained)
        return CompactionResult(
            status=CompactionStatus.COMPACTED if compacted else CompactionStatus.WITHIN_BUDGET,
            before_tokens=before,
            after_tokens=self.estimate_tokens(),
            compacted_episode_ids=compacted,
            strategy=CompactionStrategy.EXACT_PCKP,
            dossier={
                "strategy": CompactionStrategy.EXACT_PCKP.value,
                "token_budget": token_budget,
                "relevance_query_hash": query_hash,
                "mandatory_episode_ids": solution.mandatory_item_ids,
                "retained_episode_ids": solution.selected_item_ids,
                "static_utilities": [
                    utilities[episode_id].model_dump(mode="json")
                    for episode_id in sorted(utilities)
                ],
                "policy": policy.model_dump(mode="json"),
                "branch_node_limit": policy.exact_max_branch_nodes,
                "solver": solution.model_dump(mode="json"),
                "objective": "additive static utility under dependency-closed PCKP",
                "guarantee": (
                    "proven-optimal dependency-closed selection"
                    if solution.status is PckpStatus.OPTIMAL
                    else "feasible best-effort selection with explicit bound"
                ),
            },
        )

    def _compact_learned_pckp(
        self,
        token_budget: int,
        *,
        active_episode_id: str | None,
        protected_episode_ids: set[str],
        relevance_query: str,
        policy: PaskCompactionPolicy,
        before: int,
    ) -> CompactionResult:
        """Solve PCKP with learned coefficients; fall back to static utilities on any failure."""

        predictor = self._utility_predictor
        failure: dict[str, Any] | None = None
        learned: LearnedCoefficients | None = None
        if predictor is None:
            failure = {
                "error_code": "UTILITY_PREDICTOR_MISSING",
                "error": "Strategy LEARNED_PCKP was requested but the store has no "
                "utility_predictor.",
            }
        else:
            context = self._retention_context(token_budget, relevance_query)
            try:
                learned = resolve_learned_coefficients(
                    predictor, context, self._utility_quantization
                )
            except AgentSdkError as error:
                failure = {"error_code": error.code, "error": error.message}
        if learned is None or failure is not None:
            result = self._compact_exact_pckp(
                token_budget,
                active_episode_id=active_episode_id,
                protected_episode_ids=protected_episode_ids,
                relevance_query=relevance_query,
                policy=policy,
                before=before,
            )
            identity = predictor.identity.model_dump(mode="json") if predictor else None
            return result.model_copy(
                update={
                    "dossier": {
                        **result.dossier,
                        "learned_ranking": {
                            "status": "fallback-static-pckp",
                            "requested_strategy": CompactionStrategy.LEARNED_PCKP.value,
                            "predictor": identity,
                            **(failure or {}),
                        },
                    }
                }
            )
        result = self._compact_exact_pckp(
            token_budget,
            active_episode_id=active_episode_id,
            protected_episode_ids=protected_episode_ids,
            relevance_query=relevance_query,
            policy=policy,
            before=before,
            learned=learned,
        )
        solver = result.dossier.get("solver", {})
        return result.model_copy(
            update={
                "strategy": CompactionStrategy.LEARNED_PCKP,
                "dossier": {
                    **result.dossier,
                    "strategy": CompactionStrategy.LEARNED_PCKP.value,
                    "objective": "additive learned ranking utility under dependency-closed PCKP",
                    "learned_ranking": {
                        "status": "applied",
                        "predictor": learned.identity.model_dump(mode="json"),
                        "feature_schema_version": learned.identity.feature_schema_version,
                        "artifact_digest": learned.identity.artifact_digest,
                        "task_digest": result.dossier.get("relevance_query_hash"),
                        "calibration": getattr(predictor, "last_audit", None),
                        "predicted_utilities": learned.raw,
                        "quantized_utilities": learned.quantized,
                        "quantization": learned.policy.model_dump(mode="json"),
                        "clipped_below_zero": learned.clipped_below,
                        "clipped_above_ceiling": learned.clipped_above,
                        "selected_episode_ids": result.dossier.get("retained_episode_ids", []),
                        "solver_status": solver.get("status"),
                        "optimality_gap": solver.get("optimality_gap"),
                        "token_budget": token_budget,
                        "selected_token_cost": solver.get("token_cost"),
                        "guarantee_scope": (
                            "optimal for the quantized learned objective only; makes no claim "
                            "about downstream task performance"
                        ),
                    },
                },
            }
        )

    def _compact_pask(
        self,
        token_budget: int,
        *,
        active_episode_id: str | None,
        protected_episode_ids: set[str],
        relevance_query: str,
        policy: PaskCompactionPolicy,
        before: int,
    ) -> CompactionResult:
        live_ids = {
            record.id
            for record in self._records.values()
            if record.state is not EpisodeState.COMPACTED
        }
        mandatory = set(protected_episode_ids)
        if active_episode_id is not None:
            mandatory.add(active_episode_id)
        mandatory.update(
            record.id
            for record in self._records.values()
            if record.id in live_ids and record.state is EpisodeState.OPEN
        )
        mandatory = self._dependency_closure(mandatory, live_ids)
        mandatory_tokens = self._tokens_for(mandatory)
        query_hash = sha256_hex(relevance_query)
        if mandatory_tokens > token_budget:
            compacted = self._compact_unretained(live_ids - mandatory)
            status = (
                CompactionStatus.PROTECTED_OVER_BUDGET
                if protected_episode_ids
                else CompactionStatus.CONTEXT_DEADLOCK
            )
            return CompactionResult(
                status=status,
                before_tokens=before,
                after_tokens=self.estimate_tokens(),
                compacted_episode_ids=compacted,
                blocked_episode_ids=sorted(mandatory),
                strategy=CompactionStrategy.PASK,
                dossier={
                    "strategy": CompactionStrategy.PASK.value,
                    "token_budget": token_budget,
                    "mandatory_tokens": mandatory_tokens,
                    "mandatory_episode_ids": sorted(mandatory),
                    "relevance_query_hash": query_hash,
                    "reason": (
                        "Mandatory protected/open/manifest-required dependency closure "
                        "exceeds budget."
                    ),
                },
            )

        retained = set(mandatory)
        retained_tokens = mandatory_tokens
        selected_utilities: list[EpisodeUtility] = []
        covered_terms = self._terms_for(retained)
        candidates = sorted(live_ids - retained)
        live_records = [self._require(item_id) for item_id in live_ids]
        max_dependents = max((len(value.depended_on_by) for value in live_records), default=0)
        max_access_count = max((value.access_count for value in live_records), default=0)
        max_access_sequence = max((value.last_access_sequence for value in live_records), default=0)
        while candidates:
            options: list[tuple[EpisodeUtility, set[str]]] = []
            for candidate_id in candidates:
                closure = self._dependency_closure({candidate_id}, live_ids)
                additions = closure - retained
                if not additions:
                    continue
                cost = self._tokens_for(additions)
                if retained_tokens + cost > token_budget:
                    continue
                utility = self._utility(
                    candidate_id,
                    additions,
                    covered_terms,
                    relevance_query,
                    policy,
                    cost,
                    live_ids,
                    max_dependents,
                    max_access_count,
                    max_access_sequence,
                )
                options.append((utility, additions))
            if not options:
                break
            utility, additions = max(
                options,
                key=lambda item: (
                    item[0].utility_to_cost,
                    item[0].utility,
                    -item[0].marginal_token_cost,
                    item[0].episode_id,
                ),
            )
            retained.update(additions)
            retained_tokens += utility.marginal_token_cost
            covered_terms.update(self._terms_for(additions))
            selected_utilities.append(utility)
            candidates = [
                candidate_id for candidate_id in candidates if candidate_id not in retained
            ]

        compacted = self._compact_unretained(live_ids - retained)
        after = self.estimate_tokens()
        status = CompactionStatus.COMPACTED if compacted else CompactionStatus.WITHIN_BUDGET
        return CompactionResult(
            status=status,
            before_tokens=before,
            after_tokens=after,
            compacted_episode_ids=compacted,
            strategy=CompactionStrategy.PASK,
            dossier={
                "strategy": CompactionStrategy.PASK.value,
                "token_budget": token_budget,
                "relevance_query_hash": query_hash,
                "mandatory_episode_ids": sorted(mandatory),
                "retained_episode_ids": sorted(retained),
                "selected_utilities": [
                    utility.model_dump(mode="json") for utility in selected_utilities
                ],
                "policy": policy.model_dump(mode="json"),
                "objective": (
                    "deterministic weighted utility with marginal lexical feature coverage"
                ),
                "guarantee": (
                    "dependency closure is retained; no global approximation bound is claimed"
                ),
            },
        )

    def _static_utility(
        self,
        episode_id: str,
        relevance_query: str,
        policy: PaskCompactionPolicy,
        live_ids: set[str],
        max_dependents: int,
        max_access_count: int,
        max_access_sequence: int,
    ) -> EpisodeUtility:
        """Return a stable per-episode utility for exact additive PCKP.

        Unlike ``_utility``, this deliberately excludes diversity because the
        marginal new-term feature depends on the other selected episodes.
        The three ``max_*`` normalization bounds are identical for every
        episode in one ``_compact_exact_pckp`` call, so the caller computes
        them once instead of rescanning every live record per episode.
        """

        record = self._require(episode_id)
        relevance = _clamp_score(self._relevance_scorer.score(relevance_query, record))
        centrality = (
            len([child for child in record.depended_on_by if child in live_ids]) / max_dependents
            if max_dependents
            else 0.0
        )
        provenance = _provenance_weight(record)
        recency = record.last_access_sequence / max_access_sequence if max_access_sequence else 0.0
        frequency = record.access_count / max_access_count if max_access_count else 0.0
        utility = (
            policy.task_relevance_weight * relevance
            + policy.dependency_centrality_weight * centrality
            + policy.provenance_weight * provenance
            + policy.recency_weight * recency
            + policy.frequency_weight * frequency
        )
        cost = _episode_tokens(record)
        return EpisodeUtility(
            episode_id=episode_id,
            utility=utility,
            utility_to_cost=utility / max(cost, 1),
            marginal_token_cost=cost,
            task_relevance=relevance,
            dependency_centrality=centrality,
            provenance=provenance,
            recency=recency,
            frequency=frequency,
            diversity=0,
            dependency_closure=sorted(self._dependency_closure({episode_id}, live_ids)),
        )

    def _utility(
        self,
        episode_id: str,
        additions: set[str],
        covered_terms: set[str],
        relevance_query: str,
        policy: PaskCompactionPolicy,
        marginal_cost: int,
        live_ids: set[str],
        max_dependents: int,
        max_access_count: int,
        max_access_sequence: int,
    ) -> EpisodeUtility:
        """Return the marginal greedy utility of retaining one PASK candidate.

        ``live_ids`` and the three ``max_*`` bounds are constant for every
        candidate considered across every round of one ``_compact_pask``
        call, so the caller computes them once up front rather than paying
        for a full live-record rescan on each candidate evaluated here.
        """

        record = self._require(episode_id)
        relevance = _clamp_score(self._relevance_scorer.score(relevance_query, record))
        centrality = (
            len([child for child in record.depended_on_by if child in live_ids]) / max_dependents
            if max_dependents
            else 0.0
        )
        provenance = _provenance_weight(record)
        recency = record.last_access_sequence / max_access_sequence if max_access_sequence else 0.0
        frequency = record.access_count / max_access_count if max_access_count else 0.0
        candidate_terms = self._terms_for(additions)
        new_terms = candidate_terms - covered_terms
        diversity = len(new_terms) / len(candidate_terms) if candidate_terms else 0.0
        utility = (
            policy.task_relevance_weight * relevance
            + policy.dependency_centrality_weight * centrality
            + policy.provenance_weight * provenance
            + policy.recency_weight * recency
            + policy.frequency_weight * frequency
            + policy.diversity_weight * diversity
        )
        return EpisodeUtility(
            episode_id=episode_id,
            utility=utility,
            utility_to_cost=utility / max(marginal_cost, 1),
            marginal_token_cost=marginal_cost,
            task_relevance=relevance,
            dependency_centrality=centrality,
            provenance=provenance,
            recency=recency,
            frequency=frequency,
            diversity=diversity,
            dependency_closure=sorted(additions),
        )

    def _dependency_closure(self, seeds: set[str], live_ids: set[str]) -> set[str]:
        closure = set(seeds)
        pending = list(sorted(seeds))
        while pending:
            episode_id = pending.pop()
            record = self._require(episode_id)
            if episode_id not in live_ids:
                raise ValueError(
                    f'Live retention cannot depend on compacted episode "{episode_id}".'
                )
            for dependency_id in record.depends_on:
                if dependency_id not in live_ids:
                    raise ValueError(
                        f'Live episode "{episode_id}" depends on compacted episode '
                        f'"{dependency_id}".'
                    )
                if dependency_id not in closure:
                    closure.add(dependency_id)
                    pending.append(dependency_id)
        return closure

    def _compact_unretained(self, unretained: set[str]) -> list[str]:
        """Tombstone unretained leaves first so retained dependency closure is intact."""

        pending = set(unretained)
        compacted: list[str] = []
        while pending:
            leaves = [
                episode_id
                for episode_id in sorted(pending)
                if not any(
                    child_id in pending for child_id in self._require(episode_id).depended_on_by
                )
            ]
            if not leaves:
                raise ValueError("Unretained episode subgraph is not dependency-compactable.")
            for episode_id in leaves:
                self._compact_record(episode_id)
                pending.remove(episode_id)
                compacted.append(episode_id)
        return compacted

    def _open(
        self,
        owner_id: str,
        kind: EpisodeKind,
        *,
        dependencies: list[str] | None = None,
        substrate_backed: bool = False,
        snapshot_version: str | None = None,
        content: dict[str, Any] | None = None,
    ) -> EpisodeRecord:
        record = EpisodeRecord(
            id=f"episode-{self._next_id}",
            owner_id=owner_id,
            kind=kind,
            substrate_backed=substrate_backed,
            snapshot_version=snapshot_version,
            depends_on=list(dependencies or []),
            content=content,
        )
        self._next_id += 1
        self._records[record.id] = record
        return record

    def _eligible(
        self,
        active_episode_id: str | None,
        protected_episode_ids: set[str],
    ) -> Iterable[EpisodeRecord]:
        candidates = [
            record
            for record in self.list()
            if record.id != active_episode_id
            and record.id not in protected_episode_ids
            and record.state is EpisodeState.CLOSED
            and not record.depended_on_by
        ]

        def priority(record: EpisodeRecord) -> tuple[int, str]:
            if record.kind is EpisodeKind.ACTION:
                return (0, record.id)
            if record.substrate_backed:
                return (1, record.id)
            return (2, record.id)

        return iter(sorted(candidates, key=priority))

    @staticmethod
    def _structural(record: EpisodeRecord) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id": record.id,
            "owner_id": record.owner_id,
            "kind": record.kind.value,
            "state": record.state.value,
            "substrate_backed": record.substrate_backed,
            "snapshot_version": record.snapshot_version,
            "depends_on": record.depends_on,
            "depended_on_by": record.depended_on_by,
            "description": record.description,
            "tombstone": record.tombstone,
            "access_count": record.access_count,
            "last_access_sequence": record.last_access_sequence,
        }
        for name in ("source_handle", "summary_id", "archive_id", "content_hash"):
            value = getattr(record, name)
            if value is not None:
                entry[name] = value
        return entry

    def _compact_record(self, episode_id: str) -> None:
        record = self._require(episode_id)
        tombstone = record.tombstone or f"compacted:{record.id}"
        keep_content = self._semantic is not None and not self._archive_intact(record)
        compacted = record.model_copy(
            update={
                "state": EpisodeState.COMPACTED,
                "content": record.content if keep_content else None,
                "tombstone": tombstone,
            }
        )
        self._records[episode_id] = compacted
        for dependency_id in record.depends_on:
            dependency = self._require(dependency_id)
            self._records[dependency_id] = dependency.model_copy(
                update={
                    "depended_on_by": [
                        child_id for child_id in dependency.depended_on_by if child_id != episode_id
                    ]
                }
            )

    def _archive_intact(self, record: EpisodeRecord) -> bool:
        if self._archive is None or record.summary_id is None or record.archive_id is None:
            return False
        try:
            self._archive.get(record.archive_id)
        except Exception:
            return False
        return True

    def _pending(self) -> list[EpisodeRecord]:
        return sorted(
            (
                record
                for record in self._records.values()
                if record.state is EpisodeState.COMPACTED
                and record.content is not None
                and record.summary_id is None
            ),
            key=lambda record: episode_order_key(record.id),
        )

    def _known(self, episode_id: str) -> bool:
        return episode_id in self._records or episode_id in self._evicted

    @property
    def memory_version(self) -> int:
        return self._version

    def background_counts(self) -> dict[str, int]:
        scheduler = self._scheduler
        if scheduler is None:
            return {"queued": 0, "running": 0, "abandoned": 0}
        return scheduler.counts()

    def close_background(self) -> int:
        scheduler, self._scheduler = self._scheduler, None
        self._jobs.clear()
        return scheduler.close() if scheduler is not None else 0

    def settle_background(self, timeout: float = 30.0) -> SemanticPassReport:
        """Wait for queued summarization to finish and commit what completed; nothing new runs."""

        compactor = self._semantic
        assert compactor is not None
        scheduler = self._scheduler
        report = SemanticPassReport(
            policy_hash=compactor.policy_hash, summarizer_id=compactor.summarizer.summarizer_id
        )
        if scheduler is None:
            return report
        scheduler.wait_idle(timeout)
        self._apply_finished(scheduler, report, self._guard)
        self._finish_background(report, self._guard)
        return report

    def _semantic_pass(self, guard: CompactionGuard | None = None) -> SemanticPassReport:
        compactor = self._semantic
        assert compactor is not None
        if compactor.policy.background_summarization:
            return self._background_pass(guard)
        return self._synchronous_pass(guard)

    def _scheduler_for(self) -> SummarizationScheduler:
        compactor = self._semantic
        assert compactor is not None
        if self._scheduler is None or self._scheduler.closed:
            policy = compactor.policy
            self._scheduler = SummarizationScheduler(
                max_workers=policy.max_background_workers,
                max_queued=policy.max_queued_summarizations,
                job_timeout_seconds=policy.background_job_timeout_seconds,
            )
        return self._scheduler

    def _background_pass(self, guard: CompactionGuard | None = None) -> SemanticPassReport:
        compactor = self._semantic
        archive = self._archive
        assert compactor is not None and archive is not None
        policy = compactor.policy
        scheduler = self._scheduler_for()
        report = SemanticPassReport(
            policy_hash=compactor.policy_hash, summarizer_id=compactor.summarizer.summarizer_id
        )
        self._apply_finished(scheduler, report, guard)
        inflight = scheduler.pending_keys()
        for members in compactor.plan(self._pending()):
            self._ensure_active(guard)
            ids = [record.id for record in members]
            group_id = f"group-{sha256_hex(','.join(ids))[:12]}"
            attempts = self._group_attempts.get(group_id, 0)
            if group_id in inflight or attempts >= policy.max_group_attempts:
                continue
            summary_id = f"summary-{self._next_summary_id}"

            def work(
                group_id: str = group_id,
                members: list[EpisodeRecord] = members,
                summary_id: str = summary_id,
            ) -> Any:
                return compactor.summarize_group(
                    group_id,
                    members,
                    summary_id=summary_id,
                    archive=archive,
                    episode_exists=self._known,
                )

            status = scheduler.submit(group_id, work)
            if status == "accepted":
                self._next_summary_id += 1
                self._jobs[group_id] = {"kind": "group", "members": members, "ids": ids}
                report.jobs_submitted += 1
            elif status == "duplicate":
                continue
            else:
                report.jobs_not_accepted[status] = report.jobs_not_accepted.get(status, 0) + 1
                if status in {"full", "unavailable", "closed"}:
                    break
        self._submit_consolidation(scheduler, report, guard)
        self._finish_background(report, guard)
        return report

    def _submit_consolidation(
        self,
        scheduler: SummarizationScheduler,
        report: SemanticPassReport,
        guard: CompactionGuard | None,
    ) -> None:
        compactor = self._semantic
        archive = self._archive
        assert compactor is not None and archive is not None
        policy = compactor.policy
        if any(job["kind"] == "consolidation" for job in self._jobs.values()):
            return
        top = self.active_summaries()
        if not self._over_summary_limits(top):
            return
        children: list[SemanticEpisodeSummary] = []
        for level in range(1, policy.max_level):
            same = [item for item in top if item.level == level]
            if len(same) >= policy.consolidation_group_size:
                children = same[: policy.consolidation_group_size]
                break
        if not children:
            return
        group_id = f"consolidation-{sha256_hex(','.join(c.summary_id for c in children))[:12]}"
        if self._group_attempts.get(group_id, 0) >= policy.max_group_attempts:
            return
        summary_id = f"summary-{self._next_summary_id}"

        def work() -> Any:
            return compactor.consolidate(
                group_id,
                children,
                summary_id=summary_id,
                archive=archive,
                episode_exists=self._known,
            )

        status = scheduler.submit(group_id, work)
        if status == "accepted":
            self._next_summary_id += 1
            self._jobs[group_id] = {"kind": "consolidation", "children": children}
            report.jobs_submitted += 1
        elif status != "duplicate":
            report.jobs_not_accepted[status] = report.jobs_not_accepted.get(status, 0) + 1

    def _apply_finished(
        self,
        scheduler: SummarizationScheduler,
        report: SemanticPassReport,
        guard: CompactionGuard | None,
    ) -> None:
        compactor = self._semantic
        assert compactor is not None
        policy = compactor.policy
        for finished in scheduler.drain():
            job = self._jobs.pop(finished.key, None)
            if job is None:
                continue
            if finished.timed_out or finished.error is not None:
                self._group_attempts[finished.key] = self._group_attempts.get(finished.key, 0) + 1
                if finished.timed_out:
                    report.jobs_timed_out += 1
                else:
                    report.jobs_failed += 1
                continue
            result = finished.value
            with self._commit_lock:
                self._ensure_active(guard)
                report.outcomes.append(result.outcome)
                if job["kind"] == "group":
                    committed = result.summary is not None and self._commit_summary(
                        result.summary, result.archived
                    )
                else:
                    committed = result.summary is not None and self._commit_consolidation(
                        result.summary, job["children"], report
                    )
                if committed:
                    report.jobs_committed += 1
                    if job["kind"] == "group":
                        report.committed_summary_ids.append(result.summary.summary_id)
                    continue
                if result.summary is not None:
                    report.jobs_stale += 1
                    continue
                self._group_attempts[finished.key] = self._group_attempts.get(finished.key, 0) + 1
                if result.rejected is not None and policy.on_semantic_failure == "quarantine":
                    self._ledger.quarantine(
                        QuarantinedSummary(
                            summary=result.rejected,
                            group_id=finished.key,
                            reasons=[result.outcome.reason or "verification failed"],
                            recorded_at_utc=datetime.now(UTC).isoformat(),
                        ),
                        policy.max_quarantined,
                    )
                    report.quarantined_summary_ids.append(result.rejected.summary_id)

    def _commit_consolidation(
        self,
        summary: SemanticEpisodeSummary,
        children: list[SemanticEpisodeSummary],
        report: SemanticPassReport,
    ) -> bool:
        for child in children:
            ref = self._ledger.ref(child.summary_id)
            if (
                ref is None
                or ref.lifecycle is not SummaryLifecycle.ACTIVE
                or ref.integrity_hash != child.integrity_hash
                or not self._ledger.is_resident(child.summary_id)
            ):
                return False
        self._ledger.supersede(summary, children)
        self._version += 1
        report.consolidated_summary_ids.append(summary.summary_id)
        return True

    def _finish_background(self, report: SemanticPassReport, guard: CompactionGuard | None) -> None:
        self._archive_superseded(report, guard)
        self._enforce_summary_ceilings(report, guard)
        with self._commit_lock:
            self._ensure_active(guard)
            report.evicted_episode_ids = self._evict_graph()
        report.pending_episode_ids = self.pending_episode_ids()
        active = self.active_summaries()
        report.active_summary_count = len(active)
        report.active_summary_tokens = sum(item.summary_tokens for item in active)
        counts = self.background_counts()
        report.jobs_queued = counts["queued"]
        report.jobs_running = counts["running"]

    def _synchronous_pass(self, guard: CompactionGuard | None = None) -> SemanticPassReport:
        compactor = self._semantic
        archive = self._archive
        assert compactor is not None and archive is not None
        policy = compactor.policy
        report = SemanticPassReport(
            policy_hash=compactor.policy_hash, summarizer_id=compactor.summarizer.summarizer_id
        )
        for members in compactor.plan(self._pending()):
            self._ensure_active(guard)
            ids = [record.id for record in members]
            group_id = f"group-{sha256_hex(','.join(ids))[:12]}"
            if self._group_attempts.get(group_id, 0) >= policy.max_group_attempts:
                continue
            result = compactor.summarize_group(
                group_id,
                members,
                summary_id=f"summary-{self._next_summary_id}",
                archive=archive,
                episode_exists=self._known,
            )
            with self._commit_lock:
                self._ensure_active(guard)
                report.outcomes.append(result.outcome)
                if result.summary is not None and self._commit_summary(
                    result.summary, result.archived
                ):
                    report.committed_summary_ids.append(result.summary.summary_id)
                    continue
                if result.rejected is not None and policy.on_semantic_failure == "quarantine":
                    self._ledger.quarantine(
                        QuarantinedSummary(
                            summary=result.rejected,
                            group_id=group_id,
                            reasons=[result.outcome.reason or "verification failed"],
                            recorded_at_utc=datetime.now(UTC).isoformat(),
                        ),
                        policy.max_quarantined,
                    )
                    report.quarantined_summary_ids.append(result.rejected.summary_id)
                self._group_attempts[group_id] = self._group_attempts.get(group_id, 0) + 1
                if (
                    policy.failure_policy == "release-after-archive"
                    and len(result.archived) == len(members)
                    and self._group_attempts[group_id] >= policy.max_group_attempts
                ):
                    self._release_archived(members, result.archived)
                    report.released_without_summary.extend(ids)
        self._consolidate(report, guard)
        self._archive_superseded(report, guard)
        self._enforce_summary_ceilings(report, guard)
        with self._commit_lock:
            self._ensure_active(guard)
            report.evicted_episode_ids = self._evict_graph()
        report.pending_episode_ids = self.pending_episode_ids()
        active = self.active_summaries()
        report.active_summary_count = len(active)
        report.active_summary_tokens = sum(item.summary_tokens for item in active)
        return report

    def _commit_summary(self, summary: SemanticEpisodeSummary, archived: dict[str, Any]) -> bool:
        updates: dict[str, EpisodeRecord] = {}
        for episode_id in summary.source_episode_ids:
            record = self._records.get(episode_id)
            receipt = archived.get(episode_id)
            if (
                record is None
                or receipt is None
                or record.state is not EpisodeState.COMPACTED
                or record.summary_id is not None
            ):
                return False
            if sha256_hex(snapshot_text(archivable(record))) != receipt.content_hash:
                return False
            updates[episode_id] = record.model_copy(
                update={
                    "content": None,
                    "summary_id": summary.summary_id,
                    "archive_id": receipt.archive_id,
                    "content_hash": receipt.content_hash,
                }
            )
        self._ledger.add(summary, updates)
        self._records.update(updates)
        self._version += 1
        if summary.summary_id.removeprefix("summary-").isdigit():
            self._next_summary_id = max(
                self._next_summary_id, int(summary.summary_id.removeprefix("summary-")) + 1
            )
        return True

    def _release_archived(self, members: list[EpisodeRecord], archived: dict[str, Any]) -> None:
        for record in members:
            receipt = archived[record.id]
            self._records[record.id] = record.model_copy(
                update={
                    "content": None,
                    "archive_id": receipt.archive_id,
                    "content_hash": receipt.content_hash,
                }
            )

    def _over_summary_limits(self, top: list[SemanticEpisodeSummary]) -> bool:
        compactor = self._semantic
        assert compactor is not None
        policy = compactor.policy
        count_limit = policy.consolidate_when_summaries or None
        if policy.max_active_summaries is not None:
            count_limit = (
                min(count_limit, policy.max_active_summaries)
                if count_limit
                else policy.max_active_summaries
            )
        if count_limit is not None and len(top) > count_limit:
            return True
        return (
            policy.max_active_summary_tokens is not None
            and sum(item.summary_tokens for item in top) > policy.max_active_summary_tokens
        )

    def _consolidate(
        self, report: SemanticPassReport, guard: CompactionGuard | None = None
    ) -> None:
        compactor = self._semantic
        archive = self._archive
        assert compactor is not None and archive is not None
        policy = compactor.policy
        while True:
            self._ensure_active(guard)
            top = self.active_summaries()
            if not self._over_summary_limits(top):
                return
            children: list[SemanticEpisodeSummary] = []
            for level in range(1, policy.max_level):
                same = [item for item in top if item.level == level]
                if len(same) >= policy.consolidation_group_size:
                    children = same[: policy.consolidation_group_size]
                    break
            if not children:
                return
            group_id = f"consolidation-{sha256_hex(','.join(c.summary_id for c in children))[:12]}"
            if self._group_attempts.get(group_id, 0) >= policy.max_group_attempts:
                return
            result = compactor.consolidate(
                group_id,
                children,
                summary_id=f"summary-{self._next_summary_id}",
                archive=archive,
                episode_exists=self._known,
            )
            with self._commit_lock:
                self._ensure_active(guard)
                report.outcomes.append(result.outcome)
                if result.summary is None:
                    self._group_attempts[group_id] = self._group_attempts.get(group_id, 0) + 1
                    return
                if any(
                    self._ledger.lifecycle_of(child.summary_id) is not SummaryLifecycle.ACTIVE
                    for child in children
                ):
                    return
                self._ledger.supersede(result.summary, children)
                self._next_summary_id += 1
                report.consolidated_summary_ids.append(result.summary.summary_id)

    def _archive_superseded(
        self, report: SemanticPassReport, guard: CompactionGuard | None = None
    ) -> None:
        compactor = self._semantic
        assert compactor is not None
        if not compactor.policy.archive_superseded_summaries:
            return
        for summary in self._ledger.awaiting_archive():
            self._ensure_active(guard)
            try:
                archive_ref = self._ledger.write_to_archive(summary)
            except Exception as error:
                report.summary_archive_failures.append(
                    f"{summary.summary_id}: {type(error).__name__}: {error}"[:300]
                )
                continue
            with self._commit_lock:
                self._ensure_active(guard)
                self._ledger.mark_archived(summary.summary_id, archive_ref)
            report.archived_summary_ids.append(summary.summary_id)

    def _pinned_summary_ids(self) -> set[str]:
        live = {
            record.id
            for record in self._records.values()
            if record.state is not EpisodeState.COMPACTED
        }
        pinned: set[str] = set()
        for record in self._records.values():
            if record.state is not EpisodeState.COMPACTED and record.summary_id is not None:
                pinned.add(self._ledger.top_for(record.summary_id))
        for summary in self.active_summaries():
            if live.intersection(summary.source_episode_ids):
                pinned.add(summary.summary_id)
        return pinned

    def _enforce_summary_ceilings(
        self, report: SemanticPassReport, guard: CompactionGuard | None = None
    ) -> None:
        compactor = self._semantic
        assert compactor is not None
        policy = compactor.policy
        if policy.max_active_summaries is None and policy.max_active_summary_tokens is None:
            return
        while True:
            self._ensure_active(guard)
            top = self.active_summaries()
            over_count = (
                policy.max_active_summaries is not None and len(top) > policy.max_active_summaries
            )
            over_tokens = (
                policy.max_active_summary_tokens is not None
                and sum(item.summary_tokens for item in top) > policy.max_active_summary_tokens
            )
            if not (over_count or over_tokens):
                return
            pinned = self._pinned_summary_ids()
            candidates = [item for item in top if item.summary_id not in pinned]
            if not candidates:
                report.pinned_summaries_over_ceiling = len(top)
                return
            victim = candidates[0]
            try:
                archive_ref = self._ledger.write_to_archive(victim)
            except Exception as error:
                report.summary_archive_failures.append(
                    f"{victim.summary_id}: {type(error).__name__}: {error}"[:300]
                )
                return
            with self._commit_lock:
                self._ensure_active(guard)
                self._ledger.mark_archived(
                    victim.summary_id, archive_ref, lifecycle=SummaryLifecycle.ARCHIVED
                )
            report.archived_summary_ids.append(victim.summary_id)

    def _evict_graph(self) -> list[str]:
        compactor = self._semantic
        if compactor is None:
            return []
        policy = compactor.policy
        if policy.max_active_records is None and policy.max_active_edges is None:
            return []

        def over() -> bool:
            if (
                policy.max_active_records is not None
                and len(self._records) > policy.max_active_records
            ):
                return True
            edges = sum(len(record.depends_on) for record in self._records.values())
            return policy.max_active_edges is not None and edges > policy.max_active_edges

        live_dependencies = {
            dependency
            for record in self._records.values()
            if record.state is not EpisodeState.COMPACTED
            for dependency in record.depends_on
        }
        candidates = sorted(
            (
                record
                for record in self._records.values()
                if record.state is EpisodeState.COMPACTED
                and record.content is None
                and record.archive_id is not None
                and record.content_hash is not None
                and record.id not in live_dependencies
                and not any(
                    child in self._records
                    and self._records[child].state is not EpisodeState.COMPACTED
                    for child in record.depended_on_by
                )
            ),
            key=lambda record: episode_order_key(record.id),
        )
        evicted: list[str] = []
        for record in candidates:
            if not over():
                break
            self._evicted[record.id] = EvictedEpisode(
                episode_id=record.id,
                owner_id=record.owner_id,
                kind=record.kind,
                description=record.description,
                depends_on=list(record.depends_on),
                tombstone=record.tombstone or f"compacted:{record.id}",
                summary_id=record.summary_id,
                archive_id=record.archive_id or "",
                content_hash=record.content_hash or "",
            )
            del self._records[record.id]
            evicted.append(record.id)
        return evicted

    def rehydrate(self, episode_ids: Iterable[str], *, token_budget: int) -> RehydrationResult:
        """Restore archived episodes (and archived prerequisites) to live, within a budget."""

        if token_budget < 0:
            raise ValueError("rehydration token budget may not be negative")
        archive = self._archive
        restored: list[str] = []
        skipped: dict[str, str] = {}
        spent = 0
        for episode_id in dict.fromkeys(episode_ids):
            record = self.get(episode_id)
            if record is None:
                skipped[episode_id] = "unknown episode"
                continue
            if record.state is not EpisodeState.COMPACTED:
                skipped[episode_id] = "already live"
                continue
            if record.content is None and (archive is None or record.archive_id is None):
                skipped[episode_id] = "not archived"
                continue
            try:
                plan = self._rehydration_plan(record, archive)
            except AgentSdkError as error:
                skipped[episode_id] = f"{error.code}: {error.message}"[:300]
                continue
            cost = sum(_episode_tokens(snapshot) for snapshot in plan)
            if spent + cost > token_budget:
                skipped[episode_id] = f"needs {cost} tokens but {token_budget - spent} remain"
                continue
            for snapshot in plan:
                self._restore(snapshot)
                restored.append(snapshot.id)
            spent += cost
        return RehydrationResult(
            restored_episode_ids=restored,
            skipped=skipped,
            restored_tokens=spent,
            token_budget=token_budget,
        )

    def can_rehydrate(self, episode_id: str) -> bool:
        record = self.get(episode_id)
        return (
            record is not None
            and record.state is EpisodeState.COMPACTED
            and (record.content is not None or record.archive_id is not None)
        )

    def _rehydration_plan(
        self, record: EpisodeRecord, archive: EpisodeArchive | None
    ) -> list[EpisodeRecord]:
        plan: dict[str, EpisodeRecord] = {}
        pending = [record.id]
        while pending:
            current_id = pending.pop()
            if current_id in plan:
                continue
            current = self.get(current_id)
            if current is None:
                raise AgentSdkError(
                    "EPISODE_PREREQUISITE_UNKNOWN",
                    f'Prerequisite episode "{current_id}" is not known to the store.',
                )
            if current.state is not EpisodeState.COMPACTED:
                continue
            if current.content is not None:
                snapshot = archivable(current)
            elif current.archive_id is not None and archive is not None:
                snapshot = archive.get(current.archive_id)
            else:
                raise AgentSdkError(
                    "EPISODE_PREREQUISITE_NOT_ARCHIVED",
                    f'Prerequisite episode "{current_id}" has no archived original to restore.',
                )
            plan[current_id] = snapshot
            pending.extend(snapshot.depends_on)
        return sorted(plan.values(), key=lambda snapshot: episode_order_key(snapshot.id))

    def _restore(self, snapshot: EpisodeRecord) -> None:
        current = self.get(snapshot.id)
        restored = snapshot.model_copy(
            update={
                "state": EpisodeState.CLOSED,
                "tombstone": None,
                "depended_on_by": [],
                "summary_id": current.summary_id if current else snapshot.summary_id,
                "archive_id": current.archive_id if current else snapshot.archive_id,
                "content_hash": current.content_hash if current else snapshot.content_hash,
            }
        )
        self._records[snapshot.id] = restored
        self._evicted.pop(snapshot.id, None)
        for dependency_id in restored.depends_on:
            prerequisite = self._records.get(dependency_id)
            if prerequisite is not None and snapshot.id not in prerequisite.depended_on_by:
                self._records[dependency_id] = prerequisite.model_copy(
                    update={"depended_on_by": [*prerequisite.depended_on_by, snapshot.id]}
                )

    def _tokens_for(self, episode_ids: Iterable[str]) -> int:
        return sum(_episode_tokens(self._require(episode_id)) for episode_id in episode_ids)

    def _protected_tokens(self, protected: set[str], active_episode_id: str | None) -> int:
        protected_ids = set(protected)
        if active_episode_id is not None:
            protected_ids.add(active_episode_id)
        if not protected_ids:
            return 0
        return self._tokens_for(
            self._dependency_closure(
                protected_ids,
                {record.id for record in self.list() if record.state is not EpisodeState.COMPACTED},
            )
        )

    def _terms_for(self, episode_ids: Iterable[str]) -> set[str]:
        return set().union(
            *(_episode_terms(self._require(episode_id)) for episode_id in episode_ids)
        )

    def _require(self, episode_id: str) -> EpisodeRecord:
        record = self._records.get(episode_id)
        if record is None:
            raise ValueError(f'Episode "{episode_id}" is unknown.')
        return record


class FileEpisodeStore(InMemoryEpisodeStore):
    """Persist active episode state and structural-only checkpoints atomically."""

    def __init__(
        self,
        run_root: Path,
        *,
        compaction_policy: PaskCompactionPolicy | None = None,
        relevance_scorer: EpisodeRelevanceScorer | None = None,
        semantic: SemanticCompactor | None = None,
        retain_generations: int = 3,
        utility_predictor: EpisodeUtilityPredictor | None = None,
        utility_quantization: QuantizationPolicy | None = None,
        retention_observer: Callable[[RetentionDecision], None] | None = None,
        retention_feature_schema: str = FEATURE_SCHEMA_VERSION,
    ) -> None:
        if retain_generations < 1:
            raise ValueError("retain_generations must be at least 1.")
        self._retain_generations = retain_generations
        self._run_root = run_root.resolve()
        self._memory_root = self._run_root / ".agent-memory"
        self._memory_root.mkdir(parents=True, exist_ok=True)
        super().__init__(
            compaction_policy=compaction_policy,
            relevance_scorer=relevance_scorer,
            semantic=semantic,
            utility_predictor=utility_predictor,
            utility_quantization=utility_quantization,
            retention_observer=retention_observer,
            retention_feature_schema=retention_feature_schema,
        )
        self._state_path = self._memory_root / "episodes.json"
        self._checkpoint_path = self._memory_root / "checkpoint.json"

    def _default_archive(self) -> EpisodeArchive:
        return FileEpisodeArchive(self._run_root)

    @property
    def snapshot_directory(self) -> Path:
        return self._memory_root / SNAPSHOT_DIRECTORY

    def snapshot_generations(self) -> list[int]:
        directory = self.snapshot_directory
        if not directory.is_dir():
            return []
        numbers = []
        for path in directory.glob(f"{SNAPSHOT_PREFIX}*{SNAPSHOT_SUFFIX}"):
            stem = path.name[len(SNAPSHOT_PREFIX) : -len(SNAPSHOT_SUFFIX)]
            if stem.isdigit():
                numbers.append(int(stem))
        return sorted(numbers)

    def _snapshot_path(self, generation: int) -> Path:
        return self.snapshot_directory / f"{SNAPSHOT_PREFIX}{generation:010d}{SNAPSHOT_SUFFIX}"

    def persist(self) -> int:
        self.snapshot_directory.mkdir(parents=True, exist_ok=True)
        existing = self.snapshot_generations()
        generation = (existing[-1] if existing else 0) + 1
        snapshot = self.export_snapshot(generation)
        target = self._snapshot_path(generation)
        temporary = unique_temporary_path(target)
        try:
            with temporary.open("w", encoding="utf-8", newline="") as stream:
                stream.write(snapshot.model_dump_json())
                stream.flush()
                os.fsync(stream.fileno())
            replace_atomic(temporary, target)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise AgentSdkError(
                "EPISODE_MEMORY_PERSIST_FAILED",
                f'Could not write memory snapshot generation {generation} to "{target}": '
                f"{type(error).__name__}: {error}",
                {"generation": generation},
            ) from error
        fsync_directory(self.snapshot_directory)
        for stale in self.snapshot_generations()[: -self._retain_generations]:
            self._snapshot_path(stale).unlink(missing_ok=True)
        self._atomic_write(
            self._state_path, json.dumps([record.model_dump(mode="json") for record in self.list()])
        )
        return generation

    def restore(self, *, verify_archives: bool = True) -> RestoreReport:
        generations = self.snapshot_generations()
        if not generations:
            raise AgentSdkError(
                "EPISODE_MEMORY_SNAPSHOT_MISSING",
                f'No memory snapshot exists under "{self.snapshot_directory}"; persist() '
                "writes one.",
            )
        rejected: list[SnapshotRejection] = []
        for generation in reversed(generations):
            path = self._snapshot_path(generation)
            try:
                snapshot = EpisodeMemorySnapshot.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if snapshot.generation != generation:
                    raise AgentSdkError(
                        "EPISODE_MEMORY_SNAPSHOT_INVALID",
                        f"the file is named for generation {generation} but holds generation "
                        f"{snapshot.generation}",
                    )
                self.adopt_snapshot(snapshot, verify_archives=verify_archives)
            except AgentSdkError as error:
                problems = error.details.get("problems") if error.details else None
                rejected.append(
                    SnapshotRejection(
                        path=str(path),
                        generation=generation,
                        reasons=list(problems) if problems else [f"{error.code}: {error.message}"],
                    )
                )
                continue
            except (OSError, ValueError) as error:
                rejected.append(
                    SnapshotRejection(
                        path=str(path),
                        generation=generation,
                        reasons=[f"{type(error).__name__}: {error}"[:300]],
                    )
                )
                continue
            return RestoreReport(
                generation=generation,
                path=str(path),
                records=len(snapshot.records),
                evicted=len(snapshot.evicted),
                resident_summaries=len(snapshot.summaries),
                archived_summaries=sum(
                    1 for ref in snapshot.summary_index if ref.archive_ref is not None
                ),
                rejected=rejected,
            )
        detail = "; ".join(f"{item.path}: {item.reasons[0]}" for item in rejected[:3])
        raise AgentSdkError(
            "EPISODE_MEMORY_RESTORE_FAILED",
            f"None of the {len(rejected)} memory snapshot generation(s) is a consistent state: "
            f"{detail}",
            {"rejected": [item.model_dump(mode="json") for item in rejected]},
        )

    def persist_checkpoint(self) -> EpisodeCheckpoint:
        checkpoint = self.checkpoint()
        self._atomic_write(self._checkpoint_path, checkpoint.model_dump_json())
        return checkpoint

    def load_checkpoint(self) -> EpisodeCheckpoint:
        if not self._checkpoint_path.is_file():
            raise ValueError("No persisted episode checkpoint exists.")
        return EpisodeCheckpoint.model_validate_json(
            self._checkpoint_path.read_text(encoding="utf-8")
        )

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = unique_temporary_path(path)
        temporary.write_text(content, encoding="utf-8")
        replace_atomic(temporary, path)
