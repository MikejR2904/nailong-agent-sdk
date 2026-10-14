import pytest

from nailong_agent_sdk.agent.model import ProviderUsage
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call
from tests.support.otlp_runs import UsageTurns


@pytest.fixture
def recorded(tmp_path):
    services = AgentRuntimeServices.open(tmp_path)
    usage = ProviderUsage(input_tokens=120, output_tokens=30, request_id="req-1")
    subject = services.create_agent(
        definition(tools=[tool("echo")], max_iterations=6),
        UsageTurns([tool_call("c1", "echo", {"q": 1}), final()], usage),
        tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments})),
    )
    result = arun(subject.run(task("otlp-1")))
    yield services, result
    services.audit_logs.close()
    services.telemetry.close()
