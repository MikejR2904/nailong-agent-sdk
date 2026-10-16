import json

import pytest

from nailong_agent_sdk.agent.verification import (
    CallableVerificationGate,
    VerificationDecision,
    VerificationGateRegistry,
)
from nailong_agent_sdk.developer_tools.attribute_failure import attribute_failure
from nailong_agent_sdk.developer_tools.decision_links import link_decisions
from nailong_agent_sdk.developer_tools.diagnose import DiagnosisFinding
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
from tests.support.base_agent_cases import ECHO, echo_executor
from tests.support.developer_tool_runs import cli
from tests.support.diagnosis_runs import SQLITE_SIDE_FILES, fingerprints, record, tamper_event

STRICT_WRITE = tool(
    "write",
    schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
)


def audit_entries(root, run_id):
    store = AuditTranscriptStore(root, read_only=True)
    try:
        return list(store.iter_entries(run_id))
    finally:
        store.close()


def record_small_audit(root, run_id, turns, **kwargs):
    telemetry = TelemetryStore(root)
    audit = AuditTranscriptStore(root, max_payload_chars=256)
    try:
        subject, _ = agent(turns, telemetry=telemetry, audit_logs=audit, **kwargs)
        return arun(subject.run(task(run_id)))
    finally:
        telemetry.close()
        audit.close()


def test_an_undeclared_tool_traces_to_the_model_turn_that_issued_it(tmp_path):
    record(
        tmp_path,
        "ghost",
        [tool_call("c1", "ghost", {"path": "a.txt"})],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
    )
    result = attribute_failure(tmp_path, "ghost")
    assert result.verdict == "failed" and not result.ok and result.graph is None
    decision = result.terminal_decision
    assert decision.relation == "issued-the-call" and decision.iteration == 1
    assert decision.tool == "ghost" and decision.tool_call_id == "c1"
    assert decision.turn_type == "tool-call" and decision.arguments_preview == '{"path":"a.txt"}'
    turn = next(
        item for item in audit_entries(tmp_path, "ghost") if item.event_type == "model-turn"
    )
    assert decision.audit_sequence == turn.sequence and turn.iteration == 1
    assert result.first_error_decision == decision
    assert result.summary.startswith('Run "ghost" failed at iteration 1 (TOOL_NOT_DECLARED)')
    assert (
        f'The model turn at iteration 1 issued ghost call "c1" (audit entry {turn.sequence}).'
        in (result.summary)
    )


def test_a_recovered_failure_and_the_terminal_failure_trace_to_their_own_turns(tmp_path):
    record(
        tmp_path,
        "both",
        [tool_call("c1", "write", {"path": 7}), tool_call("c2", "ghost")],
        executor=FnExecutor(lambda t, c: ok({"saved": True})),
        definition_=definition(tools=[STRICT_WRITE]),
    )
    result = attribute_failure(tmp_path, "both")
    first, terminal = result.first_error_decision, result.terminal_decision
    assert (first.iteration, first.tool, first.tool_call_id) == (1, "write", "c1")
    assert first.arguments_preview == '{"path":7}'
    assert (terminal.iteration, terminal.tool, terminal.tool_call_id) == (2, "ghost", "c2")
    assert first.audit_sequence < terminal.audit_sequence
    assert "The first error, at iteration 1 (SCHEMA_VALIDATION_FAILED): " in result.summary
    assert 'The model turn at iteration 1 issued write call "c1"' in result.summary


def test_a_run_that_recovered_still_names_the_turn_behind_its_first_error(tmp_path):
    record(
        tmp_path,
        "recovered",
        [tool_call("c1", "write", {"path": 7}), final()],
        executor=FnExecutor(lambda t, c: ok({"saved": True})),
        definition_=definition(tools=[STRICT_WRITE]),
    )
    result = attribute_failure(tmp_path, "recovered")
    assert result.ok and result.verdict == "completed" and result.terminal is None
    assert result.terminal_decision is None
    assert result.first_error_decision.tool_call_id == "c1"
    assert result.summary.startswith('Run "recovered" completed. The first error, at iteration 1')


def test_the_failing_call_of_a_batch_is_singled_out_by_the_terminal_event(tmp_path):
    record(
        tmp_path,
        "mixed",
        [batch(call("c1", "echo"), call("c2", "ghost"), call("c3", "echo"))],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
    )
    decision = attribute_failure(tmp_path, "mixed").terminal_decision
    assert decision.relation == "issued-the-call" and decision.turn_type == "tool-batch"
    assert decision.tool == "ghost" and decision.tool_call_id == "c2"
    assert decision.candidate_call_ids == []


def test_a_rejected_output_traces_to_the_final_turn_that_produced_it(tmp_path):
    registry = VerificationGateRegistry()
    registry.register(
        "sum",
        CallableVerificationGate(
            lambda context: VerificationDecision(False, "the lines do not add up", verifier_id="v")
        ),
    )
    record(
        tmp_path, "gate", [final()], definition_=definition(gate="sum"), verification_gates=registry
    )
    result = attribute_failure(tmp_path, "gate")
    decision = result.terminal_decision
    assert decision.relation == "produced-the-output" and decision.turn_type == "final"
    assert decision.detail == '{"status":"complete"}' and decision.tool is None
    assert result.first_error_decision.relation == "produced-the-output"
    assert "produced that output" in result.summary


def test_a_model_that_reports_itself_blocked_is_traced_to_that_report(tmp_path):
    record(tmp_path, "stuck", [{"type": "blocked", "reason": "I need the customer's consent"}])
    result = attribute_failure(tmp_path, "stuck")
    assert result.verdict == "blocked"
    decision = result.terminal_decision
    assert decision.relation == "reported-itself-blocked" and decision.turn_type == "blocked"
    assert decision.detail == "I need the customer's consent"
    assert "reported itself blocked" in result.summary


def test_a_limit_is_not_blamed_on_a_model_decision(tmp_path):
    record(
        tmp_path,
        "endless",
        [tool_call("c1", "echo"), tool_call("c2", "echo")],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO], max_iterations=2),
    )
    result = attribute_failure(tmp_path, "endless")
    assert result.terminal.code == "MAX_ITERATIONS_REACHED"
    decision = result.terminal_decision
    assert decision.relation == "unlinked" and decision.audit_sequence is None
    assert decision.note.startswith(
        "The failure code MAX_ITERATIONS_REACHED is not tied to one model decision"
    )
    assert "No model decision is tied to it" in result.summary


def test_an_audit_entry_cut_short_is_reported_as_such_but_still_names_the_call(tmp_path):
    record_small_audit(
        tmp_path,
        "long",
        [tool_call("c1", "ghost", {"path": "x" * 600})],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
    )
    decision = attribute_failure(tmp_path, "long").terminal_decision
    assert decision.relation == "issued-the-call" and decision.entry_truncated
    assert decision.tool == "ghost" and decision.tool_call_id == "c1"
    assert decision.turn_type is None and decision.arguments_preview is None
    assert "truncated at" in decision.note and "cannot be read" in decision.note


def test_a_run_without_an_audit_ledger_says_no_turn_is_recorded(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        subject, _ = agent(
            [tool_call("c1", "ghost")],
            telemetry=telemetry,
            executor=echo_executor(),
            definition_=definition(tools=[ECHO]),
        )
        arun(subject.run(task("bare")))
    finally:
        telemetry.close()
    decision = attribute_failure(tmp_path, "bare").terminal_decision
    assert decision.relation == "unlinked"
    assert decision.note == "The audit ledger holds no model-turn entry for iteration 1."


def test_an_unreadable_audit_ledger_is_named_in_the_note_instead_of_raised():
    class Broken:
        def iter_entries(self, run_id):
            raise ValueError("boom")

    finding = DiagnosisFinding(
        sequence=1,
        event_type="agent.terminated",
        iteration=1,
        severity="error",
        code="TOOL_NOT_DECLARED",
        message="x",
        terminal=True,
    )
    (decision,) = link_decisions(Broken(), "r", [finding], [])
    assert decision.relation == "unlinked"
    assert decision.note == "The audit ledger could not be read: ValueError: boom"
    (absent,) = link_decisions(None, "r", [finding], [])
    assert absent.note == "The run root has no audit ledger."


def test_a_finding_without_an_iteration_cannot_be_tied_to_a_turn():
    finding = DiagnosisFinding(
        sequence=3,
        event_type="agent.tool-batch-completed",
        severity="error",
        message="x",
    )
    (decision,) = link_decisions(None, "r", [finding], [])
    assert decision.note == "The finding records no iteration, so no model turn can be tied to it."


def test_a_completed_run_has_nothing_to_attribute(tmp_path):
    record(tmp_path, "clean", [final()])
    result = attribute_failure(tmp_path, "clean")
    assert result.ok and result.verdict == "completed" and result.terminal is None
    assert result.terminal_decision is None and result.first_error_decision is None
    assert result.summary == 'Run "clean" completed; there is nothing to attribute.'


def test_a_tampered_ledger_makes_the_attribution_say_it_is_unverified(tmp_path):
    record(
        tmp_path,
        "forged",
        [tool_call("c1", "ghost")],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
    )
    tamper_event(tmp_path, "forged", 2)
    result = attribute_failure(tmp_path, "forged")
    assert result.evidence_intact is False
    assert result.summary.startswith("The evidence for this run is not intact")
    assert result.terminal_decision.tool_call_id == "c1"


def test_a_node_cannot_be_requested_of_an_agent_run(tmp_path):
    record(tmp_path, "clean", [final()])
    with pytest.raises(ValueError, match='Node "n1" was requested but run "clean" has no readable'):
        attribute_failure(tmp_path, "clean", node_id="n1")


def test_attributing_an_agent_run_changes_no_file(tmp_path):
    record(
        tmp_path,
        "quiet",
        [tool_call("c1", "ghost")],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
    )
    before = fingerprints(tmp_path)
    attribute_failure(tmp_path, "quiet")
    after = fingerprints(tmp_path)
    assert set(after) - set(before) <= SQLITE_SIDE_FILES
    assert {name: digest for name, digest in after.items() if name in before} == before


def test_the_command_prints_the_decision_and_exits_by_verdict(tmp_path):
    record(
        tmp_path,
        "broken",
        [tool_call("c1", "ghost")],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
    )
    failed = cli("attribute-failure", tmp_path, "broken")
    assert failed.returncode == 1
    printed = json.loads(failed.stdout)
    assert printed["terminal"]["code"] == "TOOL_NOT_DECLARED"
    assert printed["terminal_decision"]["relation"] == "issued-the-call"
    assert printed["terminal_decision"]["tool_call_id"] == "c1"
    misuse = cli("attribute-failure", tmp_path, "broken", "--node", "n1")
    assert misuse.returncode == 3 and "has no readable graph run record" in misuse.stderr
