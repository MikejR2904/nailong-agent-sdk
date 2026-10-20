import math

import pytest
from trust_bench import stats


def test_wilson_matches_a_hand_computed_interval():
    low, high = stats.wilson(45, 50)
    assert low == pytest.approx(0.786, abs=0.001) and high == pytest.approx(0.957, abs=0.001)


def test_wilson_never_leaves_the_unit_interval_and_handles_the_extremes():
    assert stats.wilson(0, 30)[0] == 0.0 and stats.wilson(30, 30)[1] == 1.0
    assert stats.wilson(0, 0) == (0.0, 1.0)


def test_counts_are_validated_with_the_numbers_named():
    with pytest.raises(ValueError, match=r"need 0 <= successes <= trials, got 5 of 3\."):
        stats.wilson(5, 3)


def test_the_rule_of_three_cases_for_zero_events():
    assert [stats.zero_event_cases(rate) for rate in (0.10, 0.05, 0.02, 0.01)] == [29, 59, 149, 299]
    assert stats.exact_upper(0, 59) == pytest.approx(1 - 0.05 ** (1 / 59), abs=1e-6)
    assert stats.exact_upper(0, 149) < 0.02 < stats.exact_upper(0, 148)


def test_exact_bounds_bracket_the_estimate():
    assert stats.exact_lower(7, 20) < 7 / 20 < stats.exact_upper(7, 20)
    assert stats.exact_upper(20, 20) == 1.0 and stats.exact_lower(0, 20) == 0.0


def test_binomial_cdf_agrees_with_a_direct_sum():
    assert stats.binomial_cdf(0, 10, 0.5) == pytest.approx(1 / 1024)
    direct = sum(math.comb(12, i) * 0.3**i * 0.7 ** (12 - i) for i in range(5))
    assert stats.binomial_cdf(4, 12, 0.3) == pytest.approx(direct)


def test_mcnemar_exact_two_sided_value():
    assert stats.mcnemar_exact(5, 15) == pytest.approx(0.0414, abs=0.0001)
    assert stats.mcnemar_exact(0, 0) == 1.0
    assert stats.mcnemar_exact(10, 10) == 1.0


def test_holm_adjusts_in_order_and_keeps_monotone_values():
    adjusted = stats.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adjusted == pytest.approx({"a": 0.03, "c": 0.06, "b": 0.06})


def test_kendall_tau_b_perfect_reversed_and_with_ties():
    assert stats.kendall_tau_b([1, 2, 3], [1, 2, 3]) == 1.0
    assert stats.kendall_tau_b([1, 2, 3], [3, 2, 1]) == -1.0
    assert stats.kendall_tau_b([1, 2, 2, 3], [1, 2, 3, 3]) == pytest.approx(0.8)
    with pytest.raises(ValueError, match="equal lengths, got 2 and 3"):
        stats.kendall_tau_b([1, 2], [1, 2, 3])


def test_cluster_bootstrap_is_deterministic_and_brackets_the_mean():
    cases = {f"case-{i}": float(i % 2) for i in range(40)}
    first = stats.cluster_bootstrap(cases, iterations=500)
    assert first == stats.cluster_bootstrap(cases, iterations=500)
    estimate, low, high = first
    assert estimate == 0.5 and low < estimate < high
    flat = stats.cluster_bootstrap({"a": 1.0, "b": 1.0}, iterations=100)
    assert flat == (1.0, 1.0, 1.0)


def test_paired_difference_requires_the_same_cases():
    with pytest.raises(ValueError, match="same cases; differing: \\['b', 'c'\\]"):
        stats.paired_difference({"a": 1.0, "b": 0.0}, {"a": 1.0, "c": 0.0})
    estimate, low, high = stats.paired_difference({"a": 1.0, "b": 1.0}, {"a": 0.0, "b": 1.0})
    assert estimate == 0.5 and low <= estimate <= high


def test_proportion_text_reports_the_counts():
    text = stats.proportion(9, 10).text()
    assert text.startswith("0.900 [") and text.endswith("(9/10)")
    assert stats.proportion(0, 0).text() == "n/a (0 trials)"


def test_newcombe_interval_matches_the_published_worked_example():
    difference, low, high = stats.newcombe_difference((56, 70), (48, 80))
    assert difference == pytest.approx(0.2)
    assert low == pytest.approx(0.0524, abs=0.002) and high == pytest.approx(0.3339, abs=0.002)
