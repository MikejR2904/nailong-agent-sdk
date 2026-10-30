import math

import pytest
from dfl_bench.generalize.learners import SchemaCache
from dfl_bench.generalize.loo import (
    loo_dataset,
    measure_loo,
    merge_datasets,
    removal_sets,
    retention_scorecard,
)
from dfl_bench.generalize.worlds import build_world
from dfl_bench.instances import replay
from dfl_bench.oracle import SyntheticOracleExecutor, quality_from_indices, to_indices
from dfl_bench.selection import mandatory_closure

from nailong_agent_sdk.memory.utility_features import FEATURE_SCHEMA_VERSION
from nailong_agent_sdk.memory.utility_objectives import ObjectiveDataset


@pytest.fixture(scope="module")
def instances():
    return build_world(11, templates=1, per_template=2)[:4]


def test_removal_sets_stay_dependency_closed_and_never_remove_mandatory_episodes(instances):
    for instance in instances:
        built = replay(instance)
        mandatory = set(mandatory_closure(built))
        for episode_id, kept in removal_sets(instance).items():
            assert episode_id not in kept and not (mandatory - kept)
            for member in kept:
                assert set(built.store.get(member).depends_on) <= kept
            for dependent in built.store.get(episode_id).depended_on_by:
                assert dependent not in kept


def test_measured_criticality_is_zero_for_redundant_episodes_and_positive_for_needed_ones(
    instances,
):
    executor = SyntheticOracleExecutor()
    labels = measure_loo(executor, instances, trials=60)
    seen_needed = seen_redundant = 0
    for instance in instances:
        built = replay(instance)
        full = quality_from_indices(instance, to_indices(built, built.ids))
        for episode_id, kept in removal_sets(instance).items():
            loss = full - quality_from_indices(instance, to_indices(built, kept))
            drop = labels[instance.instance_id][episode_id]
            if loss == 0:
                assert drop == 0.0
                seen_redundant += 1
            elif loss > 0.3:
                assert drop > 0.1
                seen_needed += 1
    assert seen_needed > 0 and seen_redundant > 0


def test_loo_pairs_rank_critical_episodes_above_neutral_ones_and_merge_cleanly(instances):
    executor = SyntheticOracleExecutor()
    labels = measure_loo(executor, instances, trials=60)
    cache = SchemaCache(instances, FEATURE_SCHEMA_VERSION)
    ids = [i.instance_id for i in instances]
    data = loo_dataset(cache, labels, ids)
    assert data.terms and all(t.kind == "pair" for t in data.terms)
    for term in data.terms:
        assert len(term.candidates[0]) == len(term.candidates[1]) == 1 and term.weight > 0
    empty = ObjectiveDataset((), ())
    assert merge_datasets(empty, data).terms == data.terms
    merged = merge_datasets(data, data)
    assert len(merged.items) == 2 * len(data.items) and len(merged.terms) == 2 * len(data.terms)
    assert merged.terms[len(data.terms)].candidates[0][0] >= len(data.items)
    assert not loo_dataset(cache, {i: {} for i in ids}, ids).terms


def test_the_scorecard_separates_critical_recall_from_distraction_load(instances):
    instance = instances[0]
    built = replay(instance)
    labels = measure_loo(SyntheticOracleExecutor(), [instance], trials=60)[instance.instance_id]
    everything = frozenset(built.ids)
    full = retention_scorecard(instance, everything, labels)
    assert math.isnan(full["critical_recall"]) or full["critical_recall"] == pytest.approx(1.0)
    assert full["critical_missed"] == 0
    nothing = retention_scorecard(instance, frozenset(mandatory_closure(built)), labels)
    if sum(labels.values()) > 0:
        assert nothing["critical_recall"] < full["critical_recall"] + 1e-9
    assert 0.0 <= full["distraction_load"] <= 1.0
