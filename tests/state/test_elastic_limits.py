import asyncio
import json

import pytest

from nailong_agent_sdk.state.elastic import (
    MAX_ELASTIC_DEPTH_LIMIT,
    MAX_ELASTIC_NODES_LIMIT,
    ElasticCapacity,
    ElasticRefusalCode,
    GraphSpawnStatus,
)
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphNodeKind, GraphNodeStatus
from tests.support.elastic import done_with, failed, req, statuses
from tests.support.plans import AGENT, arun, n, ok

ELASTIC = GraphNodeKind.ELASTIC
COMPLETED = GraphNodeStatus.COMPLETED
FAILED = GraphNodeStatus.FAILED
PENDING = GraphNodeStatus.PENDING
RUNNABLE = GraphNodeStatus.RUNNABLE
RUNNING = GraphNodeStatus.RUNNING
BLOCKED = GraphNodeStatus.BLOCKED


def complete_with(graph, node_id, result):
    graph.mark_started(node_id)
    graph.mark_terminal(node_id, result)


def roundtrip(graph):
    return StateGraph.from_snapshot(json.loads(json.dumps(graph.snapshot())))


def test_a_grant_can_never_pass_the_ceilings_the_run_was_given():
    graph = StateGraph(
        [n("root")],
        max_elastic_depth=1,
        max_elastic_nodes=3,
        elastic_depth_ceiling=2,
        elastic_nodes_ceiling=5,
    )
    with pytest.raises(ValueError, match="max_elastic_nodes 6: it is above the ceiling 5"):
        graph.grant_elastic_capacity(max_elastic_nodes=6, reason="more")
    with pytest.raises(ValueError, match="max_elastic_depth 3: it is above the ceiling 2"):
        graph.grant_elastic_capacity(max_elastic_depth=3, reason="deeper")
    assert graph.capacity_grants == ()
    grant = graph.grant_elastic_capacity(
        max_elastic_depth=2, max_elastic_nodes=5, reason="up to the ceiling"
    )
    assert (grant.max_depth, grant.max_nodes) == (2, 5)
    with pytest.raises(ValueError, match="max_elastic_nodes 6: it is above the ceiling 5"):
        graph.grant_elastic_capacity(max_elastic_nodes=6, reason="again")
    assert len(graph.capacity_grants) == 1


def test_ceilings_default_to_the_hard_limits_and_are_validated():
    snapshot = StateGraph([n("a")]).snapshot()
    assert snapshot["elastic_depth_ceiling"] == MAX_ELASTIC_DEPTH_LIMIT
    assert snapshot["elastic_nodes_ceiling"] == MAX_ELASTIC_NODES_LIMIT
    with pytest.raises(ValueError, match="max_elastic_nodes 5 is above the ceiling 4"):
        StateGraph([n("a")], max_elastic_nodes=5, elastic_nodes_ceiling=4)
    with pytest.raises(ValueError, match="max_elastic_depth 2 is above the ceiling 1"):
        StateGraph([n("a")], max_elastic_depth=2, elastic_depth_ceiling=1)
    with pytest.raises(ValueError, match="elastic_nodes_ceiling must be between 0 and"):
        StateGraph([n("a")], elastic_nodes_ceiling=MAX_ELASTIC_NODES_LIMIT + 1)
    with pytest.raises(ValueError, match="elastic_depth_ceiling must be between 0 and"):
        StateGraph([n("a")], elastic_depth_ceiling=-1)


def test_the_snapshot_round_trips_ceilings_and_older_snapshots_get_the_hard_limits():
    graph = StateGraph(
        [n("root")],
        max_elastic_depth=1,
        max_elastic_nodes=3,
        elastic_depth_ceiling=2,
        elastic_nodes_ceiling=6,
    )
    restored = roundtrip(graph)
    assert restored.snapshot()["elastic_depth_ceiling"] == 2
    assert restored.snapshot()["elastic_nodes_ceiling"] == 6
    with pytest.raises(ValueError, match="max_elastic_nodes 7: it is above the ceiling 6"):
        restored.grant_elastic_capacity(max_elastic_nodes=7, reason="x")
    legacy = json.loads(json.dumps(graph.snapshot()))
    legacy.pop("elastic_depth_ceiling")
    legacy.pop("elastic_nodes_ceiling")
    older = StateGraph.from_snapshot(legacy)
    assert older.snapshot()["elastic_depth_ceiling"] == MAX_ELASTIC_DEPTH_LIMIT
    assert older.snapshot()["elastic_nodes_ceiling"] == MAX_ELASTIC_NODES_LIMIT


def test_the_execution_context_reports_the_ceilings():
    graph = StateGraph(
        [n("root")],
        max_elastic_depth=1,
        max_elastic_nodes=3,
        elastic_depth_ceiling=2,
        elastic_nodes_ceiling=5,
    )
    graph.mark_started("root")
    assert graph.execution_context("root").elastic_capacity == ElasticCapacity(
        node_depth=0,
        max_depth=1,
        nodes_used=0,
        max_nodes=3,
        ceiling_depth=2,
        ceiling_nodes=5,
    )


def test_a_batch_no_grant_could_ever_fit_is_refused_instead_of_held():
    graph = StateGraph(
        [n("root"), n("after", ["root"])],
        max_elastic_depth=1,
        max_elastic_nodes=3,
        elastic_nodes_ceiling=3,
    )
    complete_with(graph, "root", done_with(req("a"), req("b"), req("c")))
    assert sorted(graph.nodes) == ["after", "root"]
    assert [(r.status, r.code) for r in graph.spawn_records] == [
        (GraphSpawnStatus.REFUSED, ElasticRefusalCode.NODE_CEILING_REACHED)
    ] * 3
    reason = graph.spawn_records[0].reason
    assert "ceiling" in reason and "max_elastic_nodes 3" in reason and '"root"' in reason
    assert graph.spawn_records[0].join_node_id is None
    assert graph.status("after") is RUNNABLE
    assert graph.result("root").diagnostics == [
        f"elastic-refused:ELASTIC_NODE_CEILING_REACHED:{request_id}" for request_id in "abc"
    ]


def test_a_depth_no_grant_could_ever_reach_is_refused_instead_of_held():
    graph = StateGraph(
        [n("root"), n("after", ["root"])],
        max_elastic_depth=0,
        max_elastic_nodes=3,
        elastic_depth_ceiling=0,
    )
    complete_with(graph, "root", done_with(req("a")))
    (record,) = graph.spawn_records
    assert record.status is GraphSpawnStatus.REFUSED
    assert record.code is ElasticRefusalCode.DEPTH_CEILING_REACHED
    assert "ceiling" in record.reason and "max_elastic_depth 0" in record.reason
    assert sorted(graph.nodes) == ["after", "root"]
    assert graph.status("after") is RUNNABLE


def test_a_batch_between_the_cap_and_the_ceiling_is_held_and_a_grant_to_the_ceiling_runs_it():
    graph = StateGraph(
        [n("root"), n("after", ["root"])],
        max_elastic_depth=1,
        max_elastic_nodes=2,
        elastic_nodes_ceiling=4,
    )
    complete_with(graph, "root", done_with(req("a"), req("b")))
    assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.DEFERRED] * 2
    held = graph.result("join:root").reason
    assert "max_elastic_nodes up to 4" in held and "max_elastic_depth up to 8" in held
    graph.grant_elastic_capacity(max_elastic_nodes=3, reason="just enough")
    assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.ACCEPTED] * 2
    assert graph.nodes["join:root"].elastic.joins == ["elastic:root:a", "elastic:root:b"]


def test_the_join_counts_against_the_node_cap():
    graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=2)
    complete_with(graph, "root", done_with(req("a")))
    assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.ACCEPTED]
    graph.mark_started("elastic:root:a")
    capacity = graph.execution_context("elastic:root:a").elastic_capacity
    assert (capacity.nodes_used, capacity.remaining_nodes) == (2, 0)

    two = StateGraph([n("root"), n("after", ["root"])], max_elastic_depth=1, max_elastic_nodes=2)
    complete_with(two, "root", done_with(req("a"), req("b")))
    assert [(r.status, r.code) for r in two.spawn_records] == [
        (GraphSpawnStatus.DEFERRED, ElasticRefusalCode.NODE_CAP_REACHED)
    ] * 2
    message = two.spawn_records[0].reason
    assert "2 requested plus the join node" in message and "max_elastic_nodes 2" in message
    assert sorted(two.nodes) == ["after", "join:root", "root"]

    three = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=3)
    complete_with(three, "root", done_with(req("a"), req("b")))
    assert [r.status for r in three.spawn_records] == [GraphSpawnStatus.ACCEPTED] * 2


def test_a_run_never_holds_more_elastic_nodes_than_the_cap():
    graph = StateGraph([n("p1"), n("p2"), n("p3")], max_elastic_depth=2, max_elastic_nodes=5)
    for parent in ("p1", "p2", "p3"):
        complete_with(graph, parent, done_with(req("x"), req("y")))
    real = [
        node_id
        for node_id, node in graph.nodes.items()
        if node.kind is ELASTIC
        and not (node.elastic.joins == [] and node.elastic.role.value == "join")
    ]
    assert len(real) <= 5
    assert len(real) == 3
    by_parent = {r.parent_node_id: r.status for r in graph.spawn_records}
    assert by_parent == {
        "p1": GraphSpawnStatus.ACCEPTED,
        "p2": GraphSpawnStatus.DEFERRED,
        "p3": GraphSpawnStatus.DEFERRED,
    }


def test_a_held_join_is_not_counted_until_its_batch_runs_and_a_declined_one_never_is():
    graph = StateGraph([n("p1"), n("p2"), n("p3")], max_elastic_depth=0, max_elastic_nodes=2)
    for parent in ("p1", "p2", "p3"):
        complete_with(graph, parent, done_with(req("x")))
    assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.DEFERRED] * 3
    graph.decline_elastic_requests("p3", "not needed")
    graph.grant_elastic_capacity(max_elastic_depth=1, reason="depth for p1")
    by_parent = {r.parent_node_id: r.status for r in graph.spawn_records}
    assert by_parent == {
        "p1": GraphSpawnStatus.ACCEPTED,
        "p2": GraphSpawnStatus.DEFERRED,
        "p3": GraphSpawnStatus.DISCARDED,
    }
    graph.mark_started("elastic:p1:x")
    assert graph.execution_context("elastic:p1:x").elastic_capacity.nodes_used == 2
    graph.grant_elastic_capacity(max_elastic_nodes=4, reason="room for p2")
    assert {r.parent_node_id: r.status for r in graph.spawn_records}["p2"] is (
        GraphSpawnStatus.ACCEPTED
    )


def test_restoring_more_elastic_nodes_than_the_cap_names_the_children_and_the_joins():
    graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=2)
    complete_with(graph, "root", done_with(req("a")))
    payload = json.loads(json.dumps(graph.snapshot()))
    payload["max_elastic_nodes"] = 1
    with pytest.raises(
        ValueError, match=r"2 elastic nodes \(1 child and 1 join\) exceed max_elastic_nodes 1"
    ):
        StateGraph.from_snapshot(payload)


def siblings(cap=4):
    graph = StateGraph([n("p1"), n("p2")], max_elastic_depth=1, max_elastic_nodes=cap)
    graph.start_runnable_wave()
    return graph, graph.execution_context("p1"), graph.execution_context("p2")


def test_siblings_in_one_wave_share_the_remaining_capacity():
    graph, first, second = siblings()
    assert first.elastic_reservation is not None and second.elastic_reservation is not None
    assert first.elastic_reservation.reserve(2) is None
    problem = second.elastic_reservation.reserve(1)
    assert problem.code is ElasticRefusalCode.NODE_CAP_REACHED
    assert problem.grantable is True
    assert "held by tasks running at the same time" in problem.message
    assert '"p2"' in problem.message and "max_elastic_nodes 4" in problem.message
    assert graph.status("p2") is RUNNING


def test_a_reservation_can_grow_and_a_release_frees_the_capacity_for_a_sibling():
    graph, first, second = siblings()
    assert first.elastic_reservation.reserve(1) is None
    assert first.elastic_reservation.reserve(2) is None
    assert second.elastic_reservation.reserve(1) is not None
    first.elastic_reservation.release()
    assert second.elastic_reservation.reserve(2) is None
    assert first.elastic_reservation.reserve(1) is not None


def test_the_live_handle_reports_the_nodes_still_free_beside_every_hold():
    graph, first, second = siblings(cap=6)
    assert first.elastic_reservation.remaining() == 6
    assert first.elastic_reservation.reserve(2) is None
    assert second.elastic_reservation.remaining() == 3
    assert second.elastic_reservation.reserve(1) is None
    assert first.elastic_reservation.remaining() == 1
    first.elastic_reservation.release()
    assert second.elastic_reservation.remaining() == 4


def test_a_refused_reservation_keeps_the_previous_one():
    graph, first, second = siblings()
    assert first.elastic_reservation.reserve(1) is None
    assert first.elastic_reservation.reserve(4) is not None
    assert second.elastic_reservation.reserve(1) is None
    assert second.elastic_reservation.reserve(2) is not None


def test_a_reservation_beyond_the_ceiling_is_not_grantable():
    graph = StateGraph([n("p1")], max_elastic_depth=1, max_elastic_nodes=2, elastic_nodes_ceiling=3)
    graph.start_runnable_wave()
    handle = graph.execution_context("p1").elastic_reservation
    within = handle.reserve(2)
    assert within.code is ElasticRefusalCode.NODE_CAP_REACHED and within.grantable is True
    beyond = handle.reserve(3)
    assert beyond.code is ElasticRefusalCode.NODE_CEILING_REACHED
    assert beyond.grantable is False and "ceiling" in beyond.message


def test_reservations_made_by_running_siblings_never_defer_a_batch_at_commit():
    graph, first, second = siblings(cap=4)
    assert first.elastic_reservation.reserve(1) is None
    assert second.elastic_reservation.reserve(1) is None
    graph.mark_terminal("p1", done_with(req("x")))
    graph.mark_terminal("p2", done_with(req("y")))
    assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.ACCEPTED] * 2
    assert not any(r.status is GraphSpawnStatus.DEFERRED for r in graph.spawn_records)


def test_a_node_that_finished_can_no_longer_reserve_and_its_hold_is_gone():
    graph, first, second = siblings()
    assert first.elastic_reservation.reserve(2) is None
    graph.mark_terminal("p1", ok())
    assert second.elastic_reservation.reserve(3) is None
    with pytest.raises(ValueError, match='Node "p1" is not running'):
        first.elastic_reservation.reserve(1)


def test_a_reservation_needs_at_least_one_request():
    graph, first, second = siblings()
    with pytest.raises(
        ValueError, match=r'Node "p1" must reserve at least one elastic request, got 0\.'
    ):
        first.elastic_reservation.reserve(0)
    with pytest.raises(ValueError, match="got -2"):
        first.elastic_reservation.reserve(-2)
    assert first.elastic_reservation.remaining() == second.elastic_reservation.remaining()


def test_only_a_running_node_can_be_committed():
    graph = StateGraph([n("a"), n("b", ["a"])])
    with pytest.raises(ValueError, match='Node "a" is not running'):
        graph.mark_terminal("a", ok())
    graph.start_runnable_wave()
    graph.mark_terminal("a", ok())
    with pytest.raises(ValueError, match='Node "a" is not running'):
        graph.mark_terminal("a", ok())


def test_a_failed_sibling_gives_its_capacity_back_inside_the_same_wave():
    graph = StateGraph([n("p1"), n("p2")], max_elastic_depth=1, max_elastic_nodes=3)
    gate = None
    outcome = {}

    async def executor(node, context):
        if node.node_id == "p1":
            assert context.elastic_reservation.reserve(1) is None
            gate.set()
            return failed("probe crashed")
        await gate.wait()
        outcome["second"] = context.elastic_reservation.reserve(1)
        return ok()

    async def run():
        nonlocal gate
        gate = asyncio.Event()
        return await graph.execute({AGENT: executor})

    arun(run())
    assert outcome["second"] is None
    assert statuses(graph) == {"p1": "failed", "p2": "completed"}


def test_a_sibling_that_completes_keeps_its_hold_until_the_wave_commits():
    graph = StateGraph([n("p1"), n("p2")], max_elastic_depth=1, max_elastic_nodes=3)
    gate = None
    outcome = {}

    async def executor(node, context):
        if node.node_id == "p1":
            assert context.elastic_reservation.reserve(1) is None
            gate.set()
            return done_with(req("x"))
        await gate.wait()
        outcome["second"] = context.elastic_reservation.reserve(1)
        return ok()

    async def run():
        nonlocal gate
        gate = asyncio.Event()
        return await graph.execute({AGENT: executor})

    arun(run())
    assert outcome["second"].code is ElasticRefusalCode.NODE_CAP_REACHED
    assert [r.status for r in graph.spawn_records] == [GraphSpawnStatus.ACCEPTED]


def test_recovery_asks_the_policy_about_nodes_that_were_running():
    graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=3)
    complete_with(graph, "root", done_with(req("a"), req("b")))
    graph.start_runnable_wave()
    restored = roundtrip(graph)
    seen = []

    def policy(node, context):
        seen.append((node.node_id, sorted(context.dependencies)))
        return node.node_id.endswith(":a")

    recovered = restored.recover_interrupted(is_replayable=policy)
    assert recovered == ["elastic:root:a", "elastic:root:b"]
    assert seen == [("elastic:root:a", ["root"]), ("elastic:root:b", ["root"])]
    assert restored.status("elastic:root:a") is RUNNABLE
    assert restored.status("elastic:root:b") is FAILED
    assert restored.result("elastic:root:b").diagnostics == ["interrupted-non-idempotent"]
    assert restored.status("join:root") is PENDING


def test_replayable_ids_and_the_policy_both_count():
    graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=3)
    complete_with(graph, "root", done_with(req("a"), req("b")))
    graph.start_runnable_wave()
    restored = roundtrip(graph)
    restored.recover_interrupted(
        {"elastic:root:a"}, is_replayable=lambda node, context: node.node_id.endswith(":b")
    )
    assert restored.status("elastic:root:a") is RUNNABLE
    assert restored.status("elastic:root:b") is RUNNABLE


def test_a_policy_that_raises_fails_that_node_naming_the_cause_and_leaves_the_rest():
    graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=3)
    complete_with(graph, "root", done_with(req("a"), req("b")))
    graph.start_runnable_wave()
    restored = roundtrip(graph)

    def policy(node, context):
        if node.node_id.endswith(":a"):
            raise RuntimeError("binding store offline")
        return True

    restored.recover_interrupted(is_replayable=policy)
    result = restored.result("elastic:root:a")
    assert restored.status("elastic:root:a") is FAILED
    assert '"elastic:root:a"' in result.reason
    assert "RuntimeError" in result.reason and "binding store offline" in result.reason
    assert result.diagnostics == ["interrupted-replay-check-failed"]
    assert restored.status("elastic:root:b") is RUNNABLE
