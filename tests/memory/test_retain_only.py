import pytest

from nailong_agent_sdk.memory.episode_models import (
    CompactionStatus,
    CompactionStrategy,
    EpisodeState,
    PaskCompactionPolicy,
)
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore


def fresh():
    store = InMemoryEpisodeStore()
    explore = []
    for index in range(4):
        record = store.open_exploratory("a", content={"result": {"text": "e" * (200 + index)}})
        store.close(record.id, description=f"explore {index}")
        explore.append(record.id)
    actions = []
    for dependencies in ([explore[0], explore[1]], [explore[2]], []):
        record = store.open_action("a", dependencies, content={"result": {"text": "a" * 150}})
        store.close(record.id)
        actions.append(record.id)
    return store, explore, actions


def states(store):
    return {r.id: r.state for r in store.list()}


def test_retain_only_keeps_exactly_the_named_episodes_and_tombstones_the_rest():
    store, explore, actions = fresh()
    keep = {explore[0], explore[1], actions[0]} | set(store.get(actions[0]).depends_on)
    before = store.estimate_tokens()
    result = store.retain_only(keep)
    assert result.strategy is CompactionStrategy.EXPLICIT
    assert result.status is CompactionStatus.COMPACTED
    assert result.before_tokens == before and result.after_tokens < before
    assert {i for i, s in states(store).items() if s is not EpisodeState.COMPACTED} == keep
    assert set(result.compacted_episode_ids) == set(states(store)) - keep
    for episode_id in keep:
        assert store.get(episode_id).content is not None
    for episode_id in set(result.compacted_episode_ids):
        assert store.get(episode_id).state is EpisodeState.COMPACTED


def test_retain_only_with_everything_changes_nothing():
    store, explore, actions = fresh()
    result = store.retain_only(explore + actions)
    assert result.status is CompactionStatus.WITHIN_BUDGET
    assert result.compacted_episode_ids == [] and result.after_tokens == result.before_tokens


def test_retain_only_reproduces_a_compaction_decision_exactly():
    reference, _, _ = fresh()
    budget = reference.estimate_tokens() // 2
    decided = reference.compact(budget, relevance_query="explore")
    kept = set(decided.dossier["retained_episode_ids"])
    replay, _, _ = fresh()
    applied = replay.retain_only(kept)
    assert applied.compacted_episode_ids == decided.compacted_episode_ids
    assert applied.after_tokens == decided.after_tokens
    assert states(replay) == states(reference)


def test_retain_only_rejects_unknown_compacted_and_prerequisite_violations():
    store, explore, actions = fresh()
    with pytest.raises(ValueError, match=r"unknown episodes \['episode-99'\]"):
        store.retain_only(["episode-99"])
    dependent = next(a for a in actions if store.get(a).depends_on)
    prerequisite = store.get(dependent).depends_on[0]
    with pytest.raises(ValueError, match="without its prerequisites"):
        store.retain_only([dependent])
    store.retain_only([explore[0]])
    with pytest.raises(ValueError, match="already compacted"):
        store.retain_only([prerequisite, explore[1]])


def test_retain_only_never_drops_protected_active_or_open_episodes():
    store, explore, actions = fresh()
    with pytest.raises(ValueError, match="would drop mandatory episodes"):
        store.retain_only([explore[0]], protected_episode_ids=[explore[1]])
    with pytest.raises(ValueError, match="would drop mandatory episodes"):
        store.retain_only([explore[0]], active_episode_id=explore[2])
    opened = store.open_exploratory("a", content={"result": {"text": "open"}})
    with pytest.raises(ValueError, match=rf"mandatory episodes \['{opened.id}'\]"):
        store.retain_only([explore[0]])
    store.retain_only([explore[0], opened.id], protected_episode_ids=[explore[0]])
    assert store.get(opened.id).state is EpisodeState.OPEN


def test_retain_only_closure_of_a_protected_action_is_mandatory():
    store, _, actions = fresh()
    dependent = next(a for a in actions if store.get(a).depends_on)
    with pytest.raises(ValueError, match="would drop mandatory episodes"):
        store.retain_only([dependent], protected_episode_ids=[dependent])
    keep = {dependent, *store.get(dependent).depends_on}
    store.retain_only(keep, protected_episode_ids=[dependent])
    assert {i for i, s in states(store).items() if s is not EpisodeState.COMPACTED} == keep


def test_the_existing_policy_enum_still_defaults_to_static_exact_pckp():
    assert PaskCompactionPolicy().strategy is CompactionStrategy.EXACT_PCKP
