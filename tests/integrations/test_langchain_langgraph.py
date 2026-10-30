import pytest
from pydantic import ValidationError

from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
from nailong_agent_sdk.integrations.contracts import InteropOperationStatus, InteropRunEnvelope
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
from nailong_agent_sdk.integrations.receipts import InMemoryInteropReceiptStore
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
    assert set(projected) == {
        "status",
        "task_id",
        "iterations",
        "output",
        "reason",
        "failure",
        "escalation",
    }
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
            require_tool_authorization=False,
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
            require_tool_authorization=False,
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


def test_the_langchain_runnable_keeps_the_prompt_and_trace_out_unless_asked():
    def build(include_trace):
        return LangChainSdkRunnable(
            lambda t: agent([final({"status": "complete", "answer": "x"})])[0],
            include_trace=include_trace,
        )

    outcome = arun(build(False).ainvoke(task("t-outcome")))
    assert outcome["output"] == {"status": "complete", "answer": "x"}
    for private in (
        "context",
        "project_state",
        "episodes",
        "projection_history",
        "events",
        "profile",
    ):
        assert private not in outcome
    traced = arun(build(True).ainvoke(task("t-trace")))
    assert traced["events"] and traced["context"] is not None and "profile" in traced
