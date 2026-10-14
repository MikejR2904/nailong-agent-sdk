# Copyright (c) 2026 David Michael Indraputra

"""Dependency-aware summarization, verification and safe commit for compacted episodes.

PCKP decides what stays live. This module takes the episodes it did not keep and, group by
group, archives their originals, asks a host-supplied summarizer for a digest, verifies the
digest against the archived evidence and only then lets the store release the payloads. A
group that fails any step leaves its originals untouched; the SDK never calls a model itself.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from ..foundations.contracts import EpisodeKind, StrictModel
from ..foundations.hashing import canonical_hash, canonical_json, estimate_tokens, sha256_hex
from .entailment_screen import SCREEN_ID, SCREEN_VERSION, HeuristicEntailmentScreen
from .episode_archive import snapshot_text
from .episode_models import (
    ArchivedEpisode,
    ClaimJudge,
    ClaimReview,
    ClaimVerdict,
    EpisodeArchive,
    EpisodeDigest,
    EpisodeRecord,
    EpisodeState,
    EpisodeSummarizer,
    GroundedClaim,
    SemanticCompactionPolicy,
    SemanticEpisodeSummary,
    SemanticGroupOutcome,
    SemanticSummaryDraft,
    SummarizationRequest,
    SummaryJudge,
    SummaryVerificationRecord,
    SummaryVerificationReport,
    SummaryVerificationStatus,
    VerificationCheck,
    VerificationKind,
    VerificationMethod,
)
from .episode_scoring import _episode_tokens

_EPISODE_NUMBER = re.compile(r"(\d+)$")
_IDENTIFIER = re.compile(
    r"[A-Za-z0-9_.:/#-]*\d[A-Za-z0-9_.:/#-]*|[A-Za-z_][A-Za-z0-9]*[_./][A-Za-z0-9_./-]+"
)
_TRAILING_PUNCTUATION = ".,;:)]}'\"-"
_FAILED_STATUSES = frozenset({"failed", "blocked"})
_REASON_CHARS = 400
_EXCERPT_CHARS = 1_200
CHECKS_ID = "extractive-checks"
CHECKS_VERSION = "2"


class SemanticPassReport(StrictModel):
    """What one semantic pass did, for the compaction dossier."""

    policy_hash: str
    summarizer_id: str
    outcomes: list[SemanticGroupOutcome] = []
    committed_summary_ids: list[str] = []
    consolidated_summary_ids: list[str] = []
    released_without_summary: list[str] = []
    pending_episode_ids: list[str] = []
    evicted_episode_ids: list[str] = []
    archived_summary_ids: list[str] = []
    quarantined_summary_ids: list[str] = []
    summary_archive_failures: list[str] = []
    pinned_summaries_over_ceiling: int = 0
    jobs_submitted: int = 0
    jobs_committed: int = 0
    jobs_stale: int = 0
    jobs_failed: int = 0
    jobs_timed_out: int = 0
    jobs_not_accepted: dict[str, int] = {}
    jobs_queued: int = 0
    jobs_running: int = 0
    active_summary_count: int = 0
    active_summary_tokens: int = 0


@dataclass(frozen=True)
class GroupResult:
    outcome: SemanticGroupOutcome
    summary: SemanticEpisodeSummary | None = None
    archived: dict[str, ArchivedEpisode] = field(default_factory=dict)
    rejected: SemanticEpisodeSummary | None = None


def episode_order_key(episode_id: str) -> tuple[int, str]:
    match = _EPISODE_NUMBER.search(episode_id)
    return (int(match.group(1)) if match else 0, episode_id)


def archivable(record: EpisodeRecord) -> EpisodeRecord:
    return record.model_copy(
        update={
            "state": EpisodeState.CLOSED,
            "tombstone": None,
            "depended_on_by": [],
            "summary_id": None,
            "archive_id": None,
            "content_hash": None,
        }
    )


def normalize_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def record_corpus(record: EpisodeRecord) -> str:
    content = canonical_json(record.content) if record.content is not None else ""
    return normalize_text(f"{record.description or ''} {content}")


def build_digest(record: EpisodeRecord, content_hash: str) -> EpisodeDigest:
    content = record.content or {}
    call = content.get("call") if isinstance(content.get("call"), dict) else {}
    result = content.get("result") if isinstance(content.get("result"), dict) else {}
    name = call.get("name")
    status = result.get("status")
    error = result.get("error")
    return EpisodeDigest(
        episode_id=record.id,
        kind=record.kind,
        description=record.description,
        tool_name=name if isinstance(name, str) else None,
        status=status if isinstance(status, str) else None,
        error=error if isinstance(error, str) else None,
        source_handle=record.source_handle,
        depends_on=list(record.depends_on),
        content=record.content,
        content_hash=content_hash,
        tokens=_episode_tokens(record),
    )


def plan_groups(
    records: Sequence[EpisodeRecord], policy: SemanticCompactionPolicy
) -> list[list[EpisodeRecord]]:
    """Group records by dependency component, then chunk each group to the policy bounds."""

    by_id = {record.id: record for record in records}
    parent = {episode_id: episode_id for episode_id in by_id}

    def find(episode_id: str) -> str:
        while parent[episode_id] != episode_id:
            parent[episode_id] = parent[parent[episode_id]]
            episode_id = parent[episode_id]
        return episode_id

    for record in records:
        for dependency in record.depends_on:
            if dependency in by_id:
                parent[find(record.id)] = find(dependency)
    components: dict[str, list[EpisodeRecord]] = {}
    for record in sorted(records, key=lambda item: episode_order_key(item.id)):
        components.setdefault(find(record.id), []).append(record)
    ordered = sorted(components.values(), key=lambda members: episode_order_key(members[0].id))

    related = [members for members in ordered if len(members) > 1]
    singles = [members[0] for members in ordered if len(members) == 1]
    groups: list[list[EpisodeRecord]] = []
    for members in related:
        groups.extend(_chunks(members, policy.max_group_episodes, policy))
    for start in range(0, len(singles), policy.unrelated_batch_size):
        batch = singles[start : start + policy.unrelated_batch_size]
        groups.extend(_chunks(batch, policy.max_group_episodes, policy))
    return sorted(groups, key=lambda members: episode_order_key(members[0].id))


def _chunks(
    members: Sequence[EpisodeRecord], limit: int, policy: SemanticCompactionPolicy
) -> list[list[EpisodeRecord]]:
    chunks: list[list[EpisodeRecord]] = []
    current: list[EpisodeRecord] = []
    tokens = 0
    for record in members:
        cost = _episode_tokens(record)
        if current and (len(current) >= limit or tokens + cost > policy.max_group_source_tokens):
            chunks.append(current)
            current, tokens = [], 0
        current.append(record)
        tokens += cost
    if current:
        chunks.append(current)
    return chunks


def mandatory_issue_ids(digests: Sequence[EpisodeDigest]) -> list[str]:
    return [digest.episode_id for digest in digests if digest.status in _FAILED_STATUSES]


class ExtractiveEpisodeSummarizer:
    """Deterministic summarizer that quotes the episodes instead of paraphrasing them.

    It needs no model, so its output is reproducible and always grounded: every claim cites
    the episode's own description or recorded status. It is the baseline and the test double
    for providers that call a model.
    """

    summarizer_id = "extractive-v1"

    def __init__(self, *, headline_chars: int = 160) -> None:
        self._headline_chars = headline_chars

    def summarize(self, request: SummarizationRequest) -> SemanticSummaryDraft:
        if request.level > 1:
            return self._consolidate(request)
        claim_chars = max(
            24,
            min(
                self._headline_chars,
                request.max_summary_tokens * 8 // 5 // max(len(request.episodes), 1),
            ),
        )
        index: list[str] = []
        facts: list[GroundedClaim] = []
        issues: list[str] = []
        for digest in request.episodes:
            failed = digest.status in _FAILED_STATUSES
            headline = (digest.description or "").strip()
            claim_evidence = headline[:claim_chars]
            if not claim_evidence and digest.tool_name and digest.status:
                claim_evidence = f'"status":"{digest.status}"'
                headline = f"{digest.tool_name} {digest.status}"
            index.append(
                f"{digest.episode_id} {digest.tool_name or digest.kind.value} "
                f"{digest.status or 'closed'}"
            )
            if failed:
                detail = f" - {digest.error[:200]}" if digest.error else ""
                issues.append(
                    f"{digest.episode_id}: {digest.tool_name or digest.kind.value} "
                    f"{digest.status}{detail}"
                )
            elif claim_evidence:
                facts.append(
                    GroundedClaim(
                        claim=headline[:claim_chars],
                        source_episode_ids=[digest.episode_id],
                        evidence=claim_evidence,
                    )
                )
        issues.extend(_conflict_issues(request.episodes))
        return SemanticSummaryDraft(
            summary_text=_fit("; ".join(index), max(8, request.max_summary_tokens // 4)),
            key_facts=facts[:64],
            unresolved_issues=issues[:32],
        )

    def _consolidate(self, request: SummarizationRequest) -> SemanticSummaryDraft:
        children = request.child_summaries
        bodies = [
            "; ".join(fact.claim for fact in child.key_facts) or child.summary_text
            for child in children
        ]
        total = sum(len(body) for body in bodies)
        budget = min(request.max_summary_tokens * 4, max(total // 2, 24 * len(children)))
        share = max(24, budget // max(len(children), 1))
        parts = [
            f"{child.summary_id}: {body[:share].rstrip()}"
            for child, body in zip(children, bodies, strict=True)
        ]
        issues: list[str] = []
        for child in children:
            issues.extend(issue for issue in child.unresolved_issues if issue not in issues)
        return SemanticSummaryDraft(
            summary_text=_fit(" | ".join(parts), request.max_summary_tokens),
            unresolved_issues=issues[:32],
        )


def _fit(text: str, max_tokens: int) -> str:
    limit = max(16, max_tokens * 4 - 8)
    if len(text) <= limit:
        return text or "no recorded detail"
    cut = text.rfind(";", 0, limit)
    cut = cut if cut > limit // 2 else limit
    return f"{text[:cut].rstrip()} ..."


def _conflict_issues(digests: Sequence[EpisodeDigest]) -> list[str]:
    seen: dict[str, list[EpisodeDigest]] = {}
    for digest in digests:
        content = digest.content or {}
        call = content.get("call") if isinstance(content.get("call"), dict) else {}
        key = canonical_json({"name": call.get("name"), "arguments": call.get("arguments")})
        if call.get("name"):
            seen.setdefault(key, []).append(digest)
    issues = []
    for items in seen.values():
        outcomes = {
            canonical_json((item.content or {}).get("result")) for item in items if item.content
        }
        if len(items) > 1 and len(outcomes) > 1:
            ids = ", ".join(item.episode_id for item in items)
            name = items[0].tool_name or "a tool"
            issues.append(f"conflicting results from {name} with identical arguments: {ids}")
    return issues


class SemanticCompactor:
    """Archive, summarize, verify: the work between PCKP's choice and a payload's release."""

    def __init__(
        self,
        summarizer: EpisodeSummarizer,
        *,
        archive: EpisodeArchive | None = None,
        policy: SemanticCompactionPolicy | None = None,
        judge: SummaryJudge | None = None,
        claim_judge: ClaimJudge | None = None,
        fallback: EpisodeSummarizer | None = None,
    ) -> None:
        self._summarizer = summarizer
        self._archive = archive
        self._policy = policy or SemanticCompactionPolicy()
        self._judge = judge
        self._claim_judge = claim_judge
        self._fallback = fallback
        self._screen = HeuristicEntailmentScreen() if self._policy.entailment_screen else None
        self._policy_hash = canonical_hash(self._policy.model_dump(mode="json"))

    @property
    def summarizer(self) -> EpisodeSummarizer:
        return self._summarizer

    @property
    def archive(self) -> EpisodeArchive | None:
        return self._archive

    @property
    def policy(self) -> SemanticCompactionPolicy:
        return self._policy

    @property
    def policy_hash(self) -> str:
        return self._policy_hash

    def with_archive(self, archive: EpisodeArchive) -> SemanticCompactor:
        return SemanticCompactor(
            self._summarizer,
            archive=archive,
            policy=self._policy,
            judge=self._judge,
            claim_judge=self._claim_judge,
            fallback=self._fallback,
        )

    def plan(self, records: Sequence[EpisodeRecord]) -> list[list[EpisodeRecord]]:
        floor = self._policy.compression_floor_tokens
        return [
            group
            for group in plan_groups(records, self._policy)
            if sum(_episode_tokens(record) for record in group) >= floor
        ]

    def summarize_group(
        self,
        group_id: str,
        records: Sequence[EpisodeRecord],
        *,
        summary_id: str,
        archive: EpisodeArchive,
        episode_exists: Callable[[str], bool],
    ) -> GroupResult:
        ids = [record.id for record in records]
        snapshots = {record.id: archivable(record) for record in records}
        input_hash = canonical_hash(
            {
                "level": 1,
                "policy": self._policy_hash,
                "episodes": [
                    [episode_id, sha256_hex(snapshot_text(snapshots[episode_id]))]
                    for episode_id in ids
                ],
            }
        )

        def outcome(
            status: Literal["committed", "rejected", "failed"],
            reason: str | None,
            checks: list[VerificationCheck] | None = None,
        ) -> SemanticGroupOutcome:
            return SemanticGroupOutcome(
                group_id=group_id,
                level=1,
                source_episode_ids=ids,
                input_hash=input_hash,
                status=status,
                reason=reason,
                checks=checks or [],
            )

        archived: dict[str, ArchivedEpisode] = {}
        try:
            for episode_id in ids:
                receipt = archive.put(snapshots[episode_id])
                if not archive.has(receipt.archive_id):
                    raise ValueError(f'archive does not hold "{receipt.archive_id}" after put')
                confirmed = archive.get(receipt.archive_id)
                if snapshot_text(confirmed) != snapshot_text(snapshots[episode_id]):
                    raise ValueError(
                        f'archived copy of "{episode_id}" differs from the original snapshot'
                    )
                archived[episode_id] = receipt
        except Exception as error:
            return GroupResult(
                outcome("failed", f"archival failed: {_describe(error)}"), archived=archived
            )

        digests = [build_digest(record, archived[record.id].content_hash) for record in records]
        external = sorted(
            {dependency for record in records for dependency in record.depends_on} - set(ids),
            key=episode_order_key,
        )
        request = SummarizationRequest(
            group_id=group_id,
            level=1,
            episodes=digests,
            external_dependency_ids=external,
            mandatory_issue_episode_ids=mandatory_issue_ids(digests),
            max_summary_tokens=self._policy.max_summary_tokens,
        )
        draft, summarizer_id, failure, note = self._produce(request)
        if draft is None:
            assert failure is not None
            return GroupResult(outcome(failure[0], failure[1]), archived=archived)

        corpus = {record.id: record_corpus(record) for record in records}
        source_tokens = sum(digest.tokens for digest in digests)
        summary = SemanticEpisodeSummary(
            summary_id=summary_id,
            level=1,
            source_episode_ids=ids,
            summary_text=draft.summary_text,
            key_facts=draft.key_facts,
            decisions=draft.decisions,
            unresolved_issues=draft.unresolved_issues,
            dependency_ids=external,
            source_handles=[
                digest.source_handle for digest in digests if digest.source_handle is not None
            ],
            source_hashes=[archived[episode_id].content_hash for episode_id in ids],
            archive_ids=[archived[episode_id].archive_id for episode_id in ids],
            verification_status=SummaryVerificationStatus.VERIFIED,
            summarizer_id=summarizer_id,
            policy_hash=self._policy_hash,
            summary_tokens=0,
            source_tokens=source_tokens,
        )
        summary = summary.model_copy(update={"summary_tokens": _summary_tokens(summary)})
        report = self._verify_group(
            request, summary, corpus, snapshots, archived, archive, episode_exists
        )
        excerpts = _episode_excerpts(digests)
        return self._finalize(outcome, request, summary, report, excerpts, archived, note)

    def consolidate(
        self,
        group_id: str,
        children: Sequence[SemanticEpisodeSummary],
        *,
        summary_id: str,
        archive: EpisodeArchive,
        episode_exists: Callable[[str], bool],
    ) -> GroupResult:
        level = max(child.level for child in children) + 1
        source_ids = sorted(
            {episode_id for child in children for episode_id in child.source_episode_ids},
            key=episode_order_key,
        )
        input_hash = canonical_hash(
            {
                "level": level,
                "policy": self._policy_hash,
                "children": [[child.summary_id, child.integrity_hash] for child in children],
            }
        )

        def outcome(
            status: Literal["committed", "rejected", "failed"],
            reason: str | None,
            checks: list[VerificationCheck] | None = None,
        ) -> SemanticGroupOutcome:
            return SemanticGroupOutcome(
                group_id=group_id,
                level=level,
                source_episode_ids=source_ids,
                input_hash=input_hash,
                status=status,
                reason=reason,
                checks=checks or [],
            )

        if level > self._policy.max_level:
            return GroupResult(
                outcome(
                    "rejected",
                    f"level {level} exceeds max_level {self._policy.max_level}",
                    [VerificationCheck(name="hierarchy-bounded", passed=False)],
                )
            )
        problems = [
            f'child summary "{child.summary_id}" '
            + ("failed its integrity hash" if not child.intact() else "has an unavailable archive")
            for child in children
            if not child.intact() or not all(archive.has(a) for a in child.archive_ids)
        ]
        if problems:
            return GroupResult(
                outcome(
                    "rejected",
                    "; ".join(problems),
                    [VerificationCheck(name="children-intact", passed=False, detail=problems[0])],
                )
            )
        mandatory = [issue for child in children for issue in child.unresolved_issues]
        request = SummarizationRequest(
            group_id=group_id,
            level=level,
            child_summaries=[child.view() for child in children],
            external_dependency_ids=[],
            mandatory_issues=list(dict.fromkeys(mandatory)),
            max_summary_tokens=self._policy.max_summary_tokens,
        )
        draft, summarizer_id, failure, note = self._produce(request)
        if draft is None:
            assert failure is not None
            return GroupResult(outcome(failure[0], failure[1]))

        dependencies = sorted(
            {d for child in children for d in child.dependency_ids} - set(source_ids),
            key=episode_order_key,
        )
        summary = SemanticEpisodeSummary(
            summary_id=summary_id,
            level=level,
            source_episode_ids=source_ids,
            child_summary_ids=[child.summary_id for child in children],
            summary_text=draft.summary_text,
            key_facts=draft.key_facts,
            decisions=draft.decisions,
            unresolved_issues=draft.unresolved_issues,
            dependency_ids=dependencies,
            source_handles=list(
                dict.fromkeys(handle for child in children for handle in child.source_handles)
            ),
            source_hashes=_aligned(children, source_ids, "source_hashes"),
            archive_ids=_aligned(children, source_ids, "archive_ids"),
            verification_status=SummaryVerificationStatus.VERIFIED,
            summarizer_id=summarizer_id,
            policy_hash=self._policy_hash,
            summary_tokens=0,
            source_tokens=sum(child.summary_tokens for child in children),
        )
        summary = summary.model_copy(update={"summary_tokens": _summary_tokens(summary)})
        report = self._verify_consolidation(request, summary, children, episode_exists)
        excerpts = _child_excerpts(children)
        return self._finalize(outcome, request, summary, report, excerpts, {}, note)

    def _call(
        self, summarizer: EpisodeSummarizer, request: SummarizationRequest
    ) -> tuple[SemanticSummaryDraft | None, tuple[str, str] | None]:
        try:
            produced = summarizer.summarize(request)
            draft = (
                produced
                if isinstance(produced, SemanticSummaryDraft)
                else SemanticSummaryDraft.model_validate(produced)
            )
        except ValidationError as error:
            return None, (
                "rejected",
                f"summarizer output is not a valid draft: {_fields(error)}",
            )
        except Exception as error:
            return None, ("failed", f"summarizer raised {_describe(error)}")
        return draft, None

    def _produce(
        self, request: SummarizationRequest
    ) -> tuple[SemanticSummaryDraft | None, str, tuple[str, str] | None, str | None]:
        draft, failure = self._call(self._summarizer, request)
        if draft is not None or self._fallback is None:
            return draft, self._summarizer.summarizer_id, failure, None
        assert failure is not None
        fallback_draft, second = self._call(self._fallback, request)
        fallback_id = self._fallback.summarizer_id
        if fallback_draft is None:
            assert second is not None
            return (
                None,
                fallback_id,
                (failure[0], f"{failure[1]}; fallback {fallback_id} also failed: {second[1]}"),
                None,
            )
        note = f"{self._summarizer.summarizer_id} unavailable ({failure[1]}); used {fallback_id}"
        return fallback_draft, fallback_id, None, note[:_REASON_CHARS]

    def _finalize(
        self,
        outcome: Callable[..., SemanticGroupOutcome],
        request: SummarizationRequest,
        summary: SemanticEpisodeSummary,
        report: SummaryVerificationReport,
        excerpts: Mapping[str, str],
        archived: dict[str, ArchivedEpisode],
        note: str | None,
    ) -> GroupResult:
        methods = [
            _method(
                "deterministic-extractive",
                CHECKS_ID,
                CHECKS_VERSION,
                self._policy_hash,
                report.deterministic,
            )
        ]
        if report.passed:
            report, methods = self._semantic_stages(request, summary, report, methods, excerpts)
        status = (
            SummaryVerificationStatus.VERIFIED
            if report.passed
            else SummaryVerificationStatus.REJECTED
        )
        final = summary.model_copy(
            update={
                "verification": SummaryVerificationRecord(methods=methods),
                "verification_status": status,
            }
        ).sealed()
        reason = None if report.passed else "; ".join(f.name for f in report.failures)
        verdict = outcome(
            "committed" if report.passed else "rejected",
            reason,
            [*report.deterministic, *report.semantic],
        )
        verdict = verdict.model_copy(
            update={"summary_id": summary.summary_id if report.passed else None, "note": note}
        )
        return GroupResult(
            verdict,
            summary=final if report.passed else None,
            archived=archived,
            rejected=None if report.passed else final,
        )

    def _semantic_stages(
        self,
        request: SummarizationRequest,
        summary: SemanticEpisodeSummary,
        report: SummaryVerificationReport,
        methods: list[VerificationMethod],
        excerpts: Mapping[str, str],
    ) -> tuple[SummaryVerificationReport, list[VerificationMethod]]:
        semantic: list[VerificationCheck] = []
        reviews = _reviews(summary, excerpts)

        def clean() -> bool:
            return all(check.passed for check in [*report.deterministic, *semantic])

        if self._screen is not None and reviews:
            checks = _verdict_checks(
                "heuristic-entailment-screen", self._screen.review(reviews), len(reviews), False
            )
            methods.append(
                _method(
                    "heuristic-entailment-screen",
                    SCREEN_ID,
                    SCREEN_VERSION,
                    self._policy_hash,
                    checks,
                )
            )
            semantic.extend(checks)
        if self._claim_judge is not None and reviews and clean():
            judge = self._claim_judge
            name = f"claim-judge:{judge.judge_id}"
            try:
                verdicts = judge.judge_claims(request, reviews)
                checks = _verdict_checks(
                    name, verdicts, len(reviews), self._policy.require_claim_verdicts
                )
            except Exception as error:
                checks = [
                    VerificationCheck(
                        name=name, passed=False, detail=f"judge raised {_describe(error)}"
                    )
                ]
            methods.append(
                _method(
                    "claim-judge",
                    judge.judge_id,
                    str(getattr(judge, "judge_version", "1")),
                    self._policy_hash,
                    checks,
                )
            )
            semantic.extend(checks)
        if self._judge is not None and clean():
            summary_judge = self._judge
            name = f"judge:{summary_judge.judge_id}"
            try:
                verdict = summary_judge.judge(request, summary)
                checks = [
                    VerificationCheck(
                        name=name,
                        passed=verdict.accepted,
                        detail="; ".join(verdict.reasons)[:_REASON_CHARS],
                    )
                ]
            except Exception as error:
                checks = [
                    VerificationCheck(
                        name=name, passed=False, detail=f"judge raised {_describe(error)}"
                    )
                ]
            methods.append(
                _method(
                    "summary-judge",
                    summary_judge.judge_id,
                    str(getattr(summary_judge, "judge_version", "1")),
                    self._policy_hash,
                    checks,
                )
            )
            semantic.extend(checks)
        return report.model_copy(update={"semantic": semantic}), methods

    def _verify_group(
        self,
        request: SummarizationRequest,
        summary: SemanticEpisodeSummary,
        corpus: Mapping[str, str],
        snapshots: Mapping[str, EpisodeRecord],
        archived: Mapping[str, ArchivedEpisode],
        archive: EpisodeArchive,
        episode_exists: Callable[[str], bool],
    ) -> SummaryVerificationReport:
        policy = self._policy
        checks: list[VerificationCheck] = []
        group = set(summary.source_episode_ids)

        intact = [
            episode_id
            for episode_id, receipt in archived.items()
            if not archive.has(receipt.archive_id)
            or sha256_hex(snapshot_text(snapshots[episode_id])) != receipt.content_hash
        ]
        checks.append(
            _check(
                "sources-archived-and-intact",
                not intact,
                f"missing or changed archive entries for {intact}" if intact else "",
            )
        )
        unknown = [d for d in summary.dependency_ids if not episode_exists(d)]
        checks.append(
            _check(
                "dependencies-resolve",
                not unknown,
                f"dependencies {unknown} are not known to the store" if unknown else "",
            )
        )
        checks.append(_citations_check(summary, group))
        checks.append(
            _grounding_check(
                "claims-grounded",
                [(c.claim, c.source_episode_ids, c.evidence) for c in summary.key_facts],
                corpus,
                policy.min_evidence_chars,
            )
        )
        checks.append(
            _grounding_check(
                "decisions-grounded",
                [(d.decision, d.source_episode_ids, d.evidence) for d in summary.decisions],
                corpus,
                policy.min_evidence_chars,
            )
        )
        checks.append(_mandatory_check(request, summary))
        if policy.require_action_coverage:
            checks.append(_actions_check(request, summary))
        if policy.check_unsupported_identifiers:
            checks.append(_identifier_check(request, summary, " ".join(corpus.values())))
        checks.append(_compression_check(policy, summary))
        return SummaryVerificationReport(deterministic=checks)

    def _verify_consolidation(
        self,
        request: SummarizationRequest,
        summary: SemanticEpisodeSummary,
        children: Sequence[SemanticEpisodeSummary],
        episode_exists: Callable[[str], bool],
    ) -> SummaryVerificationReport:
        policy = self._policy
        checks: list[VerificationCheck] = []
        group = set(summary.source_episode_ids)
        child_text = {
            child.summary_id: normalize_text(_summary_corpus(child)) for child in children
        }
        by_episode: dict[str, list[str]] = {}
        for child in children:
            for episode_id in child.source_episode_ids:
                by_episode.setdefault(episode_id, []).append(child.summary_id)
        corpus = {
            episode_id: " ".join(child_text[child_id] for child_id in owners)
            for episode_id, owners in by_episode.items()
        }
        unknown = [d for d in summary.dependency_ids if not episode_exists(d)]
        checks.append(
            _check(
                "dependencies-resolve",
                not unknown,
                f"dependencies {unknown} are not known to the store" if unknown else "",
            )
        )
        checks.append(_citations_check(summary, group))
        checks.append(
            _grounding_check(
                "claims-grounded",
                [(c.claim, c.source_episode_ids, c.evidence) for c in summary.key_facts],
                corpus,
                policy.min_evidence_chars,
            )
        )
        checks.append(
            _grounding_check(
                "decisions-grounded",
                [(d.decision, d.source_episode_ids, d.evidence) for d in summary.decisions],
                corpus,
                policy.min_evidence_chars,
            )
        )
        carried = {normalize_text(issue) for issue in summary.unresolved_issues}
        dropped = [
            issue for issue in request.mandatory_issues if normalize_text(issue) not in carried
        ]
        checks.append(
            _check(
                "child-issues-preserved",
                not dropped,
                f"{len(dropped)} unresolved issue(s) of the child summaries are missing, "
                f"first: {dropped[0]!r}"
                if dropped
                else "",
            )
        )
        if policy.check_unsupported_identifiers:
            checks.append(_identifier_check(request, summary, " ".join(child_text.values())))
        checks.append(_compression_check(policy, summary))
        checks.append(
            _check(
                "hierarchy-bounded",
                summary.level <= policy.max_level,
                f"level {summary.level} exceeds max_level {policy.max_level}"
                if summary.level > policy.max_level
                else "",
            )
        )
        return SummaryVerificationReport(deterministic=checks)


def _method(
    kind: VerificationKind,
    verifier_id: str,
    version: str,
    policy_hash: str,
    checks: Sequence[VerificationCheck],
) -> VerificationMethod:
    return VerificationMethod(
        kind=kind,
        verifier_id=verifier_id,
        verifier_version=version,
        policy_hash=policy_hash,
        passed=all(check.passed for check in checks),
        checks=list(checks),
    )


def _episode_excerpts(digests: Sequence[EpisodeDigest]) -> dict[str, str]:
    excerpts: dict[str, str] = {}
    for digest in digests:
        parts = [digest.description or "", f"status {digest.status or 'unknown'}"]
        if digest.error:
            parts.append(f"error {digest.error}")
        if digest.content is not None:
            parts.append(canonical_json(digest.content))
        excerpts[digest.episode_id] = " ".join(parts)[:_EXCERPT_CHARS]
    return excerpts


def _child_excerpts(children: Sequence[SemanticEpisodeSummary]) -> dict[str, str]:
    excerpts: dict[str, str] = {}
    for child in children:
        text = _summary_corpus(child)[:_EXCERPT_CHARS]
        for episode_id in child.source_episode_ids:
            excerpts.setdefault(episode_id, text)
    return excerpts


def _reviews(summary: SemanticEpisodeSummary, excerpts: Mapping[str, str]) -> list[ClaimReview]:
    reviews: list[ClaimReview] = []
    for fact in summary.key_facts:
        reviews.append(
            ClaimReview(
                index=len(reviews),
                kind="claim",
                statement=fact.claim,
                cited_episode_ids=list(fact.source_episode_ids),
                evidence=fact.evidence,
                source_excerpts={e: excerpts.get(e, "") for e in fact.source_episode_ids},
            )
        )
    for item in summary.decisions:
        reviews.append(
            ClaimReview(
                index=len(reviews),
                kind="decision",
                statement=f"{item.decision} because {item.rationale}",
                cited_episode_ids=list(item.source_episode_ids),
                evidence=item.evidence,
                source_excerpts={e: excerpts.get(e, "") for e in item.source_episode_ids},
            )
        )
    return reviews


def _verdict_checks(
    name: str, verdicts: Sequence[ClaimVerdict], reviewed: int, require_all: bool
) -> list[VerificationCheck]:
    by_index = {verdict.index: verdict for verdict in verdicts}
    problems: list[str] = []
    for index in range(reviewed):
        verdict = by_index.get(index)
        if verdict is None:
            if require_all:
                problems.append(f"claim {index} received no verdict")
        elif verdict.label in {"contradicted", "unsupported"}:
            problems.append(f"claim {index} is {verdict.label}: {verdict.reason}".strip(": "))
        elif verdict.label == "unclear" and require_all:
            problems.append(f"claim {index} is unclear: {verdict.reason}".strip(": "))
    stray = sorted(index for index in by_index if index >= reviewed or index < 0)
    if stray:
        problems.append(f"verdicts name claims that were not reviewed: {stray[:5]}")
    return [_check(name, not problems, "; ".join(problems[:3]))]


def _actions_check(
    request: SummarizationRequest, summary: SemanticEpisodeSummary
) -> VerificationCheck:
    covered: set[str] = set()
    for item in [*summary.key_facts, *summary.decisions]:
        covered.update(item.source_episode_ids)
    issue_text = " ".join(summary.unresolved_issues)
    missing = [
        digest.episode_id
        for digest in request.episodes
        if digest.kind is EpisodeKind.ACTION
        and digest.episode_id not in covered
        and digest.episode_id not in issue_text
    ]
    return _check(
        "actions-covered",
        not missing,
        f"action episodes not cited by any claim or decision: {missing}" if missing else "",
    )


def _check(name: str, passed: bool, detail: str = "") -> VerificationCheck:
    return VerificationCheck(name=name, passed=passed, detail=detail[:_REASON_CHARS])


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:_REASON_CHARS]


def _fields(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in issue['loc']) or '(root)'}: {issue['msg']}"
        for issue in error.errors()[:5]
    )[:_REASON_CHARS]


def _summary_tokens(summary: SemanticEpisodeSummary) -> int:
    return estimate_tokens(_summary_corpus(summary))


def _summary_corpus(summary: SemanticEpisodeSummary) -> str:
    parts = [summary.summary_text, *summary.unresolved_issues]
    parts.extend(fact.claim for fact in summary.key_facts)
    parts.extend(f"{item.decision} {item.rationale}" for item in summary.decisions)
    return " ".join(parts)


def _aligned(
    children: Sequence[SemanticEpisodeSummary], source_ids: Sequence[str], attribute: str
) -> list[str]:
    lookup: dict[str, str] = {}
    for child in children:
        for episode_id, value in zip(
            child.source_episode_ids, getattr(child, attribute), strict=True
        ):
            lookup.setdefault(episode_id, value)
    return [lookup[episode_id] for episode_id in source_ids]


def _citations_check(summary: SemanticEpisodeSummary, group: set[str]) -> VerificationCheck:
    cited = {
        episode_id
        for item in [*summary.key_facts, *summary.decisions]
        for episode_id in item.source_episode_ids
    }
    outside = sorted(cited - group, key=episode_order_key)
    return _check(
        "citations-within-group",
        not outside,
        f"claims or decisions cite episodes outside the group: {outside}" if outside else "",
    )


def _grounding_check(
    name: str,
    items: Sequence[tuple[str, list[str], str]],
    corpus: Mapping[str, str],
    minimum: int,
) -> VerificationCheck:
    for statement, cited, evidence in items:
        needle = normalize_text(evidence)
        if len(needle) < minimum:
            return _check(
                name,
                False,
                f"evidence for {statement[:60]!r} is shorter than {minimum} characters",
            )
        if not any(needle in corpus.get(episode_id, "") for episode_id in cited):
            return _check(
                name,
                False,
                f"evidence {evidence[:80]!r} for {statement[:60]!r} does not occur in "
                f"the cited episodes {cited}",
            )
    return _check(name, True)


def _mandatory_check(
    request: SummarizationRequest, summary: SemanticEpisodeSummary
) -> VerificationCheck:
    covered: set[str] = set()
    for item in [*summary.key_facts, *summary.decisions]:
        covered.update(item.source_episode_ids)
    issue_text = " ".join(summary.unresolved_issues)
    missing = [
        episode_id
        for episode_id in request.mandatory_issue_episode_ids
        if episode_id not in covered and episode_id not in issue_text
    ]
    return _check(
        "mandatory-outcomes-covered",
        not missing,
        f"failed or blocked episodes not mentioned in the summary: {missing}" if missing else "",
    )


def _identifier_check(
    request: SummarizationRequest, summary: SemanticEpisodeSummary, sources: str
) -> VerificationCheck:
    allowed = normalize_text(
        " ".join(
            [
                sources,
                request.group_id,
                summary.summary_id,
                *summary.source_episode_ids,
                *summary.child_summary_ids,
                *summary.dependency_ids,
                *(str(number) for number in range(0, len(summary.source_episode_ids) + 2)),
                str(summary.level),
                f"level-{summary.level}",
            ]
        )
    )
    text = " ".join(
        [
            summary.summary_text,
            *summary.unresolved_issues,
            *(fact.claim for fact in summary.key_facts),
            *(f"{item.decision} {item.rationale}" for item in summary.decisions),
        ]
    )
    unsupported: list[str] = []
    for match in _IDENTIFIER.findall(text):
        token = match.strip(_TRAILING_PUNCTUATION).casefold()
        if token and token not in allowed and token not in unsupported:
            unsupported.append(token)
    return _check(
        "identifiers-supported",
        not unsupported,
        f"numbers or identifiers that no source episode contains: {unsupported[:8]}"
        if unsupported
        else "",
    )


def _compression_check(
    policy: SemanticCompactionPolicy, summary: SemanticEpisodeSummary
) -> VerificationCheck:
    problems = []
    if summary.summary_tokens > policy.max_summary_tokens:
        problems.append(
            f"summary is {summary.summary_tokens} tokens, over max_summary_tokens "
            f"{policy.max_summary_tokens}"
        )
    if (
        summary.source_tokens >= policy.compression_floor_tokens
        and summary.summary_tokens > summary.source_tokens * policy.max_summary_ratio
    ):
        problems.append(
            f"summary is {summary.summary_tokens} tokens for {summary.source_tokens} source "
            f"tokens, over the ratio {policy.max_summary_ratio}"
        )
    return _check("compression", not problems, "; ".join(problems))
