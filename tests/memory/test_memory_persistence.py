import json

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.episode_archive import FileEpisodeArchive, InMemoryEpisodeArchive
from nailong_agent_sdk.memory.episode_models import (
    EpisodeState,
    SemanticCompactionPolicy,
    SummaryLifecycle,
)
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from tests.support.semantic_memory import (
    compact_all,
    file_store,
    fill,
    payload,
    semantic_store,
)


def policy(**options):
    return SemanticCompactionPolicy(**{"consolidate_when_summaries": 0, **options})


def populated(run_root, **options):
    store = file_store(run_root, policy=policy(**options))
    ids, originals = fill(store, 8, failed=(2,), actions=(0, 4))
    compact_all(store)
    return store, ids, originals


def tear(path, bytes_removed=60):
    path.write_text(path.read_text(encoding="utf-8")[:-bytes_removed], encoding="utf-8")


def test_a_new_store_restores_the_logical_state_after_a_restart(tmp_path):
    store, ids, originals = populated(tmp_path)
    checkpoint = store.checkpoint()
    records = {record.id: record.model_dump(mode="json") for record in store.list()}
    summaries = [summary.model_dump(mode="json") for summary in store.summaries()]
    generation = store.persist()
    restored = file_store(tmp_path, policy=policy())
    report = restored.restore()
    assert report.generation == generation and report.rejected == []
    assert restored.checkpoint() == checkpoint
    assert {r.id: r.model_dump(mode="json") for r in restored.list()} == records
    assert [s.model_dump(mode="json") for s in restored.summaries()] == summaries
    assert restored.pending_episode_ids() == store.pending_episode_ids()
    released = [
        r.id for r in restored.list() if r.state is EpisodeState.COMPACTED and not r.content
    ]
    assert released
    result = restored.rehydrate(released[:3], token_budget=100_000)
    assert set(result.restored_episode_ids) >= set(released[:3])
    for episode_id in result.restored_episode_ids:
        assert restored.get(episode_id).content == originals[episode_id]
    fresh = restored.open_exploratory("agent", content=payload(99))
    assert fresh.id == f"episode-{len(ids) + 1}"


def test_restoring_reproduces_evictions_and_summary_lifecycles(tmp_path):
    options = {
        "consolidate_when_summaries": 3,
        "consolidation_group_size": 2,
        "max_level": 3,
        "max_active_records": 4,
    }
    store = file_store(tmp_path, policy=SemanticCompactionPolicy(**options))
    fill(store, 12, failed=(2,))
    compact_all(store)
    assert store.evicted_episode_ids() and any(
        ref.lifecycle is not SummaryLifecycle.ACTIVE for ref in store.summary_refs()
    )
    checkpoint = store.checkpoint()
    refs = [ref.model_dump(mode="json") for ref in store.summary_refs()]
    store.persist()
    restored = file_store(tmp_path, policy=SemanticCompactionPolicy(**options))
    report = restored.restore()
    assert restored.checkpoint() == checkpoint
    assert [ref.model_dump(mode="json") for ref in restored.summary_refs()] == refs
    assert restored.evicted_episode_ids() == store.evicted_episode_ids()
    assert report.archived_summaries == sum(1 for ref in store.summary_refs() if ref.archive_ref)
    for ref in restored.summary_refs():
        assert restored.load_summary(ref.summary_id).intact()
    evicted = restored.evicted_episode_ids()[0]
    assert restored.get(evicted).state is EpisodeState.COMPACTED
    assert restored.rehydrate([evicted], token_budget=100_000).restored_episode_ids == [evicted]


def test_restore_falls_back_to_the_newest_generation_that_verifies(tmp_path):
    store, ids, _ = populated(tmp_path)
    first = store.persist()
    first_records = len(store.list())
    fill(store, 2, start=50)
    compact_all(store)
    second = store.persist()
    assert (first, second) == (1, 2) and len(store.list()) > first_records
    tear(store._snapshot_path(second))
    restored = file_store(tmp_path, policy=policy())
    report = restored.restore()
    assert report.generation == first and len(restored.list()) == first_records
    assert [item.generation for item in report.rejected] == [second]
    assert report.rejected[0].path.endswith("snapshot-0000000002.json")


def test_an_interrupted_write_leaves_the_previous_generation_whole(tmp_path, monkeypatch):
    from nailong_agent_sdk.memory import episode_store as module

    store, _, _ = populated(tmp_path)
    first = store.persist()
    fill(store, 1, start=70)

    def refuse(temporary, target, **_):
        raise OSError("disk full")

    monkeypatch.setattr(module, "replace_atomic", refuse)
    with pytest.raises(AgentSdkError) as raised:
        store.persist()
    assert raised.value.code == "EPISODE_MEMORY_PERSIST_FAILED"
    assert "disk full" in str(raised.value) and "generation 2" in str(raised.value)
    monkeypatch.undo()
    assert store.snapshot_generations() == [first]
    assert list(store.snapshot_directory.glob(".*.tmp")) == []
    restored = file_store(tmp_path, policy=policy())
    assert restored.restore().generation == first
    assert store.persist() == first + 1


def test_a_snapshot_edited_after_it_was_written_is_rejected_by_its_hash(tmp_path):
    store, _, _ = populated(tmp_path)
    first = store.persist()
    fill(store, 1, start=70)
    compact_all(store)
    second = store.persist()
    path = store._snapshot_path(second)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["summaries"][0]["summary_text"] = "an edited digest"
    path.write_text(json.dumps(data), encoding="utf-8")
    restored = file_store(tmp_path, policy=policy())
    report = restored.restore()
    assert report.generation == first
    assert any("integrity hash" in reason for reason in report.rejected[0].reasons)


def test_files_of_different_generations_cannot_be_combined(tmp_path):
    store, _, _ = populated(tmp_path)
    first = store.persist()
    fill(store, 2, start=60)
    compact_all(store)
    second = store.persist()
    older = json.loads(store._snapshot_path(first).read_text(encoding="utf-8"))
    newer_path = store._snapshot_path(second)
    newer = json.loads(newer_path.read_text(encoding="utf-8"))
    newer["records"] = older["records"]
    newer_path.write_text(json.dumps(newer), encoding="utf-8")
    report = file_store(tmp_path, policy=policy()).restore()
    assert report.generation == first
    assert report.rejected and any("integrity hash" in r for r in report.rejected[0].reasons)


def test_a_missing_archive_entry_rejects_the_generation_and_names_it(tmp_path):
    store, ids, _ = populated(tmp_path)
    store.persist()
    gone = next(r for r in store.list() if r.archive_id and r.content is None)
    (tmp_path / ".agent-memory" / "archive" / f"{gone.archive_id}.json").unlink()
    restored = file_store(tmp_path, policy=policy())
    with pytest.raises(AgentSdkError) as raised:
        restored.restore()
    assert raised.value.code == "EPISODE_MEMORY_RESTORE_FAILED"
    assert gone.archive_id in str(raised.value) or gone.id in str(raised.value)
    assert restored.list() == [] and restored.summaries() == []
    assert restored.open_exploratory("agent", content=payload(1)).id == "episode-1"


def test_a_corrupt_archive_entry_is_found_when_the_snapshot_is_verified_in_full(tmp_path):
    store, _, _ = populated(tmp_path)
    store.persist()
    entry = next(
        r for r in store.list() if r.archive_id and r.content is None and r.summary_id is not None
    )
    path = tmp_path / ".agent-memory" / "archive" / f"{entry.archive_id}.json"
    path.write_text(path.read_text(encoding="utf-8").replace("lorem", "forged"), encoding="utf-8")
    restored = file_store(tmp_path, policy=policy())
    with pytest.raises(AgentSdkError) as raised:
        restored.restore()
    assert raised.value.code == "EPISODE_MEMORY_RESTORE_FAILED"
    assert "EPISODE_ARCHIVE_CORRUPT" in str(raised.value) or "unreadable" in str(raised.value)


def test_nothing_is_restored_into_a_store_that_already_holds_episodes(tmp_path):
    store, _, _ = populated(tmp_path)
    store.persist()
    busy = file_store(tmp_path, policy=policy())
    busy.open_exploratory("agent", content=payload(5))
    before = busy.checkpoint()
    with pytest.raises(AgentSdkError) as raised:
        busy.restore()
    assert raised.value.code == "EPISODE_MEMORY_RESTORE_FAILED"
    assert "EPISODE_MEMORY_NOT_EMPTY" in str(raised.value)
    assert busy.checkpoint() == before


def test_an_unwritten_store_has_nothing_to_restore(tmp_path):
    empty = file_store(tmp_path, policy=policy())
    with pytest.raises(AgentSdkError) as raised:
        empty.restore()
    assert raised.value.code == "EPISODE_MEMORY_SNAPSHOT_MISSING"


def test_only_the_configured_number_of_generations_is_kept(tmp_path):
    store = file_store(tmp_path, policy=policy(), retain_generations=2)
    fill(store, 3)
    numbers = [store.persist() for _ in range(5)]
    assert numbers == [1, 2, 3, 4, 5] and store.snapshot_generations() == [4, 5]
    with pytest.raises(ValueError, match="retain_generations"):
        file_store(tmp_path, retain_generations=0)


def test_the_earlier_checkpoint_interface_and_episode_file_are_unchanged(tmp_path):
    store, _, _ = populated(tmp_path)
    checkpoint = store.persist_checkpoint()
    store.persist()
    assert store.load_checkpoint() == checkpoint and store.verify_checkpoint(checkpoint)
    legacy = json.loads((tmp_path / ".agent-memory" / "episodes.json").read_text("utf-8"))
    assert {item["id"] for item in legacy} == {record.id for record in store.list()}


def consolidating(**options):
    base = {"consolidate_when_summaries": 3, "consolidation_group_size": 2, "max_level": 3}
    return SemanticCompactionPolicy(**{**base, **options})


def test_superseded_summaries_move_to_the_archive_and_stay_retrievable():
    store = semantic_store(policy=consolidating())
    ids, _ = fill(store, 12, failed=(2,))
    compact_all(store)
    resident = {s.summary_id for s in store.summaries()}
    active = {s.summary_id for s in store.active_summaries()}
    assert resident == active
    superseded = [r for r in store.summary_refs() if r.lifecycle is SummaryLifecycle.SUPERSEDED]
    assert superseded and all(r.archive_ref and r.parent_summary_id for r in superseded)
    for ref in superseded:
        child = store.load_summary(ref.summary_id)
        parent = store.load_summary(ref.parent_summary_id)
        assert child.intact() and ref.summary_id in parent.child_summary_ids
        assert ref.summary_id not in resident
    for episode_id in ids:
        top = store.summary_for(episode_id)
        if store.get(episode_id).summary_id:
            assert (
                top is not None
                and top.summary_id in active
                and episode_id in top.source_episode_ids
            )


def test_a_parent_keeps_the_provenance_chain_down_to_the_original_evidence():
    store = semantic_store(policy=consolidating())
    ids, originals = fill(store, 12)
    compact_all(store)
    top = max(store.active_summaries(), key=lambda s: s.level)
    assert top.level >= 2
    pending = [top]
    leaves = set()
    while pending:
        current = pending.pop()
        if current.child_summary_ids:
            pending.extend(store.load_summary(child) for child in current.child_summary_ids)
        else:
            leaves.update(zip(current.source_episode_ids, current.archive_ids, strict=True))
    assert {episode for episode, _ in leaves} == set(top.source_episode_ids)
    for episode_id, archive_id in leaves:
        assert store.archive.get(archive_id).content == originals[episode_id]


def test_the_active_summary_ceiling_archives_the_oldest_and_protects_pinned_ones():
    store = semantic_store(policy=policy(max_active_summaries=3, max_level=1))
    first, originals = fill(store, 3)
    compact_all(store)
    assert len(store.active_summaries()) == 3
    store.rehydrate([first[0]], token_budget=100_000)
    pinned = store.get(first[0]).summary_id
    more, _ = fill(store, 3, start=10)
    store.compact(1, protected_episode_ids=[first[0]], relevance_query="nothing")
    active = {s.summary_id for s in store.active_summaries()}
    assert len(active) == 3 and pinned in active
    archived = [r for r in store.summary_refs() if r.lifecycle is SummaryLifecycle.ARCHIVED]
    assert len(archived) == 3 and pinned not in {r.summary_id for r in archived}
    for ref in archived:
        assert ref.archive_ref and store.load_summary(ref.summary_id).intact()
    covered = store.summary_for(first[1], resolve_archived=True)
    assert covered is not None and first[1] in covered.source_episode_ids
    assert store.summary_for(first[1]) is None


def test_a_token_ceiling_bounds_the_active_summaries_by_size():
    store = semantic_store(policy=policy(max_active_summary_tokens=200))
    fill(store, 10)
    compact_all(store)
    active = store.active_summaries()
    assert active and sum(s.summary_tokens for s in active) <= 200
    assert len(store.summary_refs()) == 10


def test_repeated_compaction_and_consolidation_keep_active_memory_bounded():
    store = semantic_store(
        policy=consolidating(
            consolidate_when_summaries=6, consolidation_group_size=3, max_level=2
        ).model_copy(update={"max_active_summaries": 5})
    )
    sizes = []
    for wave in range(12):
        fill(store, 6, start=wave * 10)
        compact_all(store)
        sizes.append((len(store.active_summaries()), len(store.summaries())))
    assert max(active for active, _ in sizes) <= 5
    assert max(resident for _, resident in sizes) <= 5
    assert len(store.summary_refs()) > 12
    for ref in store.summary_refs():
        assert store.load_summary(ref.summary_id).intact()
    assert {r.lifecycle for r in store.summary_refs()} >= {
        SummaryLifecycle.ACTIVE,
        SummaryLifecycle.SUPERSEDED,
    }


def test_the_in_memory_and_file_archives_store_and_verify_summaries(tmp_path):
    source = semantic_store(policy=policy())
    fill(source, 2)
    compact_all(source)
    summary = source.summaries()[0]
    for archive in (InMemoryEpisodeArchive(), FileEpisodeArchive(tmp_path)):
        receipt = archive.put_summary(summary)
        assert receipt.archive_id == f"summary-archive-{receipt.content_hash}"
        assert archive.has_summary(receipt.archive_id)
        assert archive.get_summary(receipt.archive_id) == summary
        assert archive.put_summary(summary) == receipt
    unsealed = summary.model_copy(update={"integrity_hash": ""})
    with pytest.raises(AgentSdkError) as raised:
        InMemoryEpisodeArchive().put_summary(unsealed)
    assert raised.value.code == "SUMMARY_ARCHIVE_UNSEALED"
    path = tmp_path / ".agent-memory" / "archive" / "summaries" / f"{receipt.archive_id}.json"
    path.write_text(path.read_text("utf-8").replace("succeeded", "forged"), encoding="utf-8")
    with pytest.raises(AgentSdkError) as corrupt:
        FileEpisodeArchive(tmp_path).get_summary(receipt.archive_id)
    assert corrupt.value.code == "SUMMARY_ARCHIVE_CORRUPT"
    with pytest.raises(AgentSdkError) as missing:
        InMemoryEpisodeArchive().get_summary(receipt.archive_id)
    assert missing.value.code == "SUMMARY_ARCHIVE_MISSING"


def test_a_store_without_a_semantic_compactor_keeps_the_old_surface(tmp_path):
    plain = InMemoryEpisodeStore()
    fill(plain, 3)
    assert plain.summaries() == [] and plain.summary_refs() == []
    assert plain.quarantined_summaries() == []
