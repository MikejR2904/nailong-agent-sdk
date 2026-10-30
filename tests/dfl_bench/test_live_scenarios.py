import pytest
from dfl_bench.live.context import build_scene
from dfl_bench.live.scenarios import (
    FAMILIES,
    LiveTask,
    contains_value,
    generate_task,
    normalize,
    score_answer,
    validate_task,
)

from nailong_agent_sdk.memory.episode_scoring import _episode_tokens

TASKS = [
    generate_task(family, variant, index, seed)
    for family in FAMILIES
    for variant in range(3)
    for index in range(3)
    for seed in (1, 2)
]


def test_generation_is_deterministic_and_round_trips_through_json():
    a = generate_task("ledger", 1, 2, 7)
    assert a == generate_task("ledger", 1, 2, 7)
    assert LiveTask.from_json(a.to_json()) == a
    assert a != generate_task("ledger", 1, 3, 7)
    with pytest.raises(ValueError, match="Unknown task family 'bogus'"):
        generate_task("bogus", 0, 0, 0)


def test_every_generated_task_passes_the_visibility_validation():
    assert len(TASKS) == 54
    for task in TASKS:
        validate_task(task)


def test_answers_are_visible_when_retained_hidden_when_compacted_and_never_in_the_question():
    for task in TASKS[:18]:
        for value in task.expected.values():
            assert not contains_value(task.instance.query, value)
        assert set(task.expected) == set(task.answer_keys) == set(task.fact_sources)
        assert len(task.answer_keys) == 3


def test_each_fact_has_a_source_episode_and_a_feasible_cheapest_set_within_budget():
    for task in TASKS:
        scene = build_scene(task.instance)
        total = sum(_episode_tokens(scene.store.get(i)) for i in scene.ids)
        assert 0 < task.instance.budget <= total
        for sources in task.fact_sources.values():
            assert sources and all(0 <= i < len(scene.ids) for i in sources)


def test_histories_are_agent_shaped_with_provenance_edges_and_failures():
    task = generate_task("ledger", 0, 0, 3)
    scene = build_scene(task.instance)
    statuses = {scene.store.get(i).content["result"]["status"] for i in scene.ids}
    kinds = {scene.store.get(i).kind.value for i in scene.ids}
    assert statuses == {"succeeded", "failed"} and kinds == {"exploratory", "action"}
    actions = [i for i in scene.ids if scene.store.get(i).kind.value == "action"]
    assert all(scene.store.get(a).depends_on for a in actions)
    assert all(scene.store.get(i).source_handle for i in scene.ids)
    assert len(scene.observations) == len(scene.ids)


def test_the_verifier_gives_partial_credit_and_normalizes_values():
    expected = {"owner": "Ana Smith", "error_code": "E1234", "total": "42"}
    assert score_answer(dict(expected), expected) == 1.0
    assert (
        score_answer({"owner": " ana smith ", "error_code": "e1234", "total": 42}, expected) == 1.0
    )
    assert score_answer({"owner": "Ana Smith", "error_code": "E9999", "total": 42.0}, expected) == (
        pytest.approx(2 / 3)
    )
    assert score_answer({"owner": None, "error_code": None, "total": None}, expected) == 0.0
    assert score_answer(None, expected) == 0.0
    assert score_answer(["Ana Smith"], expected) == 0.0
    assert normalize('"E1"') == "e1" and normalize(None) == ""


def test_whole_token_matching_does_not_confuse_substrings():
    assert contains_value("replicas 12 ready", "12")
    assert not contains_value("svc4128 ready", "12")
    assert not contains_value("E12345", "E1234")


def test_validation_rejects_a_task_whose_answer_survives_compaction():
    task = generate_task("ledger", 0, 0, 5)
    key = next(iter(task.expected))
    leaking_step = dict(task.instance.recipe[0])
    leaking_step["arguments"] = {**leaking_step["arguments"], "note": task.expected[key]}
    leaking = LiveTask(
        instance=type(task.instance)(
            **{**task.instance.__dict__, "recipe": (leaking_step, *task.instance.recipe[1:])}
        ),
        family=task.family,
        answer_keys=(key,),
        expected={key: task.expected[key]},
        fact_sources={key: (0,)},
    )
    with pytest.raises(ValueError, match="survives compaction"):
        validate_task(leaking)
    unseen = LiveTask(
        instance=task.instance,
        family=task.family,
        answer_keys=(key,),
        expected={key: "value-that-is-nowhere"},
        fact_sources={key: (0,)},
    )
    with pytest.raises(ValueError, match="is not visible in any retained source"):
        validate_task(unseen)
