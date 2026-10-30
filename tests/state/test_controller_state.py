import json
import subprocess
import sys
import textwrap
import time

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.graph_models import GraphNodeResult, GraphNodeStatus
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.orchestration import ControllerStateMachine, ControllerStateStore
from nailong_agent_sdk.state.orchestration_models import (
    ComplexityRoutingRules,
    ControllerPhase,
    GapMetadata,
    SkillToolProfile,
    WorkflowArchitecture,
)
from nailong_agent_sdk.state.shared_state import SharedSubstrateSnapshot
from tests.support.controllers import executing_runtime
from tests.support.plans import AGENT, arun, ok, run_ok, simple_plan, task
from tests.support.processes import child_environment


def test_two_processes_starting_runs_report_collisions_or_clear_errors(tmp_path):
    script = textwrap.dedent(
        f"""
        import sys, json, time
        from pathlib import Path
        from tests.support.plans import simple_plan
        from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
        coordinator = HarnessCoordinator(Path(r"{tmp_path}"))
        deadline = float(sys.argv[1])
        while time.time() < deadline:
            time.sleep(0.0005)
        out = []
        for _ in range(25):
            try:
                out.append(["ok", coordinator.start_run(simple_plan()).run_id])
            except Exception as error:
                out.append(["err", type(error).__name__ + ": " + str(error)[:120]])
        print(json.dumps(out))
        """
    )
    start_at = time.time() + 4
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(start_at)],
            env=child_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = []
    for proc in procs:
        out, err = proc.communicate(timeout=300)
        assert proc.returncode == 0, err[-500:]
        outputs.append(json.loads(out.strip().splitlines()[-1]))
    ids = [[v for tag, v in o if tag == "ok"] for o in outputs]
    errors = [v for o in outputs for tag, v in o if tag == "err"]
    collisions = set(ids[0]) & set(ids[1])
    assert not collisions and not errors, (len(collisions), errors[:2])


def test_run_state_conflict_when_another_coordinator_writes_mid_execution(tmp_path):
    a = HarnessCoordinator(tmp_path)
    run_id = a.start_run(simple_plan()).run_id
    b = HarnessCoordinator(tmp_path)

    async def executor(node, context):
        if node.node_id == "node:T1":
            b.cancel_run(run_id)
        return ok()

    with pytest.raises(AgentSdkError) as excinfo:
        arun(a.execute_run(run_id, {AGENT: executor}))
    assert excinfo.value.code == "RUN_STATE_CONFLICT"
    assert "one HarnessCoordinator per run root" in str(excinfo.value)


def test_controller_id_is_not_a_path(tmp_path):
    store = ControllerStateStore(tmp_path)
    (tmp_path / "outside_ctrl.json").write_text('{"secret": "TOP-SECRET-CONTROLLER"}', "utf-8")
    try:
        store.load("../outside_ctrl")
        outcome = "loaded"
    except Exception as error:
        outcome = f"{type(error).__name__}: {str(error)[:200]}"
    assert "TOP-SECRET" not in outcome and not store.exists("../outside_ctrl")


def make_machine(max_repair=1, **rules):
    snapshot = SharedSubstrateSnapshot(snapshot_id="snap", version="1", content_hash="h")
    profile = SkillToolProfile(stage="design", source_snapshot_id="snap")
    routing = ComplexityRoutingRules(
        multi_agent_min_categories=rules.get("cats", 3),
        multi_agent_min_blast_radius=rules.get("blast", 5),
        multi_agent_gap_types=rules.get("types", ["inconsistency"]),
    )
    return ControllerStateMachine(
        "controller-1",
        snapshot,
        profile,
        routing,
        rules.get("gap", GapMetadata()),
        project_state_id="snap",
        max_repair_attempts=max_repair,
    )


def test_controller_state_machine_transitions_and_bounds():
    machine = make_machine()
    assert machine.record.phase is ControllerPhase.PLANNING
    assert machine.record.architecture is WorkflowArchitecture.SINGLE_AGENT
    with pytest.raises(
        ValueError, match='invalid in phase "planning"; expected awaiting-plan-approval'
    ):
        machine.approve_plan(True)
    plan = simple_plan()
    machine.submit_plan(plan)
    assert machine.record.phase is ControllerPhase.AWAITING_PLAN_APPROVAL
    machine.approve_plan(False, "needs rework")
    assert (
        machine.record.phase is ControllerPhase.PLANNING and machine.record.plan_approved is False
    )
    machine.submit_plan(plan)
    machine.approve_plan(True)
    with pytest.raises(ValueError, match="invalid in phase"):
        machine.complete()
    machine.begin_dispatch()
    machine.bind_run("run-1")
    machine.bind_run("run-2")
    assert [e.type for e in machine.record.events if e.type.startswith("graph-run")] == [
        "graph-run-bound",
        "graph-run-rebound",
    ]
    machine.record_stage_failure("first")
    assert (
        machine.record.phase is ControllerPhase.REPAIR_REQUIRED
        and machine.record.repair_attempts == 1
    )
    machine.submit_plan(plan)
    machine.approve_plan(True)
    machine.begin_dispatch()
    machine.record_stage_failure("second")
    assert (
        machine.record.phase is ControllerPhase.ESCALATED
        and machine.record.escalation_reason == "second"
    )
    machine.cancel("stop")
    with pytest.raises(
        ValueError, match=r'Controller "controller-1" is already cancelled, a terminal phase'
    ):
        machine.cancel("again")
    assert [e.sequence for e in machine.record.events] == list(
        range(1, len(machine.record.events) + 1)
    )
    invalid = make_machine()

    from nailong_agent_sdk.state.planning import Plan

    invalid.submit_plan(Plan(plan_id="bad", tasks=[task("T1", deps=["ghost"])]))
    with pytest.raises(ValueError, match="deterministically valid plan"):
        invalid.approve_plan(True)


def test_complexity_router_and_advisory_monotonicity():
    from nailong_agent_sdk.state.orchestration_models import ComplexityRouter

    router = ComplexityRouter(
        ComplexityRoutingRules(
            multi_agent_min_categories=2,
            multi_agent_min_blast_radius=3,
            multi_agent_gap_types=["inconsistency"],
        )
    )
    assert (
        router.route(GapMetadata(categories_touched=["a"], blast_radius=2))
        is WorkflowArchitecture.SINGLE_AGENT
    )
    assert (
        router.route(GapMetadata(categories_touched=["a", "b"], blast_radius=0))
        is WorkflowArchitecture.MULTI_AGENT
    )
    assert (
        router.route(GapMetadata(categories_touched=["a", "a"], blast_radius=0))
        is WorkflowArchitecture.SINGLE_AGENT
    )
    assert router.route(GapMetadata(blast_radius=3)) is WorkflowArchitecture.MULTI_AGENT
    assert (
        router.route(GapMetadata(gap_types=["inconsistency"])) is WorkflowArchitecture.MULTI_AGENT
    )
    machine = make_machine()
    machine.apply_advisory_architecture(WorkflowArchitecture.MULTI_AGENT, reason="advisory")
    with pytest.raises(ValueError, match="may not lower a deterministic route"):
        machine.apply_advisory_architecture(WorkflowArchitecture.SINGLE_AGENT, reason="x")
    multi = make_machine(gap=GapMetadata(blast_radius=99))
    with pytest.raises(ValueError, match="may not lower"):
        multi.apply_advisory_architecture(WorkflowArchitecture.SINGLE_AGENT, reason="x")
    machine.submit_plan(simple_plan())
    with pytest.raises(ValueError, match="invalid in phase"):
        machine.apply_advisory_architecture(WorkflowArchitecture.MULTI_AGENT, reason="late")


def test_controller_store_roundtrip_integrity_and_recovery(tmp_path, monkeypatch):
    store = ControllerStateStore(tmp_path)
    machine = make_machine()
    store.save(machine.record)
    machine.submit_plan(simple_plan())
    store.save(machine.record)
    loaded = ControllerStateStore(tmp_path).load("controller-1")
    assert loaded.model_dump(
        exclude={"events_entry_count", "events_integrity_hash"}
    ) == machine.record.model_dump(exclude={"events_entry_count", "events_integrity_hash"})
    events_path = tmp_path / ".agent-controllers" / "controller-1.events.jsonl"
    original = events_path.read_text("utf-8")
    lines = original.splitlines()
    events_path.write_text("\n".join(lines[:-1]) + "\n", "utf-8")
    with pytest.raises(ValueError, match="event history failed integrity verification"):
        ControllerStateStore(tmp_path).load("controller-1")
    tampered = json.loads(lines[0])
    tampered["details"] = {"x": 1}
    events_path.write_text(json.dumps(tampered) + "\n" + "\n".join(lines[1:]) + "\n", "utf-8")
    with pytest.raises(ValueError, match="failed integrity verification"):
        ControllerStateStore(tmp_path).load("controller-1")
    events_path.write_text(
        original + '{"sequence": 9, "phase": "planning", "type": "ghost", "details": {}}\n', "utf-8"
    )
    assert len(ControllerStateStore(tmp_path).load("controller-1").events) == len(lines)
    import nailong_agent_sdk.state.orchestration as orch

    monkeypatch.setattr(
        orch, "_replace_with_retry", lambda *a, **k: (_ for _ in ()).throw(OSError("crash"))
    )
    machine.approve_plan(True)
    with pytest.raises(OSError):
        store.save(machine.record)
    monkeypatch.undo()
    recovered = ControllerStateStore(tmp_path).load("controller-1")
    assert recovered.phase is ControllerPhase.AWAITING_PLAN_APPROVAL
    store2 = ControllerStateStore(tmp_path)
    store2.save(machine.record)
    assert (
        ControllerStateStore(tmp_path).load("controller-1").phase is ControllerPhase.DISPATCH_READY
    )


def test_controller_runtime_end_to_end_with_telemetry(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        runtime, cid = executing_runtime(tmp_path, telemetry)
        record = arun(runtime.execute_graph(cid, {AGENT: run_ok}))
        assert all(v == "completed" for v in record.graph["statuses"].values())
        items = {w.work_item_id: w.status.value for w in runtime.project_state(cid).work_items}
        assert items == {"node:T1": "completed", "node:T2": "completed", "node:T3": "completed"}
        done = runtime.complete(cid)
        assert done.phase is ControllerPhase.COMPLETED
        resumed = ControllerRuntime(tmp_path)
        assert resumed.get_controller(cid).phase is ControllerPhase.COMPLETED
        run_id = resumed.get_controller(cid).run_id
        events = telemetry.list_events(run_id or cid, limit=500)
        types = [e.event_type for e in events]
        assert "graph.executed" in types and "controller.completed" in types
    finally:
        telemetry.close()


def test_controller_failure_path_repairs_then_escalates(tmp_path):
    runtime, cid = executing_runtime(tmp_path)

    async def failing(node, context):
        return GraphNodeResult(status=GraphNodeStatus.FAILED, reason="boom")

    arun(runtime.execute_graph(cid, {AGENT: failing}))
    assert runtime.get_controller(cid).phase is ControllerPhase.REPAIR_REQUIRED
    runtime.submit_plan(cid, simple_plan("plan2"))
    runtime.approve_plan(cid, True)
    runtime.dispatch(cid)
    arun(runtime.execute_graph(cid, {AGENT: failing}))
    assert runtime.get_controller(cid).phase is ControllerPhase.ESCALATED
    with pytest.raises(ValueError, match="requires an executing controller"):
        arun(runtime.execute_graph(cid, {AGENT: failing}))


def test_cancelling_a_completed_controller_is_refused_before_its_run_is_touched(tmp_path):
    runtime, cid = executing_runtime(tmp_path)
    arun(runtime.execute_graph(cid, {AGENT: run_ok}))
    runtime.complete(cid)
    run_id = runtime.get_controller(cid).run_id
    before = HarnessCoordinator(tmp_path).get_run_state(run_id)
    events_before = len(runtime.get_controller(cid).events)
    with pytest.raises(
        ValueError, match=rf'Controller "{cid}" is already completed, a terminal phase'
    ):
        runtime.cancel(cid, "too late")
    after = HarnessCoordinator(tmp_path).get_run_state(run_id)
    assert before.cancelled is False and after.cancelled is False
    assert after.run_hash == before.run_hash and after.graph == before.graph
    assert runtime.get_controller(cid).phase is ControllerPhase.COMPLETED
    assert len(runtime.get_controller(cid).events) == events_before


def test_cancelling_a_cancelled_controller_again_changes_nothing(tmp_path):
    runtime, cid = executing_runtime(tmp_path)
    runtime.cancel(cid, "first")
    run_id = runtime.get_controller(cid).run_id
    before = HarnessCoordinator(tmp_path).get_run_state(run_id)
    events_before = len(runtime.get_controller(cid).events)
    with pytest.raises(
        ValueError, match=rf'Controller "{cid}" is already cancelled, a terminal phase'
    ):
        runtime.cancel(cid, "second")
    after = HarnessCoordinator(tmp_path).get_run_state(run_id)
    assert before.cancelled is True and after.run_hash == before.run_hash
    assert len(runtime.get_controller(cid).events) == events_before


def test_blocked_nodes_are_not_reported_as_a_completed_execution(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        runtime, cid = executing_runtime(tmp_path, telemetry)

        async def blocked(node, context):
            return GraphNodeResult(status=GraphNodeStatus.BLOCKED, reason="needs approval")

        arun(runtime.execute_graph(cid, {AGENT: blocked}))
        events = telemetry.list_events(runtime.get_controller(cid).run_id, limit=500)
        executed = [e for e in events if e.event_type == "graph.executed"]
        assert executed and executed[-1].status != "completed", [e.status for e in executed]
    finally:
        telemetry.close()
