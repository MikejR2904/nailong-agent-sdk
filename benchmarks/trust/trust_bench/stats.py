# Copyright (c) 2026 David Michael Indraputra

"""Interval estimates and paired tests used by every Trust Profile endpoint.

Standard library only. Proportions use Wilson or exact binomial bounds, never the normal
approximation; resampling treats a case, not a run, as the unit.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist


@dataclass(frozen=True)
class Proportion:
    successes: int
    trials: int
    low: float
    high: float

    @property
    def estimate(self) -> float:
        return self.successes / self.trials if self.trials else float("nan")

    def text(self) -> str:
        if not self.trials:
            return "n/a (0 trials)"
        interval = f"[{self.low:.3f}, {self.high:.3f}]"
        return f"{self.estimate:.3f} {interval} ({self.successes}/{self.trials})"


def _require_counts(successes: int, trials: int) -> None:
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError(f"need 0 <= successes <= trials, got {successes} of {trials}.")


def wilson(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    _require_counts(successes, trials)
    if trials == 0:
        return 0.0, 1.0
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def proportion(successes: int, trials: int, confidence: float = 0.95) -> Proportion:
    low, high = wilson(successes, trials, confidence)
    return Proportion(successes, trials, low, high)


def binomial_cdf(k: int, n: int, p: float) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    log_n = math.lgamma(n + 1)
    total = 0.0
    for i in range(k + 1):
        term = log_n - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * log_p + (n - i) * log_q
        total += math.exp(term)
    return min(1.0, total)


def exact_upper(events: int, trials: int, confidence: float = 0.95) -> float:
    _require_counts(events, trials)
    if trials == 0 or events >= trials:
        return 1.0
    alpha = 1 - confidence
    low, high = events / trials, 1.0
    for _ in range(80):
        mid = (low + high) / 2
        if binomial_cdf(events, trials, mid) > alpha:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def exact_lower(events: int, trials: int, confidence: float = 0.95) -> float:
    _require_counts(events, trials)
    if events == 0:
        return 0.0
    alpha = 1 - confidence
    low, high = 0.0, events / trials
    for _ in range(80):
        mid = (low + high) / 2
        if 1 - binomial_cdf(events - 1, trials, mid) < alpha:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def zero_event_cases(max_rate: float, confidence: float = 0.95) -> int:
    if not 0 < max_rate < 1:
        raise ValueError(f"max_rate must be strictly between 0 and 1, got {max_rate}.")
    return math.ceil(math.log(1 - confidence) / math.log(1 - max_rate))


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean of an empty sequence.")
    return sum(values) / len(values)


def cluster_bootstrap(
    per_case: Mapping[str, float],
    *,
    iterations: int = 2_000,
    confidence: float = 0.95,
    seed: int = 20261018,
) -> tuple[float, float, float]:
    if not per_case:
        raise ValueError("cluster_bootstrap needs at least one case.")
    values = [per_case[key] for key in sorted(per_case)]
    generator = random.Random(seed)
    size = len(values)
    draws = sorted(
        sum(values[generator.randrange(size)] for _ in range(size)) / size
        for _ in range(iterations)
    )
    tail = (1 - confidence) / 2
    low = draws[int(math.floor(tail * (iterations - 1)))]
    high = draws[int(math.ceil((1 - tail) * (iterations - 1)))]
    return mean(values), low, high


def stratified_macro(
    strata: Mapping[str, Sequence[float]],
    *,
    iterations: int = 2_000,
    confidence: float = 0.95,
    seed: int = 20261018,
) -> tuple[float, float, float]:
    usable = {name: list(values) for name, values in strata.items() if values}
    if not usable:
        raise ValueError("stratified_macro needs at least one non-empty stratum.")
    estimate = mean([mean(values) for values in usable.values()])
    generator = random.Random(seed)
    draws = []
    for _ in range(iterations):
        means = []
        for values in usable.values():
            size = len(values)
            means.append(sum(values[generator.randrange(size)] for _ in range(size)) / size)
        draws.append(mean(means))
    draws.sort()
    tail = (1 - confidence) / 2
    low = draws[int(math.floor(tail * (iterations - 1)))]
    high = draws[int(math.ceil((1 - tail) * (iterations - 1)))]
    return estimate, low, high


def paired_difference(
    first: Mapping[str, float],
    second: Mapping[str, float],
    *,
    iterations: int = 2_000,
    confidence: float = 0.95,
    seed: int = 20261018,
) -> tuple[float, float, float]:
    if set(first) != set(second):
        missing = sorted(set(first) ^ set(second))[:5]
        raise ValueError(f"paired arms must cover the same cases; differing: {missing}.")
    return cluster_bootstrap(
        {key: first[key] - second[key] for key in first},
        iterations=iterations,
        confidence=confidence,
        seed=seed,
    )


def newcombe_difference(
    first: tuple[int, int], second: tuple[int, int], confidence: float = 0.95
) -> tuple[float, float, float]:
    p1, n1 = first[0] / first[1], first[1]
    p2, n2 = second[0] / second[1], second[1]
    low1, high1 = wilson(first[0], n1, confidence)
    low2, high2 = wilson(second[0], n2, confidence)
    difference = p1 - p2
    lower = difference - math.sqrt((p1 - low1) ** 2 + (high2 - p2) ** 2)
    upper = difference + math.sqrt((high1 - p1) ** 2 + (p2 - low2) ** 2)
    return difference, lower, upper


def mcnemar_exact(only_first: int, only_second: int) -> float:
    n = only_first + only_second
    if n == 0:
        return 1.0
    smaller = min(only_first, only_second)
    tail = sum(math.comb(n, i) for i in range(smaller + 1)) / 2**n
    return min(1.0, 2 * tail)


def holm(p_values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    count = len(ordered)
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (count - rank) * value))
        adjusted[name] = running
    return adjusted


def kendall_tau_b(first: Sequence[float], second: Sequence[float]) -> float:
    if len(first) != len(second):
        raise ValueError(f"kendall_tau_b needs equal lengths, got {len(first)} and {len(second)}.")
    concordant = discordant = ties_first = ties_second = 0
    for i in range(len(first)):
        for j in range(i + 1, len(first)):
            a = first[i] - first[j]
            b = second[i] - second[j]
            if a == 0 and b == 0:
                continue
            if a == 0:
                ties_first += 1
            elif b == 0:
                ties_second += 1
            elif (a > 0) == (b > 0):
                concordant += 1
            else:
                discordant += 1
    denominator = math.sqrt(
        (concordant + discordant + ties_first) * (concordant + discordant + ties_second)
    )
    return (concordant - discordant) / denominator if denominator else float("nan")
