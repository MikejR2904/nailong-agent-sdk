import random

import pytest
from dfl_bench.instances import generate_instance, replay
from dfl_bench.oracle import (
    SYNTHETIC,
    SyntheticOracleExecutor,
    optimum_quality,
    quality_from_indices,
    satisfied_fraction,
)
from dfl_bench.selection import (
    LEARNED,
    DictPredictor,
    assert_feasible,
    cost_of,
    generate_candidates,
    is_usable,
    mandatory_closure,
    select_via_store,
    static_utilities,
)

from nailong_agent_sdk.memory.episode_scoring import _episode_tokens


def usable_instances(count=8, regime="aligned", seed=5):
    found = []
    index = 0
    while len(found) < count:
        instance = generate_instance(f"C{seed}", regime, index, seed)
        index += 1
        if is_usable(instance):
            found.append(instance)
    return found


@pytest.mark.parametrize("regime", ["aligned", "shifted"])
def test_every_candidate_from_every_strategy_is_feasible(regime):
    for instance in usable_instances(6, regime):
        candidates = generate_candidates(instance, random.Random(1))
        built = replay(instance)
        assert len(candidates) >= 2
        for candidate in candidates:
            assert_feasible(instance, built, candidate.ids, candidate.source)
            assert mandatory_closure(built) <= candidate.ids


def test_all_candidate_generation_strategies_appear():
    instance = usable_instances(1)[0]
    predictor = DictPredictor({i: 1.0 for i in replay(instance).ids}, "current")
    sources = set()
    for candidate in generate_candidates(
        instance, random.Random(2), learned_predictors={"current": predictor}
    ):
        sources.add(candidate.source)
        sources.update(candidate.meta.get("also", []))
    assert {"static-pckp", "greedy-pask", "random-feasible", "perturbed-pckp", "edit"} <= sources
    assert "learned-policy:current" in sources


def test_candidates_are_deduplicated_by_selection():
    instance = usable_instances(1)[0]
    candidates = generate_candidates(instance, random.Random(3))
    assert len({c.ids for c in candidates}) == len(candidates)


def test_infeasible_candidates_are_rejected_with_specific_reasons():
    instance = usable_instances(1)[0]
    built = replay(instance)
    everything = frozenset(built.ids)
    assert cost_of(built, everything) > instance.budget
    with pytest.raises(ValueError, match="over the budget"):
        assert_feasible(instance, built, everything, "probe")
    with pytest.raises(ValueError, match="without prerequisites"):
        action = next(i for i in built.ids if built.store.get(i).depends_on)
        assert_feasible(instance, built, mandatory_closure(built) | {action}, "probe")
    if built.protected:
        with pytest.raises(ValueError, match="drops mandatory"):
            assert_feasible(instance, built, frozenset(), "probe")


def test_learned_strategy_candidates_report_optimal_solver_certificates():
    instance = usable_instances(1)[0]
    values = {i: 1.0 + n for n, i in enumerate(replay(instance).ids)}
    selection, _, dossier = select_via_store(instance, LEARNED, DictPredictor(values))
    assert selection.solver_status == "optimal" and selection.learned_status == "applied"
    assert dossier["learned_ranking"]["selected_episode_ids"] == sorted(selection.ids)


def test_static_selection_never_beats_the_exhaustive_optimum():
    for instance in usable_instances(5):
        built = replay(instance)
        selection, _, _ = select_via_store(
            instance, LEARNED, DictPredictor(static_utilities(instance))
        )
        positions = {i: n for n, i in enumerate(built.ids)}
        quality = quality_from_indices(instance, {positions[i] for i in selection.ids})
        best, chosen = optimum_quality(instance)
        assert quality <= best + 1e-12
        assert quality_from_indices(instance, chosen) == best
        assert (
            sum(_episode_tokens(built.store.get(built.ids[i])) for i in chosen) <= instance.budget
        )


def test_oracle_is_redundancy_aware_and_complementary():
    instance = usable_instances(1)[0]
    first = instance.requirements[0]
    assert satisfied_fraction(instance, set(first)) == satisfied_fraction(instance, {first[0]})
    full = {i for alt in instance.requirements for i in alt}
    assert quality_from_indices(instance, full) == pytest.approx(0.95)
    one = satisfied_fraction(instance, {first[0]})
    assert quality_from_indices(instance, {first[0]}) == pytest.approx(0.03 + 0.92 * one**2)
    assert quality_from_indices(instance, set()) == pytest.approx(0.03)


def test_oracle_executor_is_reproducible_and_marked_synthetic():
    instance = usable_instances(1)[0]
    built = replay(instance)
    executor = SyntheticOracleExecutor()
    ids = frozenset(built.ids[: len(built.ids) // 2])
    assert executor.evidence_kind == SYNTHETIC
    assert [executor.run(instance, ids, t) for t in range(10)] == [
        SyntheticOracleExecutor().run(instance, ids, t) for t in range(10)
    ]


def test_generation_is_deterministic_and_replays_the_same_graph():
    a = generate_instance("D", "shifted", 2, 9)
    b = generate_instance("D", "shifted", 2, 9)
    assert a == b and a.to_json() == b.to_json()
    assert replay(a).ids == replay(b).ids
    assert type(a).from_json(a.to_json()) == a
    with pytest.raises(ValueError, match="Unknown regime"):
        generate_instance("D", "bogus", 0, 0)
