import json
import shutil

import pytest

from nailong_agent_sdk.developer_tools.attribute_result import (
    MAX_LISTED_TOOLS,
    attribute_result_defect,
    result_check,
)
from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.agents import (
    FnExecutor,
    agent,
    arun,
    batch,
    call,
    definition,
    final,
    ok,
    task,
    tool,
    tool_call,
)
from tests.support.developer_tool_runs import cli
from tests.support.diagnosis_runs import fingerprints

RUN = "run-1"
BAD = "CORRUPT"


def lookup_handler(tool_definition, context):
    key = context.call.arguments.get("key")
    return ok({"value": BAD if key == "b" else f"value-{key}"})


def record(root, turns, *, tools=("lookup",), journal=True):
    telemetry = TelemetryStore(root)
    audit = AuditTranscriptStore(root)
    extra = {"result_journal": FileToolResultJournal(root)} if journal else {}
    try:
        subject, _ = agent(
            turns,
            executor=FnExecutor(lookup_handler),
            definition_=definition(tools=[tool(name) for name in tools], max_iterations=8),
            telemetry=telemetry,
            audit_logs=audit,
            **extra,
        )
        return arun(subject.run(task(RUN)))
    finally:
        telemetry.close()
        audit.close()


def three_lookups():
    return [
        tool_call("c1", "lookup", {"key": "a"}),
        tool_call("c2", "lookup", {"key": "b"}),
        tool_call("c3", "lookup", {"key": "c"}),
        final(),
    ]


def has_bad(tool_name, arguments, result):
    return BAD in json.dumps(result)


def test_the_first_rejected_result_is_named_with_the_turn_that_asked_for_it(tmp_path):
    record(tmp_path, three_lookups())
    result = attribute_result_defect(tmp_path, RUN, has_bad, check="contains CORRUPT")
    assert not result.ok and result.results == result.scanned == 3 and result.evaluations == 2
    found = result.first_defective
    assert (found.position, found.tool, found.tool_call_id) == (2, "lookup", "c2")
    assert found.status == "succeeded" and found.handle_id.startswith("result-")
    decision = found.decision
    assert decision.relation == "issued-the-call" and decision.iteration == 2
    assert decision.tool == "lookup" and decision.tool_call_id == "c2"
    assert 'first returned a rejected result (contains CORRUPT) from lookup call "c2"' in (
        result.summary
    )
    assert "result 2 of 3" in result.summary and "iteration 2 issued lookup" in result.summary


def test_a_run_whose_results_all_pass_costs_one_evaluation_each(tmp_path):
    record(tmp_path, [tool_call("c1", "lookup", {"key": "a"}), final()])
    result = attribute_result_defect(tmp_path, RUN, has_bad)
    assert result.ok and result.first_defective is None and result.evaluations == 1
    assert (
        result.summary
        == 'None of the 1 journaled result(s) of run "run-1" fails the check (custom check).'
    )


def test_the_check_receives_the_tool_name_the_arguments_and_the_result(tmp_path):
    record(tmp_path, three_lookups())
    seen = []

    def spy(tool_name, arguments, result):
        seen.append((tool_name, arguments, result["status"]))
        return False

    attribute_result_defect(tmp_path, RUN, spy)
    assert seen == [("lookup", {"key": key}, "succeeded") for key in ("a", "b", "c")]


def test_a_tool_filter_checks_only_that_tools_results(tmp_path):
    turns = [
        tool_call("c1", "lookup", {"key": "b"}),
        tool_call("c2", "other", {"key": "b"}),
        final(),
    ]
    record(tmp_path, turns, tools=("lookup", "other"))
    result = attribute_result_defect(tmp_path, RUN, has_bad, tool="other")
    assert result.scanned == 1 and result.results == 2 and result.tool_filter == "other"
    assert result.first_defective.tool_call_id == "c2" and result.first_defective.position == 2


def test_a_tool_the_run_never_called_lists_the_tools_it_did(tmp_path):
    record(tmp_path, three_lookups())
    with pytest.raises(
        ValueError, match=r'journaled no result of tool "ghost"; tools journaled: \[\'lookup\'\]'
    ):
        attribute_result_defect(tmp_path, RUN, has_bad, tool="ghost")
    assert MAX_LISTED_TOOLS >= 1


def test_a_call_inside_a_batch_is_named_exactly(tmp_path):
    turns = [batch(call("c1", "lookup", {"key": "a"}), call("c2", "lookup", {"key": "b"})), final()]
    record(tmp_path, turns)
    decision = attribute_result_defect(tmp_path, RUN, has_bad).first_defective.decision
    assert decision.relation == "issued-the-call" and decision.tool_call_id == "c2"
    assert decision.iteration == 1


def test_without_an_audit_ledger_the_result_is_still_named_and_the_decision_says_why_not(tmp_path):
    record(tmp_path, three_lookups())
    shutil.rmtree(tmp_path / ".agent-audit-logs")
    found = attribute_result_defect(tmp_path, RUN, has_bad).first_defective
    assert found.tool_call_id == "c2"
    assert found.decision.relation == "unlinked"
    assert found.decision.note == "The run root has no audit ledger."


def test_an_audit_ledger_without_the_request_says_so(tmp_path):
    record(tmp_path, three_lookups())
    journal = FileToolResultJournal(tmp_path)
    handle = journal.handles_of(RUN)[1]
    payload = journal.read(handle)
    payload["call"]["id"] = "ghost"
    (tmp_path / ".agent-tool-results" / f"{handle}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    decision = attribute_result_defect(tmp_path, RUN, has_bad).first_defective.decision
    assert decision.relation == "unlinked"
    assert 'holds no tool-requested entry for call "ghost"' in decision.note


def test_attributing_a_run_changes_no_file(tmp_path):
    record(tmp_path, three_lookups())
    before = fingerprints(tmp_path)
    attribute_result_defect(tmp_path, RUN, has_bad)
    assert fingerprints(tmp_path) == before


def test_missing_inputs_are_named(tmp_path):
    with pytest.raises(ValueError, match="does not exist or is not a directory"):
        attribute_result_defect(tmp_path / "absent", RUN, has_bad)
    with pytest.raises(ValueError, match=r"holds no \.agent-tool-results directory"):
        attribute_result_defect(tmp_path, RUN, has_bad)
    record(tmp_path, three_lookups())
    with pytest.raises(ValueError, match='No tool result of run "other" is journaled'):
        attribute_result_defect(tmp_path, "other", has_bad)


def test_a_journal_record_without_a_call_and_result_is_named(tmp_path):
    record(tmp_path, three_lookups())
    handle = FileToolResultJournal(tmp_path).handles_of(RUN)[0]
    (tmp_path / ".agent-tool-results" / f"{handle}.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match=rf'Journal record "{handle}" holds no call and result'):
        attribute_result_defect(tmp_path, RUN, has_bad)


def test_the_three_text_checks_and_their_descriptions():
    result = {"status": "succeeded", "output": {"value": "alpha"}}
    contains, said = result_check(contains="alpha")
    assert contains("t", {}, result) and said == 'result contains "alpha"'
    lacks, said = result_check(lacks="alpha")
    assert not lacks("t", {}, result) and lacks("t", {}, {"output": {}}) and "lacks" in said
    matches, said = result_check(matches=r"al.ha")
    assert matches("t", {}, result) and said == "result matches /al.ha/"


def test_exactly_one_check_is_required_and_a_bad_pattern_is_named():
    with pytest.raises(
        ValueError, match=r"exactly one of contains, lacks or matches is required, got \[\]"
    ):
        result_check()
    with pytest.raises(ValueError, match=r"got \['contains', 'lacks'\]"):
        result_check(contains="a", lacks="b")
    with pytest.raises(ValueError, match=r'matches pattern "\(" is not a valid regular expression'):
        result_check(matches="(")


def test_the_command_line_exits_one_and_prints_the_first_rejected_result(tmp_path):
    record(tmp_path, three_lookups())
    done = cli("attribute-result", tmp_path, RUN, "--contains", BAD)
    assert done.returncode == 1, done.stderr
    printed = json.loads(done.stdout)
    assert printed["ok"] is False and printed["first_defective"]["tool_call_id"] == "c2"
    assert printed["first_defective"]["decision"]["iteration"] == 2


def test_the_command_line_exits_zero_when_nothing_is_rejected(tmp_path):
    record(tmp_path, three_lookups())
    done = cli("attribute-result", tmp_path, RUN, "--contains", "NOT-THERE", "--tool", "lookup")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["ok"] is True


def test_the_command_line_names_an_unknown_run(tmp_path):
    record(tmp_path, three_lookups())
    done = cli("attribute-result", tmp_path, "nope", "--contains", BAD)
    assert done.returncode == 3
    assert 'No tool result of run "nope" is journaled' in done.stderr
