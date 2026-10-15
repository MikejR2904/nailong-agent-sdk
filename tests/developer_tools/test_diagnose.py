import json
from datetime import UTC, datetime

import pytest

from nailong_agent_sdk.agent.base_agent import ToolHookDecision
from nailong_agent_sdk.agent.retention import RetentionPolicy, prune_run_root
from nailong_agent_sdk.agent.verification import (
    CallableVerificationGate,
    VerificationDecision,
    VerificationGateRegistry,
)
from nailong_agent_sdk.developer_tools.diagnose import MAX_FINDINGS, diagnose_run
from nailong_agent_sdk.foundations.contracts import VersionedInstructions
from nailong_agent_sdk.memory.context_projection import ContextProjectionPolicy
from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.agents import (
    FnExecutor,
    batch,
    call,
    definition,
    final,
    ok,
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


def test_a_clean_run_is_healthy_and_says_so(tmp_path):
    record(tmp_path, "clean", [final()])
    diagnosis = diagnose_run(tmp_path, "clean")
    assert diagnosis.verdict == "completed" and diagnosis.ok
    assert diagnosis.terminal is None and diagnosis.findings == []
    assert diagnosis.first_problem is None and diagnosis.first_error is None
    assert diagnosis.integrity.intact and diagnosis.integrity.terminal_link == "linked"
    assert diagnosis.summary == 'Run "clean" completed.'
    assert diagnosis.graph is None


def test_a_recovered_tool_failure_is_the_first_problem_of_a_run_that_still_completed(tmp_path):
    record(
        tmp_path,
        "recovered",
        [tool_call("c1", "write", {"path": 7}), final()],
        executor=FnExecutor(lambda t, c: ok({"saved": True})),
        definition_=definition(tools=[STRICT_WRITE]),
    )
    diagnosis = diagnose_run(tmp_path, "recovered")
    assert diagnosis.verdict == "completed" and diagnosis.ok
    problem = diagnosis.first_problem
    assert problem.event_type == "agent.tool-batch-completed" and problem.iteration == 1
    assert problem.code == "SCHEMA_VALIDATION_FAILED" and problem.subject == "write"
    assert "$.path" in problem.message and "not of type 'string'" in problem.message
    assert problem.details == {"tool_call_id": "c1", "status": "failed"}
    assert diagnosis.first_error == problem and diagnosis.terminal is None
    assert diagnosis.summary.startswith('Run "recovered" completed.')
    assert "SCHEMA_VALIDATION_FAILED" in diagnosis.summary


def test_an_undeclared_tool_is_the_terminal_finding_with_its_code_and_iteration(tmp_path):
    record(
        tmp_path,
        "ghost",
        [tool_call("c1", "ghost")],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
    )
    diagnosis = diagnose_run(tmp_path, "ghost")
    assert diagnosis.verdict == "failed" and not diagnosis.ok
    terminal = diagnosis.terminal
    assert terminal.terminal and terminal.status == "failed" and terminal.iteration == 1
    assert terminal.code == "TOOL_NOT_DECLARED"
    assert terminal.message == 'Tool "ghost" is not declared for this agent.'
    assert diagnosis.first_error == terminal
    assert diagnosis.summary.startswith(
        'Run "ghost" failed at iteration 1: TOOL_NOT_DECLARED: Tool "ghost" is not declared'
    )


def test_a_run_that_recovered_and_then_failed_reports_both_the_first_error_and_the_end(tmp_path):
    record(
        tmp_path,
        "both",
        [tool_call("c1", "write", {"path": 7}), tool_call("c2", "ghost")],
        executor=FnExecutor(lambda t, c: ok({"saved": True})),
        definition_=definition(tools=[STRICT_WRITE]),
    )
    diagnosis = diagnose_run(tmp_path, "both")
    assert diagnosis.first_error.code == "SCHEMA_VALIDATION_FAILED"
    assert diagnosis.terminal.code == "TOOL_NOT_DECLARED"
    assert diagnosis.first_error.sequence < diagnosis.terminal.sequence
    assert [item.sequence for item in diagnosis.findings] == sorted(
        item.sequence for item in diagnosis.findings
    )
    assert "The first error was agent.tool-batch-completed" in diagnosis.summary


def test_a_gate_rejection_names_the_gate_and_the_verifier(tmp_path):
    registry = VerificationGateRegistry()
    registry.register(
        "sum",
        CallableVerificationGate(
            lambda context: VerificationDecision(
                False, "total is 3 but the lines sum to 4", verifier_id="sum-check"
            )
        ),
    )
    record(
        tmp_path, "gate", [final()], definition_=definition(gate="sum"), verification_gates=registry
    )
    diagnosis = diagnose_run(tmp_path, "gate")
    assert diagnosis.verdict == "failed"
    rejected = next(
        item for item in diagnosis.findings if item.event_type.endswith("verification-completed")
    )
    assert rejected.code == "VERIFICATION_GATE_REJECTED" and rejected.subject == "sum"
    assert rejected.details["verifier_id"] == "sum-check"
    assert diagnosis.terminal.code == "VERIFICATION_GATE_REJECTED"
    assert diagnosis.terminal.message == "total is 3 but the lines sum to 4"


def test_a_blocked_run_is_blocked_with_the_guard_that_stopped_it(tmp_path):
    oversized = definition().model_copy(
        update={"instructions": VersionedInstructions(version="v1", text="word " * 3000)}
    )
    record(
        tmp_path,
        "blocked",
        [final()],
        definition_=oversized,
        context_projection_policy=ContextProjectionPolicy(
            context_token_budget=256, episode_token_budget=256
        ),
    )
    diagnosis = diagnose_run(tmp_path, "blocked")
    assert diagnosis.verdict == "blocked" and not diagnosis.ok
    assert diagnosis.terminal.code == "CONTEXT_BUDGET_EXCEEDED"
    assert diagnosis.terminal.status == "blocked"


def test_a_hook_denial_is_a_blocked_run_with_the_hooks_reason(tmp_path):
    async def deny(context):
        return ToolHookDecision(False, "policy says no")

    record(
        tmp_path,
        "denied",
        [tool_call("c1", "echo"), final()],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
        pre_tool_hooks=[deny],
    )
    diagnosis = diagnose_run(tmp_path, "denied")
    assert diagnosis.verdict == "blocked"
    assert diagnosis.terminal.code == "TOOL_DENIED_BY_HOOK"
    assert diagnosis.terminal.message == "policy says no"


def test_a_cancelled_run_is_reported_as_cancelled_with_a_warning(tmp_path):
    class Token:
        def is_cancelled(self):
            return True

    from tests.support.agents import agent, arun, task

    telemetry = TelemetryStore(tmp_path)
    try:
        subject, _ = agent([final()], telemetry=telemetry)
        arun(subject.run(task("stopped"), cancellation=Token()))
    finally:
        telemetry.close()
    diagnosis = diagnose_run(tmp_path, "stopped")
    assert diagnosis.verdict == "cancelled" and not diagnosis.ok
    assert diagnosis.terminal.severity == "warning" and diagnosis.terminal.code is None
    assert diagnosis.first_error is None and diagnosis.first_problem == diagnosis.terminal


def test_a_ledger_that_ends_without_a_terminal_event_is_incomplete(tmp_path):
    store = TelemetryStore(tmp_path)
    try:
        store.emit(
            "agent.run-started",
            TelemetryContext(run_id="crashed", task_id="crashed"),
            actor=TelemetryActor(kind="agent", identifier="a", role="base-agent"),
            authority=TelemetryAuthority.DETERMINISTIC,
            status="run-started",
            payload={"iteration": 0, "details": {}},
        )
    finally:
        store.close()
    diagnosis = diagnose_run(tmp_path, "crashed")
    assert diagnosis.verdict == "incomplete" and not diagnosis.ok
    assert diagnosis.summary == 'Run "crashed" is incomplete: its ledger holds no terminal event.'


def test_a_tampered_ledger_is_not_healthy_even_though_the_run_completed(tmp_path):
    record(tmp_path, "forged", [final()])
    tamper_event(tmp_path, "forged", 2)
    diagnosis = diagnose_run(tmp_path, "forged")
    assert diagnosis.verdict == "completed" and not diagnosis.ok
    assert not diagnosis.integrity.telemetry_chain_valid
    assert diagnosis.integrity.telemetry_chain_failure["sequence"] == 2
    assert diagnosis.summary.startswith("The evidence for this run is not intact at sequence 2")


def test_findings_are_bounded_and_the_overflow_is_counted(tmp_path):
    calls = [call(f"w{index:03d}", "write", {"path": index}) for index in range(MAX_FINDINGS + 15)]
    record(
        tmp_path,
        "noisy",
        [batch(*calls), final()],
        executor=FnExecutor(lambda t, c: ok({"saved": True})),
        definition_=definition(tools=[STRICT_WRITE]),
    )
    diagnosis = diagnose_run(tmp_path, "noisy")
    assert len(diagnosis.findings) == MAX_FINDINGS and diagnosis.findings_omitted == 15
    assert diagnosis.first_problem == diagnosis.findings[0]


def test_an_unknown_run_says_what_kinds_of_id_are_accepted(tmp_path):
    record(tmp_path, "known", [final()])
    with pytest.raises(ValueError, match='Run "ghost" is unknown') as caught:
        diagnose_run(tmp_path, "ghost")
    assert "telemetry run id or a graph run id" in str(caught.value)


def test_a_pruned_run_is_named_as_pruned(tmp_path):
    record(tmp_path, "old", [final()])
    prune_run_root(
        tmp_path,
        RetentionPolicy(older_than_seconds=1),
        dry_run=False,
        now=datetime(2099, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match='Run "old" was pruned at'):
        diagnose_run(tmp_path, "old")


def test_a_missing_run_root_is_named_and_nothing_is_created(tmp_path):
    missing = tmp_path / "typo"
    with pytest.raises(ValueError, match="does not exist or is not a directory"):
        diagnose_run(missing, "x")
    assert not missing.exists()


def test_a_root_without_ledgers_says_where_it_looked(tmp_path):
    with pytest.raises(ValueError, match="has no telemetry database"):
        diagnose_run(tmp_path, "x")
    assert list(tmp_path.iterdir()) == []


def test_diagnosing_changes_no_file_and_creates_only_sqlite_side_files(tmp_path):
    record(
        tmp_path,
        "quiet",
        [tool_call("c1", "ghost")],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
    )
    before = fingerprints(tmp_path)
    diagnose_run(tmp_path, "quiet")
    after = fingerprints(tmp_path)
    created = set(after) - set(before)
    assert created <= SQLITE_SIDE_FILES, created
    assert {name: digest for name, digest in after.items() if name in before} == before
    assert not (tmp_path / ".agent-runs").exists()


def test_the_command_prints_the_diagnosis_and_exits_by_verdict(tmp_path):
    record(tmp_path, "fine", [final()])
    record(
        tmp_path,
        "broken",
        [tool_call("c1", "ghost")],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
    )
    healthy = cli("diagnose-run", tmp_path, "fine")
    assert healthy.returncode == 0 and json.loads(healthy.stdout)["ok"] is True
    failed = cli("diagnose-run", tmp_path, "broken")
    assert failed.returncode == 1
    printed = json.loads(failed.stdout)
    assert printed["verdict"] == "failed" and printed["terminal"]["code"] == "TOOL_NOT_DECLARED"
    unknown = cli("diagnose-run", tmp_path, "ghost")
    assert unknown.returncode == 3 and 'Run "ghost" is unknown' in unknown.stderr
