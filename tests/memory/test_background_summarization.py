import threading
import time

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.episode_models import EpisodeState, SemanticCompactionPolicy
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.semantic_compaction import (
    ExtractiveEpisodeSummarizer,
    SemanticCompactor,
)
from nailong_agent_sdk.memory.summarization_scheduler import SummarizationScheduler
from tests.support.semantic_memory import fill


class Gated:
    summarizer_id = "gated"

    def __init__(self, delay=0.0):
        self.release = threading.Event()
        self.started = threading.Semaphore(0)
        self.delay = delay
        self.calls = 0
        self.gated = delay == 0.0

    def summarize(self, request):
        self.calls += 1
        self.started.release()
        if self.gated:
            assert self.release.wait(20), "the test never released the summarizer"
        else:
            time.sleep(self.delay)
        return ExtractiveEpisodeSummarizer().summarize(request)


def background_store(summarizer, **options):
    policy = SemanticCompactionPolicy(
        **{"consolidate_when_summaries": 0, "background_summarization": True, **options}
    )
    return InMemoryEpisodeStore(semantic=SemanticCompactor(summarizer, policy=policy))


def test_the_projection_step_does_not_wait_for_a_slow_summarizer():
    summarizer = Gated(delay=0.4)
    store = background_store(summarizer)
    ids, originals = fill(store, 4)
    started = time.perf_counter()
    report = store.compact(1, relevance_query="x").dossier["semantic_compaction"]
    elapsed = time.perf_counter() - started
    assert elapsed < 0.3, f"compact waited {elapsed:.2f}s for the summarizer"
    assert report["jobs_submitted"] == 4 and report["committed_summary_ids"] == []
    assert sorted(report["pending_episode_ids"]) == sorted(ids[:4])
    assert all(store.get(i).content == originals[i] for i in ids)
    settled = store.settle_background(timeout=20)
    assert settled.jobs_committed == 4 and not store.pending_episode_ids()
    assert all(store.get(i).content is None and store.summary_for(i) for i in ids)
    store.close_background()


def test_a_synchronous_store_pays_the_summarizer_latency_inside_the_same_call():
    summarizer = Gated(delay=0.15)
    policy = SemanticCompactionPolicy(consolidate_when_summaries=0)
    store = InMemoryEpisodeStore(semantic=SemanticCompactor(summarizer, policy=policy))
    fill(store, 4)
    started = time.perf_counter()
    store.compact(1, relevance_query="x")
    assert time.perf_counter() - started >= 0.5


def test_a_job_that_finishes_after_its_episodes_changed_is_discarded():
    summarizer = Gated()
    store = background_store(summarizer)
    ids, originals = fill(store, 1)
    store.compact(1, relevance_query="x")
    assert summarizer.started.acquire(timeout=10)
    store.rehydrate([ids[0]], token_budget=100_000)
    assert store.get(ids[0]).state is EpisodeState.CLOSED
    summarizer.release.set()
    settled = store.settle_background(timeout=20)
    assert settled.jobs_stale == 1 and settled.jobs_committed == 0
    assert store.summaries() == [] and store.get(ids[0]).summary_id is None
    assert store.get(ids[0]).content == originals[ids[0]]
    store.close_background()


def test_the_queue_is_bounded_and_what_does_not_fit_stays_pending_with_its_payload():
    summarizer = Gated()
    store = background_store(summarizer, max_queued_summarizations=2, max_background_workers=1)
    ids, originals = fill(store, 6)
    report = store.compact(1, relevance_query="x").dossier["semantic_compaction"]
    assert report["jobs_submitted"] <= 3 and report["jobs_not_accepted"].get("full", 0) >= 1
    assert all(store.get(i).content == originals[i] for i in ids)
    summarizer.release.set()
    store.settle_background(timeout=20)
    store.compact(1, relevance_query="again")
    store.settle_background(timeout=20)
    store.compact(1, relevance_query="and again")
    store.settle_background(timeout=20)
    assert not store.pending_episode_ids()
    store.close_background()


def test_a_summarizer_that_raises_in_a_job_leaves_memory_untouched():
    def broken(request):
        raise RuntimeError("model endpoint refused the call")

    class Broken:
        summarizer_id = "broken"
        summarize = staticmethod(broken)

    store = background_store(Broken())
    ids, originals = fill(store, 2)
    store.compact(1, relevance_query="x")
    settled = store.settle_background(timeout=20)
    assert settled.committed_summary_ids == [] and store.summaries() == []
    assert "RuntimeError: model endpoint refused the call" in settled.outcomes[0].reason
    assert all(store.get(i).content == originals[i] for i in ids)
    store.close_background()


def test_closing_the_background_pipeline_drops_queued_work_and_late_results():
    summarizer = Gated()
    store = background_store(summarizer, max_queued_summarizations=4)
    ids, originals = fill(store, 3)
    store.compact(1, relevance_query="x")
    assert summarizer.started.acquire(timeout=10)
    assert store.close_background() >= 1
    summarizer.release.set()
    time.sleep(0.1)
    assert store.settle_background(timeout=1).jobs_committed == 0
    assert store.summaries() == [] and all(store.get(i).content == originals[i] for i in ids)


def test_a_primary_that_is_unavailable_falls_back_to_the_extractive_summarizer():
    class Down:
        summarizer_id = "remote"

        def summarize(self, request):
            raise ConnectionError("endpoint unreachable")

    policy = SemanticCompactionPolicy(consolidate_when_summaries=0, background_summarization=True)
    store = InMemoryEpisodeStore(
        semantic=SemanticCompactor(Down(), policy=policy, fallback=ExtractiveEpisodeSummarizer())
    )
    ids, _ = fill(store, 2)
    store.compact(1, relevance_query="x")
    settled = store.settle_background(timeout=20)
    assert settled.jobs_committed == 2
    assert store.summary_for(ids[0]).summarizer_id == "extractive-v1"
    store.close_background()


def test_background_consolidation_commits_only_while_its_children_are_still_active():
    summarizer = Gated(delay=0.0)
    summarizer.gated = False
    store = background_store(
        summarizer, consolidate_when_summaries=2, consolidation_group_size=2, max_level=2
    )
    ids, _ = fill(store, 4)
    for _round in range(4):
        store.compact(1, relevance_query="x")
        store.settle_background(timeout=20)
    levels = sorted(ref.level for ref in store.summary_refs())
    assert levels.count(1) == 4 and max(levels) == 2
    parents = [s for s in store.active_summaries() if s.level == 2]
    assert parents and all(s.intact() for s in parents)
    store.close_background()


def test_the_scheduler_bounds_its_queue_and_reports_why_work_was_not_accepted():
    release = threading.Event()
    scheduler = SummarizationScheduler(max_workers=1, max_queued=1, job_timeout_seconds=None)
    statuses = [scheduler.submit(f"job-{n}", lambda: release.wait(5)) for n in range(4)]
    assert statuses[0] == "accepted"
    assert statuses.count("full") >= 2 or "full" in statuses
    assert scheduler.submit("job-0", lambda: None) == "duplicate"
    release.set()
    assert scheduler.wait_idle(5)
    done = scheduler.drain()
    assert {item.key for item in done} <= {"job-0", "job-1", "job-2", "job-3"}
    assert scheduler.close() == 0 and scheduler.submit("late", lambda: None) == "closed"


def test_the_scheduler_abandons_a_hung_job_and_then_refuses_more_work():
    stuck = threading.Event()
    scheduler = SummarizationScheduler(
        max_workers=2, max_queued=4, job_timeout_seconds=0.05, max_abandoned=1
    )
    assert scheduler.submit("hung", lambda: stuck.wait(10)) == "accepted"
    time.sleep(0.2)
    results = scheduler.drain()
    assert [r.key for r in results if r.timed_out] == ["hung"]
    assert scheduler.counts()["abandoned"] == 1
    assert scheduler.submit("next", lambda: None) == "unavailable"
    stuck.set()
    assert scheduler.wait_idle(5)
    time.sleep(0.1)
    assert scheduler.submit("after", lambda: 1) == "accepted"
    scheduler.close()


def test_scheduler_settings_are_validated():
    with pytest.raises(ValueError, match="max_workers"):
        SummarizationScheduler(max_workers=0)
    with pytest.raises(ValueError, match="job_timeout_seconds"):
        SummarizationScheduler(job_timeout_seconds=0)


def test_the_abandon_guard_still_stops_a_background_commit():
    store = background_store(Gated(delay=0.01))
    fill(store, 2)
    store.compact(1, relevance_query="x")
    guard = store.guard_compaction()
    guard.abandon()
    with pytest.raises(AgentSdkError) as raised:
        store.settle_background(timeout=20)
    assert raised.value.code == "EPISODE_COMPACTION_ABANDONED"
    store.close_background()
    assert store.summaries() == []


def test_a_consolidation_whose_child_was_superseded_meanwhile_is_not_committed():
    class LevelGated:
        summarizer_id = "level-gated"

        def __init__(self):
            self.release = threading.Event()
            self.started = threading.Event()

        def summarize(self, request):
            if request.level > 1:
                self.started.set()
                assert self.release.wait(20)
            return ExtractiveEpisodeSummarizer().summarize(request)

    summarizer = LevelGated()
    store = background_store(
        summarizer, consolidate_when_summaries=1, consolidation_group_size=2, max_level=2
    )
    fill(store, 2)
    store.compact(1, relevance_query="x")
    store.settle_background(timeout=20)
    store.compact(1, relevance_query="again")
    assert summarizer.started.wait(10)
    child = store.active_summaries()[0]
    rival = child.model_copy(
        update={"summary_id": "summary-900", "level": 2, "child_summary_ids": [child.summary_id]}
    ).sealed()
    store._ledger.supersede(rival, [child])
    summarizer.release.set()
    settled = store.settle_background(timeout=20)
    assert settled.jobs_stale == 1 and settled.consolidated_summary_ids == []
    assert store.summary_lifecycle(child.summary_id).value in {"superseded", "archived"}
    store.close_background()
