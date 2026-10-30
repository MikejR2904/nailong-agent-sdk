"""Small statistics for the comparison: intervals and paired tests."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def wilson(hits: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 0.0, 1.0
    z = 1.96
    p = hits / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    return max(0.0, centre - spread), min(1.0, centre + spread)


def mcnemar_exact(a: Sequence[bool], b: Sequence[bool]) -> tuple[int, int, float]:
    """Exact two-sided McNemar test on paired outcomes: (a only right, b only right, p)."""

    if len(a) != len(b):
        raise ValueError(f"Paired outcomes need equal lengths, got {len(a)} and {len(b)}.")
    only_a = sum(1 for x, y in zip(a, b, strict=True) if x and not y)
    only_b = sum(1 for x, y in zip(a, b, strict=True) if y and not x)
    n = only_a + only_b
    if n == 0:
        return only_a, only_b, 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(only_a, only_b) + 1)) / 2**n
    return only_a, only_b, min(1.0, 2 * tail)


def paired_difference_interval(
    a: Sequence[bool], b: Sequence[bool], *, draws: int = 4_000, seed: int = 0
) -> tuple[float, float, float]:
    """The difference in accuracy a - b with a bootstrap 95% interval over cases."""

    if len(a) != len(b) or not a:
        raise ValueError(f"Paired outcomes need equal non-zero lengths, got {len(a)} and {len(b)}.")
    rng = random.Random(seed)
    n = len(a)
    deltas = [int(x) - int(y) for x, y in zip(a, b, strict=True)]
    observed = sum(deltas) / n
    sampled = sorted(sum(rng.choice(deltas) for _ in range(n)) / n for _ in range(draws))
    return observed, sampled[int(0.025 * draws)], sampled[int(0.975 * draws) - 1]


def agreement(runs: Sequence[Sequence[object]]) -> float:
    """The share of cases on which every run gave the same answer."""

    if not runs or not runs[0]:
        return float("nan")
    same = sum(1 for answers in zip(*runs, strict=True) if len(set(answers)) == 1)
    return same / len(runs[0])
