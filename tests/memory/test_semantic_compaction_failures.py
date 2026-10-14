import threading

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.episode_archive import InMemoryEpisodeArchive, snapshot_text
from nailong_agent_sdk.memory.episode_models import (
    EpisodeState,
    GroundedClaim,
    SemanticCompactionPolicy,
    SemanticSummaryDraft,
)
from tests.support.semantic_memory import (
    CountingArchive,
    Scripted,
    compact_all,
    default_draft,
    fill,
    semantic_store,
)


def test_a_failing_summarizer_leaves_every_original_intact_and_is_retried_later():
    broken = {"on": True}

    def handler(request):
        if broken["on"]:
            raise RuntimeError("provider unavailable")
        return default_draft(request)

    store = semantic_store(Scripted(handler))
    ids, originals = fill(store, 6)
    first = compact_all(store)
    report = first.dossier["semantic_compaction"]
    assert first.compacted_episode_ids and report["committed_summary_ids"] == []
    assert {o["status"] for o in report["outcomes"]} == {"failed"}
    assert "provider unavailable" in report["outcomes"][0]["reason"]
    for episode_id in first.compacted_episode_ids:
        record = store.get(episode_id)
        assert record.state is EpisodeState.COMPACTED
        assert record.content == originals[episode_id] and record.summary_id is None
    assert sorted(store.pending_episode_ids()) == sorted(first.compacted_episode_ids)
    broken["on"] = False
    store._group_attempts.clear()
    store.compact(1, relevance_query="nothing")
    assert store.pending_episode_ids() == []
    assert all(store.get(i).content is None for i in first.compacted_episode_ids)


def test_repeated_failures_stop_calling_the_provider_after_the_attempt_limit():
    calls = []

    def handler(request):
        calls.append(request.group_id)
        raise RuntimeError("down")

    policy = SemanticCompactionPolicy(consolidate_when_summaries=0, max_group_attempts=2)
    store = semantic_store(Scripted(handler), policy=policy)
    fill(store, 4)
    for _ in range(5):
        store.compact(1, relevance_query="x")
    assert len(calls) == len(set(calls)) * 2
    assert store.pending_episode_ids()


@pytest.mark.parametrize(
    ("make_draft", "failed_check"),
    [
        (
            lambda request: SemanticSummaryDraft(
                summary_text="read a file",
                key_facts=[
                    GroundedClaim(
                        claim="the file held the deployment password",
                        source_episode_ids=[request.episodes[0].episode_id],
                        evidence="deployment password is hunter2",
                    )
                ],
                unresolved_issues=[f"{i}: failed" for i in request.mandatory_issue_episode_ids],
            ),
            "claims-grounded",
        ),
        (
            lambda request: SemanticSummaryDraft(
                summary_text="everything went fine",
                key_facts=[],
                unresolved_issues=[],
            ),
            "mandatory-outcomes-covered",
        ),
        (
            lambda request: SemanticSummaryDraft(
                summary_text="opened config/prod_secrets.yaml and found 9137 entries",
                unresolved_issues=[f"{i}: failed" for i in request.mandatory_issue_episode_ids],
            ),
            "identifiers-supported",
        ),
        (
            lambda request: SemanticSummaryDraft(
                summary_text="x" * 3_000,
                unresolved_issues=[f"{i}: failed" for i in request.mandatory_issue_episode_ids],
            ),
            "compression",
        ),
        (
            lambda request: SemanticSummaryDraft(
                summary_text="read a file",
                key_facts=[
                    GroundedClaim(
                        claim="from another episode",
                        source_episode_ids=["episode-999"],
                        evidence="lorem ipsum",
                    )
                ],
                unresolved_issues=[f"{i}: failed" for i in request.mandatory_issue_episode_ids],
            ),
            "citations-within-group",
        ),
    ],
)
def test_a_summary_that_fails_verification_never_releases_the_originals(make_draft, failed_check):
    store = semantic_store(Scripted(make_draft))
    ids, originals = fill(store, 4, failed=(0, 1, 2, 3))
    result = compact_all(store)
    report = result.dossier["semantic_compaction"]
    assert report["committed_summary_ids"] == []
    rejected = [o for o in report["outcomes"] if o["status"] == "rejected"]
    assert rejected and failed_check in rejected[0]["reason"]
    failing = [c for c in rejected[0]["checks"] if not c["passed"]]
    assert any(c["name"] == failed_check for c in failing)
    for episode_id in result.compacted_episode_ids:
        assert store.get(episode_id).content == originals[episode_id]
        assert store.summary_for(episode_id) is None


def test_a_malformed_draft_is_rejected_naming_the_field():
    store = semantic_store(Scripted(lambda request: {"summary_text": ""}))
    ids, originals = fill(store, 3)
    result = compact_all(store)
    outcomes = result.dossier["semantic_compaction"]["outcomes"]
    assert outcomes and {o["status"] for o in outcomes} == {"rejected"}
    assert "summary_text" in outcomes[0]["reason"]
    assert all(store.get(i).content is not None for i in result.compacted_episode_ids)


class FlakyArchive(InMemoryEpisodeArchive):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    def put(self, record):
        if self.mode == "raise":
            raise OSError("disk full")
        receipt = super().put(record)
        if self.mode == "lose":
            self._entries.clear()
        if self.mode == "corrupt":
            other = record.model_copy(update={"description": "tampered"})
            self._entries[receipt.archive_id] = snapshot_text(other)
        return receipt


@pytest.mark.parametrize("mode", ["raise", "lose", "corrupt"])
def test_an_incomplete_or_failed_archive_keeps_the_originals(mode):
    summarizer = Scripted(default_draft)
    store = semantic_store(summarizer, archive=FlakyArchive(mode))
    ids, originals = fill(store, 4)
    result = compact_all(store)
    report = result.dossier["semantic_compaction"]
    assert report["committed_summary_ids"] == [] and summarizer.requests == []
    assert {o["status"] for o in report["outcomes"]} == {"failed"}
    assert "archival failed" in report["outcomes"][0]["reason"]
    for episode_id in result.compacted_episode_ids:
        assert store.get(episode_id).content == originals[episode_id]


def test_an_interrupted_summarization_cannot_leave_a_partial_commit():
    state = {"calls": 0}

    def handler(request):
        state["calls"] += 1
        if state["calls"] == 3:
            raise KeyboardInterrupt
        return default_draft(request)

    store = semantic_store(Scripted(handler))
    ids, originals = fill(store, 6)
    with pytest.raises(KeyboardInterrupt):
        compact_all(store)
    for record in store.list():
        if record.state is EpisodeState.COMPACTED:
            released = record.content is None
            covered = record.summary_id is not None and record.archive_id is not None
            assert released == covered, record.id
            if not released:
                assert record.content == originals[record.id]
    assert {s.summary_id for s in store.summaries()} == {
        store.get(i).summary_id for i in ids if store.get(i).summary_id
    }
    assert state["calls"] == 3
    store.compact(1, relevance_query="again")
    assert store.pending_episode_ids() == []


def snapshot_of(store):
    return [record.model_dump(mode="json") for record in store.list()]


def test_an_abandoned_compaction_commits_nothing_once_the_abandon_returns():
    entered = threading.Event()
    release = threading.Event()

    def handler(request):
        entered.set()
        assert release.wait(10)
        return default_draft(request)

    store = semantic_store(Scripted(handler))
    ids, originals = fill(store, 4)
    guard = store.guard_compaction()
    outcome = {}

    def work():
        try:
            compact_all(store)
        except BaseException as error:
            outcome["error"] = error

    worker = threading.Thread(target=work)
    worker.start()
    assert entered.wait(10)
    guard.abandon()
    frozen = snapshot_of(store)
    release.set()
    worker.join(10)
    assert not worker.is_alive()
    assert isinstance(outcome["error"], AgentSdkError)
    assert outcome["error"].code == "EPISODE_COMPACTION_ABANDONED"
    assert snapshot_of(store) == frozen and store.summaries() == []
    for episode_id in ids:
        record = store.get(episode_id)
        if record.state is EpisodeState.COMPACTED:
            assert record.content == originals[episode_id] and record.summary_id is None


def test_a_compaction_that_starts_after_the_abandon_changes_nothing():
    store = semantic_store()
    fill(store, 4)
    store.guard_compaction().abandon()
    before = snapshot_of(store)
    with pytest.raises(AgentSdkError) as raised:
        compact_all(store)
    assert raised.value.code == "EPISODE_COMPACTION_ABANDONED"
    assert snapshot_of(store) == before and store.summaries() == []
    store.guard_compaction()
    assert compact_all(store).dossier["semantic_compaction"]["committed_summary_ids"]


def test_an_abandon_between_planning_and_summarizing_stops_before_any_provider_call():
    summarizer = Scripted(default_draft)
    archive = CountingArchive()
    store = semantic_store(summarizer, archive=archive)
    fill(store, 4)
    guard = store.guard_compaction()
    plan = store._semantic.plan

    def abandoning_plan(records):
        groups = plan(records)
        guard.abandon()
        return groups

    store._semantic.plan = abandoning_plan
    with pytest.raises(AgentSdkError) as raised:
        compact_all(store)
    assert raised.value.code == "EPISODE_COMPACTION_ABANDONED"
    assert summarizer.requests == [] and archive.puts == 0 and store.summaries() == []


def test_a_group_finished_after_the_abandon_is_discarded_not_committed():
    holder = {}
    state = {"calls": 0}

    def handler(request):
        state["calls"] += 1
        if state["calls"] == 2:
            holder["guard"].abandon()
        return default_draft(request)

    store = semantic_store(Scripted(handler))
    ids, originals = fill(store, 4)
    holder["guard"] = store.guard_compaction()
    with pytest.raises(AgentSdkError) as raised:
        compact_all(store)
    assert raised.value.code == "EPISODE_COMPACTION_ABANDONED"
    assert len(store.summaries()) == 1 and state["calls"] == 2
    committed = store.summaries()[0].source_episode_ids
    pending = store.pending_episode_ids()
    assert committed and pending and not set(committed) & set(pending)
    for episode_id in pending:
        assert store.get(episode_id).content == originals[episode_id]
