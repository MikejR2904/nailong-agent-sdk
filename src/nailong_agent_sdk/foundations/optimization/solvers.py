# Copyright (c) 2026 David Michael Indraputra

"""Prerequisite-Constrained Knapsack solvers for context retention.

When an agent's context grows past its budget, something has to be evicted
and something has to be retained. This is called the
Prerequisite-Constrained Knapsack Problem (PCKP). It looks
like a standard 0/1 knapsack (maximize a utility sum subject to a cost
budget) with one addition: items may declare prerequisites. Selecting
an item requires selecting its entire prerequisite closure. Some items
may also be marked mandatory, meaning their closure must be selected
regardless of budget. If the mandatory closure does not fit, the problem
is infeasible.

This module provides two solvers:
- ExactPckpSolver: finds the optimal set (using tree DP for simple cases, or
  branch-and-bound search for general DAGs). Always deterministic.
- GreedyPckpBaseline: a simpler density-first heuristic, used for comparison.

Both are deterministic (no randomness, no model calls) so the same input always
produces the same selection. They are used for episode retention and evidence
packing (deciding which pieces can fit within the limited context window), but
the underlying problem is common.

Credit to: https://dlnext.acm.org/doi/10.5555/3182683.3183062 and
https://www.sciencedirect.com/science/article/abs/pii/S0377221706010642
"""

from __future__ import annotations

from collections.abc import Iterable
from fractions import Fraction

from ..hashing import sha256_hex, strict_canonical_json
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
            and _frontier_bound(problem) <= self._max_tree_token_budget
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
        incumbent_cost = _cost(incumbent_selection, items)
        branch_nodes = 0
        exhausted = True
        root_upper = _fractional_upper_bound(
            incumbent_selection, set(), items, density_order, problem.token_budget
        )

        stack: list[tuple[set[str], set[str]]] = [(set(mandatory), set())]
        while stack:
            selected, excluded = stack.pop()
            if self._max_branch_nodes is not None and branch_nodes >= self._max_branch_nodes:
                exhausted = False
                break
            branch_nodes += 1
            normalized = _propagate(selected, excluded, items, dependents)
            if normalized is None:
                continue
            selected_now, excluded_now = normalized
            selected_cost = _cost(selected_now, items)
            if selected_cost > problem.token_budget:
                continue
            upper = _fractional_upper_bound(
                selected_now, excluded_now, items, density_order, problem.token_budget
            )
            if upper < incumbent_utility:
                continue
            if upper == incumbent_utility and selected_cost > incumbent_cost:
                continue
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
                    and (selected_cost, tie_key(selected_now))
                    < (incumbent_cost, tie_key(incumbent_selection))
                ):
                    incumbent_selection = selected_now
                    incumbent_utility = candidate_utility
                    incumbent_cost = selected_cost
                continue
            stack.append(({*selected_now, undecided}, set(excluded_now)))
            stack.append((set(selected_now), {*excluded_now, undecided}))

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


def tie_key(selected: Iterable[str]) -> tuple[tuple[int, str], ...]:
    return (*((0, item_id) for item_id in sorted(selected)), (1, ""))


class _Selection:
    __slots__ = ("_ids", "_key", "_left", "_right")

    def __init__(
        self,
        ids: tuple[str, ...] = (),
        left: _Selection | None = None,
        right: _Selection | None = None,
    ) -> None:
        self._ids = ids
        self._left = left
        self._right = right
        self._key: tuple[tuple[int, str], ...] | None = None

    @classmethod
    def join(cls, left: _Selection, right: _Selection) -> _Selection:
        if left._left is None and not left._ids:
            return right
        if right._left is None and not right._ids:
            return left
        return cls(left=left, right=right)

    @property
    def ids(self) -> tuple[str, ...]:
        collected: list[str] = []
        pending = [self]
        while pending:
            node = pending.pop()
            if node._left is None or node._right is None:
                collected.extend(node._ids)
            else:
                pending.append(node._right)
                pending.append(node._left)
        return tuple(collected)

    @property
    def key(self) -> tuple[tuple[int, str], ...]:
        if self._key is None:
            self._key = tie_key(self.ids)
        return self._key


_States = dict[int, tuple[int, _Selection]]


def _solve_rooted_forest(
    problem: PckpProblem,
    mandatory: set[str],
    problem_hash: str,
) -> PckpSolution:
    """Exact Pareto-frontier DP for a verified rooted forest.

    Edges point from a child to its sole parent/prerequisite. Every feasible
    retained set is therefore ancestor-closed. Each subtree keeps only the
    states no cheaper state matches in utility, so the table is bounded by the
    number of distinct utilities rather than by the token budget. Among equal
    utilities the cheaper selection wins, then the lexicographically smaller one.
    """

    items = {item.item_id: item for item in problem.items}
    budget = problem.token_budget
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

    def subtree_states(root_id: str) -> _States:
        preorder: list[str] = []
        pending = [root_id]
        while pending:
            node_id = pending.pop()
            preorder.append(node_id)
            pending.extend(children[node_id])
        computed: dict[str, _States] = {}
        for item_id in reversed(preorder):
            item = items[item_id]
            states: _States = {item.token_cost: (item.utility, _Selection((item_id,)))}
            for child_id in children[item_id]:
                states = _merge_states(
                    states, computed.pop(child_id), budget, keep_left=child_id not in mandatory
                )
            computed[item_id] = states
        return computed.pop(root_id)

    states: _States = {0: (0, _Selection(()))}
    for root_id in roots:
        states = _merge_states(
            states, subtree_states(root_id), budget, keep_left=root_id not in mandatory
        )

    best_utility, best_selection = states[max(states)]
    selected = set(best_selection.ids)
    return PckpSolution(
        status=PckpStatus.OPTIMAL,
        selected_item_ids=sorted(selected),
        mandatory_item_ids=sorted(mandatory),
        token_cost=_cost(selected, items),
        utility=best_utility,
        upper_bound=str(best_utility),
        optimality_gap="0",
        branch_nodes=0,
        solver="tree-dynamic-program",
        problem_hash=problem_hash,
    )


def _offer(states: _States, cost: int, utility: int, selection: _Selection) -> None:
    existing = states.get(cost)
    if (
        existing is None
        or utility > existing[0]
        or (utility == existing[0] and selection.key < existing[1].key)
    ):
        states[cost] = (utility, selection)


def _merge_states(left: _States, right: _States, budget: int, *, keep_left: bool) -> _States:
    merged: _States = {}
    for left_cost, (left_utility, left_selection) in left.items():
        if keep_left:
            _offer(merged, left_cost, left_utility, left_selection)
        for right_cost, (right_utility, right_selection) in right.items():
            total_cost = left_cost + right_cost
            if total_cost > budget:
                continue
            utility = left_utility + right_utility
            existing = merged.get(total_cost)
            if existing is not None and existing[0] > utility:
                continue
            _offer(merged, total_cost, utility, _Selection.join(left_selection, right_selection))
    return _prune_dominated(merged)


def _prune_dominated(states: _States) -> _States:
    retained: _States = {}
    best_utility = -1
    for cost in sorted(states):
        utility, selection = states[cost]
        if utility > best_utility:
            retained[cost] = (utility, selection)
            best_utility = utility
    return retained


def _frontier_bound(problem: PckpProblem) -> int:
    total_cost = sum(item.token_cost for item in problem.items)
    total_utility = sum(item.utility for item in problem.items)
    return min(problem.token_budget, total_cost, total_utility + 1)


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
    return sha256_hex(strict_canonical_json(problem.model_dump(mode="json")))


def _fraction_text(value: Fraction) -> str:
    return (
        str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
    )


def _descending_id_key(value: str) -> tuple[int, ...]:
    """Invert code points so ``max`` chooses the lexicographically first ID."""

    return tuple(-ord(character) for character in value)
