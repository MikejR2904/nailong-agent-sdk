import json
import math

from nailong_agent_sdk.agent.model import ProviderUsage
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.observability import otlp
from nailong_agent_sdk.observability.otlp import (
    build_run_payloads,
    dependency_links,
    node_root_spans,
    preview_export,
)
from nailong_agent_sdk.observability.telemetry_models import (
    MetricAvailability,
    MetricDefinition,
    MetricObservation,
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.observability.trace_context import (
    graph_run_span_id,
    graph_run_trace_id,
    is_span_id,
    is_trace_id,
)
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call
from tests.support.controllers import executing_runtime
from tests.support.orchestration import close_all, make_factory, make_policy, make_request, pipeline
from tests.support.otlp_runs import UsageTurns, attributes_of, spans_of


def test_unix_nano_is_exact_and_treats_a_naive_time_as_utc():
    assert otlp._unix_nano("1970-01-01T00:00:01.000002+00:00") == 1_000_002_000
    assert otlp._unix_nano("1970-01-01T00:00:01.000002") == 1_000_002_000
    assert otlp._unix_nano("1970-01-01T02:00:00+02:00") == 0
    assert otlp._unix_nano("2026-10-13T00:00:00.123456+00:00") == 1_791_849_600_123_456_000


def test_attribute_values_use_the_otlp_json_typed_forms():
    assert otlp._any_value(True) == {"boolValue": True}
    assert otlp._any_value(7) == {"intValue": "7"}
    assert otlp._any_value(0.5) == {"doubleValue": 0.5}
    assert otlp._any_value("x") == {"stringValue": "x"}
    assert otlp._any_value(math.nan) == {"stringValue": "nan"}
    assert otlp._any_value(None) is None
    assert otlp._any_value([1, "a", None]) == {
        "arrayValue": {"values": [{"intValue": "1"}, {"stringValue": "a"}]}
    }
    assert otlp._any_value({"k": 2}) == {
        "kvlistValue": {"values": [{"key": "k", "value": {"intValue": "2"}}]}
    }
    assert otlp._key_values({"a": None, "b": False}) == [
        {"key": "b", "value": {"boolValue": False}}
    ]


def test_a_run_becomes_spans_with_the_standard_names_kinds_and_parents(recorded):
    services, result = recorded
    built = build_run_payloads(services.telemetry, "otlp-1")
    spans = spans_of(built.traces)
    profile = result.profile
    assert built.spans == len(spans) == len(profile["spans"]) and built.spans_omitted == 0
    by_name = {span["name"]: span for span in spans}
    assert set(by_name) == {
        "invoke_agent audit-agent",
        "chat fake-1",
        "execute_tool echo",
        "context-projection",
    }
    root = by_name["invoke_agent audit-agent"]
    assert root["traceId"] == profile["trace_id"] and is_trace_id(root["traceId"])
    assert "parentSpanId" not in root and root["kind"] == 1 and root["status"] == {"code": 1}
    for span in spans:
        assert is_span_id(span["spanId"])
        assert int(span["endTimeUnixNano"]) >= int(span["startTimeUnixNano"])
        if span is not root:
            assert span["parentSpanId"] == root["spanId"]
    assert by_name["chat fake-1"]["kind"] == 3
    assert by_name["execute_tool echo"]["kind"] == 1
    model_attributes = attributes_of(by_name["chat fake-1"])
    assert model_attributes["gen_ai.usage.input_tokens"] == {"intValue": "120"}
    assert model_attributes["gen_ai.request.model"] == {"stringValue": "fake-1"}
    root_attributes = attributes_of(root)
    assert root_attributes["nailong.run_id"] == {"stringValue": "otlp-1"}
    assert root_attributes["nailong.span.status"] == {"stringValue": "completed"}
    assert root_attributes["nailong.profile.integrity_hash"] == {
        "stringValue": profile["integrity_hash"]
    }


def test_resource_and_scope_come_from_the_recording_process(recorded):
    services, _ = recorded
    built = build_run_payloads(services.telemetry, "otlp-1")
    (resource_spans,) = built.traces["resourceSpans"]
    resource = {
        item["key"]: item["value"]["stringValue"]
        for item in resource_spans["resource"]["attributes"]
    }
    assert resource["service.name"] and resource["telemetry.sdk.language"] == "python"
    assert resource["service.instance.id"]
    (scope_spans,) = resource_spans["scopeSpans"]
    assert scope_spans["scope"]["name"] == "nailong-agent-sdk" and scope_spans["scope"]["version"]


def test_failed_and_blocked_runs_map_to_error_and_unset_statuses(tmp_path):
    services = AgentRuntimeServices.open(tmp_path)
    try:
        failing = services.create_agent(
            definition(),
            UsageTurns([tool_call("c1", "ghost")], ProviderUsage()),
        )
        arun(failing.run(task("failed-run")))
        blocked = services.create_agent(
            definition(),
            UsageTurns([{"type": "blocked", "reason": "need a human"}], ProviderUsage()),
        )
        arun(blocked.run(task("blocked-run")))
        failed_root = next(
            s
            for s in spans_of(build_run_payloads(services.telemetry, "failed-run").traces)
            if s["name"].startswith("invoke_agent")
        )
        assert failed_root["status"] == {"code": 2, "message": "failed"}
        assert attributes_of(failed_root)["error.type"] == {"stringValue": "failed"}
        blocked_root = next(
            s
            for s in spans_of(build_run_payloads(services.telemetry, "blocked-run").traces)
            if s["name"].startswith("invoke_agent")
        )
        assert blocked_root["status"] == {"code": 0}
        assert attributes_of(blocked_root)["nailong.span.status"] == {"stringValue": "blocked"}
    finally:
        services.audit_logs.close()
        services.telemetry.close()


def test_every_event_becomes_a_log_record_correlated_to_its_span(recorded):
    services, result = recorded
    built = build_run_payloads(services.telemetry, "otlp-1")
    (resource_logs,) = built.logs["resourceLogs"]
    records = resource_logs["scopeLogs"][0]["logRecords"]
    events = services.telemetry.list_events("otlp-1", limit=500)
    assert built.log_records == len(records) == len(events)
    root_span_id = next(s for s in result.profile["spans"] if s["kind"] == "run")["span_id"]
    for event, record in zip(events, records, strict=True):
        assert record["body"] == {"stringValue": event.event_type}
        assert record["eventName"] == event.event_type
        assert record["traceId"] == result.profile["trace_id"]
        assert record["spanId"] == root_span_id
        assert record["severityNumber"] in {9, 17}
        assert attributes_of(record)["nailong.event.sequence"] == {"intValue": str(event.sequence)}
    completed = next(r for r in records if r["eventName"] == "agent.profile-completed")
    payload = json.loads(attributes_of(completed)["nailong.payload"]["stringValue"])
    assert "spans" not in payload and "resource" not in payload
    assert payload["trace_id"] == result.profile["trace_id"]


def test_a_long_event_payload_is_cut_and_marked(tmp_path):
    store = TelemetryStore(tmp_path)
    try:
        store.emit(
            "demo.big",
            TelemetryContext(run_id="long"),
            actor=TelemetryActor(kind="test", identifier="t"),
            authority=TelemetryAuthority.SYSTEM,
            status="ok",
            payload={"text": "x" * 20_000},
        )
        built = build_run_payloads(store, "long", signals=["logs"])
        (record,) = built.logs["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        attributes = attributes_of(record)
        assert attributes["nailong.payload.truncated"] == {"boolValue": True}
        assert len(attributes["nailong.payload"]["stringValue"]) == 8_192
    finally:
        store.close()


def test_metrics_follow_the_declared_instrument_and_skip_unavailable_values(recorded):
    services, _ = recorded
    built = build_run_payloads(services.telemetry, "otlp-1")
    (resource_metrics,) = built.metrics["resourceMetrics"]
    metrics = {m["name"]: m for m in resource_metrics["scopeMetrics"][0]["metrics"]}
    tokens = metrics["model.provider_input_tokens"]
    assert tokens["unit"] == "{token}" and tokens["description"]
    assert tokens["sum"]["isMonotonic"] is True and tokens["sum"]["aggregationTemporality"] == 1
    assert [p["asDouble"] for p in tokens["sum"]["dataPoints"]] == [120.0, 120.0]
    assert "model.provider_context_window_tokens" not in metrics
    wall = metrics["agent.wall_duration_ms"]
    assert wall["unit"] == "ms" and wall["histogram"]["aggregationTemporality"] == 1
    (point,) = wall["histogram"]["dataPoints"]
    assert point["count"] == "1" and point["sum"] == point["min"] == point["max"]
    assert len(point["bucketCounts"]) == len(point["explicitBounds"]) + 1
    assert sum(int(count) for count in point["bucketCounts"]) == 1
    assert "gauge" in metrics["context.estimated_input_tokens"]
    assert built.unavailable_metrics_skipped >= 4
    assert built.metric_points == sum(
        len(next(iter(m[k] for k in ("sum", "gauge", "histogram") if k in m))["dataPoints"])
        for m in metrics.values()
    )


def test_delta_points_start_where_the_previous_observation_ended(tmp_path):
    store = TelemetryStore(tmp_path)
    try:
        store.register_metric_definition(
            MetricDefinition(
                metric_id="demo.count",
                name="Demo",
                unit="count",
                direction="lower-is-better",
                formula="n",
                aggregation="sum",
                missing_data_rule="unavailable",
                source_description="tests",
                instrument="counter",
            )
        )
        for seconds, value in ((1, 2.0), (3, 5.0)):
            store.record_metric(
                MetricObservation(
                    metric_id="demo.count",
                    run_id="run-m",
                    value=value,
                    unit="count",
                    availability=MetricAvailability.AVAILABLE,
                    observed_at_utc=f"2026-01-01T00:00:0{seconds}+00:00",
                    context={"details": {"tool": "echo"}},
                )
            )
        built = build_run_payloads(store, "run-m", signals=["metrics"])
        points = built.metrics["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["sum"][
            "dataPoints"
        ]
        first, second = points
        assert first["timeUnixNano"] == second["startTimeUnixNano"]
        assert attributes_of(first)["nailong.tool"] == {"stringValue": "echo"}
        assert attributes_of(first)["nailong.run_id"] == {"stringValue": "run-m"}
    finally:
        store.close()


def test_histogram_observations_aggregate_per_attribute_set_into_buckets(tmp_path):
    store = TelemetryStore(tmp_path)
    try:
        store.register_metric_definition(
            MetricDefinition(
                metric_id="demo.latency_ms",
                name="Latency",
                unit="milliseconds",
                direction="lower-is-better",
                formula="t",
                aggregation="last",
                missing_data_rule="unavailable",
                source_description="tests",
                instrument="histogram",
            )
        )
        for second, value, tool in (
            (1, 3.0, "a"),
            (2, 40.0, "a"),
            (3, 4_000.0, "a"),
            (4, 7.0, "b"),
        ):
            store.record_metric(
                MetricObservation(
                    metric_id="demo.latency_ms",
                    run_id="run-h",
                    value=value,
                    unit="milliseconds",
                    availability=MetricAvailability.AVAILABLE,
                    observed_at_utc=f"2026-01-01T00:00:0{second}+00:00",
                    context={"details": {"tool": tool}},
                )
            )
        built = build_run_payloads(store, "run-h", signals=["metrics"])
        metric = built.metrics["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]
        by_tool = {
            attributes_of(point)["nailong.tool"]["stringValue"]: point
            for point in metric["histogram"]["dataPoints"]
        }
        a, b = by_tool["a"], by_tool["b"]
        assert a["count"] == "3" and a["sum"] == 4_043.0 and a["min"] == 3.0 and a["max"] == 4_000.0
        bounds = a["explicitBounds"]
        counts = [int(count) for count in a["bucketCounts"]]
        assert len(counts) == len(bounds) + 1 and sum(counts) == 3
        assert counts[bounds.index(5.0)] == 1
        assert counts[bounds.index(50.0)] == 1
        assert counts[bounds.index(5_000.0)] == 1
        assert b["count"] == "1" and built.metric_points == 2
    finally:
        store.close()


def test_a_run_without_events_produces_no_payload_and_zero_counts(tmp_path):
    store = TelemetryStore(tmp_path)
    try:
        built = build_run_payloads(store, "nothing")
        assert built.by_signal() == {} and built.spans == built.log_records == 0
    finally:
        store.close()


def test_signals_can_be_selected(recorded):
    services, _ = recorded
    only_traces = build_run_payloads(services.telemetry, "otlp-1", signals=["traces"])
    assert set(only_traces.by_signal()) == {"traces"} and only_traces.log_records == 0
    only_logs = build_run_payloads(services.telemetry, "otlp-1", signals=["logs"])
    assert set(only_logs.by_signal()) == {"logs"} and only_logs.spans == 0


def test_spans_the_profile_event_did_not_keep_are_counted_as_omitted(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("nailong_agent_sdk.agent.base_agent.agent")
    monkeypatch.setattr(module, "_PROFILE_EVENT_SPAN_LIMIT", 3)
    services = AgentRuntimeServices.open(tmp_path)
    try:
        subject = services.create_agent(
            definition(tools=[tool("echo")], max_iterations=6),
            UsageTurns([tool_call("c1", "echo"), final()], ProviderUsage()),
            tool_executor=FnExecutor(lambda t, c: ok({})),
        )
        result = arun(subject.run(task("omitted")))
        built = build_run_payloads(services.telemetry, "omitted", signals=["traces"])
        assert built.spans == 3
        assert built.spans_omitted == len(result.profile["spans"]) - 3
        assert preview_export(services.telemetry, ["omitted"]).spans_omitted == built.spans_omitted
    finally:
        services.audit_logs.close()
        services.telemetry.close()


def test_the_controller_run_exports_the_workflow_span_that_parents_the_nodes(tmp_path):
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), make_factory()
    )
    try:
        run_id = executed.graph_run_id
        controller_built = build_run_payloads(services.telemetry, run_id, signals=["traces"])
        (workflow,) = spans_of(controller_built.traces)
        assert workflow["name"] == "invoke_workflow"
        assert workflow["spanId"] == graph_run_span_id(run_id)
        assert workflow["traceId"] == graph_run_trace_id(run_id)
        assert workflow["status"] == {"code": 1} and "parentSpanId" not in workflow
        assert attributes_of(workflow)["gen_ai.operation.name"] == {
            "stringValue": "invoke_workflow"
        }
        assert attributes_of(workflow)["nailong.graph.run_id"] == {"stringValue": run_id}
        for name in ("T1", "T2", "T3"):
            node_built = build_run_payloads(
                services.telemetry, f"task-node:{name}", signals=["traces"]
            )
            root = next(
                s for s in spans_of(node_built.traces) if s["name"].startswith("invoke_agent")
            )
            assert root["parentSpanId"] == workflow["spanId"]
            assert root["traceId"] == workflow["traceId"]
            assert attributes_of(root)["nailong.node_id"] == {"stringValue": f"node:{name}"}
        orchestration = build_run_payloads(
            services.telemetry, record.orchestration_id, signals=["traces", "logs"]
        )
        assert orchestration.traces is None
        assert {
            r["traceId"]
            for r in orchestration.logs["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
            if "traceId" in r
        } == {workflow["traceId"]}
    finally:
        close_all(orchestrator, services)


def test_node_dependencies_become_span_links_between_the_nodes_root_spans(tmp_path):
    orchestrator, record, executed, services = pipeline(
        tmp_path, make_policy(), make_request(blast=5), make_factory()
    )
    try:
        run_id = executed.graph_run_id
        runs = services.telemetry.run_ids_for_trace(graph_run_trace_id(run_id))
        dependencies = {run_id: {"node:T1": [], "node:T2": ["node:T1"], "node:T3": []}}
        report = preview_export(services.telemetry, runs, graph_dependencies=dependencies)
        assert report.span_links == 1
        assert preview_export(services.telemetry, runs).span_links == 0
        roots = {}
        links = dependency_links(node_root_spans(services.telemetry, runs), dependencies)
        for name in ("T1", "T2"):
            built = build_run_payloads(
                services.telemetry, f"task-node:{name}", signals=["traces"], links=links
            )
            roots[name] = next(
                s for s in spans_of(built.traces) if s["name"].startswith("invoke_agent")
            )
        assert "links" not in roots["T1"]
        (link,) = roots["T2"]["links"]
        assert link["spanId"] == roots["T1"]["spanId"] and link["traceId"] == roots["T1"]["traceId"]
        assert attributes_of(link)["nailong.link.kind"] == {"stringValue": "dependency"}
        assert attributes_of(link)["nailong.link.basis"] == {"stringValue": "declared-graph-edge"}
        assert attributes_of(link)["nailong.node_id"] == {"stringValue": "node:T1"}
    finally:
        close_all(orchestrator, services)


def test_the_latest_run_of_a_node_is_the_target_of_its_dependents_links(tmp_path):
    store = TelemetryStore(tmp_path)
    trace = graph_run_trace_id("run-links")

    def profile(run_id, started, span_id):
        store.emit(
            "agent.profile-completed",
            TelemetryContext(run_id=run_id, node_id="node:A", trace_id=trace),
            actor=TelemetryActor(kind="agent", identifier="a"),
            authority=TelemetryAuthority.DETERMINISTIC,
            status="completed",
            payload={
                "trace_id": trace,
                "spans": [
                    {
                        "span_id": span_id,
                        "kind": "run",
                        "name": "agent-run",
                        "started_at_utc": started,
                        "duration_ns": 1,
                        "status": "completed",
                        "attributes": {},
                    }
                ],
            },
        )

    try:
        profile("attempt-1", "2026-01-01T00:00:01+00:00", "1" * 16)
        profile("attempt-2", "2026-01-01T00:00:09+00:00", "2" * 16)
        assert node_root_spans(store, ["attempt-1", "attempt-2"]) == {(trace, "node:A"): "2" * 16}
        assert node_root_spans(store, ["attempt-1"]) == {(trace, "node:A"): "1" * 16}
        roots = node_root_spans(store, ["attempt-1", "attempt-2"])
        assert dependency_links(roots, {"run-links": {"node:B": ["node:A"]}}) == {}
        both = {**roots, (trace, "node:B"): "3" * 16}
        links = dependency_links(both, {"run-links": {"node:B": ["node:A", "node:missing"]}})
        assert list(links) == [(trace, "node:B")]
        assert [link["spanId"] for link in links[(trace, "node:B")]] == ["2" * 16]
    finally:
        store.close()


def test_a_failed_workflow_is_an_error_span(tmp_path):
    telemetry = TelemetryStore(tmp_path / "ledger")
    try:
        runtime, controller_id = executing_runtime(tmp_path / "state", telemetry)
        run_id = runtime.get_run(controller_id).run_id

        async def failing(node, context):
            from nailong_agent_sdk.state.graph_models import GraphNodeResult, GraphNodeStatus

            return GraphNodeResult(status=GraphNodeStatus.FAILED, reason="boom")

        arun(runtime.execute_graph(controller_id, {"agent-invocation": failing}))
        (workflow,) = spans_of(build_run_payloads(telemetry, run_id, signals=["traces"]).traces)
        assert workflow["status"] == {"code": 2, "message": "failed"}
    finally:
        telemetry.close()
