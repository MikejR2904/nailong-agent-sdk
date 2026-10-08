from nailong_agent_sdk.observability.metrics import record_metric_unavailable, record_metric_value
from nailong_agent_sdk.observability.telemetry_models import (
    MetricDefinition,
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore

FORMULAS = ("sum", "mean", "min", "max", "last", "median")


def definition(metric_id, aggregation):
    return MetricDefinition(
        metric_id=metric_id,
        name=metric_id,
        unit="count",
        direction="lower",
        formula="test",
        aggregation=aggregation,
        missing_data_rule="an unavailable observation is never counted as zero",
        source_description="test",
    )


def reported(tmp_path):
    store = TelemetryStore(tmp_path)
    try:
        context = TelemetryContext(run_id="run-1")
        store.emit(
            "agent.test",
            context,
            actor=TelemetryActor(kind="agent", identifier="tester"),
            authority=TelemetryAuthority.DETERMINISTIC,
            status="ok",
        )
        for name in FORMULAS:
            store.register_metric_definition(definition(f"m.{name}", name))
            for value in (4.0, 1.0, 7.0):
                record_metric_value(store, context, f"m.{name}", value, "count")
            record_metric_unavailable(store, context, f"m.{name}", "count", "not reported")
        record_metric_value(store, context, "m.unregistered", 3.0, "count")
        store.register_metric_definition(definition("m.empty", "sum"))
        record_metric_unavailable(store, context, "m.empty", "count", "never reported")
        return store.create_run_report("run-1")
    finally:
        store.close()


def test_the_run_report_aggregates_each_metric_by_its_registered_formula(tmp_path):
    summary = reported(tmp_path)["metric_summary"]
    assert {name: summary[f"m.{name}"]["value"] for name in FORMULAS} == {
        "sum": 12.0,
        "mean": 4.0,
        "min": 1.0,
        "max": 7.0,
        "last": 7.0,
        "median": None,
    }
    assert {summary[f"m.{name}"]["aggregation"] for name in FORMULAS} == set(FORMULAS)


def test_unavailable_observations_are_counted_but_never_aggregated_as_values(tmp_path):
    summary = reported(tmp_path)["metric_summary"]
    assert summary["m.sum"]["available_observation_count"] == 3
    assert summary["m.sum"]["unavailable_observation_count"] == 1
    assert summary["m.empty"] == {
        "aggregation": "sum",
        "value": None,
        "available_observation_count": 0,
        "unavailable_observation_count": 1,
        "unit": "count",
        "missing_data_rule": "an unavailable observation is never counted as zero",
    }


def test_a_metric_without_a_definition_is_reported_but_not_aggregated(tmp_path):
    summary = reported(tmp_path)["metric_summary"]
    assert summary["m.unregistered"] == {
        "aggregation": "unregistered",
        "value": None,
        "available_observation_count": 1,
        "unavailable_observation_count": 0,
        "unit": "count",
        "missing_data_rule": "No definition registered.",
    }
