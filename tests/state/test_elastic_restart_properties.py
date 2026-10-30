import random

import pytest

from nailong_agent_sdk.state.elastic import ElasticSpawnRequest
from nailong_agent_sdk.state.graph_models import GraphNodeKind, GraphNodeResult, GraphNodeStatus
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.planning import Plan
from tests.support.plans import AGENT, arun, task

ELASTIC = GraphNodeKind.ELASTIC


class Crash(BaseException):
    pass


def build_plan(seed):
    rng = random.Random(seed)
    count = rng.randint(1, 4)
    tasks = [task(f"T{index}") for index in range(count)]
    return Plan(plan_id=f"plan-{seed}", tasks=tasks, max_elastic_depth=2, max_elastic_nodes=64)


def request_for(rng, node_id, index):
    return ElasticSpawnRequest(
        request_id=f"r{index}",
        scope=f"scope of {node_id}",
        instructions="explore",
        reason="needed",
    )


def executors_for(seed, crash_after=None):
    calls = {"count": 0}

    async def run(node, context):
        calls["count"] += 1
        if crash_after is not None and calls["count"] == crash_after:
            raise Crash()
        rng = random.Random(f"{seed}:{node.node_id}")
        spawn = []
        if node.elastic_depth < 2 and rng.random() < 0.55:
            spawn = [request_for(rng, node.node_id, index) for index in range(rng.randint(1, 3))]
        return GraphNodeResult(
            status=GraphNodeStatus.COMPLETED, output=node.node_id, spawn_requests=spawn
        )

    return {AGENT: run, ELASTIC: run}


def outcome(record):
    graph = record.graph
    return (
        sorted(graph["statuses"].items()),
        sorted(
            (item["parent_node_id"], item["request"]["request_id"], item["status"])
            for item in graph["spawn_records"]
        ),
    )


@pytest.mark.parametrize("block", range(3))
def test_a_run_that_crashes_and_recovers_ends_where_an_uninterrupted_run_ends(tmp_path, block):
    crashed_runs = 0
    for seed in range(block * 12, (block + 1) * 12):
        reference = HarnessCoordinator(tmp_path / f"reference-{seed}")
        run_id = reference.start_run(build_plan(seed)).run_id
        expected = outcome(arun(reference.execute_run(run_id, executors_for(seed))))
        assert all(status == "completed" for _, status in expected[0])

        root = tmp_path / f"crashing-{seed}"
        first = HarnessCoordinator(root)
        crash_run = first.start_run(build_plan(seed)).run_id
        crash_at = random.Random(seed).randint(1, max(1, len(expected[0])))
        try:
            arun(first.execute_run(crash_run, executors_for(seed, crash_after=crash_at)))
        except Crash:
            crashed_runs += 1
        second = HarnessCoordinator(root)
        persisted = second.get_run_state(crash_run).graph["statuses"]
        running = {node for node, status in persisted.items() if status == "running"}
        recovered = second.recover_interrupted_run(crash_run, running)
        assert "running" not in recovered.graph["statuses"].values()
        final = arun(second.execute_run(crash_run, executors_for(seed)))
        assert outcome(final) == expected, seed
        assert HarnessCoordinator(root).get_run_state(crash_run).run_hash == final.run_hash
    assert crashed_runs >= 6
