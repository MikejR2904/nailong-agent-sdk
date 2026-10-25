import pytest

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.foundations.contracts import AgentRunStatus, ToolExecutionResult
from nailong_agent_sdk.tools.tools import ToolAuthorization
from tests.support.agents import (
    FnExecutor,
    arun,
    definition,
    final,
    ok,
    task,
    tool,
    tool_call,
)

WRITE = tool("write_thing")


class Authorizer:
    def __init__(self, allow):
        self.allow = allow
        self.seen = []

    def authorize(self, tool_definition, context):
        self.seen.append((tool_definition.name, context.call.id))
        if self.allow:
            return ToolAuthorization(
                allowed=True,
                tool=tool_definition.name,
                call_id=context.call.id,
                issuer=self,
            )
        return ToolAuthorization(
            allowed=False,
            tool=tool_definition.name,
            call_id=context.call.id,
            issuer=self,
            result=ToolExecutionResult(status="blocked", error="not authorized here"),
        )


def run_agent(authorizer=None, **options):
    executor = FnExecutor(lambda t, c: ok({"wrote": True}))
    subject = BaseAgent(
        definition(tools=[WRITE]),
        ScriptedModel([tool_call("c1", "write_thing"), final()]),
        tool_executor=executor,
        tool_authorizer=authorizer,
        **options,
    )
    return arun(subject.run(task())), executor


def test_without_an_authorizer_a_custom_executor_is_refused_by_default():
    result, executor = run_agent()
    assert executor.calls == []
    assert result.status is AgentRunStatus.FAILED
    assert "TOOL_AUTHORIZER_MISSING" in {f.code for f in [result.failure] if f}


def test_a_host_can_opt_out_and_a_custom_executor_alone_decides_what_runs():
    result, executor = run_agent(require_tool_authorization=False)
    assert result.status is AgentRunStatus.COMPLETED
    assert [call.call.id for call in executor.calls] == ["c1"]


def test_a_denying_authorizer_stops_a_custom_executor_before_it_is_called():
    authorizer = Authorizer(allow=False)
    result, executor = run_agent(authorizer)
    assert executor.calls == []
    assert authorizer.seen == [("write_thing", "c1")]
    assert result.status is AgentRunStatus.BLOCKED
    assert "not authorized here" in (result.reason or "")


def test_an_allowing_authorizer_stamps_the_invocation_the_executor_receives():
    authorizer = Authorizer(allow=True)
    result, executor = run_agent(authorizer)
    assert result.status is AgentRunStatus.COMPLETED
    (seen,) = executor.calls
    assert seen.authorization is not None and seen.authorization.issuer is authorizer
    assert seen.authorization.call_id == "c1" and seen.authorization.allowed


def test_requiring_authorization_without_an_authorizer_fails_the_run_naming_the_tool():
    result, executor = run_agent(require_tool_authorization=True)
    assert executor.calls == []
    assert result.status is AgentRunStatus.FAILED
    assert result.failure.code == "TOOL_AUTHORIZER_MISSING"
    assert "write_thing" in result.failure.message


def test_an_authorizer_that_raises_denies_the_call_instead_of_allowing_it():
    class Broken:
        def authorize(self, tool_definition, context):
            raise RuntimeError("policy store unreachable")

    result, executor = run_agent(Broken())
    assert executor.calls == []
    assert result.status is AgentRunStatus.BLOCKED
    assert "policy store unreachable" in (result.reason or "")


@pytest.mark.parametrize("allow", [True, False])
def test_the_authorizer_runs_after_the_pre_tool_hooks(allow):
    order = []

    async def hook(context):
        order.append("hook")

    class Ordered(Authorizer):
        def authorize(self, tool_definition, context):
            order.append("authorizer")
            return super().authorize(tool_definition, context)

    run_agent(Ordered(allow), pre_tool_hooks=[hook])
    assert order == ["hook", "authorizer"]
