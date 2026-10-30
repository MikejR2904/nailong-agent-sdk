import pytest
from dfl_bench.instances import REGIMES, Instance, generate_instance, replay
from dfl_bench.oracle import (
    SYNTHETIC,
    SyntheticOracleExecutor,
    optimum_quality,
    quality_from_indices,
    satisfied_fraction,
)
from dfl_bench.selection import LEARNED, select_via_store, static_utilities

from nailong_agent_sdk.memory.episode_scoring import _episode_tokens
from nailong_agent_sdk.memory.learned_retention import FixedUtilityPredictor


def test_generation_is_deterministic_and_round_trips_through_json():
    a = generate_instance("D", "shifted", 2, 9)
    assert a == generate_instance("D", "shifted", 2, 9)
    assert replay(a).ids == replay(generate_instance("D", "shifted", 2, 9)).ids
    assert Instance.from_json(a.to_json()) == a
    legacy = {k: v for k, v in a.to_json().items() if k != "oracle_form"}
    assert Instance.from_json(legacy).oracle_form == "redundant"
    with pytest.raises(ValueError, match="Unknown regime"):
        generate_instance("D", "bogus", 0, 0)


def test_all_four_regimes_generate_replayable_instances():
    assert set(REGIMES) == {"aligned", "shifted", "null", "additive"}
    for regime in REGIMES:
        instance = generate_instance("R", regime, 0, 1)
        assert instance.regime == regime and len(replay(instance).ids) == instance.size


def test_size_overrides_build_larger_graphs():
    large = generate_instance("S", "aligned", 0, 4, n_explore=30, n_action=20, max_dependencies=3)
    assert large.size == 50


def test_additive_regime_has_single_alternatives_and_a_linear_oracle():
    for index in range(12):
        instance = generate_instance("A", "additive", index, 3)
        assert instance.oracle_form == "additive"
        assert all(len(alternatives) == 1 for alternatives in instance.requirements)
        members = [alt[0] for alt in instance.requirements]
        half = set(members[: len(members) // 2])
        expected = 0.03 + 0.92 * len(half) / len(members)
        assert quality_from_indices(instance, half) == pytest.approx(expected)
    assert generate_instance("A", "aligned", 0, 3).oracle_form == "redundant"


def test_oracle_is_redundancy_aware_and_complementary(usable_instances):
    instance = usable_instances()[0]
    first = instance.requirements[0]
    assert satisfied_fraction(instance, set(first)) == satisfied_fraction(instance, {first[0]})
    full = {i for alt in instance.requirements for i in alt}
    assert quality_from_indices(instance, full) == pytest.approx(0.95)
    one = satisfied_fraction(instance, {first[0]})
    assert quality_from_indices(instance, {first[0]}) == pytest.approx(0.03 + 0.92 * one**2)
    assert quality_from_indices(instance, set()) == pytest.approx(0.03)


def test_oracle_executor_is_reproducible_and_marked_synthetic(usable_instances):
    instance = usable_instances()[0]
    ids = frozenset(replay(instance).ids[:4])
    assert SyntheticOracleExecutor().evidence_kind == SYNTHETIC
    first = [SyntheticOracleExecutor().run(instance, ids, t) for t in range(10)]
    assert first == [SyntheticOracleExecutor().run(instance, ids, t) for t in range(10)]


def test_no_selection_beats_the_exhaustive_optimum(usable_instances):
    for instance in usable_instances(5):
        built = replay(instance)
        selection, _, _ = select_via_store(
            instance,
            LEARNED,
            FixedUtilityPredictor(static_utilities(instance), predictor_id="static", version="1"),
        )
        positions = {i: n for n, i in enumerate(built.ids)}
        quality = quality_from_indices(instance, {positions[i] for i in selection.ids})
        best, chosen = optimum_quality(instance)
        assert quality <= best + 1e-12
        assert quality_from_indices(instance, chosen) == best
        cost = sum(_episode_tokens(built.store.get(built.ids[i])) for i in chosen)
        assert cost <= instance.budget
