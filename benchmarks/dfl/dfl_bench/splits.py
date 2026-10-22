"""Leakage-safe train/validation/test splits.

Instances that share any of the chosen identity keys are one group, and a group is never divided
between splits. The keys are:

* ``template``: the surface template (vocabulary and entity style) the task was drawn from;
* ``graph``: the dependency topology alone (useful when topologies are random; too coarse when every
  family has a fixed shape, because it then glues unrelated templates together);
* ``query``: the exact task text;
* ``content``: the entire recorded history, so an exact duplicate can never straddle a split.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence

from .instances import Instance

SYNTHETIC_KEYS = ("template", "graph", "query")
CONTENT_KEYS = ("template", "query", "content")
KEYS: dict[str, Callable[[Instance], str]] = {
    "template": lambda x: x.template_id,
    "graph": lambda x: x.graph_hash(),
    "query": lambda x: x.query_digest(),
    "content": lambda x: x.content_hash(),
    "instance": lambda x: x.instance_id,
}


def _selected(keys: Sequence[str]) -> list[tuple[str, Callable[[Instance], str]]]:
    unknown = sorted(set(keys) - set(KEYS))
    if unknown:
        raise ValueError(f"Unknown split keys {unknown}; expected a subset of {sorted(KEYS)}.")
    return [(name, KEYS[name]) for name in keys]


def group_instances(
    instances: Sequence[Instance], keys: Sequence[str] = SYNTHETIC_KEYS
) -> list[list[Instance]]:
    parent = list(range(len(instances)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict[tuple[str, str], int] = {}
    for index, instance in enumerate(instances):
        for name, key in _selected(keys):
            identity = (name, key(instance))
            if identity in seen:
                parent[find(index)] = find(seen[identity])
            else:
                seen[identity] = index
    groups: dict[int, list[Instance]] = {}
    for index, instance in enumerate(instances):
        groups.setdefault(find(index), []).append(instance)
    return [sorted(g, key=lambda x: x.instance_id) for _, g in sorted(groups.items())]


def split_instances(
    instances: Sequence[Instance],
    fractions: tuple[float, float, float],
    seed: int,
    keys: Sequence[str] = SYNTHETIC_KEYS,
) -> tuple[list[Instance], list[Instance], list[Instance]]:
    if abs(sum(fractions) - 1.0) > 1e-9 or any(f < 0 for f in fractions):
        raise ValueError(f"Split fractions must be non-negative and sum to 1, got {fractions}.")
    groups = group_instances(instances, keys)
    random.Random(seed).shuffle(groups)
    total = len(instances)
    targets = (fractions[0] * total, (fractions[0] + fractions[1]) * total)
    splits: tuple[list[Instance], list[Instance], list[Instance]] = ([], [], [])
    placed = 0
    for group in groups:
        index = 0 if placed < targets[0] else 1 if placed < targets[1] else 2
        splits[index].extend(group)
        placed += len(group)
    empty = [name for name, part in zip(("train", "validation", "test"), splits) if not part]
    if empty and all(fraction > 0 for fraction in fractions):
        raise ValueError(
            f"The split left {empty} empty: {len(groups)} identity groups over {total} instances "
            f"with keys {tuple(keys)} are too coarse for fractions {fractions}."
        )
    return splits


def leakage_report(
    *splits: Sequence[Instance], keys: Sequence[str] = (*SYNTHETIC_KEYS, "instance")
) -> dict[str, list[str]]:
    problems: dict[str, list[str]] = {}
    for name, key in _selected(keys):
        owners: dict[str, set[int]] = {}
        for index, split in enumerate(splits):
            for instance in split:
                owners.setdefault(key(instance), set()).add(index)
        shared = sorted(value for value, where in owners.items() if len(where) > 1)
        if shared:
            problems[name] = shared
    return problems


def assert_no_leakage(
    *splits: Sequence[Instance], keys: Sequence[str] = (*SYNTHETIC_KEYS, "instance")
) -> None:
    problems = leakage_report(*splits, keys=keys)
    if problems:
        detail = "; ".join(f"{name}: {values[:3]}" for name, values in problems.items())
        raise ValueError(f"Training/test leakage across splits ({detail}).")
