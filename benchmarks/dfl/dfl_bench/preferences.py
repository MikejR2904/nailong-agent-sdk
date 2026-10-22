"""Preference pairs from repeated downstream trials, with confidence weights and tie omission.

A pair is created only when the measured difference is both statistically distinguishable and at
least ``min_effect``. Everything else is a tie and is omitted; no preference is manufactured.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from .oracle import MEASURED, SYNTHETIC
from .stats import mean, two_proportion_z


@dataclass(frozen=True)
class CandidateOutcome:
    instance_id: str
    selection: frozenset[str]
    sources: tuple[str, ...]
    outcomes: tuple[float, ...]
    evidence_kind: str

    @property
    def mean_outcome(self) -> float:
        return mean(self.outcomes)


@dataclass(frozen=True)
class Preference:
    instance_id: str
    positive: frozenset[str]
    negative: frozenset[str]
    weight: float
    p_value: float
    effect: float
    evidence_kind: str


@dataclass(frozen=True)
class PreferencePolicy:
    alpha: float = 0.05
    min_effect: float = 0.15
    min_trials: int = 4
    max_pairs_per_instance: int = 60


def assert_single_evidence_kind(outcomes: list[CandidateOutcome]) -> str:
    kinds = {outcome.evidence_kind for outcome in outcomes}
    unknown = kinds - {SYNTHETIC, MEASURED}
    if unknown:
        raise ValueError(
            f"Unknown evidence kinds {sorted(unknown)}; use 'synthetic' or 'measured'."
        )
    if len(kinds) > 1:
        raise ValueError(
            "Refusing to mix evidence kinds "
            f"{sorted(kinds)} in one preference dataset; synthetic and measured outcomes must be "
            "reported and trained on separately."
        )
    return next(iter(kinds)) if kinds else SYNTHETIC


def build_preferences(
    outcomes: list[CandidateOutcome], policy: PreferencePolicy = PreferencePolicy()
) -> list[Preference]:
    assert_single_evidence_kind(outcomes)
    by_instance: dict[str, list[CandidateOutcome]] = {}
    for outcome in outcomes:
        by_instance.setdefault(outcome.instance_id, []).append(outcome)
    preferences: list[Preference] = []
    for instance_id, group in sorted(by_instance.items()):
        pairs: list[Preference] = []
        for first, second in combinations(group, 2):
            if first.selection == second.selection:
                continue
            n_a, n_b = len(first.outcomes), len(second.outcomes)
            if min(n_a, n_b) < policy.min_trials:
                continue
            effect = first.mean_outcome - second.mean_outcome
            if abs(effect) < policy.min_effect:
                continue
            _, p_value = two_proportion_z(sum(first.outcomes), n_a, sum(second.outcomes), n_b)
            if p_value >= policy.alpha:
                continue
            winner, loser = (first, second) if effect > 0 else (second, first)
            pairs.append(
                Preference(
                    instance_id,
                    winner.selection,
                    loser.selection,
                    1.0 - p_value,
                    p_value,
                    abs(effect),
                    first.evidence_kind,
                )
            )
        pairs.sort(key=lambda p: (-p.weight, sorted(p.positive), sorted(p.negative)))
        preferences.extend(pairs[: policy.max_pairs_per_instance])
    return resolve_conflicts(preferences)


def resolve_conflicts(preferences: list[Preference]) -> list[Preference]:
    """Merge duplicates (summing weight) and cancel opposite pairs by net weight."""

    net: dict[tuple[str, frozenset[str], frozenset[str]], float] = {}
    template: dict[tuple[str, frozenset[str], frozenset[str]], Preference] = {}
    for item in preferences:
        forward = (item.instance_id, item.positive, item.negative)
        backward = (item.instance_id, item.negative, item.positive)
        if backward in net:
            net[backward] -= item.weight
            continue
        net[forward] = net.get(forward, 0.0) + item.weight
        template.setdefault(forward, item)
    resolved = []
    for key, weight in net.items():
        if weight > 1e-12:
            base = template[key]
            resolved.append(
                Preference(
                    key[0], key[1], key[2], weight, base.p_value, base.effect, base.evidence_kind
                )
            )
        elif weight < -1e-12:
            base = template.get(key)
            if base is not None:
                resolved.append(
                    Preference(
                        key[0],
                        key[2],
                        key[1],
                        -weight,
                        base.p_value,
                        base.effect,
                        base.evidence_kind,
                    )
                )
    return sorted(resolved, key=lambda p: (p.instance_id, sorted(p.positive), sorted(p.negative)))
