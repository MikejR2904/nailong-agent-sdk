import json

import pytest
from pydantic import ValidationError

from nailong_agent_sdk.observability.otlp import (
    OtlpExportLedger,
    OtlpHttpExporter,
    build_run_payloads,
    export_runs,
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
from tests.support.collector import Collector


@pytest.fixture
def collector():
    instance = Collector()
    yield instance
    instance.stop()


@pytest.fixture
def store(tmp_path):
    instance = TelemetryStore(tmp_path)
    yield instance
    instance.close()


def define(store, metric_id, unit, instrument="histogram", **extra):
    store.register_metric_definition(
        MetricDefinition(
            metric_id=metric_id,
            name=metric_id,
            unit=unit,
            direction="lower-is-better",
            formula="x",
            aggregation="last",
            missing_data_rule="unavailable",
            source_description="tests",
            instrument=instrument,
            **extra,
        )
    )


def observe(store, metric_id, unit, value, run_id="run-1", second=1):
    store.record_metric(
        MetricObservation(
            metric_id=metric_id,
            run_id=run_id,
            value=value,
            unit=unit,
            availability=MetricAvailability.AVAILABLE,
            observed_at_utc=f"2026-01-01T00:00:{second:02d}+00:00",
        )
    )


def note(store, run_id="run-1", kind="note"):
    store.emit(
        kind,
        TelemetryContext(run_id=run_id),
        actor=TelemetryActor(kind="system", identifier="t", role="test"),
        authority=TelemetryAuthority.SYSTEM,
        status="ok",
        payload={},
    )


def histogram_point(store, run_id="run-1"):
    built = build_run_payloads(store, run_id, signals=["metrics"])
    metric = built.metrics["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]
    return metric["histogram"]["dataPoints"][0]


def test_a_ratio_histogram_spreads_values_between_zero_and_one_over_several_buckets(store):
    define(store, "demo.hit_ratio", "ratio")
    for second, value in enumerate((0.05, 0.3, 0.5, 0.95), start=1):
        observe(store, "demo.hit_ratio", "ratio", value, second=second)
    point = histogram_point(store)
    counts = [int(count) for count in point["bucketCounts"]]
    assert sum(counts) == 4 and max(counts) == 1, counts
    assert point["explicitBounds"][0] < 0.1 and point["explicitBounds"][-1] <= 1.0


@pytest.mark.parametrize(
    ("unit", "value", "expected_bound_range"),
    [("tokens", 12_345, (1_000, 100_000)), ("bytes", 5_000_000, (1_000_000, 1_000_000_000))],
)
def test_token_and_byte_histograms_have_bounds_in_their_own_scale(
    store, unit, value, expected_bound_range
):
    define(store, "demo.size", unit)
    observe(store, "demo.size", unit, value)
    bounds = histogram_point(store)["explicitBounds"]
    assert bounds[0] <= expected_bound_range[0] and bounds[-1] >= expected_bound_range[1]


def test_a_definition_may_set_its_own_bounds_and_they_are_validated(store):
    define(store, "demo.custom", "widgets", histogram_bounds=[2.0, 4.0, 8.0])
    for second, value in enumerate((1.0, 3.0, 5.0, 100.0), start=1):
        observe(store, "demo.custom", "widgets", value, second=second)
    point = histogram_point(store)
    assert point["explicitBounds"] == [2.0, 4.0, 8.0]
    assert [int(c) for c in point["bucketCounts"]] == [1, 1, 1, 1]
    with pytest.raises(ValidationError, match="strictly increasing"):
        define(store, "demo.bad", "widgets", histogram_bounds=[4.0, 2.0])
    with pytest.raises(ValidationError, match="finite"):
        define(store, "demo.bad2", "widgets", histogram_bounds=[1.0, float("inf")])


def test_exporting_a_run_twice_with_a_ledger_sends_each_point_and_record_once(
    store, collector, tmp_path
):
    define(store, "demo.latency", "milliseconds")
    observe(store, "demo.latency", "milliseconds", 12.0)
    note(store)
    ledger = OtlpExportLedger(tmp_path / "ledger.json")
    exporter = OtlpHttpExporter(collector.url)
    first = export_runs(store, ["run-1"], exporter, ledger=ledger)
    sent = len(collector.requests)
    assert first.log_records >= 1 and first.metric_points == 1 and sent >= 2
    second = export_runs(store, ["run-1"], exporter, ledger=ledger)
    assert second.requests == 0 and second.log_records == 0 and second.metric_points == 0
    assert len(collector.requests) == sent


def test_only_what_was_recorded_after_the_last_export_is_sent_next_time(store, collector, tmp_path):
    define(store, "demo.latency", "milliseconds")
    observe(store, "demo.latency", "milliseconds", 12.0, second=1)
    note(store)
    ledger = OtlpExportLedger(tmp_path / "ledger.json")
    exporter = OtlpHttpExporter(collector.url)
    export_runs(store, ["run-1"], exporter, ledger=ledger)
    before = len(collector.requests)
    observe(store, "demo.latency", "milliseconds", 80.0, second=5)
    note(store, kind="later")
    again = export_runs(store, ["run-1"], OtlpHttpExporter(collector.url), ledger=ledger)
    assert again.metric_points == 1 and again.log_records == 1
    logs = [r for r in collector.requests[before:] if r["path"] == "/v1/logs"]
    bodies = json.dumps([r["json"] for r in logs])
    assert "later" in bodies and bodies.count('"stringValue": "note"') == 0


def test_a_ledger_survives_a_restart_and_is_kept_per_collector(store, collector, tmp_path):
    note(store)
    path = tmp_path / "ledger.json"
    export_runs(store, ["run-1"], OtlpHttpExporter(collector.url), ledger=OtlpExportLedger(path))
    requests = len(collector.requests)
    export_runs(store, ["run-1"], OtlpHttpExporter(collector.url), ledger=OtlpExportLedger(path))
    assert len(collector.requests) == requests
    other = Collector()
    try:
        export_runs(store, ["run-1"], OtlpHttpExporter(other.url), ledger=OtlpExportLedger(path))
        assert other.requests, "a different collector has not received the run yet"
    finally:
        other.stop()


def test_a_failed_send_leaves_the_ledger_unchanged_so_the_retry_sends_it(
    store, collector, tmp_path
):
    note(store)
    ledger = OtlpExportLedger(tmp_path / "ledger.json")
    collector.script = [{"status": 400, "body": {"message": "no"}}]
    with pytest.raises(Exception, match="OTLP|export"):
        export_runs(store, ["run-1"], OtlpHttpExporter(collector.url), ledger=ledger)
    collector.script = []
    retry = export_runs(store, ["run-1"], OtlpHttpExporter(collector.url), ledger=ledger)
    assert retry.log_records >= 1
