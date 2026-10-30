import json

import pytest

from nailong_agent_sdk.developer_tools.attribute_root_cause import (
    CauseBasis,
    CauseKind,
    attribute_root_cause,
)
from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
from nailong_agent_sdk.foundations.hashing import canonical_json
from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from nailong_agent_sdk.tools.artifacts import ArtifactStore
from tests.support.agents import definition, final, tool_call
from tests.support.base_agent_cases import ECHO, echo_executor
from tests.support.developer_tool_runs import cli
from tests.support.diagnosis_runs import (
    SQLITE_SIDE_FILES,
    fingerprints,
    record,
    run_chain,
    tamper_event,
)


def bad_content(content, version):
    return b"BAD" in content


def bad_result(tool, arguments, result):
    return "BAD" in canonical_json(result)


def writer(run_id, call_id, iteration=1):
    return {
        "agent_identity": f"agent-{run_id}",
        "agent_run_id": run_id,
        "tool": "write_draft",
        "tool_call_id": call_id,
        "iteration": iteration,
    }


def journal_call(journal, run_id, call_id, name, arguments, output):
    return journal.record_for_run(
        run_id,
        ToolCall(id=call_id, name=name, arguments=arguments),
        ToolExecutionResult(status="succeeded", output=output),
    )


def failed_run(root, run_id="r1"):
    record(
        root,
        run_id,
        [tool_call("c9", "ghost", {"path": "a.txt"})],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
    )


def data_flow(root, written="report BAD value", copies=1):
    store, journal = ArtifactStore(root), FileToolResultJournal(root)
    journal_call(journal, "r0", "c0", "write_draft", {"path": "out.txt"}, {"ok": True})
    version = None
    for index in range(copies):
        version = store.write_text("out.txt", written, manifest=writer("r0", "c0", index + 1))
    journal_call(
        journal,
        "r1",
        "c1",
        "read_artifact",
        {"artifact_id": version.artifact_id},
        {"artifact_id": version.artifact_id, "content": written},
    )
    return version


def lineage_check(path, content):
    return b"BAD" in content


def test_a_completed_run_has_no_fault_to_attribute(tmp_path):
    record(tmp_path, "good", [final()])
    analysis = attribute_root_cause(tmp_path, "good")
    assert analysis.ok and analysis.primary is None and analysis.candidates == []
    assert "no fault" in analysis.primary_reason


def test_without_any_check_the_recorded_stop_is_named_and_what_was_skipped_is_listed(tmp_path):
    failed_run(tmp_path)
    analysis = attribute_root_cause(tmp_path, "r1")
    assert not analysis.ok and analysis.verdict == "failed"
    primary = analysis.primary
    assert primary.kind is CauseKind.RUN_TERMINAL and primary.basis is CauseBasis.RECORDED
    assert primary.decision.relation == "issued-the-call" and primary.decision.tool == "ghost"
    skipped = " ".join(analysis.not_examined)
    assert "No result check" in skipped and "no write history" in skipped
    assert "reasoning is not recorded" in skipped
    assert "does not prove" in analysis.primary_reason


def test_a_bad_artifact_read_by_the_failing_run_is_traced_to_the_write_and_ranked_first(tmp_path):
    failed_run(tmp_path)
    data_flow(tmp_path)
    analysis = attribute_root_cause(
        tmp_path,
        "r1",
        workspace=tmp_path,
        artifact_checks={"out.txt": bad_content},
        content_check=lineage_check,
        result_check=bad_result,
    )
    kinds = [item.kind for item in analysis.candidates]
    assert kinds[0] is CauseKind.UPSTREAM_ORIGIN and CauseKind.DEFECTIVE_WRITE in kinds
    assert CauseKind.RUN_TERMINAL in kinds
    assert [item.rank for item in analysis.candidates] == list(range(1, len(kinds) + 1))
    primary = analysis.primary
    assert primary.kind is CauseKind.UPSTREAM_ORIGIN and primary.basis is CauseBasis.TRACED
    assert primary.writer.agent_run_id == "r0" and primary.run_id == "r0"
    write = next(item for item in analysis.candidates if item.kind is CauseKind.DEFECTIVE_WRITE)
    assert write.basis is CauseBasis.CHECKED_EXACT and write.writer.agent_run_id == "r0"
    assert "does not prove" in analysis.primary_reason


def test_a_result_check_names_the_first_bad_result_and_the_call_behind_it(tmp_path):
    failed_run(tmp_path)
    journal = FileToolResultJournal(tmp_path)
    journal_call(journal, "r1", "c1", "read_file", {"path": "a"}, {"content": "fine"})
    journal_call(journal, "r1", "c2", "read_file", {"path": "b"}, {"content": "BAD"})
    analysis = attribute_root_cause(tmp_path, "r1", result_check=bad_result)
    result = next(item for item in analysis.candidates if item.kind is CauseKind.DEFECTIVE_RESULT)
    assert result.basis is CauseBasis.CHECKED_EXACT and result.subject == "read_file call c2"
    assert any("2 result(s) examined" in line for line in result.evidence)
    assert analysis.primary.kind is CauseKind.DEFECTIVE_RESULT


def test_a_version_several_writes_could_have_produced_is_listed_but_never_named_the_origin(
    tmp_path,
):
    failed_run(tmp_path)
    store, journal = ArtifactStore(tmp_path), FileToolResultJournal(tmp_path)
    store.write_text("a.txt", "BAD\n", manifest=writer("r8", "w8"))
    again = store.write_text("b.txt", "BAD\n", manifest=writer("r9", "w9"))
    journal_call(
        journal,
        "r1",
        "c1",
        "read_artifact",
        {"artifact_id": again.artifact_id},
        {"artifact_id": again.artifact_id, "content": "BAD\n"},
    )
    analysis = attribute_root_cause(tmp_path, "r1", workspace=tmp_path, content_check=lineage_check)
    ambiguous = [item for item in analysis.candidates if item.basis is CauseBasis.AMBIGUOUS]
    assert ambiguous and ambiguous[0].kind is CauseKind.UPSTREAM_ORIGIN
    assert ambiguous[0].subject == "c1" and ambiguous[0].writer is None
    assert analysis.candidates[-1].basis is CauseBasis.AMBIGUOUS
    assert analysis.primary.kind is CauseKind.RUN_TERMINAL


def test_a_failed_graph_names_the_node_that_stopped_it_with_its_responsibility(tmp_path):
    _, run_id = run_chain(tmp_path, 4, failing={"T0001"})
    analysis = attribute_root_cause(tmp_path, run_id)
    primary = analysis.primary
    assert primary.kind is CauseKind.NODE_FAILURE and primary.subject == "node:T0001"
    assert primary.responsibility == 3.0 and primary.basis is CauseBasis.RECORDED
    assert any("releases node:T0002" in line for line in primary.evidence)


def test_a_check_on_artifacts_needs_the_workspace_and_says_so(tmp_path):
    failed_run(tmp_path)
    with pytest.raises(
        ValueError, match="pass workspace=<the directory that holds .agent-artifacts>"
    ):
        attribute_root_cause(tmp_path, "r1", artifact_checks={"out.txt": bad_content})
    with pytest.raises(ValueError, match="max_runs must be at least 1, got 0"):
        attribute_root_cause(tmp_path, "r1", max_runs=0)


def test_a_missing_journal_or_history_is_reported_not_hidden(tmp_path):
    failed_run(tmp_path)
    analysis = attribute_root_cause(
        tmp_path,
        "r1",
        workspace=tmp_path,
        artifact_checks={"out.txt": bad_content},
        result_check=bad_result,
    )
    skipped = " ".join(analysis.not_examined)
    assert 'Run "r1": tool results were not scanned' in skipped
    assert 'Artifact "out.txt": its write history was not searched' in skipped
    assert analysis.primary.kind is CauseKind.RUN_TERMINAL


def test_altered_ledgers_are_flagged_on_the_analysis(tmp_path):
    failed_run(tmp_path)
    tamper_event(tmp_path, "r1", 2)
    analysis = attribute_root_cause(tmp_path, "r1")
    assert analysis.evidence_intact is False
    assert any("fail their own integrity check" in line for line in analysis.not_examined)
    assert "fail their integrity check" in analysis.primary_reason


def test_the_analysis_reads_without_writing_anything(tmp_path):
    failed_run(tmp_path)
    data_flow(tmp_path)
    before = fingerprints(tmp_path)
    attribute_root_cause(
        tmp_path,
        "r1",
        workspace=tmp_path,
        artifact_checks={"out.txt": bad_content},
        content_check=lineage_check,
        result_check=bad_result,
    )
    after = fingerprints(tmp_path)
    assert {k: v for k, v in before.items() if k not in SQLITE_SIDE_FILES} == {
        k: v for k, v in after.items() if k not in SQLITE_SIDE_FILES
    }


def test_the_command_prints_the_same_ranking_as_json(tmp_path):
    failed_run(tmp_path)
    data_flow(tmp_path)
    done = cli(
        "attribute-root-cause",
        tmp_path,
        "r1",
        "--workspace",
        tmp_path,
        "--artifact",
        "out.txt",
        "--artifact-contains",
        "BAD",
        "--result-contains",
        "BAD",
    )
    assert done.returncode == 1, done.stderr
    report = json.loads(done.stdout)
    assert report["primary"]["kind"] == "upstream-origin"
    assert report["primary"]["writer"]["agent_run_id"] == "r0"


def test_the_command_refuses_an_artifact_path_without_an_artifact_check(tmp_path):
    failed_run(tmp_path)
    done = cli("attribute-root-cause", tmp_path, "r1", "--workspace", tmp_path, "--artifact", "x")
    assert done.returncode != 0
    assert "--artifact names a path to search but no artifact check was given" in done.stderr


def test_a_completed_run_the_caller_rejects_is_still_traced_when_assume_fault_is_set(tmp_path):
    record(tmp_path, "r1", [final()])
    data_flow(tmp_path)
    kwargs = dict(workspace=tmp_path, content_check=lineage_check)
    assert attribute_root_cause(tmp_path, "r1", **kwargs).ok
    traced = attribute_root_cause(tmp_path, "r1", assume_fault=True, **kwargs)
    assert not traced.ok and traced.verdict == "completed"
    assert traced.primary.kind is CauseKind.UPSTREAM_ORIGIN
    assert traced.primary.writer.agent_run_id == "r0"


def test_results_are_scanned_in_the_runs_the_trace_passes_through(tmp_path):
    record(tmp_path, "r1", [final()])
    data_flow(tmp_path)
    journal = FileToolResultJournal(tmp_path)
    journal_call(journal, "r0", "c5", "fetch_rates", {"q": "x"}, {"rate": "BAD 9"})
    analysis = attribute_root_cause(
        tmp_path,
        "r1",
        workspace=tmp_path,
        content_check=lineage_check,
        result_check=bad_result,
        assume_fault=True,
    )
    scanned = [item for item in analysis.candidates if item.kind is CauseKind.DEFECTIVE_RESULT]
    assert ("r0", "fetch_rates call c5") in [(item.run_id, item.subject) for item in scanned]
    assert {item.run_id for item in scanned} == {"r0", "r1"}


def test_a_defective_result_read_before_the_defective_write_of_the_same_run_ranks_first(tmp_path):
    record(tmp_path, "r1", [final()])
    store, journal = ArtifactStore(tmp_path), FileToolResultJournal(tmp_path)
    journal.record_for_run(
        "r0",
        ToolCall(id="read1", name="fetch_rates", arguments={"q": "x"}),
        ToolExecutionResult(status="succeeded", output={"rate": "BAD 9"}),
    )
    version = store.write_text("out.txt", "report BAD", manifest=writer("r0", "w1", 2))
    journal_call(journal, "r0", "w1", "write_draft", {"path": "out.txt"}, {"ok": True})
    journal_call(
        journal,
        "r1",
        "c9",
        "read_artifact",
        {"artifact_id": version.artifact_id},
        {"artifact_id": version.artifact_id, "content": "report BAD"},
    )
    analysis = attribute_root_cause(
        tmp_path,
        "r1",
        workspace=tmp_path,
        content_check=lineage_check,
        result_check=lambda tool, arguments, result: (
            tool == "fetch_rates" and "BAD" in canonical_json(result)
        ),
        assume_fault=True,
    )
    assert analysis.primary.kind in {CauseKind.UPSTREAM_ORIGIN, CauseKind.DEFECTIVE_RESULT}
    assert analysis.primary.subject == "fetch_rates call read1"
    assert analysis.primary.run_id == "r0"


def stored_and_journaled(tmp_path, *, served, asked, kept):
    store, journal = ArtifactStore(tmp_path), FileToolResultJournal(tmp_path)
    version = store.write_text("data.txt", kept, manifest=writer("r0", "w1", 1))
    journal_call(journal, "r0", "w1", "write_draft", {"path": "data.txt", "content": asked}, {})
    journal_call(
        journal,
        "r1",
        "c1",
        "read_artifact",
        {"artifact_id": version.artifact_id},
        {"artifact_id": version.artifact_id, "path": "data.txt", "content": served},
    )
    return version


def test_a_read_that_returned_other_content_than_the_store_holds_is_named_without_any_check(
    tmp_path,
):
    record(tmp_path, "r1", [final()])
    stored_and_journaled(tmp_path, served="altered", asked="same", kept="same")
    analysis = attribute_root_cause(
        tmp_path, "r1", workspace=tmp_path, assume_fault=True, verify_records=True
    )
    primary = analysis.primary
    assert primary.kind is CauseKind.RECORD_INCONSISTENCY and primary.basis is CauseBasis.RECORDED
    assert primary.subject == "read_artifact call c1" and primary.run_id == "r1"
    assert "differs from the stored artifact" in primary.summary


def test_a_write_that_stored_other_content_than_the_call_asked_for_names_the_writer(tmp_path):
    record(tmp_path, "r1", [final()])
    stored_and_journaled(tmp_path, served="stored", asked="asked", kept="stored")
    analysis = attribute_root_cause(
        tmp_path,
        "r1",
        workspace=tmp_path,
        assume_fault=True,
        verify_records=True,
        related_runs=["r0"],
    )
    primary = analysis.primary
    assert primary.kind is CauseKind.RECORD_INCONSISTENCY
    assert primary.writer.agent_run_id == "r0" and primary.writer.tool_call_id == "w1"
    assert primary.run_id == "r0" and "asked to write" in primary.summary


def test_records_that_agree_raise_no_inconsistency_and_the_check_needs_a_workspace(tmp_path):
    record(tmp_path, "r1", [final()])
    stored_and_journaled(tmp_path, served="same", asked="same", kept="same")
    clean = attribute_root_cause(
        tmp_path,
        "r1",
        workspace=tmp_path,
        assume_fault=True,
        verify_records=True,
        related_runs=["r0"],
    )
    assert all(item.kind is not CauseKind.RECORD_INCONSISTENCY for item in clean.candidates)
    skipped = attribute_root_cause(tmp_path, "r1", assume_fault=True, verify_records=True)
    assert any("verify_records compares the journal" in line for line in skipped.not_examined)


def test_the_command_takes_assume_fault_verify_records_and_related_runs(tmp_path):
    record(tmp_path, "r1", [final()])
    stored_and_journaled(tmp_path, served="altered", asked="same", kept="same")
    done = cli(
        "attribute-root-cause",
        tmp_path,
        "r1",
        "--workspace",
        tmp_path,
        "--assume-fault",
        "--verify-records",
        "--related-run",
        "r0",
    )
    assert done.returncode == 1, done.stderr
    report = json.loads(done.stdout)
    assert report["primary"]["kind"] == "record-inconsistency"
    assert report["primary"]["subject"] == "read_artifact call c1"
