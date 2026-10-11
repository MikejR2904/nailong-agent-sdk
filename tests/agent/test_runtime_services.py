import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.retention import RetentionPolicy, read_tombstones, tombstone_path
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.developer_tools.inspect import inspect_run, verify_project_evidence
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call

POLICY = RetentionPolicy(older_than_seconds=3_600)


def later():
    return datetime.now(UTC) + timedelta(days=2)


def run_agent(services, task_id, calls=2):
    turns = [tool_call(f"{task_id}-c{index}", "echo", {"i": index}) for index in range(calls)]
    subject = services.create_agent(
        definition(tools=[tool("echo")], max_iterations=calls + 3),
        ScriptedModel([*turns, final()]),
        tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments, "task": task_id})),
    )
    result = arun(subject.run(task(task_id)))
    assert result.status is AgentRunStatus.COMPLETED, (result.status, result.reason)
    return result


@pytest.fixture
def services(tmp_path):
    opened = AgentRuntimeServices.open(tmp_path)
    yield opened
    opened.audit_logs.close()
    opened.telemetry.close()


def test_the_audit_handle_budget_is_chosen_when_the_services_are_opened(tmp_path):
    small = AgentRuntimeServices.open(tmp_path / "small", audit_max_open_handles=2)
    for run_id in ("a", "b", "c"):
        small.audit_logs.append(run_id, "step", {})
    assert small.audit_logs.handle_evictions == 1
    roomy = AgentRuntimeServices.open(tmp_path / "roomy")
    for index in range(32):
        roomy.audit_logs.append(f"run-{index}", "step", {})
    assert roomy.audit_logs.handle_evictions == 0
    for opened in (small, roomy):
        opened.audit_logs.close()
        opened.telemetry.close()
    with pytest.raises(ValueError, match="max_open_handles must be at least 1"):
        AgentRuntimeServices.open(tmp_path / "none", audit_max_open_handles=0)


def test_each_real_run_records_its_tool_results_under_its_own_run_id(services):
    run_agent(services, "alpha", calls=2)
    run_agent(services, "beta", calls=3)
    alpha = services.result_journal.handles_of("alpha")
    beta = services.result_journal.handles_of("beta")
    assert len(alpha) == 2 and len(beta) == 3 and not set(alpha) & set(beta)
    for run_id, expected in (("alpha", alpha), ("beta", beta)):
        entries = services.audit_logs.list_entries(run_id, limit=1_000)
        cited = [
            entry.payload["result_handle_id"]
            for entry in entries
            if "result_handle_id" in json.dumps(entry.payload)
        ]
        assert cited == expected, (run_id, cited, expected)
    assert re.fullmatch(r"result-\d+", alpha[0])


def test_pruning_real_runs_removes_their_files_and_keeps_their_evidence_verifiable(services):
    run_agent(services, "alpha", calls=2)
    run_agent(services, "beta", calls=1)
    before = verify_project_evidence(services.run_root, "alpha")
    assert (before.verified, before.checked, before.pruned) == (True, 2, 0)
    handles = services.result_journal.handles_of("alpha")
    report = services.retention().prune(
        RetentionPolicy(older_than_seconds=3_600, max_runs=1), now=later(), dry_run=False
    )
    assert [run.run_id for run in report.runs] == ["alpha"]
    assert report.runs[0].journal_handles == 2 and report.runs[0].audit_entries > 4
    journal = services.run_root / ".agent-tool-results"
    assert not any((journal / f"{handle}.json").exists() for handle in handles)
    assert all(
        (journal / f"{h}.json").is_file() for h in services.result_journal.handles_of("beta")
    )
    after = verify_project_evidence(services.run_root, "alpha")
    assert (after.verified, after.checked, after.pruned, after.mismatches) == (True, 2, 2, [])
    untouched = verify_project_evidence(services.run_root, "beta")
    assert (untouched.verified, untouched.pruned) == (True, 0)
    with pytest.raises(ValueError) as unknown:
        inspect_run(services.run_root, "alpha")
    for needle in (
        'Telemetry run "alpha" was pruned',
        "retention tombstone 1",
        "events ending at hash",
    ):
        assert needle in str(unknown.value), (needle, str(unknown.value))
    (tombstone,) = read_tombstones(services.run_root)
    assert [item.handle_id for item in tombstone.journal.handles] == handles
    assert inspect_run(services.run_root, "beta").telemetry_chain_valid


def test_a_tombstone_log_that_was_edited_cannot_vouch_for_pruned_evidence(services):
    run_agent(services, "alpha", calls=2)
    handles = services.result_journal.handles_of("alpha")
    services.retention().prune(POLICY, now=later(), dry_run=False)
    path = tombstone_path(services.run_root)
    forged = re.sub(
        r'"content_hash": "[0-9a-f]{64}"',
        '"content_hash": "' + "0" * 64 + '"',
        path.read_text("utf-8"),
        count=1,
    )
    path.write_text(forged, "utf-8")
    report = verify_project_evidence(services.run_root, "alpha")
    assert report.verified is False and report.pruned == 0
    assert {m.evidence_id for m in report.mismatches} == set(handles)
    for mismatch in report.mismatches:
        assert "has no journal file" in mismatch.reason
        assert "The retention tombstones cannot vouch for it" in mismatch.reason
        assert "sequence 1 (content-hash-mismatch)" in mismatch.reason
