import os
import random
import time
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from nailong_agent_sdk.foundations import atomic_io
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    AgentFailure,
    AgentTurn,
    ModelBinding,
    RuntimeOptions,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    ToolDefinition,
    VersionedInstructions,
    validate_candidate_output,
    validate_task_input,
    validate_tool_arguments,
)
from nailong_agent_sdk.foundations.dependency_graph import (
    deterministic_cycles,
    reverse_reachable_count,
    reverse_reachable_nodes,
)
from nailong_agent_sdk.foundations.errors import (
    AgentSdkError,
    TransientProviderError,
    assert_no_hidden_reasoning,
    redact_secrets,
    sanitize_failure_details,
)
from nailong_agent_sdk.foundations.optimization import (
    ExactPckpSolver,
    GreedyPckpBaseline,
    PckpItem,
    PckpProblem,
    PckpStatus,
)
from nailong_agent_sdk.foundations.optimization.solvers import tie_key

TURN = TypeAdapter(AgentTurn)


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "API-KEY",
        "apikey",
        "Authorization",
        "password",
        "client_secret",
        "access_token",
        "refresh_token",
        "cookie",
        "credential",
        "private_key",
        "token",
    ],
)
def test_secret_looking_keys_are_redacted(key):
    assert redact_secrets({key: "value"}) == {key: "[REDACTED]"}


@pytest.mark.parametrize(
    "key", ["token_budget", "input_tokens", "max_tokens", "total_tokens", "token_cost"]
)
def test_token_count_keys_are_not_redacted(key):
    assert redact_secrets({key: 5}) == {key: 5}


@pytest.mark.parametrize(
    "secret",
    [
        "sk-" + "A" * 24,
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_" + "a" * 36,
        "xoxb-1234567890-abcdefghij",
        "Bearer abcdefghijklmnop12345",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_known_credential_shapes_are_masked_in_free_text(secret):
    text = f"before {secret} after"
    redacted = redact_secrets(text)
    assert secret not in redacted
    assert redacted.startswith("before ") and redacted.endswith(" after")


@pytest.mark.parametrize(
    "text, leaked",
    [
        ("FIREWORKS_API_KEY=fw_abcdefghijklmnop1234", "fw_abcdefghijklmnop1234"),
        ('{"api_key": "abcdef123456"}', "abcdef123456"),
        ("password: hunter2hunter2", "hunter2hunter2"),
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG", "wJalrXUtnFEMI"),
    ],
)
def test_assignment_style_secrets_are_masked(text, leaked):
    assert leaked not in redact_secrets(text)


def test_non_secret_text_is_untouched():
    text = "The token budget is 4000 tokens; status = ok; key points follow."
    assert redact_secrets(text) == text


def test_sanitize_failure_details_bounds_and_masks():
    out = sanitize_failure_details(
        {"api_key": "x" * 10, "chain_of_thought": "secret plan", "note": "ok"}
    )
    assert (
        out["api_key"] == "[REDACTED]"
        and out["chain_of_thought"] == "[REDACTED]"
        and out["note"] == "ok"
    )
    big = sanitize_failure_details({"blob": "z" * 10_000})
    assert big["truncated"] is True and "content_hash" in big and big["original_chars"] > 2048


def test_assert_no_hidden_reasoning_nested():
    assert_no_hidden_reasoning({"a": [{"b": 1}]})
    with pytest.raises(ValueError):
        assert_no_hidden_reasoning({"a": [{"scratchpad": "x"}]})


def test_error_types():
    error = TransientProviderError("C", "boom", retry_after_seconds=2.0)
    assert (
        str(error) == "boom"
        and isinstance(error, AgentSdkError)
        and error.retry_after_seconds == 2.0
    )


def test_redaction_is_linear_on_adversarial_private_key_markers(run_py):
    code = """
    import time
    from nailong_agent_sdk.foundations.errors import redact_secrets
    for n in (1000, 2000, 4000):
        text = "-----BEGIN PRIVATE KEY-----" * n
        start = time.perf_counter()
        redact_secrets(text)
        print(n, len(text), round(time.perf_counter() - start, 3))
    """
    result = run_py(code, timeout=300)
    rows = [line.split() for line in result.stdout.strip().splitlines()]
    t1, t2, t3 = (float(row[2]) for row in rows)
    assert t3 < 8 * max(t1, 0.01) or t3 < 1.0, f"timings {t1}, {t2}, {t3}"


def test_redaction_is_linear_on_long_identifier_runs(run_py):
    code = """
    import time
    from nailong_agent_sdk.foundations.errors import redact_secrets
    for n in (5000, 10000, 20000):
        text = "password" * n
        start = time.perf_counter()
        redact_secrets(text)
        print(n, len(text), round(time.perf_counter() - start, 3))
    """
    result = run_py(code, timeout=300)
    rows = [line.split() for line in result.stdout.strip().splitlines()]
    t1, t2, t3 = (float(row[2]) for row in rows)
    assert t3 < 8 * max(t1, 0.01) or t3 < 1.0, f"timings {t1}, {t2}, {t3}"


def test_replace_atomic_replaces_and_retries_permission_errors(tmp_path, monkeypatch):
    target = tmp_path / "t.txt"
    target.write_text("old")
    temporary = tmp_path / ".t.tmp"
    temporary.write_text("new")
    real = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("busy")
        return real(src, dst)

    monkeypatch.setattr(atomic_io.os, "replace", flaky)
    atomic_io.replace_atomic(temporary, target)
    assert target.read_text() == "new" and calls["n"] == 3


def test_replace_atomic_gives_up_and_validates_attempts(tmp_path, monkeypatch):
    temporary = tmp_path / "a"
    temporary.write_text("x")
    monkeypatch.setattr(
        atomic_io.os, "replace", lambda *_: (_ for _ in ()).throw(PermissionError("busy"))
    )
    with pytest.raises(PermissionError):
        atomic_io.replace_atomic(temporary, tmp_path / "b", attempts=2)
    with pytest.raises(ValueError):
        atomic_io.replace_atomic(temporary, tmp_path / "b", attempts=0)


def _definition(**overrides):
    base = dict(
        identity="agent",
        instructions=VersionedInstructions(version="v1", text="do"),
        input_schema={"type": "object"},
        model_binding=ModelBinding(provider="p", model="m"),
        output_schema={
            "type": "object",
            "required": ["status"],
            "properties": {"status": {"type": "string"}},
        },
        termination_policy=TerminationPolicy(max_iterations=3, status_field="status"),
    )
    base.update(overrides)
    return AgentDefinition(**base)


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        VersionedInstructions(version="v", text="t", extra="x")


def test_definition_invariants():
    tool = ToolDefinition(
        name="t", description="d", input_schema={"type": "object"}, episode_kind="exploratory"
    )
    with pytest.raises(ValidationError, match="tool names must be unique"):
        _definition(tools=[tool, tool])
    with pytest.raises(ValidationError, match="memory_rationale"):
        _definition(memory_scope="cross-session")
    with pytest.raises(ValidationError, match="single line"):
        _definition(identity="a\nb")
    with pytest.raises(ValidationError, match="Draft 2020-12"):
        _definition(output_schema={"type": 123})
    with pytest.raises(ValidationError, match="distinct"):
        ModelBinding(provider="p", model="m", fallbacks=[{"provider": "p", "model": "m"}])


def test_schema_validation_errors_name_the_path():
    definition = _definition()
    task = ScopedAgentTask(
        id="t",
        input={},
        scope=TaskScope(label="s"),
        locked_interface={},
        instructions="i",
        acceptance_criteria=["c"],
    )
    validate_task_input(definition, task)
    with pytest.raises(AgentSdkError) as caught:
        validate_candidate_output(definition, {"status": 5})
    assert caught.value.code == "SCHEMA_VALIDATION_FAILED" and "$.status" in caught.value.message
    tool = ToolDefinition(
        name="t",
        description="d",
        input_schema={"type": "object", "required": ["x"]},
        episode_kind="action",
    )
    with pytest.raises(AgentSdkError, match='tool "t" arguments'):
        validate_tool_arguments(tool, {})


def test_non_json_schema_values_are_honored():
    schema = {"type": "number", "const": Decimal("1.5")}
    definition = _definition(output_schema=schema)
    validate_candidate_output(definition, Decimal("1.5"))
    with pytest.raises(AgentSdkError):
        validate_candidate_output(definition, Decimal("2.5"))


def test_turn_discriminator_and_batch_rules():
    assert TURN.validate_python({"type": "final", "output": 1}).type == "final"
    with pytest.raises(ValidationError):
        TURN.validate_python({"type": "nonsense"})
    with pytest.raises(ValidationError, match="acyclic"):
        TURN.validate_python(
            {
                "type": "tool-batch",
                "calls": [
                    {"id": "a", "name": "t", "depends_on_call_ids": ["b"]},
                    {"id": "b", "name": "t", "depends_on_call_ids": ["a"]},
                ],
            }
        )
    with pytest.raises(ValidationError, match="unknown batch call IDs"):
        TURN.validate_python(
            {
                "type": "tool-batch",
                "calls": [{"id": "a", "name": "t", "depends_on_call_ids": ["zz"]}],
            }
        )
    with pytest.raises(ValidationError, match="unique"):
        TURN.validate_python(
            {"type": "tool-batch", "calls": [{"id": "a", "name": "t"}, {"id": "a", "name": "t"}]}
        )
    with pytest.raises(ValidationError, match="tool-batch"):
        TURN.validate_python(
            {"type": "tool-call", "call": {"id": "a", "name": "t", "depends_on_call_ids": ["b"]}}
        )


@pytest.mark.parametrize("length", [500, 990, 1100, 3000])
def test_tool_batch_chain_of_any_reasonable_length_is_validated_without_recursion_error(length):
    calls = [
        {"id": f"c{i}", "name": "t", "depends_on_call_ids": [f"c{i - 1}"] if i else []}
        for i in range(length)
    ]
    try:
        turn = TURN.validate_python({"type": "tool-batch", "calls": calls})
    except RecursionError as error:
        pytest.fail(f"RecursionError for a {length}-call dependency chain: {error}")
    assert len(turn.calls) == length


def test_runtime_options_bounds():
    RuntimeOptions(run_deadline_seconds=1, context_token_budget=256)
    for bad in (
        {"run_deadline_seconds": 0},
        {"context_token_budget": 10},
        {"tool_result_preview_chars": 1},
        {"mode": "live"},
    ):
        with pytest.raises(ValidationError):
            RuntimeOptions(**bad)


def test_agent_failure_details_are_sanitized():
    failure = AgentFailure(code="X", message="m", details={"password": "p", "ok": 1})
    assert failure.details["password"] == "[REDACTED]"


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
