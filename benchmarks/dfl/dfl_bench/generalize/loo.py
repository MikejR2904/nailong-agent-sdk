"""Leave-one-out criticality: how much does the answer degrade when one episode is removed?

Starting from the full history (all evidence retained, budget ignored), each episode is removed
together with its dependents so the remaining set stays dependency-closed, and the drop in measured
quality is the episode's criticality. Mandatory episodes cannot be removed and are not labelled.
Most episodes in a redundant history have criticality zero; the few that do not are exactly the rare
sole-source ones that access counts and relevance can miss.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from nailong_agent_sdk.memory.episode_scoring import _episode_tokens
from nailong_agent_sdk.memory.utility_objectives import (
    ObjectiveDataset,
    ObjectiveTerm,
    build_objective_dataset,
)

from ..instances import Instance, replay
from ..oracle import DownstreamExecutor
from ..selection import mandatory_closure
from .learners import SchemaCache, family_of_id

CRITICAL_DROP = 0.2
NEUTRAL_DROP = 0.05


def removal_sets(instance: Instance) -> dict[str, frozenset[str]]:
    """For every removable episode, the full set minus it and everything that depends on it."""

    built = replay(instance)
    everything = set(built.ids)
    mandatory = set(mandatory_closure(built))
    result: dict[str, frozenset[str]] = {}
    for episode_id in built.ids:
        removed = {episode_id}
        pending = [episode_id]
        while pending:
            for dependent in built.store.get(pending.pop()).depended_on_by:
                if dependent in everything and dependent not in removed:
                    removed.add(dependent)
                    pending.append(dependent)
        if removed & mandatory:
            continue
        result[episode_id] = frozenset(everything - removed)
    return result


def measure_loo(
    executor: DownstreamExecutor,
    instances: Sequence[Instance],
    trials: int,
    *,
    batch=None,
) -> dict[str, dict[str, float]]:
    """Criticality per removable episode, one entry per instance.

    ``batch`` may be an executor's ``run_batch`` so all model calls run concurrently.
    """

    plans = []
    requests = []
    for instance in instances:
        sets = removal_sets(instance)
        full = frozenset(replay(instance).ids)
        plans.append((instance, full, sets))
        requests.extend((instance, full, t) for t in range(trials))
        for ids in sets.values():
            requests.extend((instance, ids, t) for t in range(trials))
    if batch is not None:
        scores = iter(batch(requests))
    else:
        scores = iter(executor.run(i, ids, t) for i, ids, t in requests)
    labels: dict[str, dict[str, float]] = {}
    for instance, _, sets in plans:
        baseline = sum(next(scores) for _ in range(trials)) / trials
        drops = {}
        for episode_id in sets:
            without = sum(next(scores) for _ in range(trials)) / trials
            drops[episode_id] = max(0.0, baseline - without)
        labels[instance.instance_id] = drops
    return labels


def loo_dataset(
    cache: SchemaCache,
    labels: Mapping[str, Mapping[str, float]],
    instance_ids: Sequence[str],
    *,
    critical: float = CRITICAL_DROP,
    neutral: float = NEUTRAL_DROP,
    max_pairs: int = 24,
) -> ObjectiveDataset:
    """Pairs ``{critical episode} > {neutral episode}`` per task, weighted by the measured drop."""

    features = {}
    groups = {}
    pairs = []
    for instance_id in instance_ids:
        drops = labels.get(instance_id)
        if not drops:
            continue
        important = sorted(
            (e for e, d in drops.items() if d >= critical), key=lambda e: (-drops[e], e)
        )
        plain = sorted(e for e, d in drops.items() if d < neutral)
        if not important or not plain:
            continue
        features[instance_id] = cache.get(instance_id)
        groups[instance_id] = family_of_id(instance_id)
        per = max(1, math.ceil(max_pairs / len(important)))
        for rank, episode_id in enumerate(important):
            for offset in range(min(per, len(plain))):
                other = plain[(rank * per + offset) % len(plain)]
                pairs.append((instance_id, [episode_id], [other], drops[episode_id]))
    if not pairs:
        return ObjectiveDataset((), ())
    return build_objective_dataset(features, groups, pairs=pairs)


def merge_datasets(first: ObjectiveDataset, second: ObjectiveDataset) -> ObjectiveDataset:
    shift = len(first.items)
    shifted = tuple(
        ObjectiveTerm(
            term.kind,
            tuple(tuple(i + shift for i in candidate) for candidate in term.candidates),
            term.targets,
            term.weight,
            term.group,
        )
        for term in second.terms
    )
    return ObjectiveDataset(first.items + second.items, first.terms + shifted)


def retention_scorecard(
    instance: Instance, selection: frozenset[str], drops: Mapping[str, float]
) -> dict[str, float]:
    """Information-weighted recall of critical episodes and the share of retained tokens that
    carry no measured criticality."""

    built = replay(instance)
    total = sum(drops.values())
    kept = sum(d for e, d in drops.items() if e in selection)
    tokens = {e: _episode_tokens(built.store.get(e)) for e in selection}
    wasted = sum(t for e, t in tokens.items() if e in drops and drops[e] < NEUTRAL_DROP)
    spent = sum(tokens.values())
    return {
        "critical_recall": kept / total if total else float("nan"),
        "critical_missed": sum(
            1 for e, d in drops.items() if d >= CRITICAL_DROP and e not in selection
        ),
        "critical_total": sum(1 for d in drops.values() if d >= CRITICAL_DROP),
        "distraction_load": wasted / spent if spent else 0.0,
    }
