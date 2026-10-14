from nailong_agent_sdk.memory.episode_models import (
    EpisodeState,
    SemanticCompactionPolicy,
    SemanticSummaryDraft,
    SummaryLifecycle,
)
from tests.support.semantic_memory import Scripted, compact_all, default_draft, fill, semantic_store


def test_consolidation_reaches_the_level_bound_on_a_long_run_and_keeps_every_episode_covered():
    store = semantic_store(policy=SemanticCompactionPolicy())
    ids, _ = fill(store, 80)
    compact_all(store)
    active = store.active_summaries()
    assert len(active) <= 12
    assert max(summary.level for summary in active) == 3
    assert all(summary.intact() for summary in store.summaries())
    compacted = [i for i in ids if store.get(i).state is EpisodeState.COMPACTED]
    for episode_id in compacted:
        top = store.summary_for(episode_id)
        assert top in active and episode_id in top.source_episode_ids
    assert store.pending_episode_ids() == []


def test_consolidation_builds_a_bounded_hierarchy_with_provenance():
    policy = SemanticCompactionPolicy(
        consolidate_when_summaries=3, consolidation_group_size=2, max_level=3
    )
    store = semantic_store(policy=policy)
    ids, originals = fill(store, 12, failed=(2,))
    compact_all(store)
    refs = store.summary_refs()
    assert max(ref.level for ref in refs) <= 3
    top = store.active_summaries()
    assert len(top) <= 3 or all(s.level == 3 for s in top)
    parents = [store.load_summary(ref.summary_id) for ref in refs if ref.level > 1]
    assert parents
    assert {s.summary_id for s in store.summaries()} == {s.summary_id for s in top}
    for parent in parents:
        children = [store.load_summary(c) for c in parent.child_summary_ids]
        for child in children:
            ref = next(r for r in store.summary_refs() if r.summary_id == child.summary_id)
            assert ref.lifecycle is not SummaryLifecycle.ACTIVE and ref.archive_ref is not None
        assert set(parent.source_episode_ids) == {e for c in children for e in c.source_episode_ids}
        assert all(
            issue in parent.unresolved_issues for c in children for issue in c.unresolved_issues
        )
        assert set(parent.archive_ids) == {a for c in children for a in c.archive_ids}
        assert parent.intact()
    covered = {e for s in top for e in s.source_episode_ids}
    assert covered >= {i for i in ids if store.get(i).summary_id}
    assert any("episode-3" in issue for s in top for issue in s.unresolved_issues)
    for episode_id in ids:
        record = store.get(episode_id)
        if record.summary_id:
            assert store.summary_for(episode_id) in top


def test_consolidation_respects_the_level_bound():
    policy = SemanticCompactionPolicy(
        consolidate_when_summaries=2, consolidation_group_size=2, max_level=1
    )
    store = semantic_store(policy=policy)
    fill(store, 8)
    compact_all(store)
    assert {s.level for s in store.summaries()} == {1}


def test_a_consolidation_that_drops_an_unresolved_issue_is_rejected():
    def handler(request):
        draft = default_draft(request)
        if request.level > 1:
            return SemanticSummaryDraft(summary_text=draft.summary_text, key_facts=draft.key_facts)
        return draft

    policy = SemanticCompactionPolicy(consolidate_when_summaries=1, consolidation_group_size=2)
    store = semantic_store(Scripted(handler), policy=policy)
    fill(store, 6, failed=(0, 1, 2, 3, 4, 5))
    result = compact_all(store)
    outcomes = result.dossier["semantic_compaction"]["outcomes"]
    rejected = [o for o in outcomes if o["level"] == 2]
    assert rejected and rejected[0]["status"] == "rejected"
    assert "child-issues-preserved" in rejected[0]["reason"]
    assert {s.level for s in store.summaries()} == {1}


def test_graph_size_policy_evicts_only_archived_summarized_records_nothing_live_needs():
    policy = SemanticCompactionPolicy(consolidate_when_summaries=0, max_active_records=4)
    store = semantic_store(policy=policy)
    ids, originals = fill(store, 10, actions=(0, 1))
    result = store.compact(900, protected_episode_ids=[ids[0]], relevance_query="read_file f0")
    evicted = result.dossier["semantic_compaction"]["evicted_episode_ids"]
    assert len(evicted) == 8 and len(store.list()) == 4
    for episode_id in evicted:
        assert episode_id not in {r.id for r in store.list()}
        record = store.get(episode_id)
        assert record.state is EpisodeState.COMPACTED and record.content is None
        assert record.archive_id and store.summary_for(episode_id) is not None
    live_ids = {r.id for r in store.list() if r.state is not EpisodeState.COMPACTED}
    for record in store.list():
        for dependency in record.depends_on:
            if record.state is not EpisodeState.COMPACTED:
                assert store.get(dependency) is not None and dependency in live_ids
    assert store.evicted_episode_ids() == sorted(evicted, key=lambda i: int(i.split("-")[1]))
    restored = store.rehydrate([evicted[0]], token_budget=100_000)
    assert evicted[0] in restored.restored_episode_ids
    assert store.get(evicted[0]).content == originals[evicted[0]]
    assert evicted[0] not in store.evicted_episode_ids()


def test_eviction_never_removes_a_record_a_live_episode_depends_on():
    policy = SemanticCompactionPolicy(consolidate_when_summaries=0, max_active_records=1)
    store = semantic_store(policy=policy)
    ids, _ = fill(store, 3, actions=(0,))
    store.compact(10_000, protected_episode_ids=[ids[3]], relevance_query="x")
    assert store.get(ids[0]) is not None and store.get(ids[0]).state is not EpisodeState.COMPACTED
    assert ids[0] in {r.id for r in store.list()}
