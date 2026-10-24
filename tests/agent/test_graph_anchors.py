from dataclasses import replace

from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentExecutor
from nailong_agent_sdk.agent.run_checkpointer import RunHeadCheckpointer
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.observability.run_checkpoints import FileCheckpointSink, HmacSha256Signer
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.tools.artifact_anchor import (
    ArtifactAnchorVerdict,
    artifact_anchor_name,
    verify_artifact_anchor,
)
from nailong_agent_sdk.tools.artifacts import ArtifactStore
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


def run_graph(services, bound):
    graph = StateGraph([n("root")])
    executor = GraphAgentExecutor(services, {"root": bound})
    arun(graph.execute(executor.executors(), run_id="run-9"))
    return graph


def test_a_node_artifact_log_is_anchored_when_the_node_finishes(tmp_path):
    services = services_in(tmp_path)
    try:
        store = ArtifactStore(tmp_path / "workspace", durable=True)
        store.write_text("a.txt", "from the node\n")
        custom = FnExecutor(lambda t, c: ok({"wrote": True}))
        run_graph(
            services,
            writing_binding("root", custom, artifact_store_factory=lambda node, context: store),
        )
        result = verify_artifact_anchor(
            ArtifactStore(tmp_path / "workspace", read_only=True),
            FileCheckpointSink(tmp_path / "elsewhere"),
            artifact_anchor_name("task-root"),
            SIGNER,
        )
        assert result.verdict is ArtifactAnchorVerdict.MATCHES, result.detail
    finally:
        close(services)
