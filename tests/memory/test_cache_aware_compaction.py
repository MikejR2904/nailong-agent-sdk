from itertools import combinations

import pytest

from nailong_agent_sdk.memory.episode_models import (
    CompactionStrategy,
    EpisodeState,
    PaskCompactionPolicy,
)
from nailong_agent_sdk.memory.episode_scoring import _episode_tokens
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.learned_retention import FixedUtilityPredictor
from tests.support.semantic_memory import fill


def store_with(count=10, actions=(), predictor=None):
    store = InMemoryEpisodeStore(utility_predictor=predictor)
    ids, _ = fill(store, count, actions=actions)
    return store, ids


def favour_recent(store, ids):
    for position, episode_id in enumerate(ids):
        store.mark_accessed([episode_id] * (1 + position))


def live(store):
    return [r.id for r in store.list() if r.state is not EpisodeState.COMPACTED]


def compact(store, fraction=0.5, tolerance=0.0, query="lorem", protected=()):
    budget = int(store.estimate_tokens() * fraction)
    return store.compact(
        budget,
        relevance_query=query,
        protected_episode_ids=list(protected),
        policy=PaskCompactionPolicy(cache_tolerance=tolerance),
    )


def test_a_zero_tolerance_changes_nothing_and_reports_nothing():
    plain, ids = store_with()
    favour_recent(plain, ids)
    budget = int(plain.estimate_tokens() * 0.5)
    default = plain.compact(budget, relevance_query="lorem", policy=PaskCompactionPolicy())
    other, other_ids = store_with()
    favour_recent(other, other_ids)
    explicit = other.compact(
        budget, relevance_query="lorem", policy=PaskCompactionPolicy(cache_tolerance=0.0)
    )
    assert "cache_aware" not in default.dossier and "cache_aware" not in explicit.dossier
    assert live(plain) == live(other) and default.compacted_episode_ids


def test_tolerance_keeps_a_longer_prefix_and_invalidates_fewer_tokens_within_the_utility_bound():
    reports = {}
    for tolerance in (0.0, 0.05, 0.2, 0.5):
        store, ids = store_with()
        favour_recent(store, ids)
        result = compact(store, tolerance=tolerance)
        reports[tolerance] = (result.dossier.get("cache_aware"), live(store), result)
        assert (
            store.estimate_tokens()
            <= int(sum(_episode_tokens(store.get(i)) for i in ids) * 0.5) + 1
            or result.dossier["solver"]["status"] == "optimal"
        )
    assert reports[0.0][0] is None
    previous_invalidated = None
    for tolerance in (0.05, 0.2, 0.5):
        report = reports[tolerance][0]
        assert report is not None
        assert report["utility_loss_ratio"] <= tolerance + 1e-9
        assert report["invalidated_tokens_selected"] <= report["invalidated_tokens_unconstrained"]
        if previous_invalidated is not None:
            assert report["invalidated_tokens_selected"] <= previous_invalidated
        previous_invalidated = report["invalidated_tokens_selected"]
    widest = reports[0.5][0]
    assert widest["forced_prefix_episodes"] > 0
    assert widest["invalidated_tokens_selected"] < widest["invalidated_tokens_unconstrained"]


def test_the_selection_is_optimal_among_those_that_keep_the_forced_prefix():
    store, ids = store_with(count=7)
    favour_recent(store, ids)
    costs = {i: _episode_tokens(store.get(i)) for i in ids}
    result = compact(store, tolerance=0.3)
    report = result.dossier["cache_aware"]
    utilities = {
        row["episode_id"]: round(row["utility"] * 1_000_000)
        for row in result.dossier["static_utilities"]
    }
    budget = result.dossier["token_budget"]
    order = sorted(ids, key=lambda i: int(i.rsplit("-", 1)[1]))
    forced = set(order[: report["forced_prefix_episodes"]])
    best = 0
    for size in range(len(ids) + 1):
        for subset in combinations(ids, size):
            chosen = set(subset)
            if forced <= chosen and sum(costs[i] for i in chosen) <= budget:
                best = max(best, sum(utilities[i] for i in chosen))
    retained = set(result.dossier["retained_episode_ids"])
    assert forced <= retained
    assert sum(utilities[i] for i in retained) == best == report["utility_selected"]


def test_protected_episodes_and_prerequisite_closure_survive_cache_aware_compaction():
    store, ids = store_with(count=6, actions=(0, 3))
    favour_recent(store, ids)
    protected = ids[-1]
    result = compact(store, fraction=0.55, tolerance=0.4, protected=[protected])
    kept = set(live(store))
    assert protected in kept
    for record in store.list():
        if record.id in kept:
            assert set(record.depends_on) <= kept
    assert result.dossier["cache_aware"]["live_episodes"] == len(ids)


def test_the_learned_strategy_reports_the_same_cache_aware_audit():
    learned = InMemoryEpisodeStore()
    ids, _ = fill(learned, 10)
    predictor = FixedUtilityPredictor(
        {episode_id: float(position + 1) for position, episode_id in enumerate(ids)},
        predictor_id="rising",
        version="1",
    )
    learned = InMemoryEpisodeStore(utility_predictor=predictor)
    fill(learned, 10)
    result = learned.compact(
        int(learned.estimate_tokens() * 0.5),
        relevance_query="lorem",
        policy=PaskCompactionPolicy(strategy=CompactionStrategy.LEARNED_PCKP, cache_tolerance=0.3),
    )
    assert result.dossier["learned_ranking"]["status"] == "applied"
    report = result.dossier["cache_aware"]
    assert report["utility_loss_ratio"] <= 0.3 + 1e-9
    assert report["invalidated_tokens_selected"] <= report["invalidated_tokens_unconstrained"]


def test_a_tolerance_outside_the_supported_range_is_rejected():
    with pytest.raises(ValueError, match="less than or equal to 0.5"):
        PaskCompactionPolicy(cache_tolerance=0.9)
