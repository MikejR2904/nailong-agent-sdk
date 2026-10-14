import random
import time

import pytest
from pydantic import ValidationError

from nailong_agent_sdk.foundations.dependency_graph import (
    deterministic_cycles,
    reverse_reachable_count,
    reverse_reachable_nodes,
)
from nailong_agent_sdk.foundations.optimization import (
    ExactPckpSolver,
    GreedyPckpBaseline,
    PckpItem,
    PckpProblem,
    PckpStatus,
)
from nailong_agent_sdk.foundations.optimization.solvers import tie_key


def test_reverse_reachability():
    nodes = ["a", "b", "c", "d"]
    edges = [("b", "a"), ("c", "b"), ("d", "x")]
    assert reverse_reachable_nodes(nodes, edges, "a") == ["b", "c"]
    assert reverse_reachable_nodes(nodes, edges, "missing") == []
    assert reverse_reachable_count(nodes, [("b", "ghost"), ("c", "b")], "ghost") == 2
    assert deterministic_cycles(["a", "b"], [("a", "b"), ("b", "a")]) == [["a", "b", "a"]]
    assert deterministic_cycles(["a"], [("a", "a")]) == [["a", "a"]]


def brute_force(problem: PckpProblem):
    items = {item.item_id: item for item in problem.items}
    ids = sorted(items)
    best = None
    for mask in range(1 << len(ids)):
        selected = {ids[k] for k in range(len(ids)) if mask >> k & 1}
        if any(p not in selected for i in selected for p in items[i].prerequisites):
            continue
        if any(item.mandatory and item.item_id not in selected for item in items.values()):
            continue
        cost = sum(items[i].token_cost for i in selected)
        if cost > problem.token_budget:
            continue
        utility = sum(items[i].utility for i in selected)
        key = (-utility, cost, tie_key(selected), tuple(sorted(selected)))
        if best is None or key < best:
            best = key
    return best


def random_problem(rng: random.Random, *, forest: bool, n: int | None = None) -> PckpProblem:
    n = n or rng.randint(1, 11)
    items = []
    for index in range(n):
        earlier = list(range(index))
        if forest:
            prerequisites = [f"i{rng.choice(earlier)}"] if earlier and rng.random() < 0.7 else []
        else:
            k = min(len(earlier), rng.choice([0, 0, 1, 1, 2, 3]))
            prerequisites = [f"i{p}" for p in rng.sample(earlier, k)]
        items.append(
            PckpItem(
                item_id=f"i{index}",
                token_cost=rng.randint(0, 6),
                utility=rng.randint(0, 9),
                prerequisites=prerequisites,
                mandatory=rng.random() < 0.12,
            )
        )
    return PckpProblem(token_budget=rng.randint(0, 22), items=items)


def check_solution(problem, solution, truth):
    items = {item.item_id: item for item in problem.items}
    if truth is None:
        assert solution.status is PckpStatus.INFEASIBLE_MANDATORY
        return
    assert solution.status is PckpStatus.OPTIMAL, solution
    selected = set(solution.selected_item_ids)
    assert all(p in selected for i in selected for p in items[i].prerequisites), (
        "selection is not prerequisite-closed"
    )
    assert all(item.item_id in selected for item in problem.items if item.mandatory)
    assert solution.token_cost == sum(items[i].token_cost for i in selected) <= problem.token_budget
    assert solution.utility == -truth[0], (solution.utility, truth, problem.model_dump())
    assert solution.token_cost == truth[1]
    assert tuple(solution.selected_item_ids) == truth[3], (solution, truth)


def test_exact_solver_matches_brute_force_on_random_forests_and_dags():
    rng = random.Random(1234)
    for _ in range(500):
        problem = random_problem(rng, forest=rng.random() < 0.5)
        truth = brute_force(problem)
        check_solution(problem, ExactPckpSolver().solve(problem), truth)
        check_solution(
            problem, ExactPckpSolver(enable_tree_dynamic_program=False).solve(problem), truth
        )


def test_dp_and_branch_and_bound_return_the_identical_selection_on_forests():
    rng = random.Random(99)
    for _ in range(400):
        problem = random_problem(rng, forest=True)
        exact = ExactPckpSolver().solve(problem)
        search = ExactPckpSolver(enable_tree_dynamic_program=False).solve(problem)
        assert exact.status is search.status
        if exact.status is PckpStatus.OPTIMAL:
            assert exact.selected_item_ids == search.selected_item_ids
            assert exact.token_cost == search.token_cost <= problem.token_budget


def test_equal_utility_prefers_the_cheaper_selection_then_the_earlier_ids():
    problem = PckpProblem(
        token_budget=100,
        items=[
            PckpItem(item_id="a", token_cost=10, utility=5),
            PckpItem(item_id="b", token_cost=10, utility=0),
            PckpItem(item_id="c", token_cost=0, utility=0),
        ],
    )
    for solver in (ExactPckpSolver(), ExactPckpSolver(enable_tree_dynamic_program=False)):
        solution = solver.solve(problem)
        assert solution.selected_item_ids == ["a", "c"]
        assert solution.token_cost == 10 and solution.utility == 5


def test_a_large_token_budget_does_not_slow_the_solver_down():
    rng = random.Random(5)
    items = [
        PckpItem(
            item_id=f"o{index:03d}",
            token_cost=rng.randint(50, 500),
            utility=rng.randint(0, 20),
        )
        for index in range(300)
    ]
    timings = {}
    utilities = {}
    for budget in (20_000, 50_000, 100_000):
        started = time.perf_counter()
        solution = ExactPckpSolver().solve(PckpProblem(token_budget=budget, items=items))
        timings[budget] = time.perf_counter() - started
        utilities[budget] = solution.utility
        assert solution.status is PckpStatus.OPTIMAL
    assert max(timings.values()) < 15, timings
    assert utilities[100_000] == sum(item.utility for item in items)


def test_greedy_is_feasible_and_never_beats_exact():
    rng = random.Random(7)
    for _ in range(300):
        problem = random_problem(rng, forest=False)
        exact = ExactPckpSolver().solve(problem)
        greedy = GreedyPckpBaseline().solve(problem)
        if exact.status is PckpStatus.INFEASIBLE_MANDATORY:
            assert greedy.status is PckpStatus.INFEASIBLE_MANDATORY
            continue
        items = {item.item_id: item for item in problem.items}
        selected = set(greedy.selected_item_ids)
        assert all(p in selected for i in selected for p in items[i].prerequisites)
        assert greedy.token_cost <= problem.token_budget and greedy.utility <= exact.utility


def test_branch_node_limit_yields_best_effort_never_optimal():
    rng = random.Random(5)
    problem = random_problem(rng, forest=False, n=14)
    bounded = ExactPckpSolver(max_branch_nodes=3, enable_tree_dynamic_program=False).solve(problem)
    full = ExactPckpSolver(enable_tree_dynamic_program=False).solve(problem)
    if bounded.status is PckpStatus.BEST_EFFORT:
        assert bounded.utility <= full.utility and bounded.diagnostics
    else:
        assert bounded.utility == full.utility


def test_problem_validation_errors():
    with pytest.raises(ValidationError, match="unknown prerequisites"):
        PckpProblem(
            token_budget=1,
            items=[PckpItem(item_id="a", token_cost=1, utility=1, prerequisites=["z"])],
        )
    with pytest.raises(ValidationError, match="cycle"):
        PckpProblem(
            token_budget=1,
            items=[
                PckpItem(item_id="a", token_cost=1, utility=1, prerequisites=["b"]),
                PckpItem(item_id="b", token_cost=1, utility=1, prerequisites=["a"]),
            ],
        )
    with pytest.raises(ValidationError):
        PckpItem(item_id="a", token_cost=1, utility=1, prerequisites=["a"])


@pytest.mark.parametrize("length", [900, 1100, 3000])
def test_long_prerequisite_chains_do_not_raise_recursion_error(length):
    items = [
        PckpItem(
            item_id=f"i{index:05d}",
            token_cost=1,
            utility=1,
            prerequisites=[f"i{index - 1:05d}"] if index else [],
        )
        for index in range(length)
    ]
    try:
        problem = PckpProblem(token_budget=length // 2, items=items)
        solution = ExactPckpSolver().solve(problem)
    except RecursionError as error:
        pytest.fail(f"RecursionError for a chain of {length} items: {error}")
    assert solution.utility == length // 2


def test_wide_general_dag_at_the_branch_node_cap_returns_promptly():
    rng = random.Random(3)
    problem = random_problem(rng, forest=False, n=1200)
    start = time.perf_counter()
    try:
        solution = ExactPckpSolver(max_branch_nodes=2000).solve(problem)
    except RecursionError as error:
        pytest.fail(f"RecursionError on a 1200-item DAG: {error}")
    assert time.perf_counter() - start < 60
    assert solution.status in (
        PckpStatus.OPTIMAL,
        PckpStatus.BEST_EFFORT,
        PckpStatus.INFEASIBLE_MANDATORY,
    )
