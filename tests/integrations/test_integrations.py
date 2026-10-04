import asyncio
import sys
import time

import pytest
from pydantic import ValidationError

from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.verification import VerificationContext
from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
from nailong_agent_sdk.integrations._utils import (
    OptionalDependencyError,
    assert_sanitized_interop_value,
    canonical_digest,
    require_optional_module,
)
from nailong_agent_sdk.integrations.contracts import (
    InteropFailureMode,
    InteropOperationStatus,
    InteropReceipt,
    InteropRunEnvelope,
)
from nailong_agent_sdk.integrations.jev.advisory import (
    JevAdvisoryPolicy,
    JevAdvisoryVerificationGate,
)
from nailong_agent_sdk.integrations.jev.architecture import (
    JevArchitectureRouter,
    JevArchitectureRoutingPolicy,
)
from nailong_agent_sdk.integrations.jev.decision import TypeSafeJevDecisionEvaluator
from nailong_agent_sdk.integrations.jev.exploration import (
    ExplorationCandidate,
    JevExplorationAdvisor,
)
from nailong_agent_sdk.integrations.jev.models import (
    JevChoiceQuestion,
    JevDecisionRequest,
    JevDecisionResult,
    JevNoulAnswer,
    JevNoulQuestion,
    JevQuestionSpec,
)
from nailong_agent_sdk.integrations.jev.receipts import InMemoryJevReceiptStore
from nailong_agent_sdk.integrations.langchain import (
    LangChainAgentModelAdapter,
    LangChainSdkRunnable,
    LangChainSdkToolFacade,
    parse_structured_sdk_turn,
)
from nailong_agent_sdk.integrations.langgraph import (
    LangGraphApprovalChallenge,
    LangGraphNodeExecutor,
    LangGraphSdkNode,
    LangGraphSdkNodeBinding,
    build_langgraph_state_graph,
    langgraph_interrupt_payload,
)
from nailong_agent_sdk.integrations.receipts import (
    InMemoryInteropReceiptStore,
    TelemetryInteropReceiptSink,
)
from nailong_agent_sdk.observability.telemetry_models import TelemetryContext
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.agents import (
    FnExecutor,
    agent,
    arun,
    definition,
    final,
    ok,
    task,
    tool,
    tool_call,
)
from tests.support.model_context import model_context
from tests.support.paths import STUB_ROOT


@pytest.fixture(autouse=True)
def typesafe_stub(monkeypatch):
    monkeypatch.syspath_prepend(str(STUB_ROOT))


def test_sanitizer_key_variants_limits_and_types():
    forbidden = [
        "api_key",
        "apiKey",
        "API-KEY",
        "Authorization",
        "x_secret_thing",
        "refreshToken",
        "Message",
        "messages",
        "password",
        "client-secret",
        "TOOL_RESULTS",
        "audit log",
        "secretary",
    ]
    for key in forbidden:
        with pytest.raises(ValueError, match="Unsafe key"):
            assert_sanitized_interop_value({key: 1})
    for key in ("token_count", "tokens", "summary", "description", "model", "note"):
        assert_sanitized_interop_value({key: 1})
    deep = value = {}
    for _ in range(16):
        value["k"] = {}
        value = value["k"]
    assert_sanitized_interop_value(deep)
    value["k"] = {}
    with pytest.raises(ValueError, match="nesting depth"):
        assert_sanitized_interop_value(deep)
    assert_sanitized_interop_value([0] * 256)
    with pytest.raises(ValueError, match="list exceeds"):
        assert_sanitized_interop_value([0] * 257)
    assert_sanitized_interop_value({str(i): i for i in range(128)})
    with pytest.raises(ValueError, match="object exceeds"):
        assert_sanitized_interop_value({str(i): i for i in range(129)})
    assert_sanitized_interop_value("x" * 16_384)
    with pytest.raises(ValueError, match="string exceeds"):
        assert_sanitized_interop_value("x" * 16_385)
    for bad in ((1, 2), {1, 2}, b"bytes", object(), time.gmtime()):
        with pytest.raises(TypeError):
            assert_sanitized_interop_value({"v": bad})


def test_sanitizer_does_not_inspect_values_for_credentials():
    for value in (
        "Bearer sk-live-FAKE0000000000000000000000000000",
        "password=hunter2",
        "api_key: abcdef123456",
        "see sk-" + "a" * 24,
    ):
        with pytest.raises(ValueError, match=r"string at \$\.note contains credential-shaped text"):
            assert_sanitized_interop_value({"note": value})
    with pytest.raises(ValueError, match=r"string at \$\.items\[1\] contains credential-shaped"):
        assert_sanitized_interop_value({"items": ["fine", "token=abcd1234efgh"]})
    for value in ("the token budget is 4000", "tokens: 12", "Passwords are rotated weekly"):
        assert_sanitized_interop_value({"note": value})


def test_envelope_digest_rules_and_determinism():
    envelope = InteropRunEnvelope(
        run_id="r", remaining_turn_budget=3, projection={"b": 1, "a": [1, 2]}
    )
    same = InteropRunEnvelope(run_id="r", remaining_turn_budget=3, projection={"a": [1, 2], "b": 1})
    assert (
        envelope.projection_digest
        == same.projection_digest
        == canonical_digest({"a": [1, 2], "b": 1})
    )
    with pytest.raises(ValidationError, match="does not match"):
        InteropRunEnvelope(
            run_id="r", remaining_turn_budget=3, projection={"a": 1}, projection_digest="0" * 64
        )
    with pytest.raises(ValidationError):
        InteropRunEnvelope(run_id="r", remaining_turn_budget=3, projection={"api_key": "x"})
    with pytest.raises(ValidationError):
        InteropRunEnvelope(run_id="r", remaining_turn_budget=-1)
    with pytest.raises(ValidationError, match="got tuple at \\$\\.v"):
        InteropRunEnvelope(run_id="r", remaining_turn_budget=1, projection={"v": (1, 2)})


def test_receipt_sinks(tmp_path):
    receipt = InteropReceipt(
        provider="p",
        operation="op",
        status=InteropOperationStatus.FAILED,
        run_id="run-1",
        projection_digest="a" * 64,
    )
    memory = InMemoryInteropReceiptStore()
    memory(receipt)
    assert memory.receipts == [receipt]
    store = TelemetryStore(tmp_path)
    try:
        sink = TelemetryInteropReceiptSink(store, lambda r: TelemetryContext(run_id=r.run_id))
        sink(receipt)
        events = store.list_events("run-1")
        assert (
            events[0].event_type == "interop.external_operation"
            and events[0].severity.value == "warning"
        )
        assert events[0].payload["projection_digest"] == "a" * 64
    finally:
        store.close()


class FakeClient:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = 0

    async def system_one(self, *, state, questions, model):
        self.calls += 1
        result = self.behavior(self.calls, state, questions, model)
        if asyncio.iscoroutine(result):
            result = await result
        return result


def noul_spec(question_id="verdict"):
    return JevQuestionSpec(
        spec_id="spec",
        version="v1",
        questions={question_id: JevNoulQuestion(instructions="Is it ok?")},
    )


def choice_spec():
    return JevQuestionSpec(
        spec_id="route",
        version="v1",
        questions={
            "architecture": JevChoiceQuestion(
                instructions="pick", criteria={"single-agent": "one", "multi-agent": "many"}
            )
        },
    )


def jev_request(spec=None, **overrides):
    values = dict(
        purpose="verification",
        run_id="run-1",
        state={"summary": "ok"},
        question_spec=spec or noul_spec(),
        model="jev-1",
        deadline_seconds=2,
    )
    values.update(overrides)
    return JevDecisionRequest(**values)


def evaluator(behavior, sink=None, **kwargs):
    client = FakeClient(behavior)
    return TypeSafeJevDecisionEvaluator(
        receipt_sink=sink or InMemoryJevReceiptStore(), client_factory=lambda: client, **kwargs
    ), client


def test_jev_evaluator_normalizes_answers_and_records_digest_only_receipts():
    sink = InMemoryJevReceiptStore()
    good, _ = evaluator(
        lambda n, s, q, m: {
            "model": m,
            "request_id": "req-1",
            "usage": {"input_tokens": 5, "output_tokens": 2},
            "answers": {"verdict": {"type": "noul", "noul": 0.9}},
        },
        sink,
    )
    request = jev_request()
    result = arun(good.evaluate(request))
    assert (
        result.status is InteropOperationStatus.SUCCEEDED and result.answers["verdict"].noul == 0.9
    )
    assert (
        result.state_digest == request.state_digest
        and result.response_digest
        and result.input_tokens == 5
    )
    receipt = sink.receipts[0]
    blob = receipt.model_dump_json()
    assert (
        "Is it ok?" not in blob
        and "summary" not in blob
        and receipt.projection_digest == request.state_digest
    )
    grouped, _ = evaluator(
        lambda n, s, q, m: {"nouls": {"verdict": {"noul": 0.4}}, "model": m}, sink
    )
    assert arun(grouped.evaluate(request)).answers["verdict"].noul == 0.4


def test_jev_evaluator_failure_modes_become_unavailable_with_reasons():
    sink = InMemoryJevReceiptStore()
    cases = {
        "wrong ids": (
            lambda n, s, q, m: {"answers": {"other": {"type": "noul", "noul": 0.5}}},
            "answered question IDs",
        ),
        "wrong type": (
            lambda n, s, q, m: {
                "answers": {
                    "verdict": {
                        "type": "choice",
                        "choice": "a",
                        "probabilities": {"a": 1.0},
                        "confidence": 0.5,
                    }
                }
            },
            "registered question type",
        ),
        "out of range": (
            lambda n, s, q, m: {"answers": {"verdict": {"type": "noul", "noul": 7}}},
            "typed answer contract",
        ),
        "not mapping": (lambda n, s, q, m: 42, "not mapping-compatible"),
        "raises": (
            lambda n, s, q, m: (_ for _ in ()).throw(RuntimeError("secret connection string")),
            "provider-runtimeerror",
        ),
    }
    for name, (behavior, fragment) in cases.items():
        subject, _ = evaluator(behavior, sink)
        result = arun(subject.evaluate(jev_request()))
        assert result.status is InteropOperationStatus.UNAVAILABLE, name
        assert fragment in result.unavailable_reason, (name, result.unavailable_reason)
        assert "secret connection string" not in result.unavailable_reason

    async def slow(n, s, q, m):
        await asyncio.sleep(5)

    subject, _ = evaluator(slow, sink)
    started = time.monotonic()
    result = arun(subject.evaluate(jev_request(deadline_seconds=0.5)))
    assert (
        result.status is InteropOperationStatus.UNAVAILABLE
        and "no response within the request deadline" in result.unavailable_reason
    )
    assert time.monotonic() - started < 3
    flaky, client = evaluator(
        lambda n, s, q, m: (
            (_ for _ in ()).throw(RuntimeError("x"))
            if n < 3
            else {"answers": {"verdict": {"type": "noul", "noul": 0.7}}}
        )
    )
    recovered = arun(flaky.evaluate(jev_request(max_attempts=3)))
    assert (
        recovered.status is InteropOperationStatus.SUCCEEDED
        and recovered.retry_count == 2
        and client.calls == 3
    )
    choice = evaluator(
        lambda n, s, q, m: {
            "answers": {
                "architecture": {
                    "type": "choice",
                    "choice": "bogus",
                    "probabilities": {"single-agent": 0.5, "multi-agent": 0.5},
                    "confidence": 0.9,
                }
            }
        },
        sink,
    )[0]
    assert (
        "does not match the registered options"
        in arun(choice.evaluate(jev_request(choice_spec()))).unavailable_reason
    )
    assert len(sink.receipts) == 7 and all(len(r.projection_digest) == 64 for r in sink.receipts)


def test_jev_optional_dependency_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
    with pytest.raises(OptionalDependencyError, match=r"nailong-agent-sdk\[jev\]"):
        require_optional_module("typesafe_sdk", "jev")
    subject = TypeSafeJevDecisionEvaluator(receipt_sink=InMemoryJevReceiptStore())
    result = arun(subject.evaluate(jev_request()))
    assert (
        result.status is InteropOperationStatus.UNAVAILABLE
        and "nailong-agent-sdk[jev]" in result.unavailable_reason
    )


class StubEvaluator:
    def __init__(self, result):
        self.result = result
        self.requests = []

    async def evaluate(self, request):
        self.requests.append(request)
        return self.result


def jev_result(answers=None, status=InteropOperationStatus.SUCCEEDED, reason=None):
    return JevDecisionResult(
        status=status,
        answers=answers or {},
        state_digest="a" * 64,
        question_spec_digest="b" * 64,
        unavailable_reason=reason,
    )


def test_advisory_gate_never_accepts_what_the_local_gate_rejected():
    def local_fail(context):
        return (False, "local says no")

    evaluator_ = StubEvaluator(jev_result({"verdict": JevNoulAnswer(noul=0.99)}))
    gate = JevAdvisoryVerificationGate(
        type("G", (), {"verify": staticmethod(local_fail)})(),
        evaluator_,
        lambda c: jev_request(),
        JevAdvisoryPolicy(question_id="verdict", minimum_noul=0.5),
    )
    context = VerificationContext(output={}, definition=definition(), task=task())
    decision = arun(gate.verify(context))
    assert (
        decision.passed is False
        and decision.reason == "local says no"
        and evaluator_.requests == []
    )


@pytest.mark.parametrize(
    "answers, status, mode, expected",
    [
        (
            {"verdict": JevNoulAnswer(noul=0.5)},
            InteropOperationStatus.SUCCEEDED,
            InteropFailureMode.ESCALATE,
            True,
        ),
        (
            {"verdict": JevNoulAnswer(noul=0.4999)},
            InteropOperationStatus.SUCCEEDED,
            InteropFailureMode.ESCALATE,
            False,
        ),
        ({}, InteropOperationStatus.UNAVAILABLE, InteropFailureMode.ESCALATE, False),
        ({}, InteropOperationStatus.UNAVAILABLE, InteropFailureMode.REJECT, False),
        ({}, InteropOperationStatus.UNAVAILABLE, InteropFailureMode.FALLBACK_DETERMINISTIC, True),
        ({}, InteropOperationStatus.SUCCEEDED, InteropFailureMode.REJECT, False),
    ],
)
def test_advisory_gate_policy_matrix(answers, status, mode, expected):
    evaluator_ = StubEvaluator(
        jev_result(
            answers,
            status,
            "provider down" if status is not InteropOperationStatus.SUCCEEDED else None,
        )
    )
    gate = JevAdvisoryVerificationGate(
        type("G", (), {"verify": staticmethod(lambda c: True)})(),
        evaluator_,
        lambda c: jev_request(),
        JevAdvisoryPolicy(question_id="verdict", minimum_noul=0.5, on_unavailable=mode),
    )
    decision = arun(
        gate.verify(VerificationContext(output={}, definition=definition(), task=task()))
    )
    assert decision.passed is expected, decision


def test_architecture_router_is_monotonic_and_honors_failure_modes():
    from nailong_agent_sdk.integrations.jev.models import JevChoiceAnswer

    def answer(choice, confidence):
        return JevChoiceAnswer(
            choice=choice,
            probabilities={"single-agent": 1 - confidence, "multi-agent": confidence},
            confidence=confidence,
        )

    policy = JevArchitectureRoutingPolicy(model="m", minimum_confidence=0.8)
    for evaluator_result, expected in (
        (jev_result({"architecture": answer("multi-agent", 0.8)}), "multi-agent"),
        (jev_result({"architecture": answer("multi-agent", 0.79)}), "single-agent"),
        (jev_result({"architecture": answer("single-agent", 0.99)}), "single-agent"),
        (jev_result({}, InteropOperationStatus.UNAVAILABLE, "down"), "single-agent"),
        (jev_result({"architecture": JevNoulAnswer(noul=0.9)}), "single-agent"),
    ):
        router = JevArchitectureRouter(StubEvaluator(evaluator_result), policy)
        advice = arun(
            router.advise(
                run_id="r", deterministic_architecture="single-agent", state={"summary": "x"}
            )
        )
        assert advice.architecture == expected
    never_called = StubEvaluator(jev_result({}))
    lifted = arun(
        JevArchitectureRouter(never_called, policy).advise(
            run_id="r", deterministic_architecture="multi-agent", state={}
        )
    )
    assert lifted.architecture == "multi-agent" and never_called.requests == []
    rejecting = JevArchitectureRouter(
        StubEvaluator(jev_result({}, InteropOperationStatus.UNAVAILABLE, "down")),
        JevArchitectureRoutingPolicy(model="m", on_unavailable=InteropFailureMode.REJECT),
    )
    with pytest.raises(RuntimeError, match="down"):
        arun(rejecting.advise(run_id="r", deterministic_architecture="single-agent", state={}))
    with pytest.raises(ValidationError, match="on_unavailable") as excinfo:
        JevArchitectureRoutingPolicy(model="m", on_unavailable=InteropFailureMode.ESCALATE)
    assert "has no escalation channel" in str(excinfo.value)


def test_exploration_advisor_bounds_and_selection():
    from nailong_agent_sdk.integrations.jev.models import JevChoiceAnswer

    candidates = [
        ExplorationCandidate(
            candidate_id=f"c{i}", summary=f"s{i}", deterministic_rank=i, provenance_ref="p"
        )
        for i in range(4)
    ]

    def pick(choice):
        return JevChoiceAnswer(
            choice=choice, probabilities={f"c{i}": 0.25 for i in range(4)}, confidence=0.5
        )

    advisor = JevExplorationAdvisor(
        StubEvaluator(jev_result({"priority": pick("c2")})), model="m", max_candidates=4
    )
    assert arun(advisor.prioritize("r", candidates, instructions="i")).selected_candidate_id == "c2"
    invalid = JevExplorationAdvisor(StubEvaluator(jev_result({"priority": pick("zzz")})), model="m")
    fallback = arun(invalid.prioritize("r", candidates, instructions="i"))
    assert fallback.selected_candidate_id == "c0" and fallback.used_deterministic_fallback
    rejecting = JevExplorationAdvisor(
        StubEvaluator(jev_result({}, InteropOperationStatus.UNAVAILABLE, "down")),
        model="m",
        on_unavailable=InteropFailureMode.REJECT,
    )
    assert (
        arun(rejecting.prioritize("r", candidates, instructions="i")).selected_candidate_id is None
    )
    with pytest.raises(ValueError, match="at least two candidates"):
        arun(advisor.prioritize("r", candidates[:1], instructions="i"))
    with pytest.raises(ValueError, match="exceeds the declared Jev bound"):
        arun(
            JevExplorationAdvisor(
                StubEvaluator(jev_result({})), model="m", max_candidates=2
            ).prioritize("r", candidates, instructions="i")
        )
    many = [
        ExplorationCandidate(
            candidate_id=f"c{i}", summary=f"s{i}", deterministic_rank=i, provenance_ref="p"
        )
        for i in range(300)
    ]
    with pytest.raises(ValueError, match="max_candidates 300 must be between 2 and 255") as bound:
        JevExplorationAdvisor(StubEvaluator(jev_result({})), model="m", max_candidates=300)
    assert type(bound.value) is ValueError
    full = JevExplorationAdvisor(
        StubEvaluator(jev_result({"priority": pick("c2")})), model="m", max_candidates=255
    )
    assert arun(full.prioritize("r", many[:255], instructions="i")).selected_candidate_id == "c2"
    twin = [*candidates[:3], candidates[0]]
    with pytest.raises(ValueError, match=r"candidate ids must be unique; repeated: \['c0'\]"):
        arun(advisor.prioritize("r", twin, instructions="i"))


class FakeRunnable:
    def __init__(self, response):
        self.response = response
        self.seen = []

    async def ainvoke(self, prompt, config=None):
        self.seen.append((prompt, config))
        return self.response


def test_langchain_model_adapter_prompt_safety_and_parsing():
    context = model_context()
    runnable = FakeRunnable({"type": "final", "output": {"status": "complete"}})
    good_prompt = {
        "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}],
        "temperature": 0,
    }
    adapter = LangChainAgentModelAdapter(runnable, lambda c: good_prompt, parse_structured_sdk_turn)
    assert arun(adapter.next_turn(context)).type == "final"

    def run_prompt(prompt):
        return arun(
            LangChainAgentModelAdapter(
                runnable, lambda c: prompt, parse_structured_sdk_turn
            ).next_turn(context)
        )

    bad_prompts = [
        {
            "messages": [
                {"role": "system", "content": "a"},
                {"role": "user", "content": "b"},
                {"role": "user", "content": "c"},
            ]
        },
        {"messages": [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]},
        {"messages": [{"role": "assistant", "content": "a"}]},
        {"messages": [{"role": "user", "content": ""}]},
        {"messages": [{"role": "user", "content": "x" * 16_385}]},
        {"messages": [{"role": "user", "content": "a", "name": "n"}]},
        {
            "messages": [],
        },
        {"messages": [{"role": "user", "content": "a"}], "api_key": "x"},
        {"api_key": "x"},
    ]
    for prompt in bad_prompts:
        with pytest.raises((ValueError, TypeError)):
            run_prompt(prompt)
    with pytest.raises(TypeError, match="structured SDK AgentTurn mapping"):
        parse_structured_sdk_turn("free text", context)
    with pytest.raises(ValidationError):
        arun(
            LangChainAgentModelAdapter(
                FakeRunnable({"type": "mystery"}), lambda c: "p", parse_structured_sdk_turn
            ).next_turn(context)
        )
    big = "x" * 20_000
    try:
        arun(
            LangChainAgentModelAdapter(
                runnable,
                lambda c: {
                    "messages": [
                        {"role": "system", "content": "s"},
                        {"role": "user", "content": big},
                    ]
                },
                parse_structured_sdk_turn,
            ).next_turn(context)
        )
    except ValueError:
        pass


def test_langchain_runnable_and_tool_facade_with_real_langchain():
    pytest.importorskip("langchain_core")
    echo = tool(
        "echo",
        schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
            "additionalProperties": False,
        },
    )
    runnable = LangChainSdkRunnable(
        lambda t: agent(
            [tool_call("c1", "echo", {"q": "hi"}), final()],
            executor=FnExecutor(lambda td, c: ok(c.call.arguments)),
            definition_=definition(tools=[echo]),
        )[0]
    )
    projected = arun(runnable.ainvoke(task()))
    assert projected["status"] == "completed" and projected["iterations"] == 2
    via_mapping = arun(runnable.ainvoke(task("t2").model_dump(mode="json")))
    assert via_mapping["task_id"] == "t2"
    real = runnable.as_runnable()
    assert arun(real.ainvoke(task("t3").model_dump(mode="json")))["task_id"] == "t3"

    def invocation_factory(arguments):
        from nailong_agent_sdk.tools.tools import ToolInvocationContext

        return ToolInvocationContext(
            agent_identity="a",
            task=task(),
            iteration=1,
            call=ToolCall(id="c", name="echo", arguments=arguments),
        )

    executor = FnExecutor(lambda td, c: ok({"echo": c.call.arguments}))
    facade = LangChainSdkToolFacade(echo, executor, invocation_factory)
    result = arun(facade.ainvoke({"q": "x"}))
    assert (
        result["status"] == "succeeded"
        and result["output"] == {"echo": {"q": "x"}}
        and "failure" not in result
    )
    structured = facade.as_tool()
    assert arun(structured.ainvoke({"q": "y"}))["output"] == {"echo": {"q": "y"}}
    dispatched = len(executor.calls)
    invalid = arun(structured.ainvoke({"q": 5}))
    direct = arun(facade.ainvoke({"q": 5}))
    for rejected in (invalid, direct):
        assert rejected["status"] == "failed" and "failure" not in rejected
        assert 'tool "echo" arguments does not match its declared schema' in rejected["error"]
        assert "$.q" in rejected["error"] and "5 is not of type 'string'" in rejected["error"]
    assert len(executor.calls) == dispatched
    mismatch = LangChainSdkToolFacade(
        echo, executor, lambda a: invocation_factory({"q": "different"})
    )
    with pytest.raises(ValueError, match="arguments differ"):
        arun(mismatch.ainvoke({"q": "x"}))
    wrong_name = LangChainSdkToolFacade(tool("other"), executor, invocation_factory)
    with pytest.raises(ValueError, match="does not match its declared SDK tool"):
        arun(wrong_name.ainvoke({"q": "x"}))

    async def preflight(invocation):
        return ToolExecutionResult(status="blocked", error="preflight says no")

    gated = LangChainSdkToolFacade(echo, executor, invocation_factory, preflight=preflight)
    blocked = arun(gated.ainvoke({"q": "x"}))
    assert blocked["status"] == "blocked" and len(executor.calls) == dispatched


def test_langgraph_node_and_graph_with_real_langgraph(tmp_path):
    pytest.importorskip("langgraph")
    from typing import TypedDict

    from nailong_agent_sdk.agent.runtime import AgentRuntimeServices

    class State(TypedDict, total=False):
        agent_sdk_transition: dict
        seed: str

    services = AgentRuntimeServices.open(tmp_path)
    receipts = InMemoryInteropReceiptStore()

    def make_binding(turns, name="worker"):
        definition_ = definition(tools=[tool("echo")])
        return LangGraphSdkNodeBinding(
            node_name=name,
            definition=definition_,
            task_adapter=lambda env: task(env.task_id or "lg-task"),
            model_factory=lambda env: ScriptedModel(turns),
            tool_executor_factory=lambda env: FnExecutor(lambda td, c: ok({"ok": True})),
        )

    def envelope_factory(state):
        return InteropRunEnvelope(
            run_id="lg-run",
            task_id="lg-task",
            remaining_turn_budget=5,
            projection={"checkpoint_id": "cp-1", "summary": state.get("seed", "")},
        )

    try:
        node = LangGraphSdkNode(
            services,
            make_binding([tool_call("c1", "echo"), final()]),
            envelope_factory,
            receipt_sink=receipts,
        )
        graph = build_langgraph_state_graph(State, "worker", node)
        output = arun(graph.ainvoke({"seed": "go"}))
        transition = output["agent_sdk_transition"]
        assert transition["status"] == "completed" and transition["remaining_turn_budget"] == 3
        assert (
            receipts.receipts[0].status is InteropOperationStatus.SUCCEEDED
            and receipts.receipts[0].external_checkpoint_id == "cp-1"
        )
        assert "go" not in receipts.receipts[0].model_dump_json()
        blocked_node = LangGraphSdkNode(
            services,
            make_binding([{"type": "blocked", "reason": "need human"}]),
            envelope_factory,
            receipt_sink=receipts,
        )
        blocked = arun(blocked_node({"seed": "x"}))
        assert (
            blocked["agent_sdk_transition"]["status"] == "blocked"
            and receipts.receipts[-1].status is InteropOperationStatus.ESCALATED
        )
        failing = LangGraphSdkNode(
            services,
            make_binding([{"type": "final", "output": {"status": "complete"}}]),
            lambda s: (_ for _ in ()).throw(RuntimeError("bad envelope")),
            receipt_sink=receipts,
        )
        with pytest.raises(RuntimeError, match="bad envelope"):
            arun(failing({}))
        exhausted = LangGraphSdkNode(
            services,
            make_binding([]),
            lambda s: InteropRunEnvelope(run_id="r", remaining_turn_budget=0),
            receipt_sink=receipts,
        )
        with pytest.raises(RuntimeError, match="turn budget is exhausted"):
            arun(exhausted({}))
        broken = LangGraphSdkNode(
            services, make_binding([]), envelope_factory, receipt_sink=receipts
        )
        broken_result = arun(broken({"seed": "x"}))
        assert broken_result["agent_sdk_transition"]["status"] == "failed"
        assert receipts.receipts[-1].status is InteropOperationStatus.FAILED
        challenge = LangGraphApprovalChallenge(
            challenge_id="c",
            action_digest="a" * 64,
            policy_version="v1",
            expires_at_utc="2030-01-01T00:00:00Z",
            display_summary="summary",
            idempotency_key="k",
        )
        assert langgraph_interrupt_payload(challenge)["challenge_id"] == "c"
    finally:
        services.telemetry.close()


def test_langgraph_node_executor_adapts_a_compiled_graph_into_state_graph():
    from nailong_agent_sdk.state.graph import StateGraph
    from nailong_agent_sdk.state.graph_models import (
        GraphNode,
        GraphNodeKind,
        GraphNodeResult,
        GraphNodeStatus,
    )

    class Compiled:
        async def ainvoke(self, payload, config=None):
            return {"echo": payload["summary"], "cfg": dict(config or {})}

    executor = LangGraphNodeExecutor(
        Compiled(),
        lambda node, ctx: {"summary": node.node_id},
        lambda output, node, ctx: GraphNodeResult(status=GraphNodeStatus.COMPLETED, output=output),
        config_factory=lambda node, ctx: {"thread_id": node.node_id},
    )
    graph = StateGraph([GraphNode(node_id="a", kind=GraphNodeKind.FUNCTION)])
    results = arun(graph.execute({GraphNodeKind.FUNCTION: executor.as_node_executor()}))
    assert results["a"].output == {"echo": "a", "cfg": {"thread_id": "a"}}
    bad = LangGraphNodeExecutor(
        Compiled(), lambda node, ctx: {"api_key": "x"}, lambda o, n, c: None
    )
    graph = StateGraph([GraphNode(node_id="a", kind=GraphNodeKind.FUNCTION)])
    failed = arun(graph.execute({GraphNodeKind.FUNCTION: bad.as_node_executor()}))
    assert failed["a"].status is GraphNodeStatus.FAILED and "Unsafe key" in failed["a"].reason
    wrong = LangGraphNodeExecutor(
        Compiled(), lambda node, ctx: {"summary": "x"}, lambda o, n, c: "not a result"
    )
    graph = StateGraph([GraphNode(node_id="a", kind=GraphNodeKind.FUNCTION)])
    failed = arun(graph.execute({GraphNodeKind.FUNCTION: wrong.as_node_executor()}))
    assert (
        failed["a"].status is GraphNodeStatus.FAILED
        and "must return GraphNodeResult" in failed["a"].reason
    )


def test_claim_8_option_label_named_approval_does_not_make_evaluate_raise():
    spec = JevQuestionSpec(
        spec_id="labels",
        version="v1",
        questions={
            "route": JevChoiceQuestion(
                instructions="pick",
                criteria={"approval": "needs a human", "token": "plain", "message": "text"},
            )
        },
    )
    good, _ = evaluator(
        lambda n, s, q, m: {
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "approval",
                    "probabilities": {"approval": 0.6, "token": 0.2, "message": 0.2},
                    "confidence": 0.7,
                }
            }
        }
    )
    result = arun(good.evaluate(jev_request(spec)))
    assert (
        result.status is InteropOperationStatus.SUCCEEDED
        and result.answers["route"].choice == "approval"
    )


def test_claim_7_langgraph_sdk_node_with_message_and_token_output_keys_is_not_reported_failed(
    tmp_path,
):
    from typing import TypedDict

    from nailong_agent_sdk.agent.runtime import AgentRuntimeServices

    class State(TypedDict, total=False):
        agent_sdk_transition: dict

    services = AgentRuntimeServices.open(tmp_path)
    receipts = InMemoryInteropReceiptStore()
    try:
        output = {
            "status": "complete",
            "message": "hello",
            "token": "abc",
            "messages": [1, 2],
            "secret_note": "n",
        }
        binding = LangGraphSdkNodeBinding(
            node_name="worker",
            definition=definition(),
            task_adapter=lambda env: task(env.task_id or "lg-task"),
            model_factory=lambda env: ScriptedModel([{"type": "final", "output": output}]),
            tool_executor_factory=lambda env: FnExecutor(lambda td, c: ok({})),
        )
        node = LangGraphSdkNode(
            services,
            binding,
            lambda state: InteropRunEnvelope(
                run_id="lg-run", task_id="lg-task", remaining_turn_budget=5
            ),
            receipt_sink=receipts,
        )
        transition = arun(node({}))["agent_sdk_transition"]
        assert (
            transition["status"] == "completed"
            and receipts.receipts[-1].status is InteropOperationStatus.SUCCEEDED
        )
    finally:
        services.telemetry.close()
