import json

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.episode_archive import (
    FileEpisodeArchive,
    InMemoryEpisodeArchive,
    decode_snapshot,
)
from nailong_agent_sdk.memory.episode_models import EpisodeState, SemanticCompactionPolicy
from nailong_agent_sdk.memory.episode_store import FileEpisodeStore, InMemoryEpisodeStore
from nailong_agent_sdk.memory.semantic_compaction import (
    ExtractiveEpisodeSummarizer,
    SemanticCompactor,
)
from tests.support.semantic_memory import Scripted, compact_all, fill, semantic_store


def test_rehydration_restores_the_original_within_a_budget_and_with_prerequisites():
    store = semantic_store()
    ids, originals = fill(store, 4, actions=(0,))
    compact_all(store)
    action = ids[4]
    assert store.get(action).state is EpisodeState.COMPACTED
    tight = store.rehydrate([action], token_budget=10)
    assert tight.restored_episode_ids == [] and "needs" in tight.skipped[action]
    result = store.rehydrate([action], token_budget=10_000)
    assert set(result.restored_episode_ids) == {ids[0], action}
    for episode_id in (ids[0], action):
        record = store.get(episode_id)
        assert record.state is EpisodeState.CLOSED and record.content == originals[episode_id]
        assert record.summary_id is not None
    assert store.get(ids[0]).depended_on_by == [action]
    store.mark_accessed([ids[0]])
    again = store.rehydrate([action], token_budget=10)
    assert again.skipped[action] == "already live"


def test_a_restored_episode_is_dropped_again_only_while_its_archive_entry_still_verifies():
    archive = InMemoryEpisodeArchive()
    store = semantic_store(archive=archive)
    ids, originals = fill(store, 4)
    compact_all(store)
    intact, damaged = ids[0], ids[1]
    result = store.rehydrate([intact, damaged], token_budget=100_000)
    assert set(result.restored_episode_ids) == {intact, damaged}
    archive._entries[store.get(damaged).archive_id] = "tampered"
    store.compact(1, relevance_query="again")
    assert store.get(intact).state is EpisodeState.COMPACTED
    assert store.get(intact).content is None
    assert store.get(damaged).state is EpisodeState.COMPACTED
    assert store.get(damaged).content == originals[damaged]


def test_rehydrating_a_corrupt_archive_entry_is_refused_not_trusted():
    archive = InMemoryEpisodeArchive()
    store = semantic_store(archive=archive)
    ids, _ = fill(store, 3)
    compact_all(store)
    victim = store.get(ids[0]).archive_id
    archive._entries[victim] = archive._entries[victim].replace("lorem", "forged")
    result = store.rehydrate([ids[0]], token_budget=100_000)
    assert result.restored_episode_ids == []
    assert "EPISODE_ARCHIVE_CORRUPT" in result.skipped[ids[0]]
    with pytest.raises(AgentSdkError, match="does not match its content hash"):
        archive.get(victim)


def test_the_checkpoint_covers_summaries_and_evictions_only_when_there_are_some():
    plain = InMemoryEpisodeStore()
    fill(plain, 3)
    legacy = plain.checkpoint()
    assert all("summary_id" not in item and "archive_id" not in item for item in legacy.episodes)
    policy = SemanticCompactionPolicy(consolidate_when_summaries=0, max_active_records=2)
    store = semantic_store(policy=policy)
    fill(store, 6)
    compact_all(store)
    checkpoint = store.checkpoint()
    assert any(item["state"] == "evicted" for item in checkpoint.episodes)
    assert store.verify_checkpoint(checkpoint)
    store.rehydrate([store.evicted_episode_ids()[0]], token_budget=100_000)
    assert not store.verify_checkpoint(checkpoint)


def test_decisions_are_reproducible_from_policy_and_input_records():
    def run():
        store = semantic_store()
        fill(store, 7, failed=(2,))
        report = compact_all(store).dossier["semantic_compaction"]
        return report["policy_hash"], [
            (o["group_id"], o["input_hash"], o["status"]) for o in report["outcomes"]
        ]

    assert run() == run()


def test_a_summary_is_tamper_evident():
    store = semantic_store()
    ids, _ = fill(store, 3)
    compact_all(store)
    summary = store.active_summaries()[0]
    assert summary.intact()
    forged = summary.model_copy(update={"summary_text": "nothing happened"})
    assert not forged.intact()
    assert json.loads(summary.view().model_dump_json())["summary_id"] == summary.summary_id


def test_release_after_archive_is_an_explicit_opt_in_for_a_summarizer_that_keeps_failing():
    def handler(request):
        raise RuntimeError("down")

    policy = SemanticCompactionPolicy(
        consolidate_when_summaries=0, failure_policy="release-after-archive", max_group_attempts=2
    )
    store = semantic_store(Scripted(handler), policy=policy)
    ids, originals = fill(store, 3)
    first = compact_all(store)
    assert all(store.get(i).content == originals[i] for i in first.compacted_episode_ids)
    second = store.compact(1, relevance_query="x")
    released = second.dossier["semantic_compaction"]["released_without_summary"]
    assert sorted(released) == sorted(first.compacted_episode_ids)
    for episode_id in released:
        record = store.get(episode_id)
        assert record.content is None and record.summary_id is None
        assert store.archive.get(record.archive_id).content == originals[episode_id]


def test_the_file_store_archives_durably_and_persists_one_consistent_snapshot(tmp_path):
    store = FileEpisodeStore(
        tmp_path,
        semantic=SemanticCompactor(
            ExtractiveEpisodeSummarizer(),
            policy=SemanticCompactionPolicy(consolidate_when_summaries=0, max_active_records=3),
        ),
    )
    ids, originals = fill(store, 6)
    compact_all(store)
    generation = store.persist()
    memory = tmp_path / ".agent-memory"
    assert generation == 1 and store.snapshot_generations() == [1]
    assert isinstance(store.archive, FileEpisodeArchive)
    entries = sorted((memory / "archive").glob("archive-*.json"))
    assert len(entries) >= len(store.summaries())
    snapshot = json.loads(next(store.snapshot_directory.glob("snapshot-*.json")).read_text("utf-8"))
    assert {item["summary_id"] for item in snapshot["summaries"]} == {
        s.summary_id for s in store.summaries()
    }
    assert [item["episode_id"] for item in snapshot["evicted"]] == store.evicted_episode_ids()
    persisted = json.loads((memory / "episodes.json").read_text("utf-8"))
    assert {record["id"] for record in persisted} == {record.id for record in store.list()}
    for record in persisted:
        if record["state"] == "compacted":
            assert record["content"] is None and record["archive_id"] and record["summary_id"]
    fresh = FileEpisodeArchive(tmp_path)
    some = store.summaries()[0]
    for archive_id, episode_id in zip(some.archive_ids, some.source_episode_ids, strict=True):
        assert fresh.get(archive_id).content == originals[episode_id]
        text = (memory / "archive" / f"{archive_id}.json").read_text("utf-8")
        assert decode_snapshot(archive_id, text).id == episode_id


def test_the_archive_names_entries_by_content_and_refuses_substitutes(tmp_path):
    archive = FileEpisodeArchive(tmp_path)
    store = InMemoryEpisodeStore()
    ids, originals = fill(store, 1)
    record = store.get(ids[0])
    receipt = archive.put(record)
    assert receipt.archive_id == f"archive-{receipt.content_hash}"
    assert archive.put(record).archive_id == receipt.archive_id
    path = archive.directory / f"{receipt.archive_id}.json"
    path.write_text(path.read_text("utf-8").replace("lorem", "forged"), encoding="utf-8")
    with pytest.raises(AgentSdkError) as raised:
        archive.get(receipt.archive_id)
    assert raised.value.code == "EPISODE_ARCHIVE_CORRUPT"
    with pytest.raises(AgentSdkError, match="not valid"):
        archive.get("archive-../x")
    with pytest.raises(AgentSdkError) as missing:
        archive.get(f"archive-{'0' * 64}")
    assert missing.value.code == "EPISODE_ARCHIVE_MISSING"
