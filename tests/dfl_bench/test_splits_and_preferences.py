import pytest
from dfl_bench.instances import Instance, generate_instance
from dfl_bench.oracle import MEASURED, SYNTHETIC
from dfl_bench.preferences import (
    CandidateOutcome,
    Preference,
    PreferencePolicy,
    assert_single_evidence_kind,
    build_preferences,
    resolve_conflicts,
)
from dfl_bench.splits import (
    assert_no_leakage,
    group_instances,
    leakage_report,
    split_instances,
)


def world(templates=8, per=3, seed=1):
    return [
        generate_instance(f"W{seed}x{t}", "aligned", i, seed)
        for t in range(templates)
        for i in range(per)
    ]


def test_instances_of_one_template_are_never_split_apart():
    instances = world()
    train, val, test = split_instances(instances, (0.5, 0.2, 0.3), seed=3)
    assert_no_leakage(train, val, test)
    assert len(train) + len(val) + len(test) == len(instances)
    for template in {i.template_id for i in instances}:
        homes = {
            n
            for n, part in enumerate((train, val, test))
            if any(i.template_id == template for i in part)
        }
        assert len(homes) == 1


def test_a_shared_task_digest_glues_different_templates_into_one_group():
    first = generate_instance("A", "aligned", 0, 1)
    second = generate_instance("B", "aligned", 0, 1)
    twin = Instance(**{**second.__dict__, "query": first.query})
    groups = group_instances([first, twin])
    assert len(groups) == 1


def test_leakage_detector_names_the_shared_key():
    instances = world(4, 2)
    leaked = [instances[0]]
    report = leakage_report(instances[:4], leaked + instances[4:])
    assert "instance" in report and "template" in report
    with pytest.raises(ValueError, match="Training/test leakage"):
        assert_no_leakage(instances[:4], leaked)


def test_split_fractions_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1"):
        split_instances(world(2, 1), (0.5, 0.5, 0.5), seed=0)


def test_split_is_deterministic_for_a_seed():
    instances = world()
    a = split_instances(instances, (0.6, 0.2, 0.2), seed=5)
    b = split_instances(instances, (0.6, 0.2, 0.2), seed=5)
    assert [[i.instance_id for i in p] for p in a] == [[i.instance_id for i in p] for p in b]


def outcome(selection, values, kind=SYNTHETIC, instance="i"):
    return CandidateOutcome(instance, frozenset(selection), ("t",), tuple(values), kind)


def test_equal_and_indistinguishable_outcomes_create_no_preference():
    same = [outcome("a", [1, 0, 1, 0, 1, 0, 1, 0]), outcome("b", [1, 0, 1, 0, 1, 0, 1, 0])]
    assert build_preferences(same) == []
    near = [outcome("a", [1] * 5 + [0] * 3), outcome("b", [1] * 4 + [0] * 4)]
    assert build_preferences(near) == []


def test_clear_difference_creates_a_correctly_oriented_confident_pair():
    pairs = build_preferences([outcome("a", [0] * 10), outcome("b", [1] * 10)])
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.positive == frozenset("b") and pair.negative == frozenset("a")
    assert 0.95 < pair.weight <= 1.0 and pair.p_value < 0.05


def test_more_trials_give_more_confidence_to_the_same_effect():
    few = build_preferences(
        [outcome("a", [0] * 4), outcome("b", [1] * 4)], PreferencePolicy(alpha=0.2)
    )
    many = build_preferences([outcome("a", [0] * 30), outcome("b", [1] * 30)])
    assert many[0].weight > few[0].weight


def test_too_few_trials_are_omitted_not_guessed():
    assert build_preferences([outcome("a", [0, 0]), outcome("b", [1, 1])]) == []


def test_identical_selections_are_never_compared():
    assert build_preferences([outcome("a", [0] * 10), outcome("a", [1] * 10)]) == []


def test_mixing_synthetic_and_measured_evidence_is_refused():
    mixed = [outcome("a", [0] * 10), outcome("b", [1] * 10, kind=MEASURED)]
    with pytest.raises(ValueError, match="Refusing to mix evidence kinds"):
        build_preferences(mixed)
    with pytest.raises(ValueError, match="Unknown evidence kinds"):
        assert_single_evidence_kind([outcome("a", [1], kind="guess")])


def pref(pos, neg, weight, instance="i"):
    return Preference(instance, frozenset(pos), frozenset(neg), weight, 0.01, 0.5, SYNTHETIC)


def test_duplicate_preferences_merge_their_weight():
    merged = resolve_conflicts([pref("a", "b", 0.4), pref("a", "b", 0.5)])
    assert len(merged) == 1 and merged[0].weight == pytest.approx(0.9)


def test_exactly_conflicting_preferences_cancel():
    assert resolve_conflicts([pref("a", "b", 0.6), pref("b", "a", 0.6)]) == []


def test_conflicting_preferences_keep_the_net_winner():
    merged = resolve_conflicts([pref("a", "b", 0.3), pref("b", "a", 0.8)])
    assert len(merged) == 1
    assert merged[0].positive == frozenset("b") and merged[0].weight == pytest.approx(0.5)
