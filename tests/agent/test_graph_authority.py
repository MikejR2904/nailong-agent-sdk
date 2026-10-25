from dataclasses import replace

from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentExecutor
from nailong_agent_sdk.agent.run_checkpointer import RunHeadCheckpointer
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import ToolExecutionResult
from nailong_agent_sdk.observability.run_checkpoints import FileCheckpointSink, HmacSha256Signer
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphNodeStatus
from nailong_agent_sdk.tools.tools import ToolAuthorization
from tests.support.agents import FnExecutor, arun, call, definition, final, ok, tool
from tests.support.elastic_agents import binding
from tests.support.plans import n

SIGNER = HmacSha256Signer(b"graph-key-0123456789abcdef-0123!", signer_id="graph")
WRITE = tool("write_thing")


def services_in(tmp_path):
    return AgentRuntimeServices.open(
        tmp_path / "run",
        checkpointer=RunHeadCheckpointer(SIGNER, FileCheckpointSink(tmp_path / "elsewhere")),
    )


def close(services):
    services.audit_logs.close()
    services.telemetry.close()


def writing_binding(node_id, executor, **changes):
    base = binding(
        node_id, [{"type": "tool-call", "call": call("c1", "write_thing")}, final()], tool=False
    )
    return replace(
        base,
        definition=definition(tools=[WRITE]),
        tool_executor_factory=lambda node, context: executor,
        **changes,
    )


class Refusing:
    def authorize(self, tool_definition, context):
        return ToolAuthorization(
            allowed=False,
            tool=tool_definition.name,
            call_id=context.call.id,
            issuer=self,
            result=ToolExecutionResult(status="blocked", error="refused at the shared boundary"),
        )


def run_graph(services, bound):
    graph = StateGraph([n("root")])
    executor = GraphAgentExecutor(services, {"root": bound})
    arun(graph.execute(executor.executors(), run_id="run-9"))
    return graph


def test_a_node_with_an_authorizer_never_reaches_its_custom_executor(tmp_path):
    services = services_in(tmp_path)
    try:
        custom = FnExecutor(lambda t, c: ok({"wrote": True}))
        graph = run_graph(
            services,
            writing_binding("root", custom, tool_authorizer_factory=lambda n_, c: Refusing()),
        )
        assert custom.calls == []
        assert graph.status("root") is not GraphNodeStatus.COMPLETED
    finally:
        close(services)


def test_a_node_that_must_be_authorized_fails_when_nothing_authorizes_it(tmp_path):
    services = services_in(tmp_path)
    try:
        custom = FnExecutor(lambda t, c: ok({"wrote": True}))
        graph = run_graph(
            services, writing_binding("root", custom, require_tool_authorization=True)
        )
        assert custom.calls == []
        assert graph.status("root") is GraphNodeStatus.FAILED
        assert "TOOL_AUTHORIZER_MISSING" in " ".join(graph.result("root").diagnostics)
    finally:
        close(services)


def test_a_node_without_an_authorizer_fails_closed_unless_it_opts_out(tmp_path):
    services = services_in(tmp_path)
    try:
        custom = FnExecutor(lambda t, c: ok({"wrote": True}))
        graph = run_graph(services, writing_binding("root", custom))
        assert custom.calls == []
        assert graph.status("root") is GraphNodeStatus.FAILED
    finally:
        close(services)


def test_a_node_that_opts_out_runs_its_executor_unprotected(tmp_path):
    services = services_in(tmp_path)
    try:
        custom = FnExecutor(lambda t, c: ok({"wrote": True}))
        graph = run_graph(
            services, writing_binding("root", custom, require_tool_authorization=False)
        )
        assert [c.call.id for c in custom.calls] == ["c1"]
        assert graph.status("root") is GraphNodeStatus.COMPLETED
        assert custom.calls[0].authorization is None
    finally:
        close(services)
