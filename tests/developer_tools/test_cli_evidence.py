import json
import sqlite3

import pytest

from tests.support.developer_tool_runs import cli


def record_evidence(root, project="p1", count=3):
    from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
    from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
    from nailong_agent_sdk.state.project_state_engine import ProjectStateReducer
    from nailong_agent_sdk.state.project_state_models import StageStateSchema
    from nailong_agent_sdk.state.project_state_store import FileProjectStateStore

    journal = FileToolResultJournal(root)
    store = FileProjectStateStore(root)
    store.ensure(project, StageStateSchema(schema_id="s-v1", stage="design"))
    handles = []
    for index in range(count):
        call = ToolCall(id=f"c{index}", name="echo", arguments={"i": index})
        result = ToolExecutionResult(status="succeeded", output={"v": index})
        handle = journal.record(call, result)
        store.apply(project, ProjectStateReducer.tool_transition(call, result, handle))
        handles.append(handle)
    return handles


def test_verify_project_evidence_accepts_untouched_journals(tmp_path):
    from nailong_agent_sdk.developer_tools.inspect import verify_project_evidence

    record_evidence(tmp_path)
    report = verify_project_evidence(tmp_path, "p1")
    assert report.verified is True and report.checked == 3 and report.mismatches == []


def test_verify_project_evidence_reports_overwritten_missing_and_corrupt_results(tmp_path):
    from nailong_agent_sdk.developer_tools.inspect import verify_project_evidence

    handles = record_evidence(tmp_path)
    journal = tmp_path / ".agent-tool-results"
    (journal / f"{handles[0].handle_id}.json").write_text("{not json", "utf-8")
    (journal / f"{handles[1].handle_id}.json").write_text('{"call":{},"result":{}}', "utf-8")
    (journal / f"{handles[2].handle_id}.json").unlink()
    report = verify_project_evidence(tmp_path, "p1")
    assert report.verified is False and report.checked == 3
    by_id = {m.evidence_id: m for m in report.mismatches}
    assert set(by_id) == {handle.handle_id for handle in handles}
    assert "is corrupt" in by_id[handles[0].handle_id].reason
    changed = by_id[handles[1].handle_id]
    assert changed.recorded_hash == handles[1].content_hash
    assert changed.journal_hash and changed.journal_hash != changed.recorded_hash
    assert changed.recorded_hash in changed.reason and changed.journal_hash in changed.reason
    assert 'project "p1" at revision 2' in changed.reason
    assert "has no journal file" in by_id[handles[2].handle_id].reason


def test_verify_project_evidence_is_read_only_and_names_unknown_inputs(tmp_path):
    from nailong_agent_sdk.developer_tools.inspect import verify_project_evidence

    with pytest.raises(ValueError, match="does not exist or is not a directory"):
        verify_project_evidence(tmp_path / "no-such-root", "p1")
    assert not (tmp_path / "no-such-root").exists()
    with pytest.raises(ValueError, match='Project state "p1" is unknown'):
        verify_project_evidence(tmp_path, "p1")
    assert not (tmp_path / ".agent-project-state").exists()
    record_evidence(tmp_path)
    with pytest.raises(ValueError, match='Project state "ghost" is unknown'):
        verify_project_evidence(tmp_path, "ghost")


def test_cli_verify_evidence_uses_the_verdict_exit_status(tmp_path):
    handles = record_evidence(tmp_path)
    clean = cli("verify-evidence", tmp_path, "p1")
    assert clean.returncode == 0 and json.loads(clean.stdout)["verified"] is True
    (tmp_path / ".agent-tool-results" / f"{handles[0].handle_id}.json").write_text("{}", "utf-8")
    tampered = cli("verify-evidence", tmp_path, "p1")
    assert tampered.returncode == 1 and json.loads(tampered.stdout)["verified"] is False
    unknown = cli("verify-evidence", tmp_path, "ghost")
    assert unknown.returncode == 3 and 'Project state "ghost" is unknown' in unknown.stderr


def record_run_with_evidence(root, run_id, project, count=2, wrong_hash_at=None):
    from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
    from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
    from nailong_agent_sdk.observability.telemetry_models import (
        TelemetryActor,
        TelemetryAuthority,
        TelemetryContext,
    )
    from nailong_agent_sdk.state.project_state_engine import ProjectStateReducer
    from nailong_agent_sdk.state.project_state_models import StageStateSchema

    services = AgentRuntimeServices.open(root)
    try:
        services.project_state_store.ensure(
            project, StageStateSchema(schema_id="s-v1", stage="design")
        )
        scoped = services.result_journal.for_run(run_id)
        handles = []
        for index in range(count):
            call = ToolCall(id=f"c{index}", name="echo", arguments={"i": index})
            result = ToolExecutionResult(status="succeeded", output={"v": index})
            handle = scoped.record(call, result)
            recorded = (
                handle.model_copy(update={"content_hash": "0" * 64})
                if index == wrong_hash_at
                else handle
            )
            services.project_state_store.apply(
                project, ProjectStateReducer.tool_transition(call, result, recorded)
            )
            handles.append(handle)
        services.audit_logs.append(run_id, "step", {"note": "recorded"})
        services.telemetry.emit(
            "agent.terminated",
            TelemetryContext(run_id=run_id),
            actor=TelemetryActor(kind="system", identifier="test"),
            authority=TelemetryAuthority.DETERMINISTIC,
            status="completed",
        )
        return handles
    finally:
        services.audit_logs.close()
        services.telemetry.close()


def test_cli_prune_runs_is_a_dry_run_until_told_to_apply(tmp_path):
    record_run_with_evidence(tmp_path, "old-run", "p1")
    missing_policy = cli("prune-runs", tmp_path)
    assert missing_policy.returncode == 2 and "--older-than-seconds" in missing_policy.stderr
    dry = cli("prune-runs", tmp_path, "--older-than-seconds", "0.001")
    assert dry.returncode == 0, dry.stderr
    report = json.loads(dry.stdout)
    assert report["dry_run"] is True and [run["run_id"] for run in report["runs"]] == ["old-run"]
    assert report["runs"][0]["journal_handles"] == 2 and report["problems"] == []
    assert len(list((tmp_path / ".agent-tool-results").glob("result-*.json"))) == 2
    assert not (tmp_path / ".agent-retention").exists()
    applied = cli("prune-runs", tmp_path, "--older-than-seconds", "0.001", "--apply")
    assert applied.returncode == 0, applied.stderr
    done = json.loads(applied.stdout)
    assert done["dry_run"] is False and done["runs"][0]["tombstone_sequence"] == 1
    assert list((tmp_path / ".agent-tool-results").glob("result-*.json")) == []
    assert (tmp_path / ".agent-retention" / "tombstones.jsonl").is_file()
    verified = cli("verify-evidence", tmp_path, "p1")
    assert verified.returncode == 0 and json.loads(verified.stdout)["pruned"] == 2
    inspected = cli("inspect-run", tmp_path, "old-run")
    assert inspected.returncode == 3 and "was pruned" in inspected.stderr


def test_cli_prune_runs_names_bad_policies_and_bad_roots_and_flags_broken_chains(tmp_path):
    bad_policy = cli("prune-runs", tmp_path, "--older-than-seconds", "0")
    assert bad_policy.returncode == 3 and "older_than_seconds" in bad_policy.stderr
    assert "Traceback" not in bad_policy.stderr
    no_root = cli("prune-runs", tmp_path / "nowhere", "--older-than-seconds", "1")
    assert no_root.returncode == 3 and "does not exist or is not a directory" in no_root.stderr
    record_run_with_evidence(tmp_path, "tampered", "p1")
    database = tmp_path / ".agent-telemetry" / "telemetry.sqlite3"
    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT event_json FROM events WHERE run_id='tampered' AND sequence=1"
    ).fetchone()
    payload = json.loads(row[0])
    payload["status"] = "tampered"
    connection.execute(
        "UPDATE events SET event_json=? WHERE run_id='tampered' AND sequence=1",
        (json.dumps(payload),),
    )
    connection.commit()
    connection.close()
    flagged = cli("prune-runs", tmp_path, "--older-than-seconds", "0.001", "--apply")
    assert flagged.returncode == 1, flagged.stderr
    report = json.loads(flagged.stdout)
    assert report["runs"] == [] and report["problems"][0]["run_id"] == "tampered"
    assert "left untouched as evidence" in report["problems"][0]["reason"]
    assert len(list((tmp_path / ".agent-tool-results").glob("result-*.json"))) == 2


def test_a_pruned_handle_whose_recorded_hash_differs_from_the_tombstone_is_a_mismatch(tmp_path):
    from nailong_agent_sdk.agent.retention import RetentionPolicy, prune_run_root
    from nailong_agent_sdk.developer_tools.inspect import verify_project_evidence

    handles = record_run_with_evidence(tmp_path, "old-run", "p1", count=3, wrong_hash_at=1)
    before = verify_project_evidence(tmp_path, "p1")
    assert [m.evidence_id for m in before.mismatches] == [handles[1].handle_id]
    prune_run_root(tmp_path, RetentionPolicy(older_than_seconds=0.001), dry_run=False)
    after = verify_project_evidence(tmp_path, "p1")
    assert after.verified is False and after.checked == 3 and after.pruned == 2
    (mismatch,) = after.mismatches
    assert mismatch.evidence_id == handles[1].handle_id and mismatch.journal_hash is None
    assert mismatch.recorded_hash == "0" * 64
    for needle in ("retention tombstone 1", 'run "old-run"', handles[1].content_hash):
        assert needle in mismatch.reason, (needle, mismatch.reason)
