import pytest
from dfl_bench.oracle import MEASURED, SYNTHETIC
from dfl_bench.preferences import (
    CandidateOutcome,
    Preference,
    PreferencePolicy,
    assert_single_evidence_kind,
    build_preferences,
    resolve_conflicts,
)


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


def test_fractional_scores_from_a_verifier_are_accepted_as_outcomes():
    pairs = build_preferences(
        [outcome("a", [0.0, 0.0, 1 / 3] * 2), outcome("b", [1.0, 1.0, 2 / 3] * 2)]
    )
    assert len(pairs) == 1 and pairs[0].positive == frozenset("b")


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
