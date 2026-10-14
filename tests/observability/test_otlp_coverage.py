import importlib
from datetime import UTC, datetime, timedelta

import pytest

from nailong_agent_sdk.agent.model import ProviderUsage
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.observability.otlp import (
    build_run_payloads,
    exported_span_ids,
    preview_export,
)
from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
    TelemetryEvent,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.observability.trace_context import graph_run_span_id, graph_run_trace_id
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call
from tests.support.orchestration import close_all, make_factory, make_policy, make_request, pipeline
from tests.support.otlp_runs import UsageTurns, attributes_of, spans_of

START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
ACTOR = TelemetryActor(kind="system", identifier="controller-runtime", role="controller")


def controller_event(store, run_id, event_type, offset_seconds, *, status="ok"):
    store.append(
        TelemetryEvent(
            event_type=event_type,
            occurred_at_utc=(START + timedelta(seconds=offset_seconds)).isoformat(),
            occurred_at_monotonic_ns=0,
            context=TelemetryContext(
                run_id=run_id,
                controller_id="controller-1",
                trace_id=graph_run_trace_id(run_id),
                span_id=graph_run_span_id(run_id),
            ),
            actor=ACTOR,
            authority=TelemetryAuthority.DETERMINISTIC,
            status=status,
        )
    )


def workflow_of(store, run_id):
    (span,) = spans_of(build_run_payloads(store, run_id, signals=["traces"]).traces)
    return span, attributes_of(span)


@pytest.fixture
def store(tmp_path):
    opened = TelemetryStore(tmp_path)
    yield opened
    opened.close()


def seconds_after_start(span, key):
    nanos = int(span[key])
    return (nanos - int(START.timestamp() * 1_000_000_000)) / 1_000_000_000


def test_the_workflow_span_ends_with_the_last_execution_pass_not_later_bookkeeping(store):
    controller_event(store, "run-w", "controller.dispatched", 0)
    controller_event(store, "run-w", "graph.node-result", 2)
    controller_event(store, "run-w", "graph.executed", 5, status="completed")
    controller_event(store, "run-w", "provenance.checked", 7)
    controller_event(store, "run-w", "controller.completed", 3_600)
    span, attributes = workflow_of(store, "run-w")
    assert seconds_after_start(span, "startTimeUnixNano") == 0
    assert seconds_after_start(span, "endTimeUnixNano") == 5
    assert attributes["nailong.window.basis"] == {"stringValue": "dispatch-to-last-execution-pass"}
    assert attributes["nailong.graph.execution_passes"] == {"intValue": "1"}
    assert attributes["nailong.graph.controller_events"] == {"intValue": "5"}
    assert span["status"] == {"code": 1}


def test_a_workflow_that_never_finished_an_execution_pass_says_its_window_is_only_events(store):
    controller_event(store, "run-open", "controller.dispatched", 0)
    controller_event(store, "run-open", "graph.node-result", 4)
    span, attributes = workflow_of(store, "run-open")
    assert seconds_after_start(span, "endTimeUnixNano") == 4
    assert attributes["nailong.window.basis"] == {"stringValue": "controller-event-window"}
    assert attributes["nailong.graph.execution_passes"] == {"intValue": "0"}
    assert span["status"] == {"code": 0}


def test_a_resumed_workflow_reports_every_pass_and_ends_with_the_last_one(store):
    controller_event(store, "run-resumed", "controller.dispatched", 0)
    controller_event(store, "run-resumed", "graph.executed", 3, status="blocked")
    controller_event(store, "run-resumed", "graph.elastic-capacity-granted", 600)
    controller_event(store, "run-resumed", "graph.executed", 610, status="completed")
    span, attributes = workflow_of(store, "run-resumed")
    assert seconds_after_start(span, "endTimeUnixNano") == 610
    assert attributes["nailong.graph.execution_passes"] == {"intValue": "2"}
    assert span["status"] == {"code": 1}


def test_a_parent_that_is_not_in_the_export_is_marked_and_counted(tmp_path):
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), make_factory()
    )
    try:
        run_id = executed.graph_run_id
        nodes = [f"task-node:{name}" for name in ("T1", "T2", "T3")]
        report = preview_export(services.telemetry, nodes, signals=["traces"])
        assert report.parents_not_exported == 3
        for node in nodes:
            built = build_run_payloads(
                services.telemetry,
                node,
                signals=["traces"],
                exported_spans=exported_span_ids(services.telemetry, nodes),
            )
            root = next(s for s in spans_of(built.traces) if s["name"].startswith("invoke_agent"))
            assert root["parentSpanId"] == graph_run_span_id(run_id)
            assert attributes_of(root)["nailong.parent_span_exported"] == {"boolValue": False}
            assert built.parents_not_exported == 1
        whole = services.telemetry.run_ids_for_trace(graph_run_trace_id(run_id))
        assert run_id in whole
        complete = preview_export(services.telemetry, whole, signals=["traces"])
        assert complete.parents_not_exported == 0
        assert complete.spans > report.spans
    finally:
        close_all(orchestrator, services)


def test_a_span_with_an_exported_parent_carries_no_marker(tmp_path):
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), make_factory()
    )
    try:
        whole = services.telemetry.run_ids_for_trace(graph_run_trace_id(executed.graph_run_id))
        exported = exported_span_ids(services.telemetry, whole)
        for run in whole:
            built = build_run_payloads(
                services.telemetry, run, signals=["traces"], exported_spans=exported
            )
            for span in spans_of(built.traces) if built.traces else []:
                assert "nailong.parent_span_exported" not in attributes_of(span)
    finally:
        close_all(orchestrator, services)


def test_kept_spans_never_point_at_an_omitted_parent(tmp_path, monkeypatch):
    module = importlib.import_module("nailong_agent_sdk.agent.base_agent.agent")
    monkeypatch.setattr(module, "_PROFILE_EVENT_SPAN_LIMIT", 2)
    services = AgentRuntimeServices.open(tmp_path)
    try:
        subject = services.create_agent(
            definition(tools=[tool("echo")], max_iterations=6),
            UsageTurns(
                [tool_call("c1", "echo"), tool_call("c2", "echo"), final()], ProviderUsage()
            ),
            tool_executor=FnExecutor(lambda t, c: ok({})),
        )
        arun(subject.run(task("pruned-profile")))
        built = build_run_payloads(services.telemetry, "pruned-profile", signals=["traces"])
        spans = spans_of(built.traces)
        assert built.spans_omitted > 0 and len(spans) == 2
        ids = {span["spanId"] for span in spans}
        roots = [s for s in spans if s["name"].startswith("invoke_agent")]
        assert len(roots) == 1 and "parentSpanId" not in roots[0]
        for span in spans:
            assert span.get("parentSpanId") is None or span["parentSpanId"] in ids
        assert built.parents_not_exported == 0
    finally:
        services.audit_logs.close()
        services.telemetry.close()
