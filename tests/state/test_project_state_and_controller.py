import json
import subprocess
import sys
import textwrap
import time

import pytest

from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult, ToolResultHandle
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
from nailong_agent_sdk.state.project_state_engine import ProjectStateProjector, ProjectStateReducer
from nailong_agent_sdk.state.project_state_models import (
    ArtifactStatus,
    ProjectStateProjectionPolicy,
    StageStateSchema,
    StateAuthority,
)
from nailong_agent_sdk.state.project_state_store import (
    FileProjectStateStore,
    InMemoryProjectStateStore,
)
from nailong_agent_sdk.state.shared_state import SharedSubstrateSnapshot
from nailong_agent_sdk.state.stage_gates import StageCompletenessGate, StageCompletenessPolicy
from tests.support.controllers import executing_runtime
from tests.support.plans import AGENT, arun, ok, run_ok, simple_plan, task
from tests.support.processes import child_environment
from tests.support.project_state import SCHEMA, T, evidence, transition


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


def test_reducer_authority_rules_and_payload_errors():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    with pytest.raises(ValueError, match="Only a human authority"):
        store.apply(
            "p",
            transition(
                T.HUMAN_DECISION,
                {"decision_id": "d", "content": "c", "status": "locked"},
                StateAuthority.HARNESS,
            ),
        )
    state = store.apply(
        "p",
        transition(
            T.HUMAN_DECISION,
            {"decision_id": "d", "content": "c", "status": "locked"},
            StateAuthority.HUMAN,
        ),
    )
    assert state.decisions[0].authority is StateAuthority.HUMAN and state.revision == 1
    with pytest.raises(ValueError, match="Only controller or harness"):
        store.apply(
            "p",
            transition(
                T.WORK_ITEM_UPDATED,
                {"work_item_id": "w", "status": "completed", "owner": "o"},
                StateAuthority.HUMAN,
            ),
        )
    with pytest.raises(ValueError, match="Only controller or human"):
        store.apply(
            "p",
            transition(
                T.STAGE_CHANGED, {"stage_schema": SCHEMA.model_dump()}, StateAuthority.HARNESS
            ),
        )
    try:
        store.apply("p", transition(T.HUMAN_DECISION, {"decision_id": "d2"}, StateAuthority.HUMAN))
        outcome = "ok"
    except Exception as error:
        outcome = f"{type(error).__name__}: {error}"
    assert outcome.startswith("ValueError"), outcome
    with pytest.raises(ValueError, match="unknown"):
        store.load("nope")
    assert store.events("p")[0].previous_state_hash != store.events("p")[0].state_hash


def test_stage_change_requires_fields_and_gate_semantics():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    new_schema = StageStateSchema(schema_id="s2", stage="build", required_field_ids=["target"])
    with pytest.raises(ValueError, match="missing required stage fields"):
        store.apply(
            "p",
            transition(
                T.STAGE_CHANGED,
                {"stage_schema": new_schema.model_dump(), "stage_fields": []},
                StateAuthority.CONTROLLER,
            ),
        )
    field = {"field_id": "target", "value": 1, "evidence": [e.model_dump() for e in evidence()]}
    state = store.apply(
        "p",
        transition(
            T.STAGE_CHANGED,
            {"stage_schema": new_schema.model_dump(), "stage_fields": [field]},
            StateAuthority.CONTROLLER,
        ),
    )
    assert state.stage == "build"


def test_capacity_eviction_rules_and_error_message():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    for i in range(1_030):
        store.apply(
            "p",
            transition(
                T.TOOL_OUTCOME,
                {
                    "tool_call_id": f"c{i}",
                    "tool_name": "write_draft",
                    "status": "succeeded",
                    "artifact": {
                        "relative_path": f"f{i}.txt",
                        "artifact_id": f"sha256:{i}",
                        "status": "in-progress",
                    },
                },
                action=f"c{i}",
            ),
        )
    state = store.load("p")
    assert len(state.artifacts) == 1_024 and state.artifacts[-1].relative_path == "f1029.txt"
    assert state.artifacts[0].relative_path == "f6.txt"
    for i in range(257):
        try:
            store.apply(
                "p",
                transition(
                    T.QUESTION_OPENED,
                    {"question_id": f"q{i}", "content": "?", "owner": "human"},
                    action=f"q{i}",
                ),
            )
        except AgentSdkError as error:
            assert (
                error.code == "PROJECT_STATE_CAPACITY_EXCEEDED"
                and "question" in str(error)
                and "q256" in str(error)
            )
            break
    else:
        pytest.fail("question capacity was never enforced")


def test_tool_outcome_tracks_artifacts_from_every_write_tool():
    handle = ToolResultHandle(handle_id="handle-1", content_hash="h", byte_count=1, truncated=False)
    results = {
        "write_draft": ToolExecutionResult(
            status="succeeded", output={"artifact_id": "sha256:a", "relative_path": "a.md"}
        ),
        "edit_draft": ToolExecutionResult(
            status="succeeded",
            output={
                "replacements": 1,
                "artifact": {"artifact_id": "sha256:b", "relative_path": "a.md"},
            },
        ),
        "notebook_edit": ToolExecutionResult(
            status="succeeded",
            output={
                "cell_index": 0,
                "artifact": {"artifact_id": "sha256:c", "relative_path": "n.ipynb"},
            },
        ),
    }
    tracked = {}
    for name, result in results.items():
        transition_ = ProjectStateReducer.tool_transition(
            ToolCall(id=f"c-{name}", name=name), result, handle
        )
        tracked[name] = "artifact" in transition_.payload
    assert all(tracked.values()), tracked


def draft_outcome(relative_path="a.md", artifact_id="sha256:a", status="in-progress"):
    return transition(
        T.TOOL_OUTCOME,
        {
            "tool_call_id": f"c-{artifact_id}",
            "tool_name": "write_draft",
            "status": "succeeded",
            "artifact": {
                "relative_path": relative_path,
                "artifact_id": artifact_id,
                "status": status,
            },
        },
        action=f"c-{artifact_id}",
    )


def artifact_status_update(
    status="complete", artifact_id="sha256:a", actor=StateAuthority.CONTROLLER
):
    return transition(
        T.ARTIFACT_STATUS_UPDATED,
        {"relative_path": "a.md", "artifact_id": artifact_id, "status": status},
        actor,
        action=f"status-{status}-{artifact_id}",
    )


def test_a_tool_outcome_cannot_mark_an_artifact_complete():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    with pytest.raises(ValueError, match='Artifact "a.md" cannot be recorded as complete'):
        store.apply("p", draft_outcome(status="complete"))


def test_the_controller_can_complete_the_artifact_it_names_by_id():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    store.apply("p", draft_outcome())
    store.apply("p", artifact_status_update())
    assert {a.status for a in store.load("p").artifacts} == {ArtifactStatus.COMPLETE}


def test_artifact_status_updates_need_controller_or_human_authority():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    store.apply("p", draft_outcome())
    with pytest.raises(ValueError, match="Only controller or human authority"):
        store.apply("p", artifact_status_update(actor=StateAuthority.HARNESS))


def test_a_completion_decision_for_an_older_artifact_version_is_rejected():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    store.apply("p", draft_outcome(artifact_id="sha256:new"))
    with pytest.raises(ValueError, match="recorded as sha256:new, but the status update names"):
        store.apply("p", artifact_status_update(artifact_id="sha256:old"))


def test_a_completion_decision_for_an_unrecorded_artifact_is_rejected():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    with pytest.raises(ValueError, match='Artifact "a.md" is not recorded'):
        store.apply("p", artifact_status_update())


def test_editing_a_completed_artifact_returns_it_to_in_progress():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    store.apply("p", draft_outcome())
    store.apply("p", artifact_status_update())
    store.apply("p", draft_outcome(artifact_id="sha256:edited"))
    (artifact,) = store.load("p").artifacts
    assert artifact.status is ArtifactStatus.IN_PROGRESS and artifact.artifact_id == "sha256:edited"


def test_stage_gate_with_required_artifacts_passes_once_the_controller_completes_them(tmp_path):
    runtime = ControllerRuntime(tmp_path)
    snapshot = SharedSubstrateSnapshot(snapshot_id="snap", version="1", content_hash="h")
    profile = SkillToolProfile(stage="design", source_snapshot_id="snap")
    rules = ComplexityRoutingRules(multi_agent_min_categories=3, multi_agent_min_blast_radius=5)
    controller = runtime.create_controller(
        snapshot, profile, rules, GapMetadata(), max_repair_attempts=2
    )
    runtime.submit_plan(controller.controller_id, simple_plan())
    runtime.approve_plan(controller.controller_id, True)
    runtime.dispatch(controller.controller_id)
    runtime._project_state_store.apply("snap", draft_outcome())
    policy = StageCompletenessPolicy(
        policy_id="p", stage="design", required_artifact_paths=["a.md"]
    )
    before = StageCompletenessGate().evaluate(
        runtime.project_state(controller.controller_id), policy
    )
    assert not before.complete and before.incomplete_artifact_paths == ["a.md"]
    runtime.set_artifact_status(
        controller.controller_id, "a.md", ArtifactStatus.COMPLETE, reason="reviewed"
    )
    decision = runtime.evaluate_stage_completeness(controller.controller_id, policy)
    assert decision.complete, decision.reasons
    with pytest.raises(ValueError, match='Artifact "missing.md" is not recorded'):
        runtime.set_artifact_status(
            controller.controller_id, "missing.md", ArtifactStatus.COMPLETE, reason="x"
        )


def test_file_project_state_store_roundtrip_hash_chain_and_tamper(tmp_path):
    store = FileProjectStateStore(tmp_path)
    store.ensure("p", SCHEMA)
    for i in range(5):
        store.apply(
            "p",
            transition(
                T.QUESTION_OPENED,
                {"question_id": f"q{i}", "content": "?", "owner": "human"},
                action=f"q{i}",
            ),
        )
    fresh = FileProjectStateStore(tmp_path)
    state = fresh.load("p")
    assert state.revision == 5 and len(fresh.events("p")) == 5
    import hashlib

    key = hashlib.sha256(b"p").hexdigest()
    event_file = tmp_path / ".agent-project-state" / key / "00000003.json"
    original = event_file.read_text("utf-8")
    event_file.write_text(original.replace('"revision":3', '"revision":4'), "utf-8")
    with pytest.raises(Exception):
        FileProjectStateStore(tmp_path).load("p")
    event_file.write_text(original, "utf-8")
    event_file.unlink()
    with pytest.raises(ValueError, match="is at revision 5 but 4 events are persisted"):
        FileProjectStateStore(tmp_path).load("p")


def test_crash_between_event_and_state_write_does_not_brick_the_project(tmp_path, monkeypatch):
    store = FileProjectStateStore(tmp_path)
    store.ensure("p", SCHEMA)
    store.apply(
        "p",
        transition(
            T.QUESTION_OPENED, {"question_id": "q0", "content": "?", "owner": "human"}, action="q0"
        ),
    )
    real = FileProjectStateStore._write_json

    def failing(path, value):
        if path.parent.name == ".agent-project-state" and path.suffix == ".json":
            raise OSError("simulated crash before state write")
        real(path, value)

    monkeypatch.setattr(FileProjectStateStore, "_write_json", staticmethod(failing))
    with pytest.raises(OSError):
        store.apply(
            "p",
            transition(
                T.QUESTION_OPENED,
                {"question_id": "q1", "content": "?", "owner": "human"},
                action="q1",
            ),
        )
    monkeypatch.undo()
    try:
        fresh = FileProjectStateStore(tmp_path)
        fresh.load("p")
        fresh.apply(
            "p",
            transition(
                T.QUESTION_OPENED,
                {"question_id": "q1", "content": "?", "owner": "human"},
                action="q1",
            ),
        )
        outcome = f"recovered at revision {fresh.load('p').revision}"
    except Exception as error:
        outcome = f"{type(error).__name__}: {error}"
    assert outcome.startswith("recovered"), outcome


def test_concurrent_reader_never_sees_a_spurious_integrity_failure(tmp_path):
    writer = textwrap.dedent(
        f"""
        import sys, time
        from pathlib import Path
        from tests.support.project_state import SCHEMA, T, transition
        from nailong_agent_sdk.state.project_state_store import FileProjectStateStore
        store = FileProjectStateStore(Path(r"{tmp_path}"))
        store.ensure("p", SCHEMA)
        print("ready", flush=True)
        for i in range(120):
            payload = {{"question_id": f"q{{i}}", "content": "?", "owner": "human"}}
            store.apply("p", transition(T.QUESTION_OPENED, payload, action=f"q{{i}}"))
        print("done", flush=True)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", writer],
        env=child_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout.readline().strip() == "ready"
    reader = FileProjectStateStore(tmp_path)
    errors, reads = [], 0
    while proc.poll() is None:
        try:
            reader.load("p")
            reads += 1
        except Exception as error:
            errors.append(f"{type(error).__name__}: {str(error)[:100]}")
    stdout, stderr = proc.communicate(timeout=120)
    assert proc.returncode == 0 and "done" in stdout, stderr[-500:]
    assert errors == [], errors[:2]
    assert reads > 0


def test_projection_budget_behaviour():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    for i in range(60):
        store.apply(
            "p",
            transition(
                T.TOOL_OUTCOME,
                {
                    "tool_call_id": f"c{i}",
                    "tool_name": "write_draft",
                    "status": "succeeded",
                    "artifact": {
                        "relative_path": f"dir/file-{i}.txt",
                        "artifact_id": f"sha256:{i:064d}",
                        "status": "in-progress",
                    },
                },
                action=f"c{i}",
            ),
        )
    state = store.load("p")
    view = ProjectStateProjector(ProjectStateProjectionPolicy(token_budget=400)).project(state)
    assert view.over_budget is False and view.omitted_artifact_count > 0
    assert view.artifacts[-1].relative_path == "dir/file-59.txt"
    tiny = ProjectStateProjector(ProjectStateProjectionPolicy(token_budget=128)).project(state)
    assert tiny.estimated_tokens <= 128 or tiny.over_budget


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
