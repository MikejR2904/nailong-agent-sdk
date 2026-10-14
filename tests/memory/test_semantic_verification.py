import pytest

from nailong_agent_sdk.memory.entailment_screen import HeuristicEntailmentScreen
from nailong_agent_sdk.memory.episode_models import (
    ClaimReview,
    ClaimVerdict,
    GroundedClaim,
    JudgeVerdict,
    SemanticCompactionPolicy,
    SemanticSummaryDraft,
    SummaryVerificationStatus,
)
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.semantic_compaction import (
    ExtractiveEpisodeSummarizer,
    SemanticCompactor,
)
from tests.support.adversarial_summaries import (
    BAD,
    FAITHFUL,
    OracleClaimJudge,
    evaluate,
)
from tests.support.semantic_memory import (
    Scripted,
    compact_all,
    compactor_for,
    default_draft,
    fill,
    semantic_store,
)


def review(statement, evidence, source="", index=0):
    return ClaimReview(
        index=index,
        kind="claim",
        statement=statement,
        cited_episode_ids=["episode-1"],
        evidence=evidence,
        source_excerpts={"episode-1": source or evidence},
    )


@pytest.mark.parametrize(
    "statement, evidence, label",
    [
        ("the build succeeded", "the build succeeded", "entailed"),
        (
            "the deployment succeeded",
            "permission denied for service account ci-bot",
            "contradicted",
        ),
        ("the tests failed", "42 passed, 0 failed in the unit suite", "contradicted"),
        ("no errors were reported", "3 errors were reported by the linter", "contradicted"),
        ("7 files were changed", "the patch changed 5 files", "unsupported"),
        ("the cluster restarted and nodes rebooted", "the cache was cleared", "unsupported"),
        ("the cache was cleared", "cache cleared by the maintenance job at noon", "entailed"),
    ],
)
def test_the_screen_flags_what_it_can_see_and_says_so(statement, evidence, label):
    (verdict,) = HeuristicEntailmentScreen().review([review(statement, evidence)])
    assert verdict.label == label, verdict.reason


def test_the_screen_is_blind_to_a_contradiction_that_uses_no_word_it_knows():
    flipped = review(
        "account ci-bot had permission for the billing service",
        "permission denied for service account ci-bot",
        "deploy billing failed: permission denied for service account ci-bot",
    )
    assert HeuristicEntailmentScreen().review([flipped])[0].label == "entailed"
    half_invented = review("the cache was cleared and the node rebooted", "the cache was cleared")
    assert HeuristicEntailmentScreen().review([half_invented])[0].label == "entailed"


def test_the_screen_rejects_its_own_threshold_values_when_they_are_nonsense():
    with pytest.raises(ValueError, match="min_support"):
        HeuristicEntailmentScreen(min_support=0)


def drafted(status_claim):
    return SemanticSummaryDraft(
        summary_text="read f0.txt",
        key_facts=[status_claim],
        unresolved_issues=[],
    )


def contradicting_summarizer(request):
    digest = request.episodes[0]
    return drafted(
        GroundedClaim(
            claim=f"{digest.tool_name} failed",
            source_episode_ids=[digest.episode_id],
            evidence="succeeded",
        )
    )


def test_without_a_semantic_stage_a_claim_that_contradicts_its_evidence_is_committed():
    store = semantic_store(Scripted(contradicting_summarizer))
    ids, _ = fill(store, 1)
    report = compact_all(store).dossier["semantic_compaction"]
    summary = store.summary_for(ids[0])
    assert report["committed_summary_ids"] and summary is not None
    record = summary.verification
    assert [m.kind for m in record.methods] == ["deterministic-extractive"]
    assert record.extractive_verified and not record.entailment_checked and not record.host_judged


def test_the_screen_rejects_the_contradiction_and_the_original_stays_intact():
    policy = SemanticCompactionPolicy(consolidate_when_summaries=0, entailment_screen=True)
    store = semantic_store(Scripted(contradicting_summarizer), policy=policy)
    ids, originals = fill(store, 1)
    report = compact_all(store).dossier["semantic_compaction"]
    assert report["committed_summary_ids"] == [] and store.summaries() == []
    (outcome,) = report["outcomes"]
    assert outcome["status"] == "rejected" and "heuristic-entailment-screen" in outcome["reason"]
    failing = [c for c in outcome["checks"] if not c["passed"]]
    assert failing[0]["name"] == "heuristic-entailment-screen"
    assert "the claim says failed but its evidence says succeeded" in failing[0]["detail"]
    assert store.get(ids[0]).content == originals[ids[0]]
    assert store.quarantined_summaries() == []


def test_a_rejected_summary_can_be_quarantined_for_audit_and_never_projected():
    policy = SemanticCompactionPolicy(
        consolidate_when_summaries=0,
        entailment_screen=True,
        on_semantic_failure="quarantine",
        max_quarantined=2,
    )
    store = semantic_store(Scripted(contradicting_summarizer), policy=policy)
    ids, originals = fill(store, 3)
    store.compact(1, relevance_query="x")
    for _ in range(3):
        store.compact(1, relevance_query="again")
    held = store.quarantined_summaries()
    assert 1 <= len(held) <= 2
    for item in held:
        assert item.summary.verification_status is SummaryVerificationStatus.REJECTED
        assert item.summary.intact() and item.reasons and item.recorded_at_utc
        assert not item.summary.verification.entailment_checked
    assert store.summaries() == [] and store.active_summaries() == []
    for episode_id in ids:
        assert store.get(episode_id).content == originals[episode_id]


class Judge:
    judge_id = "recording-judge"
    judge_version = "3"

    def __init__(self, label="entailed", error=None):
        self.label = label
        self.error = error
        self.seen = []

    def judge_claims(self, request, reviews):
        self.seen.append(reviews)
        if self.error is not None:
            raise self.error
        return [ClaimVerdict(index=r.index, label=self.label, reason="x") for r in reviews]


def store_with(judge, **policy):
    options = {"consolidate_when_summaries": 0, **policy}
    compactor = SemanticCompactor(
        ExtractiveEpisodeSummarizer(),
        policy=SemanticCompactionPolicy(**options),
        claim_judge=judge,
    )
    return InMemoryEpisodeStore(semantic=compactor)


def test_a_claim_judge_sees_the_claim_its_evidence_and_the_cited_text():
    judge = Judge()
    store = store_with(judge)
    ids, _ = fill(store, 2)
    report = compact_all(store).dossier["semantic_compaction"]
    assert report["committed_summary_ids"]
    review = judge.seen[0][0]
    assert review.kind == "claim" and review.cited_episode_ids == [ids[0]]
    assert "read_file f0.txt" in review.statement and review.evidence
    assert "f0.txt" in review.source_excerpts[ids[0]]


def test_a_committed_summary_records_every_method_with_its_version_and_policy():
    judge = Judge()
    policy = SemanticCompactionPolicy(consolidate_when_summaries=0, entailment_screen=True)
    store = InMemoryEpisodeStore(
        semantic=SemanticCompactor(
            ExtractiveEpisodeSummarizer(), policy=policy, claim_judge=judge, judge=AcceptingJudge()
        )
    )
    ids, _ = fill(store, 1)
    compact_all(store)
    summary = store.summary_for(ids[0])
    kinds = [m.kind for m in summary.verification.methods]
    assert kinds == [
        "deterministic-extractive",
        "heuristic-entailment-screen",
        "claim-judge",
        "summary-judge",
    ]
    methods = summary.verification.methods
    assert all(m.passed and m.policy_hash == summary.policy_hash for m in methods)
    versions = {m.kind: (m.verifier_id, m.verifier_version) for m in methods}
    assert versions["claim-judge"] == ("recording-judge", "3")
    assert versions["summary-judge"] == ("accepting-judge", "1")
    assert summary.verification.host_judged and summary.verification.entailment_checked
    assert summary.verification_status is SummaryVerificationStatus.VERIFIED and summary.intact()


class AcceptingJudge:
    judge_id = "accepting-judge"

    def judge(self, request, summary):
        return JudgeVerdict(accepted=True)


@pytest.mark.parametrize("label", ["contradicted", "unsupported"])
def test_a_claim_judge_verdict_of_contradiction_keeps_the_original(label):
    store = store_with(Judge(label))
    ids, originals = fill(store, 1)
    report = compact_all(store).dossier["semantic_compaction"]
    (outcome,) = report["outcomes"]
    assert outcome["status"] == "rejected" and "claim-judge:recording-judge" in outcome["reason"]
    assert store.get(ids[0]).content == originals[ids[0]] and store.summaries() == []


def test_a_claim_judge_that_raises_fails_the_group_naming_the_exception():
    store = store_with(Judge(error=TimeoutError("provider timed out")))
    ids, originals = fill(store, 1)
    report = compact_all(store).dossier["semantic_compaction"]
    (outcome,) = report["outcomes"]
    detail = [c["detail"] for c in outcome["checks"] if not c["passed"]][0]
    assert "TimeoutError: provider timed out" in detail
    assert store.get(ids[0]).content == originals[ids[0]]


def test_unclear_verdicts_pass_unless_the_policy_requires_a_verdict_for_every_claim():
    lenient = store_with(Judge("unclear"))
    fill(lenient, 1)
    assert compact_all(lenient).dossier["semantic_compaction"]["committed_summary_ids"]
    strict = store_with(Judge("unclear"), require_claim_verdicts=True)
    fill(strict, 1)
    report = compact_all(strict).dossier["semantic_compaction"]
    assert report["committed_summary_ids"] == []
    assert "unclear" in [c for c in report["outcomes"][0]["checks"] if not c["passed"]][0]["detail"]


def test_an_action_episode_the_summary_never_mentions_is_not_dropped_silently():
    def forgetful(request):
        draft = default_draft(request)
        last = request.episodes[-1].episode_id
        kept = [f for f in draft.key_facts if f.source_episode_ids[0] != last]
        return SemanticSummaryDraft(
            summary_text=draft.summary_text, key_facts=kept, unresolved_issues=[]
        )

    store = semantic_store(Scripted(forgetful))
    ids, originals = fill(store, 1, actions=(0,))
    report = compact_all(store).dossier["semantic_compaction"]
    outcome = report["outcomes"][0]
    assert outcome["status"] == "rejected" and "actions-covered" in outcome["reason"]
    assert store.get(ids[1]).content == originals[ids[1]]
    relaxed = SemanticCompactionPolicy(consolidate_when_summaries=0, require_action_coverage=False)
    lenient = semantic_store(Scripted(forgetful), policy=relaxed)
    fill(lenient, 1, actions=(0,))
    assert compact_all(lenient).dossier["semantic_compaction"]["committed_summary_ids"]


def test_a_fallback_summarizer_takes_over_when_the_primary_is_unavailable():
    def down(request):
        raise ConnectionError("provider unreachable")

    compactor = SemanticCompactor(
        Scripted(down),
        policy=SemanticCompactionPolicy(consolidate_when_summaries=0),
        fallback=ExtractiveEpisodeSummarizer(),
    )
    store = InMemoryEpisodeStore(semantic=compactor)
    ids, _ = fill(store, 2)
    report = compact_all(store).dossier["semantic_compaction"]
    assert len(report["committed_summary_ids"]) == 2
    summary = store.summary_for(ids[0])
    assert summary.summarizer_id == "extractive-v1"
    note = report["outcomes"][0]["note"]
    assert "scripted unavailable" in note and "ConnectionError: provider unreachable" in note


def test_when_the_fallback_fails_too_the_originals_stay_and_both_causes_are_named():
    def down(request):
        raise ConnectionError("provider unreachable")

    def also_down(request):
        raise RuntimeError("no local model")

    class Second(Scripted):
        summarizer_id = "local"

    compactor = SemanticCompactor(
        Scripted(down),
        policy=SemanticCompactionPolicy(consolidate_when_summaries=0),
        fallback=Second(also_down),
    )
    store = InMemoryEpisodeStore(semantic=compactor)
    ids, originals = fill(store, 1)
    report = compact_all(store).dossier["semantic_compaction"]
    reason = report["outcomes"][0]["reason"]
    assert "ConnectionError: provider unreachable" in reason
    assert "RuntimeError: no local model" in reason
    assert store.get(ids[0]).content == originals[ids[0]]


def test_the_default_extractive_path_is_unchanged_and_passes_every_stage_it_can():
    policy = SemanticCompactionPolicy(consolidate_when_summaries=0, entailment_screen=True)
    store = semantic_store(policy=policy)
    ids, _ = fill(store, 6, failed=(2,), actions=(0,))
    report = compact_all(store).dossier["semantic_compaction"]
    assert {o["status"] for o in report["outcomes"]} == {"committed"}
    assert compactor_for().policy.entailment_screen is False


def rates(policy, judge=None):
    return evaluate(policy, scenarios=24, seed=5, judge=judge)


def test_measured_on_the_labeled_set_the_checks_have_the_limits_documented_for_them():
    base = {"consolidate_when_summaries": 0}
    deterministic = rates(SemanticCompactionPolicy(**base))
    assert deterministic.overall_false_rejection() == 0.0
    for name in ("status-flip", "negation", "fabricated-fact", "reworded-flip", "quantifier-flip"):
        assert deterministic.false_acceptance(name) == 1.0, name
    for name in (
        "number-change",
        "entity-swap",
        "missing-evidence",
        "outside-citation",
        "omitted-failure",
        "dropped-action",
    ):
        assert deterministic.false_acceptance(name) == 0.0, name

    screened = rates(SemanticCompactionPolicy(**base, entailment_screen=True))
    for name in ("status-flip", "negation", "fabricated-fact", "quantifier-flip"):
        assert screened.false_acceptance(name) == 0.0, name
    assert screened.false_acceptance("reworded-flip") == 1.0
    assert screened.false_rejection("far-paraphrase") == 1.0
    assert screened.false_rejection("quote") == screened.false_rejection("paraphrase") == 0.0
    assert screened.overall_false_acceptance() < deterministic.overall_false_acceptance()


def test_a_host_judge_is_the_only_stage_that_closes_the_remaining_gaps():
    base = SemanticCompactionPolicy(consolidate_when_summaries=0)
    perfect = rates(base, judge=OracleClaimJudge(0.0))
    assert perfect.overall_false_acceptance() == 0.0 and perfect.overall_false_rejection() == 0.0
    noisy = rates(base, judge=OracleClaimJudge(0.1, seed=2))
    assert noisy.overall_false_rejection() > 0.0
    assert set(BAD) | set(FAITHFUL) == set(noisy.total)
