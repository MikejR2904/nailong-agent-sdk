import json

import pytest

from nailong_agent_sdk.agent.model import ProviderUsage
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability import otlp
from nailong_agent_sdk.observability.otlp import OtlpHttpExporter, export_runs, preview_export
from tests.support.agents import arun, definition, final, task
from tests.support.collector import Collector
from tests.support.otlp_runs import UsageTurns, spans_of

KEY = "collector-secret-token-0123456789"


@pytest.fixture
def collector():
    instance = Collector()
    yield instance
    instance.stop()


def exporter_for(collector, **options):
    sleeps = []
    options.setdefault("sleep", sleeps.append)
    return OtlpHttpExporter(collector.url, **options), sleeps


def test_a_request_goes_to_the_signal_path_as_json_with_the_callers_headers(collector):
    exporter, sleeps = exporter_for(collector, headers={"Authorization": f"Bearer {KEY}"})
    result = exporter.send("traces", {"resourceSpans": []})
    assert (result.status, result.attempts, result.rejected) == (200, 1, 0) and sleeps == []
    (request,) = collector.requests
    assert request["path"] == "/v1/traces" and request["json"] == {"resourceSpans": []}
    assert request["headers"]["content-type"] == "application/json"
    assert request["headers"]["authorization"] == f"Bearer {KEY}"
    exporter.send("metrics", {})
    exporter.send("logs", {})
    assert [r["path"] for r in collector.requests] == ["/v1/traces", "/v1/metrics", "/v1/logs"]


def test_a_trailing_slash_and_gzip_are_handled(collector):
    exporter = OtlpHttpExporter(collector.url + "/", compress=True)
    exporter.send("logs", {"resourceLogs": [{"a": 1}]})
    (request,) = collector.requests
    assert request["path"] == "/v1/logs" and request["headers"]["content-encoding"] == "gzip"
    assert request["json"] == {"resourceLogs": [{"a": 1}]}


def test_a_partial_success_message_is_scrubbed_like_any_collector_text(collector):
    collector.script = [
        {"body": {"partialSuccess": {"rejectedSpans": 1, "errorMessage": f"rejected key {KEY}"}}}
    ]
    exporter, _ = exporter_for(collector, headers={"Authorization": f"Bearer {KEY}"})
    result = exporter.send("traces", {})
    assert result.rejected == 1 and KEY not in result.error_message
    assert "[REDACTED]" in result.error_message


def test_a_lone_surrogate_in_an_attribute_is_replaced_so_the_body_stays_valid_utf8():
    broken = "tool" + chr(0xD800) + "name"
    converted = otlp._key_values({broken: broken, "ok": "fine"})
    assert converted[0] == {
        "key": "tool�name",
        "value": {"stringValue": "tool�name"},
    }
    json.dumps(converted).encode("utf-8")
    assert json.dumps(converted, ensure_ascii=False).encode("utf-8")


def test_a_partial_success_is_reported_and_not_retried(collector):
    collector.script = [
        {"body": {"partialSuccess": {"rejectedSpans": "2", "errorMessage": "two spans too old"}}}
    ]
    exporter, sleeps = exporter_for(collector)
    result = exporter.send("traces", {})
    assert result.rejected == 2 and result.error_message == "two spans too old"
    assert len(collector.requests) == 1 and sleeps == []


def test_throttling_is_retried_with_backoff_and_retry_after(collector):
    collector.script = [
        {"status": 429, "headers": {"Retry-After": "7"}, "body": {"message": "slow down"}},
        {"status": 503},
        {"status": 200},
    ]
    exporter, sleeps = exporter_for(collector, max_attempts=4)
    result = exporter.send("traces", {})
    assert (result.status, result.attempts) == (200, 3)
    assert sleeps == [7.0, 1.0] and len(collector.requests) == 3


def test_retries_run_out_with_an_error_naming_the_endpoint_and_status(collector):
    collector.default = {"status": 503, "body": {"message": "overloaded"}}
    exporter, sleeps = exporter_for(collector, max_attempts=3)
    with pytest.raises(AgentSdkError) as raised:
        exporter.send("traces", {})
    error = raised.value
    assert error.code == "OTLP_EXPORT_FAILED"
    assert f"{collector.url}/v1/traces" in error.message
    assert "after 3 attempt(s)" in error.message and "HTTP 503" in error.message
    assert "overloaded" in error.message
    assert error.details["attempts"] == 3 and sleeps == [0.5, 1.0]


def test_a_rejected_request_is_final_and_not_retried(collector):
    collector.script = [{"status": 400, "body": {"message": "malformed span"}}]
    exporter, sleeps = exporter_for(collector)
    with pytest.raises(AgentSdkError) as raised:
        exporter.send("traces", {})
    assert "HTTP 400" in raised.value.message and "malformed span" in raised.value.message
    assert raised.value.details["status"] == 400 and raised.value.details["attempts"] == 1
    assert len(collector.requests) == 1 and sleeps == []


def test_a_redirect_is_refused_so_headers_stay_on_the_configured_host(collector):
    collector.script = [{"status": 302, "headers": {"Location": "http://127.0.0.1:9/v1/traces"}}]
    exporter, _ = exporter_for(collector, headers={"Authorization": f"Bearer {KEY}"})
    with pytest.raises(AgentSdkError) as raised:
        exporter.send("traces", {})
    assert "redirect" in raised.value.message and "refused" in raised.value.message
    assert KEY not in raised.value.message and len(collector.requests) == 1


def test_a_credential_echoed_by_the_collector_is_scrubbed_from_the_error(collector):
    collector.script = [{"status": 401, "body": {"message": f"bad token {KEY}"}}]
    exporter, _ = exporter_for(collector, headers={"Authorization": f"Bearer {KEY}"})
    with pytest.raises(AgentSdkError) as raised:
        exporter.send("traces", {})
    assert KEY not in raised.value.message and "[REDACTED]" in raised.value.message


def test_an_unreachable_collector_is_retried_then_named(collector):
    port = collector.server.server_address[1]
    collector.stop()
    sleeps = []
    exporter = OtlpHttpExporter(f"http://127.0.0.1:{port}", max_attempts=2, sleep=sleeps.append)
    with pytest.raises(AgentSdkError) as raised:
        exporter.send("traces", {})
    assert "after 2 attempt(s)" in raised.value.message and sleeps == [0.5]
    assert raised.value.details["status"] is None


@pytest.mark.parametrize(
    ("endpoint", "options", "message"),
    [
        ("collector:4318", {}, "absolute http\\(s\\) URL"),
        ("ftp://collector", {}, "absolute http\\(s\\) URL"),
        ("http://user:pw@collector:4318", {}, "must not embed credentials"),
        ("http://collector", {"timeout_seconds": 0}, "timeout_seconds must be positive"),
        ("http://collector", {"max_attempts": 0}, "max_attempts must be at least 1"),
    ],
)
def test_a_bad_exporter_setting_is_refused_naming_it(endpoint, options, message):
    with pytest.raises(ValueError, match=message):
        OtlpHttpExporter(endpoint, **options)


def test_export_runs_sends_one_request_per_signal_and_totals_the_counts(recorded, collector):
    services, _ = recorded
    exporter, _ = exporter_for(collector)
    report = export_runs(services.telemetry, ["otlp-1"], exporter)
    assert report.runs == 1 and report.requests == 3 and report.rejected == {}
    assert report.spans == 6 and report.log_records > 5 and report.metric_points > 5
    assert sorted(r["path"] for r in collector.requests) == [
        "/v1/logs",
        "/v1/metrics",
        "/v1/traces",
    ]
    traces = next(r for r in collector.requests if r["path"] == "/v1/traces")["json"]
    assert len(spans_of(traces)) == 6
    preview = preview_export(services.telemetry, ["otlp-1"])
    assert preview.requests == 0 and preview.spans == report.spans


def test_export_runs_reports_the_run_that_failed_and_how_far_it_got(recorded, collector):
    services, _ = recorded
    collector.script = [{"status": 200}, {"status": 400, "body": {"message": "bad logs"}}]
    exporter, _ = exporter_for(collector)
    with pytest.raises(AgentSdkError) as raised:
        export_runs(services.telemetry, ["otlp-1"], exporter)
    assert 'run "otlp-1"' in raised.value.message and "after 1 request(s)" in raised.value.message
    assert raised.value.code == "OTLP_EXPORT_FAILED"
    assert raised.value.details["run_id"] == "otlp-1"


def test_rejections_reported_by_the_collector_are_totalled_by_signal(recorded, collector):
    services, _ = recorded
    collector.script = [
        {"body": {"partialSuccess": {"rejectedSpans": "1", "errorMessage": "skewed clock"}}},
        {"body": {"partialSuccess": {"rejectedLogRecords": "3"}}},
        {},
    ]
    exporter, _ = exporter_for(collector)
    report = export_runs(services.telemetry, ["otlp-1"], exporter)
    assert report.rejected == {"traces": 1, "logs": 3}
    assert report.rejection_messages == ["otlp-1 traces: skewed clock"]


def test_runs_are_found_by_the_trace_their_events_recorded(tmp_path):
    services = AgentRuntimeServices.open(tmp_path)
    try:
        for run_id in ("first", "second"):
            subject = services.create_agent(definition(), UsageTurns([final()], ProviderUsage()))
            arun(subject.run(task(run_id)))
        trace = json.loads(
            services.telemetry.list_events("first", limit=500)[-1].model_dump_json()
        )["context"]["trace_id"]
        assert services.telemetry.run_ids_for_trace(trace) == ["first"]
        assert services.telemetry.run_ids_for_trace("0" * 31 + "1") == []
    finally:
        services.audit_logs.close()
        services.telemetry.close()
