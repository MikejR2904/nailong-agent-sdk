# Copyright (c) 2026 David Michael Indraputra

"""Typed episode records and PASK/PCKP compaction contracts.

The baseline compactor follows the framework's safe episode lifecycle (updated
systems-design PDF, pp. 62-64). ``PaskCompactionPolicy`` weights are deterministic;
relevance itself is supplied by a host-selectable scorer such as the lexical
default in :mod:`episode_scoring`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import Field, field_validator, model_validator

from ..foundations.contracts import EpisodeKind, StrictModel
from ..foundations.hashing import canonical_hash
from ..foundations.identifiers import require_unique

SUMMARY_VIEW_SOURCE_LIMIT = 16


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
    source_handle: str | None = None
    summary_id: str | None = None
    archive_id: str | None = None
    content_hash: str | None = None

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


class SummaryVerificationStatus(StrEnum):
    VERIFIED = "verified"
    REJECTED = "rejected"


class SummaryLifecycle(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class VerificationCheck(StrictModel):
    name: str = Field(min_length=1)
    passed: bool
    detail: str = ""


VerificationKind = Literal[
    "deterministic-extractive",
    "heuristic-entailment-screen",
    "claim-judge",
    "summary-judge",
]


class VerificationMethod(StrictModel):
    """One verification stage a summary went through, with the policy it ran under."""

    kind: VerificationKind
    verifier_id: str = Field(min_length=1)
    verifier_version: str = "1"
    policy_hash: str = Field(min_length=1)
    passed: bool
    checks: list[VerificationCheck] = Field(default_factory=list)


class SummaryVerificationRecord(StrictModel):
    """Which methods verified a summary; a deterministic pass is not semantic entailment."""

    methods: list[VerificationMethod] = Field(default_factory=list)

    @property
    def extractive_verified(self) -> bool:
        return any(
            method.kind == "deterministic-extractive" and method.passed for method in self.methods
        )

    @property
    def entailment_checked(self) -> bool:
        return any(
            method.kind in {"claim-judge", "summary-judge", "heuristic-entailment-screen"}
            and method.passed
            for method in self.methods
        )

    @property
    def host_judged(self) -> bool:
        return any(
            method.kind in {"claim-judge", "summary-judge"} and method.passed
            for method in self.methods
        )


class GroundedClaim(StrictModel):
    """One factual statement tied to the episodes and the verbatim excerpt that support it."""

    claim: str = Field(min_length=1, max_length=600)
    source_episode_ids: list[str] = Field(min_length=1)
    evidence: str = Field(min_length=1, max_length=1_000)


class GroundedDecision(StrictModel):
    """A decision taken in the summarized episodes, with its rationale and supporting excerpt."""

    decision: str = Field(min_length=1, max_length=600)
    rationale: str = Field(min_length=1, max_length=600)
    source_episode_ids: list[str] = Field(min_length=1)
    evidence: str = Field(min_length=1, max_length=1_000)


class SemanticSummaryDraft(StrictModel):
    """What a summarizer returns; ids, hashes and provenance are filled in by the SDK."""

    summary_text: str = Field(min_length=1, max_length=4_000)
    key_facts: list[GroundedClaim] = Field(default_factory=list, max_length=64)
    decisions: list[GroundedDecision] = Field(default_factory=list, max_length=32)
    unresolved_issues: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("unresolved_issues")
    @classmethod
    def issues_are_bounded(cls, value: list[str]) -> list[str]:
        for index, issue in enumerate(value):
            if not issue.strip() or len(issue) > 600:
                raise ValueError(
                    f"unresolved_issues[{index}] must be 1 to 600 non-blank characters, "
                    f"got {len(issue)}"
                )
        return value


class SummaryFactView(StrictModel):
    claim: str
    source_episode_ids: list[str]


class SummaryDecisionView(StrictModel):
    decision: str
    rationale: str
    source_episode_ids: list[str]


class SemanticSummaryView(StrictModel):
    """Bounded model-facing form of a verified summary; evidence excerpts are left out."""

    summary_id: str = Field(min_length=1)
    level: int = Field(ge=1)
    summary_text: str
    key_facts: list[SummaryFactView] = Field(default_factory=list)
    decisions: list[SummaryDecisionView] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)
    source_episode_ids: list[str] = Field(default_factory=list)
    source_episode_count: int = Field(default=0, ge=0)
    source_handles: list[str] = Field(default_factory=list)
    source_handle_count: int = Field(default=0, ge=0)


class SemanticEpisodeSummary(StrictModel):
    """A verified, provenance-carrying digest of closed episodes; never authoritative evidence."""

    schema_version: str = "semantic-episode-summary-v2"
    summary_id: str = Field(min_length=1)
    level: int = Field(default=1, ge=1)
    source_episode_ids: list[str] = Field(min_length=1)
    child_summary_ids: list[str] = Field(default_factory=list)
    summary_text: str = Field(min_length=1)
    key_facts: list[GroundedClaim] = Field(default_factory=list)
    decisions: list[GroundedDecision] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)
    dependency_ids: list[str] = Field(default_factory=list)
    source_handles: list[str] = Field(default_factory=list)
    source_hashes: list[str] = Field(min_length=1)
    archive_ids: list[str] = Field(min_length=1)
    verification_status: SummaryVerificationStatus = SummaryVerificationStatus.VERIFIED
    verification: SummaryVerificationRecord = Field(default_factory=SummaryVerificationRecord)
    summarizer_id: str = Field(min_length=1)
    policy_hash: str = Field(min_length=1)
    summary_tokens: int = Field(ge=0)
    source_tokens: int = Field(ge=0)
    integrity_hash: str = ""

    @model_validator(mode="after")
    def provenance_is_consistent(self) -> SemanticEpisodeSummary:
        require_unique(self.source_episode_ids, f'summary "{self.summary_id}" source episodes')
        if len(self.source_hashes) != len(self.source_episode_ids):
            raise ValueError(
                f'summary "{self.summary_id}" has {len(self.source_episode_ids)} source episodes '
                f"but {len(self.source_hashes)} source hashes"
            )
        if len(self.archive_ids) != len(self.source_episode_ids):
            raise ValueError(
                f'summary "{self.summary_id}" has {len(self.source_episode_ids)} source episodes '
                f"but {len(self.archive_ids)} archive ids"
            )
        if self.level == 1 and self.child_summary_ids:
            raise ValueError(f'level-1 summary "{self.summary_id}" cannot have child summaries')
        if self.level > 1 and not self.child_summary_ids:
            raise ValueError(
                f'level-{self.level} summary "{self.summary_id}" needs child summaries'
            )
        return self

    def computed_hash(self) -> str:
        blank = self.model_copy(update={"integrity_hash": ""})
        return canonical_hash(blank.model_dump(mode="json"))

    def sealed(self) -> SemanticEpisodeSummary:
        return self.model_copy(update={"integrity_hash": self.computed_hash()})

    def intact(self) -> bool:
        return bool(self.integrity_hash) and self.integrity_hash == self.computed_hash()

    def view(self) -> SemanticSummaryView:
        return SemanticSummaryView(
            summary_id=self.summary_id,
            level=self.level,
            summary_text=self.summary_text,
            key_facts=[
                SummaryFactView(claim=fact.claim, source_episode_ids=fact.source_episode_ids)
                for fact in self.key_facts
            ],
            decisions=[
                SummaryDecisionView(
                    decision=item.decision,
                    rationale=item.rationale,
                    source_episode_ids=item.source_episode_ids,
                )
                for item in self.decisions
            ],
            unresolved_issues=list(self.unresolved_issues),
            source_episode_ids=_head_and_tail(self.source_episode_ids),
            source_episode_count=len(self.source_episode_ids),
            source_handles=_head_and_tail(self.source_handles),
            source_handle_count=len(self.source_handles),
        )


def _head_and_tail(items: list[str], limit: int = SUMMARY_VIEW_SOURCE_LIMIT) -> list[str]:
    if len(items) <= limit:
        return list(items)
    half = limit // 2
    return [*items[:half], *items[-half:]]


class ArchivedEpisode(StrictModel):
    """Receipt for one archived episode snapshot; the hash covers the whole snapshot."""

    archive_id: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    byte_count: int = Field(ge=0)


class EpisodeArchive(Protocol):
    """Durable home of original episode snapshots, addressed by their content hash."""

    def put(self, record: EpisodeRecord) -> ArchivedEpisode: ...

    def get(self, archive_id: str) -> EpisodeRecord: ...

    def has(self, archive_id: str) -> bool: ...


class ArchivedSummary(StrictModel):
    """Receipt for one archived summary; the hash covers the whole sealed summary."""

    archive_id: str = Field(min_length=1)
    summary_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    byte_count: int = Field(ge=0)


class SummaryArchive(Protocol):
    """Optional capability of an archive: durable storage for superseded summaries."""

    def put_summary(self, summary: SemanticEpisodeSummary) -> ArchivedSummary: ...

    def get_summary(self, archive_id: str) -> SemanticEpisodeSummary: ...

    def has_summary(self, archive_id: str) -> bool: ...


class SummaryRef(StrictModel):
    """What the store keeps of every summary: enough to find, order and verify it."""

    summary_id: str = Field(min_length=1)
    level: int = Field(ge=1)
    lifecycle: SummaryLifecycle = SummaryLifecycle.ACTIVE
    parent_summary_id: str | None = None
    archive_ref: str | None = None
    integrity_hash: str = Field(min_length=1)
    summary_tokens: int = Field(default=0, ge=0)


class QuarantinedSummary(StrictModel):
    """A summary that failed verification, kept for audit and never projected."""

    summary: SemanticEpisodeSummary
    group_id: str
    reasons: list[str]
    recorded_at_utc: str


class EpisodeDigest(StrictModel):
    """One source episode as a summarizer sees it."""

    episode_id: str
    kind: EpisodeKind
    description: str | None = None
    tool_name: str | None = None
    status: str | None = None
    error: str | None = None
    source_handle: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    content: dict[str, Any] | None = None
    content_hash: str
    tokens: int = Field(ge=0)


class SummarizationRequest(StrictModel):
    """A bounded, self-contained unit of summarization work."""

    group_id: str
    level: int = Field(ge=1)
    episodes: list[EpisodeDigest] = Field(default_factory=list)
    child_summaries: list[SemanticSummaryView] = Field(default_factory=list)
    external_dependency_ids: list[str] = Field(default_factory=list)
    mandatory_issue_episode_ids: list[str] = Field(default_factory=list)
    mandatory_issues: list[str] = Field(default_factory=list)
    max_summary_tokens: int = Field(ge=1)


class ClaimReview(StrictModel):
    """One claim or decision with the evidence a verifier should check it against."""

    index: int = Field(ge=0)
    kind: Literal["claim", "decision"]
    statement: str
    cited_episode_ids: list[str]
    evidence: str
    source_excerpts: dict[str, str] = Field(default_factory=dict)


class ClaimVerdict(StrictModel):
    index: int = Field(ge=0)
    label: Literal["entailed", "contradicted", "unsupported", "unclear"]
    reason: str = ""


class ClaimJudge(Protocol):
    """Optional host check that a claim follows from the episodes it cites.

    It is a model or a rule set, not a proof; its verdicts are recorded with its id.
    """

    judge_id: str

    def judge_claims(
        self, request: SummarizationRequest, reviews: list[ClaimReview]
    ) -> list[ClaimVerdict]: ...


class EpisodeSummarizer(Protocol):
    """Host extension point; the SDK works without one and never calls a model itself."""

    summarizer_id: str

    def summarize(self, request: SummarizationRequest) -> SemanticSummaryDraft: ...


class JudgeVerdict(StrictModel):
    accepted: bool
    reasons: list[str] = Field(default_factory=list)


class SummaryJudge(Protocol):
    """Optional semantic check; it never replaces the deterministic checks, which run first."""

    judge_id: str

    def judge(
        self, request: SummarizationRequest, summary: SemanticEpisodeSummary
    ) -> JudgeVerdict: ...


class SummaryVerificationReport(StrictModel):
    """Deterministic integrity checks and optional semantic checks, kept apart."""

    deterministic: list[VerificationCheck] = Field(default_factory=list)
    semantic: list[VerificationCheck] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in [*self.deterministic, *self.semantic])

    @property
    def failures(self) -> list[VerificationCheck]:
        return [check for check in [*self.deterministic, *self.semantic] if not check.passed]


class SemanticGroupOutcome(StrictModel):
    """Auditable result of one summarization group."""

    group_id: str
    level: int = Field(ge=1)
    source_episode_ids: list[str]
    input_hash: str
    status: Literal["committed", "rejected", "failed"]
    summary_id: str | None = None
    reason: str | None = None
    note: str | None = None
    checks: list[VerificationCheck] = Field(default_factory=list)


class EvictedEpisode(StrictModel):
    """What the active graph keeps of an episode whose record was archived and removed."""

    episode_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    kind: EpisodeKind
    description: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    tombstone: str = Field(min_length=1)
    summary_id: str | None = None
    archive_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    def to_record(self) -> EpisodeRecord:
        return EpisodeRecord(
            id=self.episode_id,
            owner_id=self.owner_id,
            kind=self.kind,
            state=EpisodeState.COMPACTED,
            depends_on=list(self.depends_on),
            description=self.description,
            tombstone=self.tombstone,
            summary_id=self.summary_id,
            archive_id=self.archive_id,
            content_hash=self.content_hash,
        )


class RehydrationResult(StrictModel):
    restored_episode_ids: list[str] = Field(default_factory=list)
    skipped: dict[str, str] = Field(default_factory=dict)
    restored_tokens: int = Field(default=0, ge=0)
    token_budget: int = Field(ge=0)


class SemanticCompactionPolicy(StrictModel):
    """Deterministic bounds for summarization, consolidation and active-graph size."""

    max_group_episodes: int = Field(default=8, ge=1, le=64)
    max_group_source_tokens: int = Field(default=8_000, ge=64, le=1_000_000)
    unrelated_batch_size: int = Field(default=1, ge=1, le=64)
    max_summary_tokens: int = Field(default=400, ge=16, le=20_000)
    max_summary_ratio: float = Field(default=0.75, gt=0, le=1)
    compression_floor_tokens: int = Field(default=64, ge=0)
    min_evidence_chars: int = Field(default=4, ge=1, le=200)
    check_unsupported_identifiers: bool = True
    failure_policy: Literal["preserve-original", "release-after-archive"] = "preserve-original"
    consolidate_when_summaries: int = Field(default=12, ge=0)
    consolidation_group_size: int = Field(default=4, ge=2, le=32)
    max_level: int = Field(default=3, ge=1, le=8)
    max_active_records: int | None = Field(default=None, ge=1)
    max_active_edges: int | None = Field(default=None, ge=0)
    max_group_attempts: int = Field(default=2, ge=1, le=100)
    max_active_summaries: int | None = Field(default=None, ge=1)
    max_active_summary_tokens: int | None = Field(default=None, ge=1)
    archive_superseded_summaries: bool = True
    entailment_screen: bool = False
    require_claim_verdicts: bool = False
    on_semantic_failure: Literal["reject", "quarantine"] = "reject"
    max_quarantined: int = Field(default=16, ge=0, le=1_000)
    require_action_coverage: bool = True
    background_summarization: bool = False
    max_background_workers: int = Field(default=1, ge=1, le=16)
    max_queued_summarizations: int = Field(default=8, ge=1, le=1_000)
    background_job_timeout_seconds: float | None = Field(default=60.0, gt=0)
