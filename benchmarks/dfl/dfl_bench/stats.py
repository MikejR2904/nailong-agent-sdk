"""Small standard-library statistics: bootstrap intervals, paired tests, two-proportion z."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def bootstrap_ci(
    values: Sequence[float], *, seed: int = 0, resamples: int = 2000, confidence: float = 0.95
) -> tuple[float, float]:
    if not values:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    n = len(values)
    estimates = sorted(mean([values[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples))
    tail = (1 - confidence) / 2
    return estimates[int(tail * resamples)], estimates[
        min(resamples - 1, int((1 - tail) * resamples))
    ]


def sign_test_p(differences: Sequence[float]) -> float:
    """Exact two-sided sign test; exact zeros are dropped."""

    nonzero = [d for d in differences if d != 0]
    n = len(nonzero)
    if n == 0:
        return 1.0
    wins = sum(1 for d in nonzero if d > 0)
    k = min(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def holm_adjust(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (name, p) in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - rank) * p))
        adjusted[name] = running
    return adjusted


def two_proportion_z(
    successes_a: float, n_a: int, successes_b: float, n_b: int
) -> tuple[float, float]:
    """Return ``(z, two_sided_p)`` with add-half smoothing so a 0/n or n/n arm has variance."""

    pa = (successes_a + 0.5) / (n_a + 1)
    pb = (successes_b + 0.5) / (n_b + 1)
    standard_error = math.sqrt(pa * (1 - pa) / (n_a + 1) + pb * (1 - pb) / (n_b + 1))
    z = (pa - pb) / standard_error
    return z, math.erfc(abs(z) / math.sqrt(2))


def paired_summary(
    candidate: Sequence[float], reference: Sequence[float], *, seed: int = 0
) -> dict[str, float]:
    if len(candidate) != len(reference):
        raise ValueError(
            f"Paired comparison needs equal lengths, got {len(candidate)} and {len(reference)}."
        )
    differences = [c - r for c, r in zip(candidate, reference, strict=True)]
    low, high = bootstrap_ci(differences, seed=seed)
    return {
        "mean_difference": mean(differences),
        "ci_low": low,
        "ci_high": high,
        "sign_test_p": sign_test_p(differences),
        "wins": sum(1 for d in differences if d > 0),
        "losses": sum(1 for d in differences if d < 0),
        "ties": sum(1 for d in differences if d == 0),
        "n": len(differences),
    }


def cluster_bootstrap(
    groups: dict[str, Sequence[float]],
    *,
    seed: int = 0,
    resamples: int = 4000,
    confidence: float = 0.95,
) -> dict[str, float]:
    """Resample whole clusters (templates) and return the pooled mean, interval and two-sided p."""

    keys = sorted(groups)
    if not keys:
        raise ValueError("cluster_bootstrap needs at least one cluster.")
    sums = [sum(groups[k]) for k in keys]
    counts = [len(groups[k]) for k in keys]
    rng = random.Random(seed)
    k = len(keys)
    estimates = []
    for _ in range(resamples):
        picks = [rng.randrange(k) for _ in range(k)]
        total = sum(counts[i] for i in picks)
        estimates.append(sum(sums[i] for i in picks) / total if total else 0.0)
    estimates.sort()
    tail = (1 - confidence) / 2
    at_most = sum(1 for e in estimates if e <= 0)
    at_least = sum(1 for e in estimates if e >= 0)
    p = min(1.0, 2 * min((at_most + 1) / (resamples + 1), (at_least + 1) / (resamples + 1)))
    return {
        "mean": sum(sums) / sum(counts),
        "ci_low": estimates[int(tail * resamples)],
        "ci_high": estimates[min(resamples - 1, int((1 - tail) * resamples))],
        "p": p,
        "clusters": k,
        "n": sum(counts),
    }


def template_of(instance_id: str) -> str:
    return instance_id.rsplit("-", 2)[0]
