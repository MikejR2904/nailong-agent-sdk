"""SYNTHETIC ground truth for solver and learning sanity checks.

``quality`` is a closed-form model of downstream success that is non-additive on purpose: a
requirement is met when ANY of its alternatives is retained (redundancy) and success grows with the
square of the met fraction (complementarity). The ``additive`` oracle form drops both: success is
linear in the met fraction and requirements have a single alternative, so an additive utility can be
exact. It is NOT measured agent performance, and every number derived from it carries
``evidence_kind="synthetic"``.
"""

from __future__ import annotations

import random
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from .instances import BuiltStore, Instance, replay

SYNTHETIC = "synthetic"
MEASURED = "measured"


class DownstreamExecutor(Protocol):
    """Runs one candidate selection on one task and returns an outcome in [0, 1].

    A real executor (an agent run scored by a verifier) has ``evidence_kind == "measured"``.
    """

    evidence_kind: str

    def run(self, instance: Instance, selection: Collection[str], seed: int) -> float: ...


def satisfied_fraction(instance: Instance, selected_indices: Collection[int]) -> float:
    met = sum(1 for alt in instance.requirements if any(i in selected_indices for i in alt))
    return met / len(instance.requirements) if instance.requirements else 0.0


def quality_from_indices(instance: Instance, selected_indices: Collection[int]) -> float:
    fraction = satisfied_fraction(instance, selected_indices)
    exponent = 1 if instance.oracle_form == "additive" else 2
    return 0.03 + 0.92 * fraction**exponent


def to_indices(built: BuiltStore, selection: Iterable[str]) -> set[int]:
    position = {episode_id: index for index, episode_id in enumerate(built.ids)}
    return {position[episode_id] for episode_id in selection}


@dataclass
class SyntheticOracleExecutor:
    """Bernoulli outcomes with success probability ``quality``."""

    evidence_kind: str = SYNTHETIC
    _positions: dict[str, dict[str, int]] = field(default_factory=dict)

    def run(self, instance: Instance, selection: Collection[str], seed: int) -> float:
        if instance.instance_id not in self._positions:
            built = replay(instance)
            self._positions[instance.instance_id] = {i: n for n, i in enumerate(built.ids)}
        positions = self._positions[instance.instance_id]
        probability = quality_from_indices(instance, {positions[i] for i in selection})
        return (
            1.0 if random.Random(f"{instance.instance_id}/{seed}").random() < probability else 0.0
        )


def optimum_quality(instance: Instance) -> tuple[float, frozenset[int]]:
    """Exhaustive best feasible selection under the synthetic oracle (small instances only)."""

    from nailong_agent_sdk.memory.episode_scoring import _episode_tokens

    built = replay(instance)
    n = len(built.ids)
    if n > 16:
        raise ValueError(
            f'Instance "{instance.instance_id}" has {n} episodes; exhaustive regret supports at '
            "most 16."
        )
    costs = [_episode_tokens(built.store.get(i)) for i in built.ids]
    deps = [[built.ids.index(d) for d in built.store.get(i).depends_on] for i in built.ids]
    mandatory = _closure_mask(deps, [built.ids.index(i) for i in built.protected])
    best: tuple[float, int] | None = None
    for mask in range(1 << n):
        if mask & mandatory != mandatory:
            continue
        if any(mask >> i & 1 and any(not mask >> d & 1 for d in deps[i]) for i in range(n)):
            continue
        if sum(costs[i] for i in range(n) if mask >> i & 1) > instance.budget:
            continue
        chosen = {i for i in range(n) if mask >> i & 1}
        value = quality_from_indices(instance, chosen)
        if best is None or value > best[0]:
            best = (value, mask)
    if best is None:
        raise ValueError(f'Instance "{instance.instance_id}" has no feasible selection.')
    return best[0], frozenset(i for i in range(n) if best[1] >> i & 1)


def _closure_mask(deps: list[list[int]], seeds: list[int]) -> int:
    mask = 0
    pending = list(seeds)
    while pending:
        item = pending.pop()
        if mask >> item & 1:
            continue
        mask |= 1 << item
        pending.extend(deps[item])
    return mask
