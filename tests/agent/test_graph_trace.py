from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentExecutor
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.integrations.contracts import InteropRunEnvelope
from nailong_agent_sdk.integrations.langgraph import LangGraphSdkNode, LangGraphSdkNodeBinding
from nailong_agent_sdk.integrations.receipts import InMemoryInteropReceiptStore
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.observability.trace_context import (
    graph_run_span_id,
    graph_run_trace_id,
    is_span_id,
    is_trace_id,
)
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphNodeKind
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from tests.support.agents import arun, definition, final, task
from tests.support.controllers import executing_runtime
from tests.support.elastic_agents import binding, close, services_for
from tests.support.orchestration import (
    close_all,
    make_factory,
    make_policy,
    make_request,
    pipeline,
)
from tests.support.plans import n, run_ok, simple_plan

AGENT = GraphNodeKind.AGENT


def profile_of(telemetry, run_id):
    (completed,) = [
        event
        for event in telemetry.list_events(run_id, limit=500)
        if event.event_type == "agent.profile-completed"
    ]
    return completed


def root_span(completed):
    (root,) = [span for span in completed.payload["spans"] if span["kind"] == "run"]
    return root


def test_agent_nodes_of_a_graph_run_share_the_trace_derived_from_the_run_id(tmp_path):
    services = services_for(tmp_path)
    try:
        executor = GraphAgentExecutor(
            services, {"root": binding("root", tool=False), "after": binding("after", tool=False)}
        )
        graph = StateGraph([n("root"), n("after", ["root"])])
        arun(graph.execute(executor.executors(), run_id="run-7"))
        trace, parent = graph_run_trace_id("run-7"), graph_run_span_id("run-7")
        roots = []
        for node_id in ("root", "after"):
            events = services.telemetry.list_events(f"task-{node_id}", limit=500)
            assert len(events) > 5
            for event in events:
                assert event.context.trace_id == trace
                assert event.context.parent_span_id == parent
                assert event.context.node_id == node_id
            completed = profile_of(services.telemetry, f"task-{node_id}")
            assert completed.payload["trace_id"] == trace
            assert root_span(completed)["parent_span_id"] == parent
            roots.append(root_span(completed)["span_id"])
        assert len(set(roots)) == 2 and all(is_span_id(span) for span in roots)
    finally:
        close(services)


def test_without_a_run_id_every_node_keeps_its_own_trace(tmp_path):
    services = services_for(tmp_path)
    try:
        executor = GraphAgentExecutor(
            services, {"root": binding("root", tool=False), "after": binding("after", tool=False)}
        )
        graph = StateGraph([n("root"), n("after", ["root"])])
        arun(graph.execute(executor.executors()))
        traces = []
        for node_id in ("root", "after"):
            completed = profile_of(services.telemetry, f"task-{node_id}")
            traces.append(completed.payload["trace_id"])
            assert completed.context.parent_span_id is None and completed.context.node_id is None
            assert root_span(completed)["parent_span_id"] is None
        assert all(is_trace_id(trace) for trace in traces) and traces[0] != traces[1]
    finally:
        close(services)


def test_the_run_id_reaches_a_node_executor_through_its_execution_context():
    seen = {}

    async def record(node, context):
        seen[node.node_id] = context.run_id
        return await run_ok(node, context)

    graph = StateGraph([n("a"), n("b", ["a"])])
    arun(graph.execute({AGENT: record}, run_id="run-9"))
    assert seen == {"a": "run-9", "b": "run-9"}
    plain = StateGraph([n("a")])
    arun(plain.execute({AGENT: record}))
    assert seen["a"] is None


def test_a_coordinated_run_gives_every_node_the_trace_of_its_run(tmp_path):
    services = services_for(tmp_path / "services")
    coordinator = HarnessCoordinator(tmp_path / "run")
    try:
        record = coordinator.start_run(simple_plan())
        executor = GraphAgentExecutor(
            services,
            {f"node:{name}": binding(f"node:{name}", tool=False) for name in ("T1", "T2", "T3")},
        )
        arun(coordinator.execute_run(record.run_id, executor.executors()))
        trace = graph_run_trace_id(record.run_id)
        for name in ("T1", "T2", "T3"):
            completed = profile_of(services.telemetry, f"task-node:{name}")
            assert completed.payload["trace_id"] == trace
            assert completed.context.node_id == f"node:{name}"
    finally:
        close(services)


def test_controller_events_of_a_run_carry_its_trace_and_graph_span(tmp_path):
    telemetry = TelemetryStore(tmp_path / "ledger")
    try:
        runtime, controller_id = executing_runtime(tmp_path / "state", telemetry)
        run_id = runtime.get_run(controller_id).run_id
        arun(runtime.execute_graph(controller_id, {AGENT: run_ok}))
        events = telemetry.list_events(run_id, limit=200)
        executed = [event for event in events if event.event_type == "graph.executed"]
        assert executed
        for event in events:
            if event.context.trace_id is None:
                continue
            assert event.context.trace_id == graph_run_trace_id(run_id)
            assert event.context.span_id == graph_run_span_id(run_id)
        assert all(event.context.trace_id == graph_run_trace_id(run_id) for event in executed)
        assert all(event.context.controller_id == controller_id for event in executed)
    finally:
        telemetry.close()


def test_orchestration_events_after_dispatch_join_the_trace_of_the_graph_run(tmp_path):
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), make_factory()
    )
    try:
        trace = graph_run_trace_id(executed.graph_run_id)
        by_type = {
            event.event_type: event
            for event in services.telemetry.list_events(record.orchestration_id, limit=100)
        }
        assert by_type["orchestration.prepared"].context.trace_id is None
        assert by_type["orchestration.dispatched"].context.trace_id == trace
        assert by_type["orchestration.executed"].context.trace_id == trace
        node_runs = services.telemetry.run_ids_for_trace(trace)
        assert executed.graph_run_id in node_runs and record.orchestration_id in node_runs
        assert {"task-node:T1", "task-node:T2", "task-node:T3"} <= set(node_runs)
    finally:
        close_all(orchestrator, services)


def test_a_langgraph_node_joins_the_trace_of_the_envelope_run(tmp_path):
    services = AgentRuntimeServices.open(tmp_path)
    receipts = InMemoryInteropReceiptStore()
    try:
        node = LangGraphSdkNode(
            services,
            LangGraphSdkNodeBinding(
                node_name="worker",
                definition=definition(),
                task_adapter=lambda envelope: task("lg-task"),
                model_factory=lambda envelope: ScriptedModel([final()]),
            ),
            lambda state: InteropRunEnvelope(
                run_id="lg-run", graph_node_id="graph-node-3", remaining_turn_budget=5
            ),
            receipt_sink=receipts,
        )
        arun(node({}))
        completed = profile_of(services.telemetry, "lg-task")
        assert completed.payload["trace_id"] == graph_run_trace_id("lg-run")
        assert completed.context.node_id == "graph-node-3"
        assert completed.context.parent_span_id is None
        assert root_span(completed)["parent_span_id"] is None
    finally:
        services.audit_logs.close()
        services.telemetry.close()
