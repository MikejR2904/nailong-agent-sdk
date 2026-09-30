# Copyright (c) 2026 David Michael Indraputra

"""Exact and greedy PCKP solvers.

The exact solver uses a fixed-order, exact-arithmetic branch-and-bound search,
or the exact capacity-indexed tree dynamic program when the instance is a
rooted forest. Neither solver makes model calls or uses random ordering, so
both are reusable by episode retention and source-evidence packing.
"""

from __future__ import annotations

from collections.abc import Iterable
from fractions import Fraction

from .models import PckpItem, PckpProblem, PckpSolution, PckpStatus


class ExactPckpSolver:
    """Solve PCKP exactly with deterministic branch-and-bound or tree DP.

    ``max_branch_nodes`` enables a transparent anytime result.  A bounded run
    is never reported as ``OPTIMAL``.  The default has no artificial search
    limit and therefore only returns ``OPTIMAL`` after a full proof.
    """

    def __init__(
        self,
        *,
        max_branch_nodes: int | None = None,
        enable_tree_dynamic_program: bool = True,
        max_tree_token_budget: int = 50_000,
    ) -> None:
        if max_branch_nodes is not None and max_branch_nodes < 1:
            raise ValueError("max_branch_nodes must be at least one when configured")
        if max_tree_token_budget < 0:
            raise ValueError("max_tree_token_budget may not be negative")
        self._max_branch_nodes = max_branch_nodes
        self._enable_tree_dynamic_program = enable_tree_dynamic_program
        self._max_tree_token_budget = max_tree_token_budget

    def solve(self, problem: PckpProblem) -> PckpSolution:
        """Return an exact or explicitly bounded dependency-closed selection."""

        items = {item.item_id: item for item in problem.items}
        dependents = _dependents(items.values())
        mandatory = _closure({item.item_id for item in items.values() if item.mandatory}, items)
        mandatory_cost = _cost(mandatory, items)
        problem_hash = _problem_hash(problem)
        if mandatory_cost > problem.token_budget:
            return PckpSolution(
                status=PckpStatus.INFEASIBLE_MANDATORY,
                mandatory_item_ids=sorted(mandatory),
                token_cost=mandatory_cost,
                utility=_utility(mandatory, items),
                upper_bound=str(_utility(mandatory, items)),
                optimality_gap="0",
                branch_nodes=0,
                solver="mandatory-closure",
                problem_hash=problem_hash,
                diagnostics=["Mandatory prerequisite closure exceeds the token budget."],
            )

        if (
            self._enable_tree_dynamic_program
            and problem.token_budget <= self._max_tree_token_budget
            and _is_rooted_forest(items.values())
        ):
            return _solve_rooted_forest(problem, mandatory, problem_hash)
        return self._solve_branch_and_bound(problem, mandatory, dependents, problem_hash)

    def _solve_branch_and_bound(
        self,
        problem: PckpProblem,
        mandatory: set[str],
        dependents: dict[str, set[str]],
        problem_hash: str,
    ) -> PckpSolution:
        items = {item.item_id: item for item in problem.items}
        ordered_ids = tuple(sorted(items))
        # Each item's utility/cost density is fixed for the whole search, so the
        # bound's greedy fill order is sorted once here rather than re-sorted
        # (with a fresh Fraction per item) on every node visited below.
        density_order = sorted(
            items.values(),
            key=lambda value: (
                value.token_cost != 0,
                -Fraction(value.utility, value.token_cost or 1),
                value.item_id,
            ),
        )
        incumbent_selection = set(mandatory)
        incumbent_utility = _utility(incumbent_selection, items)
        branch_nodes = 0
        exhausted = True
        root_upper = _fractional_upper_bound(
            incumbent_selection, set(), items, density_order, problem.token_budget
        )

        def visit(selected: set[str], excluded: set[str]) -> None:
            nonlocal incumbent_selection, incumbent_utility, branch_nodes, exhausted
            if self._max_branch_nodes is not None and branch_nodes >= self._max_branch_nodes:
                exhausted = False
                return
            branch_nodes += 1
            normalized = _propagate(selected, excluded, items, dependents)
            if normalized is None:
                return
            selected_now, excluded_now = normalized
            selected_cost = _cost(selected_now, items)
            if selected_cost > problem.token_budget:
                return
            upper = _fractional_upper_bound(
                selected_now, excluded_now, items, density_order, problem.token_budget
            )
            if upper < incumbent_utility:
                return
            undecided = next(
                (
                    item_id
                    for item_id in ordered_ids
                    if item_id not in selected_now and item_id not in excluded_now
                ),
                None,
            )
            if undecided is None:
                candidate_utility = _utility(selected_now, items)
                if candidate_utility > incumbent_utility or (
                    candidate_utility == incumbent_utility
                    and tuple(sorted(selected_now)) < tuple(sorted(incumbent_selection))
                ):
                    incumbent_selection = selected_now
                    incumbent_utility = candidate_utility
                return
            # Exclusion first gives stable search traces; final tie resolution is explicit.
            visit(set(selected_now), {*excluded_now, undecided})
            visit({*selected_now, undecided}, set(excluded_now))

        visit(set(mandatory), set())
        status = PckpStatus.OPTIMAL if exhausted else PckpStatus.BEST_EFFORT
        certified_upper = Fraction(incumbent_utility) if exhausted else root_upper
        gap = Fraction(0) if exhausted else max(Fraction(0), root_upper - incumbent_utility)
        return PckpSolution(
            status=status,
            selected_item_ids=sorted(incumbent_selection),
            mandatory_item_ids=sorted(mandatory),
            token_cost=_cost(incumbent_selection, items),
            utility=incumbent_utility,
            upper_bound=_fraction_text(certified_upper),
            optimality_gap=_fraction_text(gap),
            branch_nodes=branch_nodes,
            solver="branch-and-bound",
            problem_hash=problem_hash,
            diagnostics=(
                []
                if exhausted
                else ["Branch-node limit reached; selected set is feasible but not proven optimal."]
            ),
        )


class GreedyPckpBaseline:
    """Deterministic density-first baseline for the same additive PCKP objective.

    This is intentionally not an exact algorithm. It makes the exact-versus-
    greedy benchmark meaningful because both solvers consume identical costs,
    utilities, and prerequisite constraints.
    """

    def solve(self, problem: PckpProblem) -> PckpSolution:
        items = {item.item_id: item for item in problem.items}
        mandatory = _closure({item.item_id for item in items.values() if item.mandatory}, items)
        mandatory_cost = _cost(mandatory, items)
        problem_hash = _problem_hash(problem)
        if mandatory_cost > problem.token_budget:
            return PckpSolution(
                status=PckpStatus.INFEASIBLE_MANDATORY,
                mandatory_item_ids=sorted(mandatory),
                token_cost=mandatory_cost,
                utility=_utility(mandatory, items),
                upper_bound=str(_utility(mandatory, items)),
                optimality_gap="0",
                branch_nodes=0,
                solver="greedy-static-baseline",
                problem_hash=problem_hash,
                diagnostics=["Mandatory prerequisite closure exceeds the token budget."],
            )
        selected = set(mandatory)
        candidates = sorted(set(items) - selected)
        while candidates:
            options: list[tuple[Fraction, int, int, str, set[str]]] = []
            for item_id in candidates:
                additions = _closure({item_id}, items) - selected
                cost = _cost(additions, items)
                utility = _utility(additions, items)
                if _cost(selected, items) + cost > problem.token_budget or utility == 0:
                    continue
                options.append((Fraction(utility, cost or 1), utility, cost, item_id, additions))
            if not options:
                break
            _, _, _, _, additions = max(
                options,
                key=lambda item: (item[0], item[1], -item[2], _descending_id_key(item[3])),
            )
            selected.update(additions)
            candidates = [item_id for item_id in candidates if item_id not in selected]
        return PckpSolution(
            status=PckpStatus.BEST_EFFORT,
            selected_item_ids=sorted(selected),
            mandatory_item_ids=sorted(mandatory),
            token_cost=_cost(selected, items),
            utility=_utility(selected, items),
            upper_bound="unavailable",
            optimality_gap="unavailable",
            branch_nodes=0,
            solver="greedy-static-baseline",
            problem_hash=problem_hash,
            diagnostics=["Greedy density baseline; no optimality claim is made."],
        )


def _solve_rooted_forest(
    problem: PckpProblem,
    mandatory: set[str],
    problem_hash: str,
) -> PckpSolution:
    """Exact capacity-indexed DP for a verified rooted forest.

    Edges point from a child to its sole parent/prerequisite.  Every feasible
    retained set is therefore ancestor-closed.  The DP stores only the best
    utility/tie-break selection for each exact cost, then combines root trees.
    """

    items = {item.item_id: item for item in problem.items}
    children: dict[str, list[str]] = {item_id: [] for item_id in items}
    roots: list[str] = []
    for item in items.values():
        if item.prerequisites:
            children[item.prerequisites[0]].append(item.item_id)
        else:
            roots.append(item.item_id)
    for value in children.values():
        value.sort()
    roots.sort()

    def better(
        current: tuple[int, tuple[str, ...]] | None,
        candidate: tuple[int, tuple[str, ...]],
    ) -> tuple[int, tuple[str, ...]]:
        if current is None:
            return candidate
        if candidate[0] > current[0] or (candidate[0] == current[0] and candidate[1] < current[1]):
            return candidate
        return current

    def selected_states(item_id: str) -> dict[int, tuple[int, tuple[str, ...]]]:
        item = items[item_id]
        states: dict[int, tuple[int, tuple[str, ...]]] = {
            item.token_cost: (item.utility, (item_id,))
        }
        for child_id in children[item_id]:
            child_states = selected_states(child_id)
            child_mandatory = child_id in mandatory
            next_states: dict[int, tuple[int, tuple[str, ...]]] = {}
            for left_cost, left in states.items():
                if not child_mandatory:
                    next_states[left_cost] = better(next_states.get(left_cost), left)
                for right_cost, right in child_states.items():
                    total_cost = left_cost + right_cost
                    if total_cost > problem.token_budget:
                        continue
                    candidate = (
                        left[0] + right[0],
                        tuple(sorted((*left[1], *right[1]))),
                    )
                    next_states[total_cost] = better(next_states.get(total_cost), candidate)
            states = _prune_dominated(next_states, better)
        return states

    states: dict[int, tuple[int, tuple[str, ...]]] = {0: (0, ())}
    for root_id in roots:
        root_states = selected_states(root_id)
        root_mandatory = root_id in mandatory
        next_states: dict[int, tuple[int, tuple[str, ...]]] = {}
        for left_cost, left in states.items():
            if not root_mandatory:
                next_states[left_cost] = better(next_states.get(left_cost), left)
            for right_cost, right in root_states.items():
                total_cost = left_cost + right_cost
                if total_cost > problem.token_budget:
                    continue
                candidate = (
                    left[0] + right[0],
                    tuple(sorted((*left[1], *right[1]))),
                )
                next_states[total_cost] = better(next_states.get(total_cost), candidate)
        states = _prune_dominated(next_states, better)

    best: tuple[int, tuple[str, ...]] = max(
        states.values(),
        key=lambda value: (value[0], tuple(-ord(char) for char in "".join(value[1]))),
    )
    # The preceding max handles utility; resolve lexicographic ties explicitly.
    for candidate in states.values():
        if candidate[0] > best[0] or (candidate[0] == best[0] and candidate[1] < best[1]):
            best = candidate
    selected = set(best[1])
    return PckpSolution(
        status=PckpStatus.OPTIMAL,
        selected_item_ids=sorted(selected),
        mandatory_item_ids=sorted(mandatory),
        token_cost=_cost(selected, items),
        utility=best[0],
        upper_bound=str(best[0]),
        optimality_gap="0",
        branch_nodes=0,
        solver="tree-dynamic-program",
        problem_hash=problem_hash,
    )


def _prune_dominated(
    states: dict[int, tuple[int, tuple[str, ...]]],
    better,
) -> dict[int, tuple[int, tuple[str, ...]]]:
    """Discard a state dominated by a no-costlier, higher-utility state."""

    retained: dict[int, tuple[int, tuple[str, ...]]] = {}
    best_prior: tuple[int, tuple[str, ...]] | None = None
    for cost in sorted(states):
        state = states[cost]
        if best_prior is not None and best_prior[0] > state[0]:
            continue
        if best_prior is not None and best_prior[0] == state[0] and best_prior[1] <= state[1]:
            continue
        retained[cost] = state
        best_prior = better(best_prior, state)
    return retained


def _dependents(items: Iterable[PckpItem]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for item in items:
        result.setdefault(item.item_id, set())
        for prerequisite in item.prerequisites:
            result.setdefault(prerequisite, set()).add(item.item_id)
    return result


def _closure(seeds: set[str], items: dict[str, PckpItem]) -> set[str]:
    closure = set(seeds)
    pending = list(sorted(seeds, reverse=True))
    while pending:
        item_id = pending.pop()
        for prerequisite in sorted(items[item_id].prerequisites, reverse=True):
            if prerequisite not in closure:
                closure.add(prerequisite)
                pending.append(prerequisite)
    return closure


def _propagate(
    selected: set[str],
    excluded: set[str],
    items: dict[str, PckpItem],
    dependents: dict[str, set[str]],
) -> tuple[set[str], set[str]] | None:
    selected_now = set(selected)
    excluded_now = set(excluded)
    changed = True
    while changed:
        if selected_now & excluded_now:
            return None
        changed = False
        for item_id in sorted(selected_now):
            for prerequisite in items[item_id].prerequisites:
                if prerequisite not in selected_now:
                    selected_now.add(prerequisite)
                    changed = True
        for item_id in sorted(excluded_now):
            for dependent in sorted(dependents.get(item_id, ())):
                if dependent not in excluded_now:
                    excluded_now.add(dependent)
                    changed = True
    return (selected_now, excluded_now) if not (selected_now & excluded_now) else None


def _fractional_upper_bound(
    selected: set[str],
    excluded: set[str],
    items: dict[str, PckpItem],
    density_order: list[PckpItem],
    budget: int,
) -> Fraction:
    selected_cost = _cost(selected, items)
    total = Fraction(_utility(selected, items))
    remaining = budget - selected_cost
    if remaining < 0:
        return Fraction(-1)
    for item in density_order:
        if item.item_id in selected or item.item_id in excluded:
            continue
        if item.token_cost == 0:
            total += item.utility
        elif item.token_cost <= remaining:
            total += item.utility
            remaining -= item.token_cost
        else:
            total += Fraction(item.utility * remaining, item.token_cost)
            break
    return total


def _cost(selected: Iterable[str], items: dict[str, PckpItem]) -> int:
    return sum(items[item_id].token_cost for item_id in selected)


def _utility(selected: Iterable[str], items: dict[str, PckpItem]) -> int:
    return sum(items[item_id].utility for item_id in selected)


def _is_rooted_forest(items: Iterable[PckpItem]) -> bool:
    return all(len(item.prerequisites) <= 1 for item in items)


def _problem_hash(problem: PckpProblem) -> str:
    import hashlib
    import json

    payload = problem.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _fraction_text(value: Fraction) -> str:
    return (
        str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
    )


def _descending_id_key(value: str) -> tuple[int, ...]:
    """Invert code points so ``max`` chooses the lexicographically first ID."""

    return tuple(-ord(character) for character in value)
