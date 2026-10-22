import random

import pytest
from dfl_bench.instances import replay
from dfl_bench.selection import (
    LEARNED,
    assert_feasible,
    cost_of,
    generate_candidates,
    mandatory_closure,
    select_via_store,
)

from nailong_agent_sdk.memory.learned_retention import FixedUtilityPredictor


@pytest.mark.parametrize("regime", ["aligned", "shifted"])
def test_every_candidate_from_every_strategy_is_feasible(usable_instances, regime):
    for instance in usable_instances(6, regime):
        candidates = generate_candidates(instance, random.Random(1))
        built = replay(instance)
        assert len(candidates) >= 2
        assert len({c.ids for c in candidates}) == len(candidates)
        for candidate in candidates:
            assert_feasible(instance, built, candidate.ids, candidate.source)
            assert mandatory_closure(built) <= candidate.ids


def test_all_candidate_generation_strategies_appear(usable_instances):
    instance = usable_instances()[0]
    predictor = FixedUtilityPredictor(
        {i: 1.0 for i in replay(instance).ids}, predictor_id="current", version="1"
    )
    sources = set()
    for candidate in generate_candidates(
        instance, random.Random(2), learned_predictors={"current": predictor}
    ):
        sources.add(candidate.source)
        sources.update(candidate.meta.get("also", []))
    assert {"static-pckp", "greedy-pask", "random-feasible", "perturbed-pckp", "edit"} <= sources
    assert "learned-policy:current" in sources


def test_infeasible_candidates_are_rejected_with_specific_reasons(usable_instances):
    instance = usable_instances()[0]
    built = replay(instance)
    everything = frozenset(built.ids)
    assert cost_of(built, everything) > instance.budget
    with pytest.raises(ValueError, match="over the budget"):
        assert_feasible(instance, built, everything, "probe")
    action = next(i for i in built.ids if built.store.get(i).depends_on)
    with pytest.raises(ValueError, match="without prerequisites"):
        assert_feasible(instance, built, mandatory_closure(built) | {action}, "probe")
    if built.protected:
        with pytest.raises(ValueError, match="drops mandatory"):
            assert_feasible(instance, built, frozenset(), "probe")


def test_learned_strategy_candidates_report_optimal_solver_certificates(usable_instances):
    instance = usable_instances()[0]
    values = {i: 1.0 + n for n, i in enumerate(replay(instance).ids)}
    selection, _, dossier = select_via_store(
        instance, LEARNED, FixedUtilityPredictor(values, predictor_id="ramp", version="1")
    )
    assert selection.solver_status == "optimal" and selection.learned_status == "applied"
    assert dossier["learned_ranking"]["selected_episode_ids"] == sorted(selection.ids)
