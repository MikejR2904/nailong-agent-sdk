import dataclasses

from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentExecutor
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphNode, GraphNodeKind, GraphNodeStatus
from tests.support.agents import arun, final, tool_call
from tests.support.elastic_agents import binding, close, services_for
from tests.support.plans import n

FAILED = GraphNodeStatus.FAILED


def run_graph(tmp_path, bindings, nodes):
    services = services_for(tmp_path)
    try:
        executor = GraphAgentExecutor(services, bindings)
        graph = StateGraph(nodes)
        arun(graph.execute(executor.executors()))
        return graph
    finally:
        close(services)


def test_a_completed_node_names_the_agent_run_that_produced_it_and_no_failure(tmp_path):
    graph = run_graph(tmp_path, {"root": binding("root", [final()], tool=False)}, [n("root")])
    result = graph.result("root")
    assert result.status is GraphNodeStatus.COMPLETED
    assert result.agent_run_id == "task-root" and result.failure_code is None
    assert "agent-status:completed" in result.diagnostics


def test_a_failed_node_carries_the_typed_code_of_the_agent_failure(tmp_path):
    graph = run_graph(
        tmp_path,
        {"root": binding("root", [tool_call("c1", "ghost")], tool=False)},
        [n("root"), n("after", ["root"])],
    )
    result = graph.result("root")
    assert result.status is FAILED
    assert result.failure_code == "TOOL_NOT_DECLARED"
    assert result.agent_run_id == "task-root"
    assert result.reason == 'Tool "ghost" is not declared for this agent.'
    assert graph.result("after").root_causes == ("root",)


def test_a_node_without_a_binding_is_a_typed_failure_naming_the_node(tmp_path):
    graph = run_graph(tmp_path, {}, [n("orphan")])
    result = graph.result("orphan")
    assert result.status is FAILED and result.failure_code == "GRAPH_BINDING_MISSING"
    assert 'node "orphan"' in result.reason


def resolve(tmp_path, executor_kwargs, node_id="e"):
    services = services_for(tmp_path)
    try:
        executor = GraphAgentExecutor(services, {}, **executor_kwargs)
        node = GraphNode.model_construct(node_id=node_id, kind=GraphNodeKind.ELASTIC)
        return executor._resolve_missing_binding(node, None)
    finally:
        close(services)


def test_an_elastic_node_without_a_binding_factory_is_a_typed_failure(tmp_path):
    result = resolve(tmp_path, {})
    assert result.failure_code == "GRAPH_ELASTIC_BINDING_FACTORY_MISSING"
    assert "elastic_binding_factory" in result.reason


def test_an_elastic_binding_factory_that_raises_is_a_typed_failure_with_the_error_type(tmp_path):
    def factory(node, context):
        raise KeyError("scope")

    result = resolve(tmp_path, {"elastic_binding_factory": factory})
    assert result.failure_code == "GRAPH_ELASTIC_BINDING_FACTORY_FAILED"
    assert "error-type:KeyError" in result.diagnostics


def test_a_factory_that_returns_another_nodes_binding_is_a_typed_failure(tmp_path):
    other = dataclasses.replace(binding("someone-else", tool=False))
    result = resolve(tmp_path, {"elastic_binding_factory": lambda node, context: other})
    assert result.failure_code == "GRAPH_BINDING_MISMATCH"
    assert '"someone-else" instead of "e"' in result.reason
