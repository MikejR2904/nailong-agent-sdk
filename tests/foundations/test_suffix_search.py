import math
import random

import pytest

from nailong_agent_sdk.foundations.suffix_search import SuffixStrategy, find_suffix_start

GALLOP = SuffixStrategy.GALLOP
SCAN = SuffixStrategy.SCAN


def counting(predicate):
    seen = []

    def check(index):
        seen.append(index)
        return predicate(index)

    return check, seen


def gallop_bound(defective):
    return 2 * math.ceil(math.log2(defective + 1)) + 2


@pytest.mark.parametrize("strategy", [GALLOP, SCAN])
def test_every_introduction_point_of_every_short_history_is_found_exactly(strategy):
    for count in range(1, 70):
        for introduced in range(count + 1):
            result = find_suffix_start(
                count, lambda index, k=introduced: index >= k, strategy=strategy
            )
            if introduced == count:
                assert result.start is None and result.last_good == count - 1
            else:
                assert result.start == introduced
                assert result.last_good == (introduced - 1 if introduced else None)


def test_gallop_needs_logarithmically_many_probes_in_the_length_of_the_bad_suffix():
    worst = {}
    for count in range(1, 200):
        for introduced in range(count):
            check, seen = counting(lambda index, k=introduced: index >= k)
            result = find_suffix_start(count, check, strategy=GALLOP)
            defective = count - introduced
            assert result.evaluations == len(seen) == len(set(seen))
            assert result.evaluations <= gallop_bound(defective), (count, introduced)
            worst[defective] = max(worst.get(defective, 0), result.evaluations)
    assert worst[1] == 2 and worst[2] <= 4


def test_scan_probes_every_bad_element_once_and_then_one_good_element():
    check, seen = counting(lambda index: index >= 90)
    result = find_suffix_start(100, check, strategy=SCAN)
    assert result.start == 90 and result.evaluations == 11
    assert seen == list(range(99, 88, -1))


def test_a_history_of_a_million_writes_is_searched_in_a_few_dozen_probes():
    count = 1_000_000
    recent, seen_recent = counting(lambda index: index >= count - 1)
    assert find_suffix_start(count, recent).start == count - 1 and len(seen_recent) == 2
    middle, seen_middle = counting(lambda index: index >= count // 2)
    result = find_suffix_start(count, middle)
    assert result.start == count // 2
    assert result.evaluations <= gallop_bound(count - count // 2) <= 44
    first, seen_first = counting(lambda index: True)
    assert find_suffix_start(count, first).start == 0
    assert len(seen_first) <= 22


def test_a_clean_newest_version_costs_one_probe():
    check, seen = counting(lambda index: False)
    result = find_suffix_start(500, check)
    assert result.start is None and result.last_good == 499 and seen == [499]


@pytest.mark.parametrize("seed", range(40))
def test_an_uneven_history_still_yields_an_adjacent_clean_to_defective_pair(seed):
    generator = random.Random(seed)
    count = generator.randrange(2, 90)
    pattern = [generator.random() < 0.5 for _ in range(count)]
    pattern[-1] = True
    gallop = find_suffix_start(count, lambda index: pattern[index], strategy=GALLOP)
    assert gallop.start is not None and pattern[gallop.start]
    assert gallop.start == 0 or (
        gallop.last_good == gallop.start - 1 and not pattern[gallop.last_good]
    )
    assert all(pattern[index] is verdict for index, verdict in gallop.probes)
    scan = find_suffix_start(count, lambda index: pattern[index], strategy=SCAN)
    expected = count - 1
    while expected > 0 and pattern[expected - 1]:
        expected -= 1
    assert scan.start == expected


def test_an_empty_history_names_the_count():
    with pytest.raises(ValueError, match="count must be at least 1, got 0"):
        find_suffix_start(0, lambda index: True)


def test_an_error_in_the_check_is_not_swallowed():
    def broken(index):
        raise RuntimeError(f"cannot read version {index}")

    with pytest.raises(RuntimeError, match="cannot read version 4"):
        find_suffix_start(5, broken)
