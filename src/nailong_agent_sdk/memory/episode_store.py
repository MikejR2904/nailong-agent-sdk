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

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..foundations.atomic_io import replace_atomic, unique_temporary_path
from ..foundations.contracts import EpisodeKind
from ..foundations.optimization import ExactPckpSolver, PckpItem, PckpProblem, PckpStatus
from .episode_models import (
    CompactionResult,
    CompactionStatus,
    CompactionStrategy,
    EpisodeCheckpoint,
    EpisodeRecord,
    EpisodeRelevanceScorer,
    EpisodeState,
    EpisodeUtility,
    PaskCompactionPolicy,
)
from .episode_scoring import (
    LexicalEpisodeRelevanceScorer,
    _clamp_score,
    _episode_terms,
    _episode_tokens,
    _provenance_weight,
)


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
    ) -> None:
        self._records: dict[str, EpisodeRecord] = {}
        self._next_id = 1
        self._next_access_sequence = 1
        self._compaction_policy = compaction_policy or PaskCompactionPolicy()
        self._relevance_scorer = relevance_scorer or LexicalEpisodeRelevanceScorer()

    @property
    def compaction_policy(self) -> PaskCompactionPolicy:
        return self._compaction_policy

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
        requires_manifest: bool = False,
        eda_manifest: dict[str, Any] | None = None,
    ) -> EpisodeRecord:
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("action episode dependencies must be unique")
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
            requires_manifest=requires_manifest,
            eda_manifest=eda_manifest,
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

    def attach_eda_manifest(self, episode_id: str, manifest: dict[str, Any]) -> EpisodeRecord:
        record = self._require(episode_id)
        if record.kind is not EpisodeKind.ACTION:
            raise ValueError("Only action episodes can receive EDA manifests.")
        updated = record.model_copy(update={"eda_manifest": dict(manifest)})
        self._records[episode_id] = updated
        return updated

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
        return self._records.get(episode_id)

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
        """Retain a safe bounded episode set, then tombstone other closed records."""

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
        structural = [
            {
                "id": record.id,
                "owner_id": record.owner_id,
                "kind": record.kind.value,
                "state": record.state.value,
                "substrate_backed": record.substrate_backed,
                "snapshot_version": record.snapshot_version,
                "depends_on": record.depends_on,
                "depended_on_by": record.depended_on_by,
                "description": record.description,
                "requires_manifest": record.requires_manifest,
                "eda_manifest": record.eda_manifest,
                "tombstone": record.tombstone,
                "access_count": record.access_count,
                "last_access_sequence": record.last_access_sequence,
            }
            for record in self.list()
        ]
        encoded = json.dumps(structural, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return EpisodeCheckpoint(
            graph_hash=hashlib.sha256(encoded).hexdigest(),
            episodes=structural,
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
                        "reason": (
                            "No closed, dependency-free, manifest-complete episode is eligible."
                        ),
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
            if record.id in live_ids
            and (
                record.state is EpisodeState.OPEN
                or (
                    record.kind is EpisodeKind.ACTION
                    and record.requires_manifest
                    and record.eda_manifest is None
                )
            )
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
                    utility=round(utilities[episode_id].utility * 1_000_000),
                    prerequisites=self._require(episode_id).depends_on,
                    mandatory=episode_id in mandatory,
                )
                for episode_id in sorted(live_ids)
            ],
        )
        solution = ExactPckpSolver(max_branch_nodes=policy.exact_max_branch_nodes).solve(problem)
        query_hash = hashlib.sha256(relevance_query.encode("utf-8")).hexdigest()
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
            if record.id in live_ids
            and (
                record.state is EpisodeState.OPEN
                or (
                    record.kind is EpisodeKind.ACTION
                    and record.requires_manifest
                    and record.eda_manifest is None
                )
            )
        )
        mandatory = self._dependency_closure(mandatory, live_ids)
        mandatory_tokens = self._tokens_for(mandatory)
        query_hash = hashlib.sha256(relevance_query.encode("utf-8")).hexdigest()
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
        requires_manifest: bool = False,
        eda_manifest: dict[str, Any] | None = None,
    ) -> EpisodeRecord:
        record = EpisodeRecord(
            id=f"episode-{self._next_id}",
            owner_id=owner_id,
            kind=kind,
            substrate_backed=substrate_backed,
            snapshot_version=snapshot_version,
            depends_on=list(dependencies or []),
            content=content,
            requires_manifest=requires_manifest,
            eda_manifest=eda_manifest,
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
            and not (
                record.kind is EpisodeKind.ACTION
                and record.requires_manifest
                and record.eda_manifest is None
            )
        ]

        def priority(record: EpisodeRecord) -> tuple[int, str]:
            if record.kind is EpisodeKind.ACTION:
                return (0, record.id)
            if record.substrate_backed:
                return (1, record.id)
            return (2, record.id)

        return iter(sorted(candidates, key=priority))

    def _compact_record(self, episode_id: str) -> None:
        record = self._require(episode_id)
        tombstone = record.tombstone or f"compacted:{record.id}"
        compacted = record.model_copy(
            update={"state": EpisodeState.COMPACTED, "content": None, "tombstone": tombstone}
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
    ) -> None:
        super().__init__(compaction_policy=compaction_policy, relevance_scorer=relevance_scorer)
        self._run_root = run_root.resolve()
        self._memory_root = self._run_root / ".agent-memory"
        self._memory_root.mkdir(parents=True, exist_ok=True)
        self._state_path = self._memory_root / "episodes.json"
        self._checkpoint_path = self._memory_root / "checkpoint.json"

    def persist(self) -> None:
        self._atomic_write(
            self._state_path, json.dumps([record.model_dump(mode="json") for record in self.list()])
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
