import json

from nailong_agent_sdk.developer_tools.diagnose import diagnose_run
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from tests.support.agents import tool_call
from tests.support.developer_tool_runs import cli
from tests.support.diagnosis_runs import SQLITE_SIDE_FILES, executing, fingerprints, run_chain
from tests.support.orchestration import (
    close_all,
    independent_plan,
    make_factory,
    make_policy,
    make_request,
    pipeline,
)
from tests.support.plans import AGENT, arun, chain_plan


def test_a_failed_node_is_the_root_and_names_everything_it_held_up(tmp_path):
    _, run_id = run_chain(tmp_path, 4, failing={"T0001"})
    diagnosis = diagnose_run(tmp_path, run_id)
    assert diagnosis.verdict == "failed" and not diagnosis.ok
    graph = diagnosis.graph
    assert graph.node_count == 4 and graph.status_counts == {
        "blocked": 2,
        "completed": 1,
        "failed": 1,
    }
    (root,) = graph.roots
    assert root.node_id == "node:T0001" and root.status == "failed"
    assert root.failure_code == "HARNESS_TOOL_EXECUTION_FAILED"
    assert root.reason == "T0001 raised ValueError: bad input"
    assert root.blocked_descendants == ["node:T0002", "node:T0003"]
    assert root.graph_event_sequence is not None
    assert graph.unfinished == {} and graph.blocked_without_root == []
    assert 'node "node:T0001" failed (HARNESS_TOOL_EXECUTION_FAILED)' in diagnosis.summary
    assert "2 node(s) blocked downstream" in diagnosis.summary


def test_two_independent_failures_are_two_roots_in_the_order_they_happened(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    started = coordinator.start_run(independent_plan(3))
    arun(coordinator.execute_run(started.run_id, {AGENT: executing(failing={"T02", "T00"})}))
    diagnosis = diagnose_run(tmp_path, started.run_id)
    roots = diagnosis.graph.roots
    assert [root.node_id for root in roots] == ["node:T00", "node:T02"]
    assert roots[0].graph_event_sequence < roots[1].graph_event_sequence
    assert all(root.blocked_descendants == [] for root in roots)
    assert "1 other node(s) stopped independently" in diagnosis.summary


def test_a_node_blocked_by_its_own_result_is_a_blocked_root(tmp_path):
    _, run_id = run_chain(tmp_path, 3, blocking={"T0000"})
    diagnosis = diagnose_run(tmp_path, run_id)
    assert diagnosis.verdict == "blocked" and not diagnosis.ok
    (root,) = diagnosis.graph.roots
    assert root.node_id == "node:T0000" and root.status == "blocked"
    assert root.blocked_descendants == ["node:T0001", "node:T0002"]


def test_a_run_that_has_not_started_is_incomplete_and_lists_its_unfinished_nodes(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    started = coordinator.start_run(chain_plan(3))
    diagnosis = diagnose_run(tmp_path, started.run_id)
    assert diagnosis.verdict == "incomplete" and not diagnosis.ok
    assert set(diagnosis.graph.unfinished) == {"node:T0000", "node:T0001", "node:T0002"}
    assert diagnosis.graph.roots == []


def test_a_finished_run_is_healthy(tmp_path):
    _, run_id = run_chain(tmp_path, 3)
    diagnosis = diagnose_run(tmp_path, run_id)
    assert diagnosis.verdict == "completed" and diagnosis.ok
    assert diagnosis.graph.roots == [] and diagnosis.graph.status_counts == {"completed": 3}


def test_a_cancelled_run_names_the_cancelled_nodes_as_roots(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    started = coordinator.start_run(chain_plan(3))
    coordinator.cancel_run(started.run_id)
    diagnosis = diagnose_run(tmp_path, started.run_id)
    assert diagnosis.verdict == "cancelled" and not diagnosis.ok
    assert {root.status for root in diagnosis.graph.roots} == {"cancelled"}


def test_a_record_that_cannot_be_read_is_reported_instead_of_raised(tmp_path):
    _, run_id = run_chain(tmp_path, 2)
    (tmp_path / ".agent-runs" / f"{run_id}.json").write_text("{not json", encoding="utf-8")
    diagnosis = diagnose_run(tmp_path, run_id)
    assert diagnosis.graph is None and diagnosis.graph_record_error is not None
    assert not diagnosis.ok
    assert "could not be read" in diagnosis.summary or "no readable" in diagnosis.summary


def test_a_graph_record_is_read_without_changing_any_file(tmp_path):
    _, run_id = run_chain(tmp_path, 3, failing={"T0000"})
    before = fingerprints(tmp_path)
    diagnose_run(tmp_path, run_id)
    after = fingerprints(tmp_path)
    assert set(after) - set(before) <= SQLITE_SIDE_FILES
    assert {name: digest for name, digest in after.items() if name in before} == before


def test_an_orchestrated_failure_leads_from_the_graph_to_the_agent_run_that_caused_it(tmp_path):
    def factory(context):
        broken = context.assignment.node_id.endswith("T1")
        turns = [tool_call("c1", "ghost")] if broken else None
        return make_factory(model_turns=turns)(context)

    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), factory
    )
    try:
        diagnosis = diagnose_run(tmp_path, executed.graph_run_id)
    finally:
        close_all(orchestrator, services)
    assert diagnosis.verdict == "failed"
    (root,) = diagnosis.graph.roots
    assert root.failure_code == "TOOL_NOT_DECLARED"
    assert root.agent_run_id == "task-" + root.node_id
    assert root.agent_terminal.code == "TOOL_NOT_DECLARED"
    assert root.agent_terminal.message == 'Tool "ghost" is not declared for this agent.'
    assert root.agent_first_problem.sequence <= root.agent_terminal.sequence
    assert len(root.blocked_descendants) == 1
    assert diagnosis.graph.status_counts["completed"] == 1
    assert any(item.event_type == "graph.executed" for item in diagnosis.findings)
    drill = diagnose_run(tmp_path, root.agent_run_id)
    assert drill.terminal.code == "TOOL_NOT_DECLARED" and drill.graph is None


def test_the_command_reports_a_graph_run_and_exits_with_the_verdict(tmp_path):
    _, run_id = run_chain(tmp_path, 3, failing={"T0001"})
    result = cli("diagnose-run", tmp_path, run_id)
    assert result.returncode == 1
    printed = json.loads(result.stdout)
    assert printed["verdict"] == "failed"
    assert printed["graph"]["roots"][0]["blocked_descendants"] == ["node:T0002"]
