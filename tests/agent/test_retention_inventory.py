import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from nailong_agent_sdk.agent.orchestrator import Orchestrator
from nailong_agent_sdk.agent.retention import RetentionPolicy, prune_run_root
from nailong_agent_sdk.agent.retention_inventory import (
    Disposition,
    Ownership,
    ResourceClass,
    inventory_run_root,
)
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
from nailong_agent_sdk.memory.episode_store import FileEpisodeStore
from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.project_state_models import StageStateSchema
from nailong_agent_sdk.state.project_state_store import FileProjectStateStore
from tests.support.controllers import new_controller
from tests.support.orchestration import make_policy, make_request
from tests.support.plans import arun, simple_plan
from tests.support.retention_runs import emit, finished_run

POLICY = RetentionPolicy(older_than_seconds=3_600)


def later():
    return datetime.now(UTC) + timedelta(days=2)


def age(path, days=3):
    moment = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    targets = [path]
    if path.is_dir():
        targets.extend(sorted(path.rglob("*")))
    for target in targets:
        os.utime(target, (moment, moment))


def snapshot_of(root):
    return sorted(
        (path.relative_to(root).as_posix(), path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    )


@pytest.fixture
def populated(tmp_path):
    services = AgentRuntimeServices.open(tmp_path)
    finished_run(services, "old-run", handles=2, entries=3)
    finished_run(services, "live-run", handles=1, entries=1, terminal=False)
    services.result_journal.record(
        ToolCall(id="loose", name="echo", arguments={}),
        ToolExecutionResult(status="succeeded", output={"loose": True}),
    )
    services.telemetry.create_run_report("old-run")
    emit(services, "mcp-decisions", "mcp.decision.applied")
    workspace = tmp_path / "workspaces"
    (workspace / "old-run").mkdir(parents=True)
    (workspace / "old-run" / "out.txt").write_text("finished work", encoding="utf-8")
    (workspace / "stranger").mkdir()
    (workspace / "stranger" / "x.txt").write_text("who owns this", encoding="utf-8")
    (tmp_path / "scratch-notes.txt").write_text("left by an operator", encoding="utf-8")
    yield services
    services.audit_logs.close()
    services.telemetry.close()


def by_id(inventory, resource_class):
    return {
        item.resource_id: item for item in inventory.items if item.resource_class is resource_class
    }


def test_run_scoped_classes_follow_the_runs_verdict_and_say_why(populated, tmp_path):
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    telemetry = by_id(inventory, ResourceClass.TELEMETRY)
    assert telemetry["old-run"].disposition is Disposition.PRUNABLE
    assert telemetry["old-run"].ownership is Ownership.RUN_OWNED
    assert telemetry["live-run"].disposition is Disposition.PROTECTED
    assert 'no "agent.terminated" event' in telemetry["live-run"].reason
    audit = by_id(inventory, ResourceClass.AUDIT_TRANSCRIPT)
    assert audit["old-run"].disposition is Disposition.PRUNABLE
    assert audit["live-run"].disposition is Disposition.PROTECTED
    results = by_id(inventory, ResourceClass.TOOL_RESULTS)
    assert results["old-run"].disposition is Disposition.PRUNABLE and results["old-run"].files == 2
    assert results["live-run"].disposition is Disposition.PROTECTED


def test_the_decision_stream_is_protected_and_says_the_runtime_writes_it(populated, tmp_path):
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    stream = by_id(inventory, ResourceClass.TELEMETRY)["mcp-decisions"]
    assert stream.disposition is Disposition.PROTECTED
    assert "the runtime itself writes" in stream.reason


def test_tool_results_without_a_run_are_unowned_and_protected(populated, tmp_path):
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    (loose,) = by_id(inventory, ResourceClass.UNSCOPED_TOOL_RESULTS).values()
    assert loose.ownership is Ownership.UNOWNED and loose.files == 1
    assert loose.disposition is Disposition.PROTECTED
    assert "recorded without a run scope" in loose.reason


def test_exported_reports_and_retention_evidence_are_protected_as_evidence(populated, tmp_path):
    prune_run_root(
        tmp_path,
        RetentionPolicy(older_than_seconds=3_600, export_reports=True),
        dry_run=False,
        now=later(),
    )
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    reports = by_id(inventory, ResourceClass.EXPORTED_REPORT)
    assert reports and all(item.ownership is Ownership.EVIDENCE for item in reports.values())
    assert all(item.disposition is Disposition.PROTECTED for item in reports.values())
    evidence = by_id(inventory, ResourceClass.RETENTION_EVIDENCE)
    assert "tombstones.jsonl" in evidence and inventory.tombstones == 1
    assert all(item.disposition is Disposition.PROTECTED for item in evidence.values())


def test_a_workspace_follows_its_run_and_an_unknown_one_is_unowned(populated, tmp_path):
    age(tmp_path / "workspaces" / "old-run")
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    workspaces = by_id(inventory, ResourceClass.WORKSPACE)
    assert workspaces["old-run"].disposition is Disposition.RECLAIMABLE
    assert workspaces["old-run"].owner == "old-run"
    assert "no SDK operation removes it" in workspaces["old-run"].reason
    assert workspaces["stranger"].ownership is Ownership.UNOWNED
    assert workspaces["stranger"].disposition is Disposition.PROTECTED


def test_a_workspace_of_a_retained_run_is_protected_with_the_runs_reason(populated, tmp_path):
    (tmp_path / "workspaces" / "live-run").mkdir()
    (tmp_path / "workspaces" / "live-run" / "wip.txt").write_text("in progress", encoding="utf-8")
    age(tmp_path / "workspaces" / "live-run")
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    held = by_id(inventory, ResourceClass.WORKSPACE)["live-run"]
    assert held.disposition is Disposition.PROTECTED
    assert held.reason.startswith("its run is retained:")


def test_a_workspace_written_inside_the_window_is_protected_even_for_a_finished_run(
    populated, tmp_path
):
    now = datetime.now(UTC) + timedelta(seconds=5)
    fresh = (now - timedelta(seconds=0.2)).timestamp()
    for target in (tmp_path / "workspaces" / "old-run").rglob("*"):
        os.utime(target, (fresh, fresh))
    inventory = inventory_run_root(tmp_path, RetentionPolicy(older_than_seconds=1), now=now)
    runs = by_id(inventory, ResourceClass.TELEMETRY)
    assert runs["old-run"].disposition is Disposition.PRUNABLE
    held = by_id(inventory, ResourceClass.WORKSPACE)["old-run"]
    assert held.disposition is Disposition.PROTECTED
    assert "within the retention window" in held.reason


def test_unclassified_entries_are_accounted_for_and_left_alone(populated, tmp_path):
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    (note,) = by_id(inventory, ResourceClass.UNCLASSIFIED).values()
    assert note.resource_id == "scratch-notes.txt" and note.disposition is Disposition.PROTECTED
    assert note.bytes == len("left by an operator")


def test_totals_reconcile_and_every_listed_item_is_in_a_class_summary(populated, tmp_path):
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    assert inventory.total_items == len(inventory.items) + inventory.items_omitted
    assert inventory.total_bytes == sum(summary.bytes for summary in inventory.classes)
    assert inventory.total_bytes == (
        inventory.prunable_bytes + inventory.reclaimable_bytes + inventory.protected_bytes
    )
    summarized = {summary.resource_class for summary in inventory.classes}
    assert {item.resource_class for item in inventory.items} <= summarized
    assert inventory.telemetry_database_bytes > 0
    telemetry = inventory.of_class(ResourceClass.TELEMETRY)
    assert telemetry is not None and telemetry.prunable == 1 and telemetry.protected == 2


def test_the_item_list_is_bounded_but_the_totals_are_not(populated, tmp_path):
    full = inventory_run_root(tmp_path, POLICY, now=later())
    cut = inventory_run_root(tmp_path, POLICY, now=later(), max_items=3)
    assert len(cut.items) == 3 and cut.items_omitted == full.total_items - 3
    assert cut.total_bytes == full.total_bytes and cut.classes == full.classes


def test_an_inventory_writes_nothing(populated, tmp_path):
    before = snapshot_of(tmp_path)
    inventory_run_root(tmp_path, POLICY, now=later())
    inventory_run_root(tmp_path, RetentionPolicy(older_than_seconds=1, include_unfinished=True))
    assert snapshot_of(tmp_path) == before


def test_an_empty_or_missing_run_root_is_reported_plainly(tmp_path):
    empty = inventory_run_root(tmp_path, POLICY)
    assert empty.total_items == 0 and empty.classes == [] and empty.clean
    with pytest.raises(ValueError, match="does not exist or is not a directory"):
        inventory_run_root(tmp_path / "missing", POLICY)
    with pytest.raises(ValueError, match="timezone-aware"):
        inventory_run_root(tmp_path, POLICY, now=datetime.now())
    with pytest.raises(ValueError, match="max_items"):
        inventory_run_root(tmp_path, POLICY, max_items=-1)


def test_a_run_whose_chain_is_broken_is_protected_with_the_integrity_reason(populated, tmp_path):
    database = tmp_path / ".agent-telemetry" / "telemetry.sqlite3"
    populated.telemetry.close()
    import sqlite3

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE events SET event_json = replace(event_json, 'ok', 'changed') "
            "WHERE run_id = 'old-run'"
        )
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    held = by_id(inventory, ResourceClass.TELEMETRY)["old-run"]
    assert held.disposition is Disposition.PROTECTED
    assert "chain is broken" in held.reason
    assert any(problem.run_id == "old-run" for problem in inventory.problems)
    assert inventory.clean is False


@pytest.fixture
def records(tmp_path):
    runtime = ControllerRuntime(tmp_path)
    harness = HarnessCoordinator(tmp_path)
    live = new_controller(runtime)
    runtime.submit_plan(live.controller_id, simple_plan())
    runtime.approve_plan(live.controller_id, True)
    dispatched, run = runtime.dispatch(live.controller_id)
    finished = new_controller(runtime)
    runtime.cancel(finished.controller_id, "done")
    orphan_run = harness.start_run(simple_plan("orphan"))
    harness.cancel_run(orphan_run.run_id)
    projects = FileProjectStateStore(tmp_path)
    projects.ensure("project-a", StageStateSchema(schema_id="s-v1", stage="design"))
    return {
        "live": dispatched.controller_id,
        "run": run.run_id,
        "finished": finished.controller_id,
        "orphan_run": orphan_run.run_id,
    }


def test_controllers_and_their_runs_are_protected_while_the_work_is_live(records, tmp_path):
    for entry in tmp_path.rglob("*"):
        age(entry, days=7)
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    controllers = by_id(inventory, ResourceClass.CONTROLLER)
    live = controllers[records["live"]]
    assert live.disposition is Disposition.PROTECTED and "is not final" in live.reason
    finished = controllers[records["finished"]]
    assert finished.disposition is Disposition.RECLAIMABLE
    runs = by_id(inventory, ResourceClass.GRAPH_RUN)
    held = runs[records["run"]]
    assert held.disposition is Disposition.PROTECTED
    assert held.referenced_by == [f"controller:{records['live']}"]
    assert runs[records["orphan_run"]].disposition is Disposition.RECLAIMABLE
    assert runs[records["orphan_run"]].owner == records["orphan_run"]


def test_a_finished_run_a_retained_controller_references_stays_protected(records, tmp_path):
    HarnessCoordinator(tmp_path).cancel_run(records["run"])
    for entry in tmp_path.rglob("*"):
        age(entry, days=7)
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    held = by_id(inventory, ResourceClass.GRAPH_RUN)[records["run"]]
    assert held.disposition is Disposition.PROTECTED
    assert held.reason == (
        f"referenced by a retained record: controller {records['live']} is retained"
    )
    ControllerRuntime(tmp_path).cancel(records["live"], "stop")
    for entry in tmp_path.rglob("*"):
        age(entry, days=7)
    released = inventory_run_root(tmp_path, POLICY, now=later())
    assert by_id(released, ResourceClass.GRAPH_RUN)[records["run"]].disposition is (
        Disposition.RECLAIMABLE
    )


def test_project_state_is_shared_and_never_reclaimable(records, tmp_path):
    for entry in tmp_path.rglob("*"):
        age(entry, days=7)
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    states = by_id(inventory, ResourceClass.PROJECT_STATE)
    assert states and all(item.ownership is Ownership.SHARED for item in states.values())
    assert all(item.disposition is Disposition.PROTECTED for item in states.values())
    assert any("retention never reclaims project state" in item.reason for item in states.values())


def test_an_orchestration_and_the_controller_it_binds_are_reported_together(tmp_path):
    orchestrator = Orchestrator(tmp_path, make_policy())
    record = arun(orchestrator.prepare(make_request()))
    orchestrator.submit_for_approval(record.orchestration_id)
    orchestrator.cancel(record.orchestration_id, "not needed")
    orchestrator._telemetry.close()
    for entry in tmp_path.rglob("*"):
        age(entry, days=7)
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    orchestrations = by_id(inventory, ResourceClass.ORCHESTRATION)
    assert orchestrations[record.orchestration_id].disposition is Disposition.RECLAIMABLE
    assert "policies" in orchestrations
    (controller,) = by_id(inventory, ResourceClass.CONTROLLER).values()
    assert controller.referenced_by == [f"orchestration:{record.orchestration_id}"]
    assert controller.disposition is Disposition.RECLAIMABLE


def test_archive_entries_no_snapshot_references_are_reported_and_referenced_ones_are_kept(
    tmp_path,
):
    store = FileEpisodeStore(tmp_path)
    first = store.open_exploratory("agent", content={"text": "kept " * 80})
    store.close(first.id, description="first")
    store.persist()
    archive = tmp_path / ".agent-memory" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    stray = archive / ("archive-" + "0" * 64 + ".json")
    stray.write_text("{}", encoding="utf-8")
    for entry in tmp_path.rglob("*"):
        age(entry, days=7)
    inventory = inventory_run_root(tmp_path, POLICY, now=later())
    entries = by_id(inventory, ResourceClass.EPISODE_ARCHIVE)
    assert entries[stray.stem].disposition is Disposition.RECLAIMABLE
    assert "historical evidence" in entries[stray.stem].reason
    memory = by_id(inventory, ResourceClass.SEMANTIC_MEMORY)
    assert "snapshots" in memory and memory["snapshots"].disposition is Disposition.PROTECTED
    assert json.loads((tmp_path / ".agent-memory" / "episodes.json").read_text("utf-8")) is not None
