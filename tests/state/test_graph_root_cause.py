import pytest

from nailong_agent_sdk.state.graph import MAX_ROOT_CAUSE_DIAGNOSTICS, StateGraph
from nailong_agent_sdk.state.graph_models import (
    GraphEdge,
    GraphEdgeKind,
    GraphNodeResult,
    GraphNodeStatus,
)
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.planning import Plan
from tests.support.plans import A, B, C, F, K, P, arun, n, proof
from tests.support.plans import task as plan_task


def outcome_by_node(failing=(), blocking=()):
    async def execute(node, context):
        if node.node_id in failing:
            return GraphNodeResult(status=F, reason=f"{node.node_id} raised ValueError: bad input")
        if node.node_id in blocking:
            return GraphNodeResult(status=B, reason=f"{node.node_id} needs an approval")
        return GraphNodeResult(status=C, output={"node": node.node_id})

    return execute


def chain(length, prefix="N"):
    nodes = [n(f"{prefix}0")]
    nodes += [n(f"{prefix}{index}", [f"{prefix}{index - 1}"]) for index in range(1, length)]
    return nodes


def test_every_node_held_up_by_a_failure_points_at_the_node_that_failed():
    graph = StateGraph([n("A"), n("B", ["A"]), n("C", ["B"]), n("D", ["C"]), n("E")])
    results = arun(graph.execute({A: outcome_by_node(failing={"A"})}))
    assert results["A"].root_causes == () and results["E"].status is C
    for blocked in ("B", "C", "D"):
        assert results[blocked].status is B
        assert results[blocked].diagnostics == ["blocked-by-dependency", "root-cause:A"]
    assert results["D"].reason == 'Dependency "C" did not complete.'
    assert graph.root_causes("D") == ("A",)
    assert graph.root_causes("A") == () and graph.root_causes("E") == ()
    assert graph.blocked_descendants("A") == ("B", "C", "D")
    assert graph.blocked_descendants("E") == ()


def test_a_node_held_up_by_two_failures_names_both_roots_in_order():
    graph = StateGraph(
        [n("A1"), n("A2"), n("B", ["A1"]), n("C", ["A2"]), n("D", ["B", "C"]), n("OK")]
    )
    results = arun(graph.execute({A: outcome_by_node(failing={"A2", "A1"})}))
    assert results["D"].root_causes == ("A1", "A2")
    assert graph.root_causes("D") == ("A1", "A2")
    assert graph.blocked_descendants("A1") == ("B", "D")
    assert graph.blocked_descendants("A2") == ("C", "D")


def test_a_node_blocked_by_its_own_result_is_the_root_of_what_waits_on_it():
    graph = StateGraph([n("gate"), n("next", ["gate"]), n("last", ["next"])])
    results = arun(graph.execute({A: outcome_by_node(blocking={"gate"})}))
    assert results["gate"].diagnostics == []
    assert results["last"].diagnostics == ["blocked-by-dependency", "root-cause:gate"]
    assert graph.blocked_nodes() == ["gate"]
    assert graph.root_causes("last") == ("gate",)


def test_a_join_that_waits_for_a_blocked_fan_in_parent_names_that_parent():
    graph = StateGraph(
        [n("left"), n("right"), n("join")],
        [
            GraphEdge(parent_node_id="left", child_node_id="join", kind=GraphEdgeKind.FAN_IN),
            GraphEdge(parent_node_id="right", child_node_id="join", kind=GraphEdgeKind.FAN_IN),
        ],
    )
    results = arun(graph.execute({A: outcome_by_node(blocking={"right"})}))
    assert results["join"].status is B
    assert results["join"].diagnostics == ["blocked-by-dependency", "root-cause:right"]
    assert graph.root_causes("join") == ("right",)


def test_a_failed_fan_in_parent_does_not_block_the_join_so_nothing_is_attributed():
    graph = StateGraph(
        [n("left"), n("right"), n("join")],
        [
            GraphEdge(parent_node_id="left", child_node_id="join", kind=GraphEdgeKind.FAN_IN),
            GraphEdge(parent_node_id="right", child_node_id="join", kind=GraphEdgeKind.FAN_IN),
        ],
    )
    results = arun(graph.execute({A: outcome_by_node(failing={"left"})}))
    assert results["join"].status is C and results["join"].root_causes == ()


def test_more_roots_than_the_cap_are_counted_not_listed():
    roots = [f"F{index:02d}" for index in range(MAX_ROOT_CAUSE_DIAGNOSTICS + 2)]
    graph = StateGraph([n(root) for root in roots] + [n("J", roots)])
    results = arun(graph.execute({A: outcome_by_node(failing=set(roots))}))
    listed = results["J"].root_causes
    assert listed == tuple(roots[:MAX_ROOT_CAUSE_DIAGNOSTICS])
    assert results["J"].root_causes_omitted == 2
    assert results["J"].diagnostics[-1] == "root-causes-omitted:2"
    assert graph.root_causes("J") == tuple(roots)


def test_a_very_long_chain_is_attributed_without_recursion_or_a_rescan_per_node():
    nodes = chain(1_500)
    graph = StateGraph(nodes)
    results = arun(graph.execute({A: outcome_by_node(failing={"N0"})}))
    assert results["N1499"].root_causes == ("N0",)
    assert len(graph.blocked_descendants("N0")) == 1_499


def test_a_snapshot_without_the_diagnostic_is_attributed_by_walking_the_graph():
    graph = StateGraph([n("A"), n("B", ["A"]), n("C", ["B"])])
    arun(graph.execute({A: outcome_by_node(failing={"A"})}))
    snapshot = graph.snapshot()
    for result in snapshot["results"].values():
        result["diagnostics"] = [
            item for item in result["diagnostics"] if not item.startswith("root-cause:")
        ]
    restored = StateGraph.from_snapshot(snapshot)
    assert restored.root_causes("C") == ("A",)
    assert restored.blocked_descendants("A") == ("B", "C")
    assert restored.result("C").diagnostics == ["blocked-by-dependency", "root-cause:A"]
    assert restored.snapshot()["results"]["B"]["diagnostics"] == [
        "blocked-by-dependency",
        "root-cause:A",
    ]


def test_a_restored_graph_keeps_the_recorded_roots():
    graph = StateGraph([n("A"), n("B", ["A"])])
    arun(graph.execute({A: outcome_by_node(failing={"A"})}))
    restored = StateGraph.from_snapshot(graph.snapshot())
    assert restored.result("B").diagnostics == ["blocked-by-dependency", "root-cause:A"]
    assert restored.root_causes("B") == ("A",)


def test_reopening_the_root_clears_the_attribution_and_a_second_failure_sets_it_again():
    graph = StateGraph([n("gate"), n("next", ["gate"])])
    arun(graph.execute({A: outcome_by_node(blocking={"gate"})}))
    assert graph.result("next").root_causes == ("gate",)
    assert graph.reopen_blocked(["gate"]) == ["gate"]
    assert graph.status("next") is GraphNodeStatus.PENDING and graph.result("next") is None
    arun(graph.execute({A: outcome_by_node(failing={"gate"})}))
    assert graph.status("gate") is GraphNodeStatus.FAILED
    assert graph.result("next").diagnostics == ["blocked-by-dependency", "root-cause:gate"]
    assert graph.root_causes("next") == ("gate",)


def test_asking_about_a_node_that_is_not_in_the_graph_names_it():
    graph = StateGraph([n("A")])
    with pytest.raises(ValueError, match='Node "ghost" is not in the graph.'):
        graph.root_causes("ghost")
    with pytest.raises(ValueError, match='Node "ghost" is not in the graph.'):
        graph.blocked_descendants("ghost")


def test_the_result_reads_its_typed_diagnostics_back():
    result = GraphNodeResult(
        status=F,
        reason="x",
        diagnostics=[
            "agent-status:failed",
            "agent-run:task-9",
            "failure-code:MODEL_TURN_INVALID",
            "root-cause:A",
            "root-cause:B",
            "root-causes-omitted:3",
        ],
    )
    assert result.failure_code == "MODEL_TURN_INVALID"
    assert result.agent_run_id == "task-9"
    assert result.root_causes == ("A", "B") and result.root_causes_omitted == 3
    bare = GraphNodeResult(status=C)
    assert bare.failure_code is None and bare.agent_run_id is None
    assert bare.root_causes == () and bare.root_causes_omitted == 0


def test_an_executor_that_is_missing_or_raises_leaves_a_code_on_the_failed_result():
    graph = StateGraph([n("A"), n("B")])

    async def raises(node, context):
        raise ValueError("no input")

    results = arun(graph.execute({A: raises}))
    for node_id in ("A", "B"):
        assert results[node_id].failure_code == "GRAPH_NODE_EXECUTOR_RAISED"
        assert "error-type:ValueError" in results[node_id].diagnostics
    missing = StateGraph([n("A")])
    absent = arun(missing.execute({}))
    assert absent["A"].failure_code == "GRAPH_NODE_EXECUTOR_MISSING"


def test_a_saved_run_holds_every_root_of_a_node_blocked_before_its_other_parents_failed(tmp_path):
    plan = Plan(
        plan_id="join",
        tasks=[
            plan_task("T1", ["a"], [("a", P)]),
            plan_task("T2", ["b"], [("b", P)]),
            plan_task("T3", ["a", "b"], [("a", K), ("b", K)], deps=["T1", "T2"]),
        ],
        dependency_proofs=[proof("T1", "T3", ["a"]), proof("T2", "T3", ["b"])],
    )
    coordinator = HarnessCoordinator(tmp_path)
    started = coordinator.start_run(plan)

    async def execute(node, context):
        return GraphNodeResult(status=F, reason=f"{node.node_id} raised ValueError: bad input")

    arun(coordinator.execute_run(started.run_id, {A: execute}))
    saved = HarnessCoordinator(tmp_path).get_run_state(started.run_id).graph
    assert saved["results"]["node:T3"]["diagnostics"] == [
        "blocked-by-dependency",
        "root-cause:node:T1",
        "root-cause:node:T2",
    ]
