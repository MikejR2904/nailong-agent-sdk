import asyncio
import json
import subprocess
import sys
import textwrap
import time

import pytest

import nailong_agent_sdk.state.run_state_store as rss
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.state.graph_models import (
    GraphNodeResult,
    GraphNodeStatus,
    GraphSharedState,
)
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.planning import Plan
from nailong_agent_sdk.state.run_state_store import RunStateStore
from nailong_agent_sdk.state.shared_state import (
    DiscoveryRoutingRefs,
    ExploratoryDiscovery,
    LateralDependencyRequest,
    SharedStateWrite,
    SharedSubstrateSnapshot,
)
from nailong_agent_sdk.tools.approvals import ApprovalRegistry, ApprovalStatus
from tests.support.payloads import nested
from tests.support.plans import AGENT, arun, chain_plan, ok, run_ok, simple_plan, task
from tests.support.processes import child_environment


def test_invalid_plan_rejection_names_the_validation_errors(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    bad = Plan(plan_id="bad", tasks=[task("T1", deps=["ghost"])])
    with pytest.raises(ValueError) as excinfo:
        coordinator.start_run(bad)
    assert "ghost" in str(excinfo.value) or "UNKNOWN_TASK_DEPENDENCY" in str(excinfo.value), str(
        excinfo.value
    )


def test_run_lifecycle_resume_and_integrity(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    started = coordinator.start_run(simple_plan())
    assert started.run_id == "run-1"
    final = arun(coordinator.execute_run(started.run_id, {AGENT: run_ok}))
    assert all(v == "completed" for v in final.graph["statuses"].values())
    fresh = HarnessCoordinator(tmp_path)
    resumed = fresh.resume_run("run-1")
    assert resumed.run_hash == final.run_hash and resumed.graph["results"] == final.graph["results"]
    assert HarnessCoordinator(tmp_path).start_run(simple_plan()).run_id == "run-2"
    snapshot_path = tmp_path / ".agent-runs" / "run-1.json"
    original = snapshot_path.read_text("utf-8")
    payload = json.loads(original)
    payload["graph"]["statuses"]["node:T1"] = "failed"
    snapshot_path.write_text(json.dumps(payload), "utf-8")
    with pytest.raises(ValueError, match='Run "run-1" failed integrity verification'):
        HarnessCoordinator(tmp_path).get_run_state("run-1")
    snapshot_path.write_text(original, "utf-8")
    history = tmp_path / ".agent-runs" / "run-1.history.jsonl"
    lines = history.read_text("utf-8").splitlines()
    history.write_text("\n".join(lines[:-1]) + "\n", "utf-8")
    with pytest.raises(ValueError, match="history failed integrity verification"):
        HarnessCoordinator(tmp_path).get_run_state("run-1")
    history.write_text("\n".join(lines) + "\n", "utf-8")
    entry = json.loads(lines[0])
    entry["value"]["reason"] = "tampered"
    history.write_text(json.dumps(entry) + "\n" + "\n".join(lines[1:]) + "\n", "utf-8")
    with pytest.raises(ValueError, match="history failed integrity verification"):
        HarnessCoordinator(tmp_path).get_run_state("run-1")
    history.write_text(
        "\n".join(lines)
        + "\n"
        + '{"kind": "event", "value": {"sequence": 99, "node_id": "x", "status": "running"}}\n',
        "utf-8",
    )
    assert HarnessCoordinator(tmp_path).get_run_state("run-1").run_hash == final.run_hash
    history.unlink()
    with pytest.raises(ValueError, match="history failed integrity verification"):
        HarnessCoordinator(tmp_path).get_run_state("run-1")


def test_crash_before_snapshot_publish_recovers_previous_generation(tmp_path, monkeypatch):
    coordinator = HarnessCoordinator(tmp_path)
    record = coordinator.start_run(simple_plan())
    before = coordinator.get_run_state(record.run_id)
    real = rss._replace_with_retry
    calls = {"n": 0}

    def crash_once(temporary, target, *, attempts=5):
        calls["n"] += 1
        raise OSError("simulated crash before publish")

    monkeypatch.setattr(rss, "_replace_with_retry", crash_once)
    with pytest.raises(OSError, match="simulated crash"):
        coordinator.record_node_result(record.run_id, "node:T1", ok({"v": 1}))
    monkeypatch.setattr(rss, "_replace_with_retry", real)
    fresh = HarnessCoordinator(tmp_path)
    loaded = fresh.get_run_state(record.run_id)
    assert loaded.run_hash == before.run_hash
    after = fresh.record_node_result(record.run_id, "node:T1", ok({"v": 1}))
    assert after.graph["statuses"]["node:T1"] == "completed"
    again = HarnessCoordinator(tmp_path).get_run_state(record.run_id)
    assert again.run_hash == after.run_hash


def test_discoveries_values_and_lateral_requests_survive_restarts_and_later_saves(tmp_path):
    snapshot = SharedSubstrateSnapshot(snapshot_id="s", version="1", content_hash="h")
    coordinator = HarnessCoordinator(tmp_path)
    plan = Plan(plan_id="p", tasks=[task("A"), task("B"), task("C")])
    run_id = coordinator.start_run(plan, shared_state=GraphSharedState(substrate=snapshot)).run_id
    coordinator.record_node_result(run_id, "node:A", ok("a done"))
    coordinator.publish_discovery(
        run_id,
        ExploratoryDiscovery(
            episode_id="e1",
            producer_node_id="node:A",
            owner_id="o",
            snapshot_id="s",
            snapshot_version="1",
            source_spans=["x"],
            description="finding",
            payload={"k": "v"},
            provenance_hash="p",
            affected_refs=DiscoveryRoutingRefs(task_ids=["B"]),
        ),
    )
    coordinator.write_shared_value(
        run_id,
        SharedStateWrite(producer_node_id="node:A", key="k", value={"a": 1}, provenance_hash="h"),
    )
    saved = coordinator.request_lateral_dependency(
        run_id,
        LateralDependencyRequest(
            consumer_node_id="node:C",
            consumer_action_id="act",
            discovery_episode_id="e1",
            reason="needs the finding",
        ),
    )
    loaded = HarnessCoordinator(tmp_path).get_run_state(run_id)
    assert loaded.graph == saved.graph and loaded.run_hash == saved.run_hash
    shared = loaded.graph["shared_state"]
    assert sorted(shared["discoveries"]) == ["e1"] and sorted(shared["values"]) == ["k"]
    assert {key: len(items) for key, items in shared["lateral_dependencies"].items()} == {"e1": 1}
    HarnessCoordinator(tmp_path).record_node_result(run_id, "node:B", ok("b done"))
    later = HarnessCoordinator(tmp_path).get_run_state(run_id)
    assert later.graph["statuses"]["node:B"] == "completed"
    assert later.graph["shared_state"] == shared


def test_a_second_coordinator_reloads_and_merges_instead_of_overwriting(tmp_path):
    a = HarnessCoordinator(tmp_path)
    run_id = a.start_run(simple_plan()).run_id
    b = HarnessCoordinator(tmp_path)
    b.get_run_state(run_id)
    a.record_node_result(run_id, "node:T1", ok())
    merged = b.record_node_result(run_id, "node:T3", ok())
    assert merged.graph["statuses"]["node:T1"] == "completed"
    assert merged.graph["statuses"]["node:T3"] == "completed"
    assert a.get_run_state(run_id).run_hash == merged.run_hash


def test_a_change_by_another_coordinator_during_execution_is_reported_with_guidance(tmp_path):
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
    assert b.get_run_state(run_id).cancelled


def test_cancel_semantics(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id
    cancelled = coordinator.cancel_run(run_id)
    assert cancelled.cancelled and cancelled.graph["statuses"]["node:T1"] == "cancelled"
    assert cancelled.graph["statuses"]["node:T2"] == "blocked"
    again = arun(coordinator.execute_run(run_id, {AGENT: run_ok}))
    assert again.cancelled
    assert HarnessCoordinator(tmp_path).get_run_state(run_id).cancelled


def test_cancel_during_execution_stops_further_waves(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id
    seen = []

    async def executor(node, context):
        seen.append(node.node_id)
        if node.node_id == "node:T1":
            coordinator._cancelled.add(run_id)
        return ok()

    record = arun(coordinator.execute_run(run_id, {AGENT: executor}))
    assert "node:T2" not in seen and record.cancelled


def test_approvals_survive_a_restart(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id
    request = coordinator.approvals(run_id).request(run_id, "node:T1", "process.execute", "r")
    fresh = HarnessCoordinator(tmp_path)
    decided = fresh.submit_approval(run_id, request.approval_id, True, "ok")
    assert decided.status is ApprovalStatus.APPROVED and decided.decision_reason == "ok"
    assert HarnessCoordinator(tmp_path).approvals(run_id).get(request.approval_id) == decided
    with pytest.raises(ValueError, match='already has the decision "approved"'):
        HarnessCoordinator(tmp_path).submit_approval(run_id, request.approval_id, False)


def test_approvals_of_an_unknown_run_are_reported_as_unknown(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    with pytest.raises(ValueError, match='Run "ghost" is unknown'):
        coordinator.approvals("ghost")
    with pytest.raises(ValueError, match='Run "../escape" is unknown'):
        coordinator.submit_approval("../escape", "approval-1", True)


def test_an_identical_blocked_request_is_not_duplicated(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id
    registry = coordinator.approvals(run_id)
    first = registry.request(run_id, "node:T1", "process.execute", "first reason")
    again = registry.request(run_id, "node:T1", "process.execute", "second reason")
    other_node = registry.request(run_id, "node:T2", "process.execute", "r")
    other_capability = registry.request(run_id, "node:T1", "draft.write", "r")
    assert again == first
    assert len({first.approval_id, other_node.approval_id, other_capability.approval_id}) == 3
    registry.submit(first.approval_id, False, "no")
    reissued = registry.request(run_id, "node:T1", "process.execute", "after rejection")
    assert reissued.approval_id not in {first.approval_id, other_node.approval_id}


def test_two_registries_on_one_file_never_issue_the_same_approval_id(tmp_path):
    first = ApprovalRegistry(tmp_path / "approvals.json")
    second = ApprovalRegistry(tmp_path / "approvals.json")
    ids = [
        registry.request("run-1", f"node:{label}{index}", "draft.write", "r").approval_id
        for index in range(5)
        for label, registry in (("a", first), ("b", second))
    ]
    assert len(set(ids)) == 10
    assert {request.approval_id for request in first.list()} == set(ids)
    assert second.get(ids[0]) == first.get(ids[0])


def test_a_blocked_node_is_reopened_only_after_its_approval_is_granted(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id

    async def block_first(node, context):
        if node.node_id == "node:T1":
            coordinator.approvals(run_id).request(run_id, node.node_id, "draft.write", "needs it")
            return GraphNodeResult(status=GraphNodeStatus.BLOCKED, reason="needs approval")
        return ok()

    blocked = arun(coordinator.execute_run(run_id, {AGENT: block_first}))
    assert blocked.graph["statuses"]["node:T1"] == "blocked"
    assert blocked.graph["statuses"]["node:T2"] == "blocked"
    still_blocked = coordinator.resume_run(run_id)
    assert still_blocked.graph["statuses"]["node:T1"] == "blocked"
    coordinator.submit_approval(run_id, "approval-1", True)
    reopened = coordinator.resume_run(run_id)
    assert reopened.graph["statuses"]["node:T1"] == "runnable"
    assert reopened.graph["statuses"]["node:T2"] == "pending"
    final = arun(coordinator.execute_run(run_id, {AGENT: run_ok}))
    assert set(final.graph["statuses"].values()) == {"completed"}
    assert HarnessCoordinator(tmp_path).get_run_state(run_id).run_hash == final.run_hash


def test_a_rejected_approval_does_not_reopen_its_blocked_node(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id

    async def block_first(node, context):
        if node.node_id == "node:T1":
            coordinator.approvals(run_id).request(run_id, node.node_id, "draft.write", "needs it")
            return GraphNodeResult(status=GraphNodeStatus.BLOCKED, reason="needs approval")
        return ok()

    arun(coordinator.execute_run(run_id, {AGENT: block_first}))
    coordinator.submit_approval(run_id, "approval-1", False, "not allowed")
    assert coordinator.resume_run(run_id).graph["statuses"]["node:T1"] == "blocked"


def test_blocked_nodes_can_be_reopened_explicitly_and_only_if_blocked_by_their_own_result(
    tmp_path,
):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id

    async def block_first(node, context):
        if node.node_id == "node:T1":
            return GraphNodeResult(status=GraphNodeStatus.BLOCKED, reason="waiting")
        return ok()

    arun(coordinator.execute_run(run_id, {AGENT: block_first}))
    with pytest.raises(ValueError, match=r"\['node:T2'\] are not blocked by their own result"):
        coordinator.reopen_blocked_nodes(run_id, ["node:T2"])
    reopened = coordinator.reopen_blocked_nodes(run_id)
    assert reopened.graph["statuses"]["node:T1"] == "runnable"
    final = arun(coordinator.execute_run(run_id, {AGENT: run_ok}))
    assert set(final.graph["statuses"].values()) == {"completed"}


def test_interrupted_run_recovery(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id

    async def hang(node, context):
        raise asyncio.CancelledError()

    async def scenario():
        try:
            await coordinator.execute_run(run_id, {AGENT: hang})
        except asyncio.CancelledError:
            return "cancelled"

    arun(scenario())
    fresh = HarnessCoordinator(tmp_path)
    state = fresh.get_run_state(run_id)
    assert state.graph["statuses"]["node:T1"] == "running"
    recovered = fresh.recover_interrupted_run(run_id, replayable_node_ids={"node:T1"})
    assert recovered.graph["statuses"]["node:T1"] == "runnable"
    assert recovered.graph["statuses"]["node:T3"] == "failed"


def test_run_id_is_not_a_path(tmp_path):
    store = RunStateStore(tmp_path)
    outside = tmp_path / "outside_secret.json"
    outside.write_text('{"api_key": "TOP-SECRET-IN-OUTSIDE-FILE", "unexpected": true}', "utf-8")
    outcomes = {}
    for run_id in ("../outside_secret", "..\\outside_secret", str(tmp_path / "outside_secret")):
        try:
            store.load(run_id)
            outcomes[run_id] = "loaded"
        except Exception as error:
            outcomes[run_id] = f"{type(error).__name__}: {str(error)[:300]}"
        outcomes[run_id + "|exists"] = store.exists(run_id)
    blob = json.dumps(outcomes)
    assert "TOP-SECRET" not in blob and not any(
        v is True for k, v in outcomes.items() if k.endswith("|exists")
    ), outcomes


def test_two_processes_starting_runs_do_not_collide(tmp_path):
    script = textwrap.dedent(
        f"""
        import sys, json
        from pathlib import Path
        from tests.support.plans import simple_plan
        from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
        import time
        coordinator = HarnessCoordinator(Path(r"{tmp_path}"))
        ids = []
        deadline = float(sys.argv[1])
        while time.time() < deadline:
            time.sleep(0.0005)
        for _ in range(25):
            ids.append(coordinator.start_run(simple_plan()).run_id)
        print(json.dumps(ids))
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
    lists = []
    for proc in procs:
        out, err = proc.communicate(timeout=300)
        assert proc.returncode == 0, err[-500:]
        lists.append(json.loads(out.strip().splitlines()[-1]))
    both = set(lists[0]) & set(lists[1])
    assert not both, f"{len(both)} run ids handed out to both processes"


def test_overdeep_node_output_fails_that_node_and_leaves_the_run_readable(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id

    async def executor(node, context):
        return ok(nested(120) if node.node_id == "node:T1" else None)

    final = arun(coordinator.execute_run(run_id, {AGENT: executor}))
    reloaded = HarnessCoordinator(tmp_path).get_run_state(run_id)
    assert reloaded.run_hash == final.run_hash
    assert final.graph["statuses"]["node:T1"] == "failed"
    assert "nests more than 64 levels deep" in final.graph["results"]["node:T1"]["reason"]


def test_persistent_execution_scaling(tmp_path):
    report = {}
    for size in (100, 300, 600):
        root = tmp_path / f"s{size}"
        coordinator = HarnessCoordinator(root)
        plan = chain_plan(size, plan_id="chain")
        run_id = coordinator.start_run(plan).run_id

        async def executor(node, context):
            return ok({"echo": node.node_id, "payload": "x" * 200})

        started = time.monotonic()
        arun(coordinator.execute_run(run_id, {AGENT: executor}), timeout=1500)
        elapsed = time.monotonic() - started
        sizes = {p.name: p.stat().st_size for p in (root / ".agent-runs").iterdir() if p.is_file()}
        report[size] = {
            "seconds": round(elapsed, 2),
            "files_kb": {k: round(v / 1024) for k, v in sizes.items()},
        }
    assert report[600]["seconds"] < 60, report
