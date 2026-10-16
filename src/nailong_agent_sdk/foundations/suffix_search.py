# Copyright (c) 2026 David Michael Indraputra

"""Locate where a defect starts in an ordered history with as few probes as the history allows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class SuffixStrategy(StrEnum):
    GALLOP = "gallop"
    SCAN = "scan"


@dataclass(frozen=True)
class SuffixSearch:
    """Where the bad suffix of a history starts and which probes found it."""

    start: int | None
    last_good: int | None
    probes: tuple[tuple[int, bool], ...]
    strategy: SuffixStrategy

    @property
    def evaluations(self) -> int:
        return len(self.probes)


def find_suffix_start(
    count: int,
    is_bad: Callable[[int], bool],
    *,
    strategy: SuffixStrategy = SuffixStrategy.GALLOP,
) -> SuffixSearch:
    """Find the first index of the bad suffix of ``count`` ordered elements.

    ``gallop`` probes the newest element, then steps back 1, 2, 4, ... elements until it
    finds a good one and bisects the gap: ``2 * log2(d) + O(1)`` probes for a bad suffix of
    ``d`` elements, whatever the length of the history. It returns an adjacent good-to-bad
    pair and is exact when badness persists up to the newest element. ``scan`` walks back
    one element at a time until a good one: ``d + 1`` probes and exact without any
    assumption. ``start`` is None when the newest element is good.
    """

    if count < 1:
        raise ValueError(f"count must be at least 1, got {count}.")
    verdicts: dict[int, bool] = {}
    probes: list[tuple[int, bool]] = []

    def probe(index: int) -> bool:
        if index not in verdicts:
            verdicts[index] = bool(is_bad(index))
            probes.append((index, verdicts[index]))
        return verdicts[index]

    head = count - 1
    if not probe(head):
        return SuffixSearch(None, head, tuple(probes), strategy)
    if strategy is SuffixStrategy.SCAN:
        index = head
        while index > 0 and probe(index - 1):
            index -= 1
        return SuffixSearch(index, index - 1 if index > 0 else None, tuple(probes), strategy)
    bad, step = head, 1
    while True:
        index = max(bad - step, 0)
        if index == bad:
            return SuffixSearch(0, None, tuple(probes), strategy)
        if not probe(index):
            good = index
            break
        bad, step = index, step * 2
    while bad - good > 1:
        middle = (good + bad) // 2
        if probe(middle):
            bad = middle
        else:
            good = middle
    return SuffixSearch(bad, good, tuple(probes), strategy)
