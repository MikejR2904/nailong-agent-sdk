import asyncio
import sys

import pytest

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.agent.cost_model import PriceTable, TokenPrice
from nailong_agent_sdk.mcp.client_types import McpStdioServerConfig
from nailong_agent_sdk.starter import (
    Agent,
    CancelToken,
    ModelSetup,
    connect_mcp,
    skill,
    tool,
)
from tests.support.mcp_stdio import STDIO_SERVER


def scripted(*turns):
    return ModelSetup(ScriptedModel(list(turns)), "scripted", "scripted-1")


def final(answer="done"):
    return {"type": "final", "output": {"status": "complete", "answer": answer}}


def call(name, **arguments):
    return {"type": "tool-call", "call": {"id": f"c-{name}", "name": name, "arguments": arguments}}


def build(tmp_path, setup, *tools, **options):
    return Agent(
        model=setup,
        instructions="Answer briefly.",
        tools=tools,
        workspace=tmp_path / "workspace",
        **options,
    )


def seen_by_model(setup):
    context = setup.model.calls[-1]
    return str(list(context.observations)) + str(list(context.episodes))


@tool
def add(a: int, b: int = 1) -> int:
    """Add two whole numbers."""
    return a + b


def test_a_before_hook_can_refuse_a_call_with_a_reason(tmp_path):
    ran = []

    @tool
    def wipe(path: str) -> str:
        """Wipe a path."""
        ran.append(path)
        return "wiped"

    setup = scripted(call("wipe", path="/"), final("stopped"))
    refusals = []

    def policy(event):
        refusals.append((event.tool, event.arguments, event.iteration))
        return (
            "paths outside the workspace are off limits" if event.arguments["path"] == "/" else None
        )

    with build(tmp_path, setup, wipe, before_tool=[policy]) as agent:
        answer = agent.run_sync("Wipe it.")
    assert ran == [] and refusals == [("wipe", {"path": "/"}, 1)]
    assert "off limits" in str(answer.result.model_dump()) or "off limits" in seen_by_model(setup)


def test_after_hooks_see_each_result_and_async_hooks_work(tmp_path):
    seen = []

    async def record(event):
        seen.append((event.tool, event.result.status, event.result.output))

    setup = scripted(call("add", a=2, b=3), final("5"))
    with build(tmp_path, setup, add, after_tool=[record]) as agent:
        assert agent.run_sync("Add.").ok
    assert seen == [("add", "succeeded", 5)]


def test_a_hook_that_returns_something_else_fails_with_a_message_that_names_it(tmp_path):
    def sloppy(event):
        return 1

    setup = scripted(call("add", a=1), final())
    with build(tmp_path, setup, add, before_tool=[sloppy]) as agent:
        answer = agent.run_sync("Add.")
    assert not answer.ok or "sloppy" in seen_by_model(setup) + str(answer.result.model_dump())
    assert "sloppy" in str(answer.result.model_dump()) + seen_by_model(setup)


def test_an_output_check_accepts_or_rejects_with_its_own_reason(tmp_path):
    accepted = build(tmp_path / "a", scripted(final("42")), check=lambda out: out["answer"] == "42")
    with accepted as agent:
        assert agent.run_sync("Q").ok

    def must_be_42(output):
        return "the answer must be 42" if output["answer"] != "42" else True

    rejected = build(tmp_path / "b", scripted(final("41")), check=must_be_42)
    with rejected as agent:
        answer = agent.run_sync("Q")
    assert not answer.ok
    assert "the answer must be 42" in str(answer.result.model_dump())


def test_a_check_returning_none_is_an_error_not_a_pass(tmp_path):
    with build(tmp_path, scripted(final()), check=lambda out: None) as agent:
        answer = agent.run_sync("Q")
    assert not answer.ok
    assert "returned NoneType" in str(answer.result.model_dump())


def test_a_skill_file_is_given_to_the_model_as_task_context(tmp_path):
    path = tmp_path / "style-guide.md"
    path.write_text("Always cite the file you read.", encoding="utf-8")
    setup = scripted(final())
    with build(tmp_path, setup, skills=[skill(path)]) as agent:
        assert agent.run_sync("Q").ok
    first = setup.model.calls[0]
    assert [s.id for s in first.task.skills] == ["style-guide"]
    assert "Always cite the file you read." in str(first.prompt.model_dump())
    with pytest.raises(ValueError, match="is empty"):
        (tmp_path / "empty.md").write_text("  ", encoding="utf-8")
        skill(tmp_path / "empty.md")


def test_a_session_and_resume_carry_the_project_and_the_previous_answer(tmp_path):
    setup = scripted(final("first"), final("second"))
    with build(tmp_path, setup) as agent:
        first = agent.run_sync("Start.", session="draft-1")
        second = agent.run_sync("Continue.", resume_from=first)
    contexts = setup.model.calls
    assert contexts[0].task.scope.boundaries == {"project_id": "draft-1"}
    assert contexts[1].task.scope.boundaries == {"project_id": "draft-1"}
    assert contexts[1].task.input["previous_answer"] == {"status": "complete", "answer": "first"}
    assert first.session == second.session == "draft-1"


def test_without_a_session_each_run_is_its_own_project(tmp_path):
    with build(tmp_path, scripted(final("a"), final("b"))) as agent:
        first, second = agent.run_sync("A"), agent.run_sync("B")
    assert first.session != second.session and first.session.startswith("agent-")


def test_an_agent_can_be_a_tool_of_another_agent(tmp_path):
    researcher = build(tmp_path / "r", scripted(final("found it")), name="researcher").as_tool(
        name="research", description="Look something up and report back."
    )
    setup = scripted(call("research", task="find x"), final("the researcher said it"))
    with build(tmp_path / "p", setup, researcher) as parent:
        answer = parent.run_sync("Ask the researcher.")
    assert answer.ok and "found it" in seen_by_model(setup)


def test_a_failed_subagent_reports_why_to_the_parent(tmp_path):
    sub = build(tmp_path / "s", scripted(call("add", a=1)), max_steps=1, name="helper")
    delegate = sub.as_tool(name="helper", description="Do a helper task.")
    setup = scripted(call("helper", task="go"), final("gave up"))
    with build(tmp_path / "p", setup, delegate) as parent:
        parent.run_sync("Delegate.")
    assert 'Agent \\"helper\\" did not complete' in seen_by_model(setup) or (
        'Agent "helper" did not complete' in seen_by_model(setup)
    )


def test_a_cancelled_token_ends_the_run_as_cancelled(tmp_path):
    token = CancelToken()
    token.cancel()
    with build(tmp_path, scripted(final())) as agent:
        answer = asyncio.run(agent.run("Q", cancellation=token))
    assert answer.status == "cancelled"


def test_prices_turn_provider_usage_into_a_cost(tmp_path):
    from nailong_agent_sdk.agent.model import ModelTurnResponse, ProviderUsage
    from nailong_agent_sdk.foundations.contracts import FinalTurn

    class Priced:
        async def next_turn(self, context):
            turn = FinalTurn(output={"status": "complete", "answer": "ok"})
            return ModelTurnResponse(
                turn, usage=ProviderUsage(input_tokens=1000, output_tokens=100)
            )

    prices = PriceTable(
        {
            ("scripted", "priced"): TokenPrice(
                input_per_million="1", cached_input_per_million="0.5", output_per_million="2"
            )
        }
    )
    with build(tmp_path, ModelSetup(Priced(), "scripted", "priced"), prices=prices) as agent:
        answer = agent.run_sync("Q")
    assert answer.cost is not None and answer.cost["input_tokens"] == 1000
    with build(tmp_path / "n", scripted(final())) as agent:
        assert agent.run_sync("Q").cost is None


def test_a_tool_timeout_stops_a_slow_tool(tmp_path):
    @tool
    async def slow(seconds: float) -> str:
        """Wait."""
        await asyncio.sleep(seconds)
        return "late"

    setup = scripted(call("slow", seconds=5), final("moved on"))
    with build(tmp_path, setup, slow, tool_timeout_seconds=0.3) as agent:
        answer = asyncio.run(agent.run("Wait."))
    assert "late" not in seen_by_model(setup) + str(answer.output)
    with pytest.raises(ValueError, match="must be positive"):
        build(tmp_path / "bad", scripted(final()), deadline_seconds=0)


def test_services_and_evidence_cannot_both_be_given(tmp_path):
    from nailong_agent_sdk.agent.runtime import AgentRuntimeServices

    services = AgentRuntimeServices.open(tmp_path / "s")
    try:
        with pytest.raises(ValueError, match="either services or evidence"):
            build(tmp_path, scripted(final()), services=services, evidence=object())
    finally:
        services.audit_logs.close()
        services.telemetry.close()


def test_a_stream_ends_with_the_answer_even_when_the_model_does_not_stream(tmp_path):
    async def collect(agent):
        return [item async for item in agent.stream("Q")]

    with build(tmp_path, scripted(final("hi"))) as agent:
        items = asyncio.run(collect(agent))
    assert items[-1].text == "hi" and len(items) >= 1


@pytest.fixture
def stdio_config(tmp_path):
    script = tmp_path / "stdio_server.py"
    script.write_text(STDIO_SERVER, "utf-8")
    return McpStdioServerConfig(name="demo", command=sys.executable, args=[str(script)])


def test_mcp_tools_are_declared_approved_and_called_like_any_other(tmp_path, stdio_config):
    async def scenario():
        mcp = await connect_mcp([stdio_config], needs_approval=True)
        names = [definition.name for definition in mcp.definitions]
        assert "mcp__demo__echo" in names
        blocked_setup = scripted(call("mcp__demo__echo", text="hi"), final("blocked"))
        with build(tmp_path / "a", blocked_setup, mcp) as agent:
            blocked = await agent.run("Echo.")
        assert "needs approval" in str(blocked.result.model_dump()) + seen_by_model(blocked_setup)

        setup = scripted(call("mcp__demo__echo", text="hi"), final("echoed"))
        with build(tmp_path / "b", setup, mcp, approve=lambda request: True) as agent:
            answer = await agent.run("Echo.")
            assert answer.ok
        assert "echo:hi" in seen_by_model(setup)
        assert "untrusted" in seen_by_model(setup)
        await mcp.aclose()

    asyncio.run(asyncio.wait_for(scenario(), 120))


def test_run_sync_refuses_mcp_tools_and_says_what_to_do(tmp_path, stdio_config):
    async def scenario():
        mcp = await connect_mcp([stdio_config], needs_approval=False)
        try:
            agent = build(tmp_path, scripted(final()), mcp)
            with pytest.raises(RuntimeError, match="cannot be used with MCP tools"):
                agent.run_sync("Q")
            agent.close()
        finally:
            await mcp.aclose()

    asyncio.run(asyncio.wait_for(scenario(), 120))


def test_an_unreachable_mcp_server_fails_to_connect_and_names_it(tmp_path):
    broken = McpStdioServerConfig(
        name="ghost", command=sys.executable, args=["-c", "raise SystemExit(3)"]
    )
    with pytest.raises(ConnectionError, match='"ghost"'):
        asyncio.run(asyncio.wait_for(connect_mcp([broken]), 120))


def test_recoverable_refusals_tell_the_model_and_let_the_run_continue(tmp_path):
    ran = []

    @tool
    def wipe(path: str) -> str:
        """Wipe a path."""
        ran.append(path)
        return "wiped"

    def policy(event):
        return "not allowed here" if event.tool == "wipe" else None

    for recoverable, expected_ok in ((False, False), (True, True)):
        setup = scripted(call("wipe", path="x"), final("adapted"))
        with build(
            tmp_path / str(recoverable),
            setup,
            wipe,
            before_tool=[policy],
            recoverable_refusals=recoverable,
        ) as agent:
            answer = agent.run_sync("Wipe it.")
        assert answer.ok is expected_ok and ran == []
    assert "not allowed here" in seen_by_model(setup)


def test_a_declined_approval_does_not_end_a_recoverable_run(tmp_path):
    @tool(needs_approval=True)
    def send(text: str) -> str:
        """Send."""
        return "sent"

    setup = scripted(call("send", text="hi"), final("understood"))
    with build(
        tmp_path, setup, send, approve=lambda request: False, recoverable_refusals=True
    ) as agent:
        answer = agent.run_sync("Send.")
    assert answer.ok and "declined by the approve= callback" in seen_by_model(setup)
