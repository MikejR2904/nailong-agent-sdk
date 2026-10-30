import json

import pytest

from nailong_agent_sdk.developer_tools.attribute_failure import (
    MAX_LINKED_ROOTS,
    attribute_failure,
)
from nailong_agent_sdk.state.graph_models import GraphNodeResult
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from tests.support.agents import final, tool_call
from tests.support.developer_tool_runs import cli
from tests.support.diagnosis_runs import (
    SQLITE_SIDE_FILES,
    executing,
    fingerprints,
    record,
    run_chain,
)
from tests.support.orchestration import (
    close_all,
    independent_plan,
    make_factory,
    make_policy,
    make_request,
    pipeline,
)
from tests.support.plans import AGENT, F, arun, chain_plan


def test_a_failed_node_is_ranked_with_everything_repairing_it_would_release(tmp_path):
    _, run_id = run_chain(tmp_path, 4, failing={"T0001"})
    result = attribute_failure(tmp_path, run_id)
    assert result.verdict == "failed" and not result.ok and result.evidence_intact is not False
    graph = result.graph
    assert graph.node_count == 4 and graph.root_count == 1 and graph.blocked_count == 2
    (root,) = graph.roots
    assert root.node_id == "node:T0001" and root.failure_code == "HARNESS_TOOL_EXECUTION_FAILED"
    assert root.responsibility == 3.0 and graph.total_responsibility == 3.0
    assert root.blocked_count == 2
    assert root.released_alone == ["node:T0002", "node:T0003"] and root.also_requires == []
    assert root.agent_run_id is None and root.agent_terminal_decision is None
    assert 'node "node:T0001" carries the most responsibility (3 of 3)' in result.summary
    assert "repairing it alone releases 2" in result.summary


def test_two_independent_failures_each_carry_only_their_own_stop(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    started = coordinator.start_run(independent_plan(3))
    arun(coordinator.execute_run(started.run_id, {AGENT: executing(failing={"T02", "T00"})}))
    result = attribute_failure(tmp_path, started.run_id)
    roots = result.graph.roots
    assert [root.node_id for root in roots] == ["node:T00", "node:T02"]
    assert [root.responsibility for root in roots] == [1.0, 1.0]
    assert all(root.released_alone == [] and root.blocked_count == 0 for root in roots)
    assert "1 other node(s) stopped independently" in result.summary


def test_one_blocked_node_can_be_explained_with_its_causes_and_a_path(tmp_path):
    _, run_id = run_chain(tmp_path, 4, failing={"T0001"})
    result = attribute_failure(tmp_path, run_id, node_id="node:T0003")
    (item,) = result.graph.blocked
    assert item.root_causes == ["node:T0001"]
    assert item.path == ["node:T0001", "node:T0002", "node:T0003"] and item.path_length == 3
    assert result.graph.blocked_omitted == 1


def test_a_node_that_is_not_blocked_cannot_be_explained(tmp_path):
    _, run_id = run_chain(tmp_path, 3, failing={"T0001"})
    with pytest.raises(ValueError, match=r'Node "node:T0000" is not blocked by a dependency'):
        attribute_failure(tmp_path, run_id, node_id="node:T0000")
    with pytest.raises(ValueError, match=r'Node "ghost" is not in the graph'):
        attribute_failure(tmp_path, run_id, node_id="ghost")


def test_a_finished_run_has_nothing_to_attribute(tmp_path):
    _, run_id = run_chain(tmp_path, 3)
    result = attribute_failure(tmp_path, run_id)
    assert result.verdict == "completed" and result.ok
    assert result.graph.roots == [] and result.graph.total_responsibility == 0.0
    assert result.summary == f'Graph run "{run_id}" completed; there is nothing to attribute.'


def test_a_run_that_has_not_started_names_no_cause(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    started = coordinator.start_run(chain_plan(3))
    result = attribute_failure(tmp_path, started.run_id)
    assert result.verdict == "incomplete" and not result.ok and result.graph.roots == []
    assert "no node failed, was cancelled or blocked itself" in result.summary


def test_a_graph_record_that_cannot_be_read_is_reported_instead_of_raised(tmp_path):
    _, run_id = run_chain(tmp_path, 2)
    (tmp_path / ".agent-runs" / f"{run_id}.json").write_text("{not json", encoding="utf-8")
    result = attribute_failure(tmp_path, run_id)
    assert result.graph is None and result.graph_record_error is not None and not result.ok
    assert "The graph run record could not be read" in result.summary
    with pytest.raises(ValueError, match="has no readable graph run record, so it has no nodes"):
        attribute_failure(tmp_path, run_id, node_id="node:T0000")


def test_attributing_a_run_changes_no_file(tmp_path):
    _, run_id = run_chain(tmp_path, 3, failing={"T0000"})
    before = fingerprints(tmp_path)
    attribute_failure(tmp_path, run_id)
    after = fingerprints(tmp_path)
    assert set(after) - set(before) <= SQLITE_SIDE_FILES
    assert {name: digest for name, digest in after.items() if name in before} == before


def test_an_orchestrated_failure_leads_from_the_node_to_the_model_turn_that_caused_it(tmp_path):
    def factory(context):
        broken = context.assignment.node_id.endswith("T1")
        turns = [tool_call("c1", "ghost")] if broken else None
        return make_factory(model_turns=turns)(context)

    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), factory
    )
    try:
        result = attribute_failure(tmp_path, executed.graph_run_id)
    finally:
        close_all(orchestrator, services)
    (root,) = result.graph.roots
    assert root.failure_code == "TOOL_NOT_DECLARED" and root.responsibility == 2.0
    decision = root.agent_terminal_decision
    assert decision.relation == "issued-the-call" and decision.iteration == 1
    assert decision.tool == "ghost" and decision.tool_call_id == "c1"
    assert decision.turn_type == "tool-call" and decision.arguments_preview == "{}"
    assert decision.audit_sequence is not None and decision.entry_truncated is False
    assert (
        f'Its agent run "{root.agent_run_id}": The model turn at iteration 1 issued ghost call'
        in (result.summary)
    )


def test_only_the_most_responsible_roots_are_linked_to_decisions(tmp_path):
    record(tmp_path, "unrelated", [final()])
    coordinator = HarnessCoordinator(tmp_path)
    count = MAX_LINKED_ROOTS + 2

    async def failing(node, context):
        suffix = node.node_id.removeprefix("node:")
        return GraphNodeResult(
            status=F,
            reason=f"{suffix} failed",
            diagnostics=[f"agent-run:run-of-{suffix}", "failure-code:SOME_FAILURE"],
        )

    started = coordinator.start_run(independent_plan(count))
    arun(coordinator.execute_run(started.run_id, {AGENT: failing}))
    roots = attribute_failure(tmp_path, started.run_id).graph.roots
    assert len(roots) == count
    linked, beyond = roots[:MAX_LINKED_ROOTS], roots[MAX_LINKED_ROOTS:]
    for item in linked:
        suffix = item.node_id.removeprefix("node:")
        assert item.agent_terminal_decision.relation == "unlinked"
        assert item.agent_terminal_decision.note == (
            f'The agent run "run-of-{suffix}" recorded no terminal failure event.'
        )
    for item in beyond:
        assert item.agent_terminal_decision.note == (
            f"Only the {MAX_LINKED_ROOTS} roots with the highest responsibility are linked to "
            "model decisions."
        )


def test_a_graph_without_a_telemetry_database_cannot_link_its_agent_runs(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)

    async def failing(node, context):
        return GraphNodeResult(status=F, reason="x", diagnostics=["agent-run:some-run"])

    started = coordinator.start_run(independent_plan(1))
    arun(coordinator.execute_run(started.run_id, {AGENT: failing}))
    (root,) = attribute_failure(tmp_path, started.run_id).graph.roots
    assert root.agent_terminal_decision.note == "The run root has no telemetry database."


def test_the_command_ranks_the_roots_and_exits_with_the_verdict(tmp_path):
    _, run_id = run_chain(tmp_path, 3, failing={"T0001"})
    result = cli("attribute-failure", tmp_path, run_id)
    assert result.returncode == 1
    printed = json.loads(result.stdout)
    assert printed["verdict"] == "failed" and printed["schema_version"].endswith("-v1")
    assert printed["graph"]["roots"][0]["released_alone"] == ["node:T0002"]
    explained = cli("attribute-failure", tmp_path, run_id, "--node", "node:T0002")
    assert json.loads(explained.stdout)["graph"]["blocked"][0]["root_causes"] == ["node:T0001"]


def test_the_command_exits_zero_for_a_healthy_run_and_three_for_an_unknown_one(tmp_path):
    _, run_id = run_chain(tmp_path, 2)
    assert cli("attribute-failure", tmp_path, run_id).returncode == 0
    unknown = cli("attribute-failure", tmp_path, "nope")
    assert unknown.returncode == 3
    assert 'no graph run record for "nope"' in unknown.stderr
