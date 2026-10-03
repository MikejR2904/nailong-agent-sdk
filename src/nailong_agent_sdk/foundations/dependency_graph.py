# Copyright (c) 2026 David Michael Indraputra

"""Deterministic traversal helpers for declared directed dependency graphs.

Edges use the project convention ``(dependent, prerequisite)``. Therefore a
reverse traversal from a missing prerequisite returns all known dependents that
are transitively affected by its absence.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

DependencyPair = tuple[str, str]


def deterministic_cycles(nodes: Iterable[str], edges: Iterable[DependencyPair]) -> list[list[str]]:
    """Return stable closed cycle paths over declared known nodes only.

    The traversal is intentionally structural: unknown endpoints are excluded so
    traceability validation can report them as missing references separately.
    """

    known = set(nodes)
    dependencies: dict[str, list[str]] = {node: [] for node in sorted(known)}
    for dependent, prerequisite in edges:
        if dependent in known and prerequisite in known:
            dependencies[dependent].append(prerequisite)
    for values in dependencies.values():
        values.sort()

    found: list[list[str]] = []
    visited: set[str] = set()

    for root in sorted(dependencies):
        if root in visited:
            continue
        path = [root]
        position = {root: 0}
        stack = [iter(dependencies[root])]
        while stack:
            advanced = False
            for dependency in stack[-1]:
                if dependency in position:
                    found.append([*path[position[dependency] :], dependency])
                elif dependency not in visited:
                    position[dependency] = len(path)
                    path.append(dependency)
                    stack.append(iter(dependencies[dependency]))
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                node = path.pop()
                del position[node]
                visited.add(node)
    return found


def reverse_reachable_nodes(
    nodes: Iterable[str],
    edges: Iterable[DependencyPair],
    root_id: str,
) -> list[str]:
    """Return sorted known dependents transitively affected by ``root_id``.

    ``root_id`` may be absent from ``nodes``. It remains a traversal seed but is
    never counted, which makes the result directly usable as a missing-node
    blast radius.
    """

    known = set(nodes)
    dependents: dict[str, set[str]] = {}
    for dependent, prerequisite in edges:
        if dependent in known:
            dependents.setdefault(prerequisite, set()).add(dependent)

    seen = {root_id}
    affected: set[str] = set()
    queue: deque[str] = deque([root_id])
    while queue:
        prerequisite = queue.popleft()
        for dependent in sorted(dependents.get(prerequisite, ())):
            if dependent in seen:
                continue
            seen.add(dependent)
            affected.add(dependent)
            queue.append(dependent)
    return sorted(affected)


def reverse_reachable_count(
    nodes: Iterable[str],
    edges: Iterable[DependencyPair],
    root_id: str,
) -> int:
    """Return the number of known transitive dependents of ``root_id``."""

    return len(reverse_reachable_nodes(nodes, edges, root_id))
