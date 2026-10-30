import json

from nailong_agent_sdk.memory.episode_models import (
    EpisodeState,
    JudgeVerdict,
    SemanticCompactionPolicy,
)
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.semantic_compaction import plan_groups
from tests.support.semantic_memory import (
    CountingArchive,
    Scripted,
    compact_all,
    default_draft,
    fill,
    payload,
    semantic_store,
)


def test_without_a_semantic_compactor_compaction_is_unchanged():
    store = InMemoryEpisodeStore()
    ids, _ = fill(store, 6)
    result = compact_all(store)
    assert "semantic_compaction" not in result.dossier
    assert not store.semantic_compaction_enabled and store.summaries() == []
    assert all(store.get(i).content is None for i in result.compacted_episode_ids)


def test_unselected_episodes_are_archived_summarized_and_only_then_released():
    store = semantic_store()
    ids, originals = fill(store, 8)
    result = compact_all(store)
    report = result.dossier["semantic_compaction"]
    assert result.compacted_episode_ids and report["pending_episode_ids"] == []
    assert len(report["committed_summary_ids"]) == len(result.compacted_episode_ids)
    for episode_id in result.compacted_episode_ids:
        record = store.get(episode_id)
        summary = store.summary_for(episode_id)
        assert record.state is EpisodeState.COMPACTED and record.content is None
        assert record.summary_id == summary.summary_id and record.archive_id in summary.archive_ids
        assert store.archive.has(record.archive_id)
        restored = store.archive.get(record.archive_id)
        assert restored.content == originals[episode_id] and restored.id == episode_id
        assert summary.verification_status.value == "verified" and summary.intact()
        assert summary.source_hashes[summary.source_episode_ids.index(episode_id)] == (
            record.content_hash
        )
        assert summary.summary_tokens < summary.source_tokens


def test_groups_below_the_compression_floor_are_left_alone_and_never_reach_the_summarizer():
    summarizer = Scripted(default_draft)
    archive = CountingArchive()
    policy = SemanticCompactionPolicy(consolidate_when_summaries=0, compression_floor_tokens=50_000)
    store = semantic_store(summarizer, archive=archive, policy=policy)
    ids, originals = fill(store, 4)
    result = compact_all(store)
    report = result.dossier["semantic_compaction"]
    assert result.compacted_episode_ids and summarizer.requests == []
    assert archive.puts == 0 and report["outcomes"] == []
    assert report["pending_episode_ids"] == result.compacted_episode_ids
    for episode_id in result.compacted_episode_ids:
        assert store.get(episode_id).content == originals[episode_id]


def test_a_digest_does_not_repeat_the_description_so_a_modest_payload_still_compresses():
    store = semantic_store()
    descriptions = {}
    for index in range(3):
        content = payload(index, words=40)
        record = store.open_exploratory("agent", content=content)
        description = (
            f'Tool "read_file"({{"path":"f{index}.txt"}}) succeeded: '
            + json.dumps(content["result"]["output"])[:220]
        )
        store.close(record.id, description=description)
        descriptions[record.id] = description
    result = compact_all(store)
    report = result.dossier["semantic_compaction"]
    assert {o["status"] for o in report["outcomes"]} == {"committed"}, report["outcomes"]
    for episode_id in result.compacted_episode_ids:
        summary = store.summary_for(episode_id)
        assert summary.summary_tokens <= summary.source_tokens * 0.75
        claims = [fact.claim for fact in summary.key_facts]
        assert claims and summary.summary_text.count(descriptions[episode_id][:40]) == 0


def test_retained_episodes_keep_their_payloads_and_are_not_summarized():
    store = semantic_store()
    ids, originals = fill(store, 8)
    result = store.compact(
        10_000, protected_episode_ids=[ids[0]], active_episode_id=ids[1], relevance_query="x"
    )
    assert result.compacted_episode_ids == []
    result = store.compact(
        600, protected_episode_ids=[ids[0]], active_episode_id=ids[1], relevance_query="x"
    )
    for episode_id in (ids[0], ids[1]):
        assert store.get(episode_id).state is not EpisodeState.COMPACTED
        assert store.get(episode_id).content == originals[episode_id]
        assert store.summary_for(episode_id) is None


def test_related_episodes_are_summarized_as_one_unit_with_their_real_dependencies():
    store = semantic_store(policy=SemanticCompactionPolicy(consolidate_when_summaries=0))
    ids, _ = fill(store, 6, actions=(0, 0, 3))
    compact_all(store)
    related = [s for s in store.active_summaries() if len(s.source_episode_ids) > 1]
    assert related
    first = next(s for s in related if ids[0] in s.source_episode_ids)
    assert ids[6] in first.source_episode_ids and ids[7] in first.source_episode_ids
    assert first.dependency_ids == []
    lonely = store.summary_for(ids[5])
    assert lonely.source_episode_ids == [ids[5]]


def test_a_dependency_outside_the_group_is_recorded_not_invented():
    store = semantic_store()
    ids, _ = fill(store, 3, actions=(0,))
    store.compact(10_000, protected_episode_ids=[ids[0]], relevance_query="x")
    store._compact_unretained({ids[3]})
    store._semantic_pass()
    summary = store.summary_for(ids[3])
    assert summary.source_episode_ids == [ids[3]]
    assert summary.dependency_ids == [ids[0]]


def test_grouping_is_deterministic_and_bounded():
    store = InMemoryEpisodeStore()
    ids, _ = fill(store, 9, actions=(0, 1, 2, 3, 4, 5))
    for record in store.list():
        store._compact_record(record.id) if record.state is EpisodeState.CLOSED else None
    records = store.list()
    policy = SemanticCompactionPolicy(max_group_episodes=3, consolidate_when_summaries=0)
    first = [[r.id for r in group] for group in plan_groups(records, policy)]
    second = [[r.id for r in group] for group in plan_groups(list(reversed(records)), policy)]
    assert first == second
    assert all(len(group) <= 3 for group in first)
    assert sorted(i for group in first for i in group) == sorted(r.id for r in records)


def test_a_judge_can_veto_a_deterministically_valid_summary():
    class Veto:
        judge_id = "veto"

        def judge(self, request, summary):
            return JudgeVerdict(accepted=False, reasons=["contradicts the task"])

    store = semantic_store(judge=Veto())
    ids, originals = fill(store, 3)
    result = compact_all(store)
    outcomes = result.dossier["semantic_compaction"]["outcomes"]
    assert {o["status"] for o in outcomes} == {"rejected"}
    assert any(c["name"] == "judge:veto" and not c["passed"] for c in outcomes[0]["checks"])
    assert any(c["passed"] for c in outcomes[0]["checks"])
    assert all(store.get(i).content is not None for i in result.compacted_episode_ids)


def test_a_judge_that_raises_counts_as_a_rejection_and_one_that_accepts_commits():
    class Boom:
        judge_id = "boom"

        def judge(self, request, summary):
            raise RuntimeError("scorer offline")

    class Accept:
        judge_id = "accept"

        def judge(self, request, summary):
            return JudgeVerdict(accepted=True)

    broken = semantic_store(judge=Boom())
    fill(broken, 3)
    out = compact_all(broken).dossier["semantic_compaction"]["outcomes"]
    assert {o["status"] for o in out} == {"rejected"} and "scorer offline" in str(out)
    fine = semantic_store(judge=Accept())
    fill(fine, 3)
    committed = compact_all(fine).dossier["semantic_compaction"]
    assert committed["committed_summary_ids"] and not committed["pending_episode_ids"]


def test_contradictory_observations_are_kept_apart_and_flagged():
    store = semantic_store(policy=SemanticCompactionPolicy(unrelated_batch_size=4))
    first = store.open_exploratory(
        "agent", content=payload(0, "succeeded", args={"path": "same.txt"})
    )
    store.close(first.id, description="read same.txt succeeded")
    second = store.open_exploratory(
        "agent", content=payload(1, "failed", "now missing", args={"path": "same.txt"})
    )
    store.close(second.id, description="read same.txt failed")
    compact_all(store)
    summary = store.summary_for(first.id)
    assert summary is store.summary_for(second.id)
    assert summary.source_episode_ids == [first.id, second.id]
    assert [fact.source_episode_ids for fact in summary.key_facts] == [[first.id]]
    failure = [issue for issue in summary.unresolved_issues if issue.startswith(second.id)]
    assert failure and "now missing" in failure[0]
    conflict = [issue for issue in summary.unresolved_issues if "conflicting results" in issue]
    assert conflict and first.id in conflict[0] and second.id in conflict[0]


def test_the_model_view_of_a_large_summary_lists_a_bounded_number_of_sources_and_counts_all():
    policy = SemanticCompactionPolicy(
        consolidate_when_summaries=0,
        max_group_episodes=24,
        max_group_source_tokens=100_000,
        unrelated_batch_size=24,
    )
    store = semantic_store(policy=policy)
    ids, _ = fill(store, 24)
    for number, episode_id in enumerate(ids, start=1):
        store.attach_source_handle(episode_id, f"result-{number}")
    report = compact_all(store).dossier["semantic_compaction"]
    assert len(store.active_summaries()) == 1, report["outcomes"]
    summary = store.active_summaries()[0]
    assert len(summary.source_episode_ids) == len(summary.source_hashes) == 24
    view = summary.view()
    assert view.source_episode_count == 24 and view.source_handle_count == 24
    assert view.source_episode_ids == [*ids[:8], *ids[-8:]]
    assert view.source_handles == [f"result-{n}" for n in [*range(1, 9), *range(17, 25)]]


def test_a_group_of_many_related_episodes_still_fits_the_summary_budget():
    store = semantic_store()
    ids, _ = fill(store, 1, actions=(0,) * 7)
    compact_all(store)
    summaries = store.active_summaries()
    assert len(summaries) == 1 and len(summaries[0].source_episode_ids) == 8
    assert summaries[0].summary_tokens <= SemanticCompactionPolicy().max_summary_tokens
    assert summaries[0].dependency_ids == []
