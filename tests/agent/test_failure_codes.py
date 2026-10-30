from nailong_agent_sdk.agent.base_agent import BaseAgent, ToolHookDecision
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.verification import (
    CallableVerificationGate,
    VerificationDecision,
    VerificationGateRegistry,
)
from nailong_agent_sdk.foundations.contracts import (
    AgentFailure,
    AgentRunStatus,
    EpisodeKind,
    ToolBatchTurn,
    ToolCall,
    ToolExecutionResult,
    VersionedInstructions,
)
from nailong_agent_sdk.memory.context_projection import ContextProjectionPolicy
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.episodes import InMemoryEpisodeGraph
from nailong_agent_sdk.observability.telemetry_models import TelemetryAuthority
from nailong_agent_sdk.state.project_state_models import (
    ProjectStateProjectionPolicy,
    StageStateSchema,
    StateAuthority,
    StateEvidence,
    StateTransition,
    StateTransitionKind,
)
from nailong_agent_sdk.state.project_state_store import InMemoryProjectStateStore
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

BLOCKED = AgentRunStatus.BLOCKED
FAILED = AgentRunStatus.FAILED


def assert_typed(result, code, status):
    assert result.status is status, (result.status, result.reason)
    assert result.failure is not None, f"{code}: the result carries no typed failure"
    assert result.failure.code == code, result.failure
    assert result.failure.message == result.reason
    ended = next(event for event in result.events if event.type == "terminated")
    assert ended.details["failure_code"] == code
    assert ended.details["status"] == status.value


def test_an_undeclared_tool_names_the_tool_the_call_and_the_declared_set():
    subject, _ = agent(
        [tool_call("c9", "ghost")],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO, tool("zeta")]),
    )
    result = arun(subject.run(task()))
    assert_typed(result, "TOOL_NOT_DECLARED", FAILED)
    assert result.reason == 'Tool "ghost" is not declared for this agent.'
    assert result.failure.details == {
        "tool": "ghost",
        "tool_call_id": "c9",
        "declared_tools": ["echo", "zeta"],
    }


def test_a_declared_tool_without_an_executor_names_the_tool_and_the_call():
    subject, _ = agent([tool_call("c1", "echo")], definition_=definition(tools=[ECHO]))
    result = arun(subject.run(task()))
    assert_typed(result, "TOOL_EXECUTOR_MISSING", FAILED)
    assert 'declared tool "echo"' in result.reason
    assert result.failure.details == {"tool": "echo", "tool_call_id": "c1"}


def test_a_gate_rejection_names_the_gate_the_verifier_and_the_output_digest():
    registry = VerificationGateRegistry()
    registry.register(
        "sum",
        CallableVerificationGate(
            lambda context: VerificationDecision(
                False,
                "total is 3 but the lines sum to 4",
                verifier_id="sum-check",
                authority=TelemetryAuthority.DETERMINISTIC,
            )
        ),
    )
    subject, _ = agent([final()], definition_=definition(gate="sum"), verification_gates=registry)
    result = arun(subject.run(task()))
    assert_typed(result, "VERIFICATION_GATE_REJECTED", FAILED)
    assert result.reason == "total is 3 but the lines sum to 4"
    details = result.failure.details
    assert details["verification_gate_id"] == "sum" and details["verifier_id"] == "sum-check"
    assert len(details["output_digest"]) == 64
    verification = next(e for e in result.events if e.type == "verification-completed")
    assert verification.details["failure_code"] == "VERIFICATION_GATE_REJECTED"
    assert verification.details["output_digest"] == details["output_digest"]


def test_a_rejection_without_a_reason_still_reads_as_a_rejection():
    registry = VerificationGateRegistry()
    registry.register(
        "quiet", CallableVerificationGate(lambda context: VerificationDecision(False))
    )
    subject, _ = agent([final()], definition_=definition(gate="quiet"), verification_gates=registry)
    result = arun(subject.run(task()))
    assert_typed(result, "VERIFICATION_GATE_REJECTED", FAILED)
    assert result.reason == "Deterministic verification gate rejected output."


def test_an_accepted_output_leaves_the_verification_event_without_a_failure_code():
    registry = VerificationGateRegistry()
    registry.register("fine", CallableVerificationGate(lambda context: VerificationDecision(True)))
    subject, _ = agent([final()], definition_=definition(gate="fine"), verification_gates=registry)
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED and result.failure is None
    verification = next(e for e in result.events if e.type == "verification-completed")
    assert "failure_code" not in verification.details


def test_the_iteration_limit_names_the_limit():
    subject, _ = agent(
        [tool_call("c1", "echo"), tool_call("c2", "echo")],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO], max_iterations=2),
    )
    result = arun(subject.run(task()))
    assert_typed(result, "MAX_ITERATIONS_REACHED", FAILED)
    assert result.failure.details == {"max_iterations": 2}
    assert "Maximum iteration limit (2) reached" in result.reason


def test_a_model_that_reports_itself_blocked_is_a_typed_block_with_its_own_words():
    subject, _ = agent([{"type": "blocked", "reason": "the credentials file is missing"}])
    result = arun(subject.run(task()))
    assert_typed(result, "MODEL_REPORTED_BLOCKED", BLOCKED)
    assert result.reason == "the credentials file is missing"


def test_a_pre_tool_hook_denial_names_the_tool_and_the_call():
    async def deny(context):
        return ToolHookDecision(False, "policy says no")

    subject, _ = agent(
        [tool_call("c1", "echo"), final()],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
        pre_tool_hooks=[deny],
    )
    result = arun(subject.run(task()))
    assert_typed(result, "TOOL_DENIED_BY_HOOK", BLOCKED)
    assert result.reason == "policy says no"
    assert result.failure.details == {"tool": "echo", "tool_call_id": "c1"}


def test_a_blocked_call_in_a_batch_ends_the_run_with_that_calls_code():
    async def deny_second(context):
        if context.call.id == "second":
            return ToolHookDecision(False, "the second call is not allowed")
        return None

    subject, _ = agent(
        [batch(call("first", "echo"), call("second", "echo"))],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
        pre_tool_hooks=[deny_second],
    )
    result = arun(subject.run(task()))
    assert_typed(result, "TOOL_DENIED_BY_HOOK", BLOCKED)
    assert result.failure.details["tool_call_id"] == "second"


def test_an_unresolvable_batch_names_the_calls_that_cannot_start():
    subject = BaseAgent(
        definition(tools=[ECHO]),
        ScriptedModel([]),
        require_tool_authorization=False,
        tool_executor=echo_executor(),
    )
    subject.profiler.begin_run("t1", "t1", "audit-agent")
    stuck = ToolBatchTurn.model_construct(
        type="tool-batch",
        calls=[
            ToolCall(id="a", name="echo", arguments={}, depends_on_call_ids=["b"]),
            ToolCall(id="b", name="echo", arguments={}, depends_on_call_ids=["a"]),
        ],
    )
    emitted = []

    def emit(event_type, iteration, **details):
        emitted.append((event_type, details))

    result = arun(
        subject._execute_tool_batch(
            task(), 1, stuck, None, InMemoryEpisodeGraph(), InMemoryEpisodeStore(), [], emit, None
        )
    )
    assert result.status is FAILED
    assert result.failure.code == "TOOL_BATCH_DEPENDENCIES_UNRESOLVED"
    assert result.failure.details == {"pending_call_ids": ["a", "b"]}
    assert emitted[-1][0] == "terminated"
    assert emitted[-1][1]["failure_code"] == "TOOL_BATCH_DEPENDENCIES_UNRESOLVED"


def test_the_context_budget_guard_carries_both_numbers():
    oversized = definition().model_copy(
        update={"instructions": VersionedInstructions(version="v1", text="word " * 3000)}
    )
    subject, _ = agent(
        [final()],
        definition_=oversized,
        context_projection_policy=ContextProjectionPolicy(
            context_token_budget=256, episode_token_budget=256
        ),
    )
    result = arun(subject.run(task()))
    assert_typed(result, "CONTEXT_BUDGET_EXCEEDED", BLOCKED)
    assert result.reason.startswith("CONTEXT_BUDGET_EXCEEDED: immutable prompt plus bounded")
    assert result.failure.details["context_token_budget"] == 256
    assert result.failure.details["estimated_tokens"] > 256


def test_the_project_state_guard_carries_both_numbers():
    store = InMemoryProjectStateStore()
    store.ensure("t1", StageStateSchema(schema_id="unclassified-v1", stage="unclassified"))
    for index in range(6):
        store.apply(
            "t1",
            StateTransition(
                kind=StateTransitionKind.QUESTION_OPENED,
                actor=StateAuthority.HUMAN,
                action_id=f"open-{index}",
                payload={"question_id": f"q{index}", "content": "why " * 120, "owner": "human"},
                evidence=[StateEvidence(evidence_id=f"open-{index}", kind="human-note")],
            ),
        )
    subject, _ = agent(
        [final()],
        project_state_store=store,
        project_state_projection_policy=ProjectStateProjectionPolicy(token_budget=400),
    )
    result = arun(subject.run(task()))
    assert_typed(result, "PROJECT_STATE_BUDGET_EXCEEDED", BLOCKED)
    assert result.failure.details["token_budget"] == 400
    assert result.failure.details["estimated_tokens"] > 400


class NeverClosingStore(InMemoryEpisodeStore):
    def close(self, episode_id, *, description=None):
        return self._require(episode_id)


def test_the_deadlock_guard_carries_the_episode_budget():
    unclosed = tool("probe", kind=EpisodeKind.ACTION)
    executor = FnExecutor(
        lambda tool_definition, context: ToolExecutionResult(
            status="succeeded", output={"blob": "payload " * 800}
        )
    )
    subject, _ = agent(
        [tool_call("c1", "probe"), final({"status": "wrong"}), final()],
        executor=executor,
        definition_=definition(tools=[unclosed], max_iterations=6),
        episode_store_factory=NeverClosingStore,
        context_projection_policy=ContextProjectionPolicy(
            context_token_budget=4000, episode_token_budget=256
        ),
    )
    result = arun(subject.run(task()))
    assert_typed(result, "CONTEXT_DEADLOCK", BLOCKED)
    assert result.failure.details["episode_token_budget"] == 256


def test_a_failed_call_carries_its_message_in_the_batch_event_and_a_succeeded_one_nothing_extra():
    def fail_one(tool_definition, context):
        if context.call.id == "broken":
            return ToolExecutionResult(status="failed", error="disk is full")
        return ok({"fine": True})

    subject, _ = agent(
        [batch(call("fine", "echo"), call("broken", "echo")), final()],
        executor=FnExecutor(fail_one),
        definition_=definition(tools=[ECHO]),
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED
    done = next(e for e in result.events if e.type == "tool-batch-completed")
    by_id = {item["tool_call_id"]: item for item in done.details["outcomes"]}
    assert by_id["fine"] == {"tool_call_id": "fine", "tool": "echo", "status": "succeeded"}
    assert by_id["broken"] == {
        "tool_call_id": "broken",
        "tool": "echo",
        "status": "failed",
        "failure_code": None,
        "error": "disk is full",
    }


def test_a_blocked_call_ends_the_batch_with_its_own_code_and_a_bounded_event_message():
    long_reason = "approval is required because " + "x" * 600

    def gate(tool_definition, context):
        if context.call.id == "gated":
            return ToolExecutionResult(
                status="blocked",
                error=long_reason,
                failure=AgentFailure(
                    code="TOOL_APPROVAL_REQUIRED",
                    message=long_reason,
                    details={"approval_id": "approval-7"},
                ),
            )
        return ok({"fine": True})

    subject, _ = agent(
        [batch(call("fine", "echo"), call("gated", "echo"))],
        executor=FnExecutor(gate),
        definition_=definition(tools=[ECHO]),
    )
    result = arun(subject.run(task()))
    assert_typed(result, "TOOL_APPROVAL_REQUIRED", BLOCKED)
    assert result.failure.details == {
        "tool": "echo",
        "tool_call_id": "gated",
        "approval_id": "approval-7",
    }
    assert result.reason == long_reason
    done = next(e for e in result.events if e.type == "tool-batch-completed")
    gated = next(item for item in done.details["outcomes"] if item["tool_call_id"] == "gated")
    assert gated["status"] == "blocked" and gated["failure_code"] == "TOOL_APPROVAL_REQUIRED"
    assert len(gated["error"]) == 300 and gated["error"].endswith("...")


def test_an_argument_violation_is_recorded_with_its_schema_code_and_path():
    strict = tool(
        "write",
        schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    executor = FnExecutor(lambda tool_definition, context: ok({"saved": True}))
    subject, _ = agent(
        [tool_call("c1", "write", {"path": 7}), final()],
        executor=executor,
        definition_=definition(tools=[strict]),
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED and executor.calls == []
    outcome = next(e for e in result.events if e.type == "tool-batch-completed").details[
        "outcomes"
    ][0]
    assert outcome["failure_code"] == "SCHEMA_VALIDATION_FAILED"
    assert "$.path" in outcome["error"] and "not of type 'string'" in outcome["error"]


def test_a_rejected_candidate_output_records_the_schema_code():
    subject, _ = agent(
        [{"type": "final", "output": {"status": "nope"}}, final()],
        definition_=definition(max_iterations=3),
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED
    rejected = next(e for e in result.events if e.type == "output-rejected")
    assert rejected.details["failure_code"] == "SCHEMA_VALIDATION_FAILED"
    assert "candidate output" in rejected.details["reason"]


def test_a_cancelled_run_reports_its_status_and_no_failure_code():
    class Token:
        def is_cancelled(self):
            return True

    subject, _ = agent([final()])
    result = arun(subject.run(task(), cancellation=Token()))
    assert result.status is AgentRunStatus.CANCELLED and result.failure is None
    ended = next(event for event in result.events if event.type == "terminated")
    assert ended.details["failure_code"] is None
