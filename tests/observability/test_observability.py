import json

import pytest

from nailong_agent_sdk.observability.metric_definitions import STANDARD_METRIC_DEFINITIONS
from nailong_agent_sdk.observability.metrics import (
    record_metric_unavailable,
    record_metric_value,
    register_standard_metric_definitions,
)
from nailong_agent_sdk.observability.profiler import (
    AgentRunProfiler,
    ProfileSpanKind,
    ProfileSpanStatus,
)
from nailong_agent_sdk.observability.telemetry_models import (
    MetricAvailability,
    MetricDefinition,
    MetricObservation,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.observability_cases import emit


def test_metrics_registry_recording_and_report(tmp_path):
    store = TelemetryStore(tmp_path)
    register_standard_metric_definitions(store)
    register_standard_metric_definitions(store)
    assert len(store.list_metric_definitions()) == len(
        {d.metric_id for d in STANDARD_METRIC_DEFINITIONS}
    )
    ctx = TelemetryContext(run_id="r")
    emit(store, run_id="r")
    record_metric_value(store, ctx, "agent.wall_duration_ms", 12.5, "milliseconds")
    record_metric_unavailable(store, ctx, "model.provider_input_tokens", "tokens", "not reported")
    metrics = store.list_metrics("r")
    assert {m.availability for m in metrics} == {
        MetricAvailability.AVAILABLE,
        MetricAvailability.UNAVAILABLE,
    }
    with pytest.raises(ValueError):
        store.record_metric(
            MetricObservation(
                metric_id="x",
                run_id="r",
                value=None,
                unit="u",
                availability=MetricAvailability.AVAILABLE,
                observed_at_utc="t",
            )
        )
    with pytest.raises(ValueError):
        store.record_metric(
            MetricObservation(
                metric_id="x",
                run_id="r",
                value=None,
                unit="u",
                availability=MetricAvailability.UNAVAILABLE,
                observed_at_utc="t",
            )
        )
    report = store.create_run_report("r")
    assert report["integrity_chain_valid"] is True and report["metric_availability"]
    assert (store.root / report["report_path"]).is_file()
    with pytest.raises(ValueError, match="unknown"):
        store.create_run_report("nope")
    store.close()


def test_standard_metric_definitions_are_unique_and_consistent():
    ids = [d.metric_id for d in STANDARD_METRIC_DEFINITIONS]
    assert len(ids) == len(set(ids))
    for definition in STANDARD_METRIC_DEFINITIONS:
        MetricDefinition.model_validate(definition.model_dump())


def test_profiler_lifecycle_summary_and_integrity(tmp_path):
    profiler = AgentRunProfiler()
    root = profiler.begin_run("r", "t", "agent")
    span = profiler.start_span(
        ProfileSpanKind.TOOL, "x", parent_span_id=root.span_id, attributes={"a": 1}
    )
    profiler.finish_span(span, ProfileSpanStatus.FAILED)
    dangling = profiler.start_span(ProfileSpanKind.MODEL_TURN, "m")
    profile = profiler.finish_run(ProfileSpanStatus.COMPLETED)
    assert profile.finished_at_utc and profile.integrity_hash
    kinds = {s.kind.value: s for s in profile.summaries}
    assert kinds["tool"].failed_count == 1 and kinds["model-turn"].cancelled_count == 1
    assert profiler.finish_run(ProfileSpanStatus.FAILED) == profile
    with pytest.raises(RuntimeError):
        profiler.start_span(ProfileSpanKind.TOOL, "late")
    with pytest.raises(RuntimeError, match="only one run"):
        profiler.begin_run("r2", "t", "a")
    path = profiler.write_json(tmp_path / "p" / "profile.json")
    assert json.loads(path.read_text(encoding="utf-8"))["integrity_hash"] == profile.integrity_hash
    hidden = AgentRunProfiler()
    hidden.begin_run("r", "t", "a")
    with pytest.raises(ValueError):
        hidden.start_span(ProfileSpanKind.TOOL, "x", attributes={"scratchpad": 1})
    assert dangling.span_id
