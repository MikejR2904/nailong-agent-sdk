import json
import sqlite3

import pytest
from pydantic import ValidationError

from nailong_agent_sdk.foundations.hashing import canonical_hash
from nailong_agent_sdk.foundations.version import PACKAGE_NAME, package_version
from nailong_agent_sdk.observability import trace_context
from nailong_agent_sdk.observability.metric_definitions import (
    STANDARD_METRIC_DEFINITIONS,
    _instrument_for,
)
from nailong_agent_sdk.observability.profiler import AgentRunProfiler, ProfileSpanStatus
from nailong_agent_sdk.observability.telemetry_models import (
    MetricDefinition,
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.observability.trace_context import (
    TraceParent,
    extract_traceparent,
    format_traceparent,
    inject_traceparent,
    is_span_id,
    is_trace_id,
    new_span_id,
    new_trace_id,
    parse_traceparent,
    require_span_id,
    require_trace_id,
    resource_attributes,
)

TRACE = "0af7651916cd43dd8448eb211c80319c"
SPAN = "b7ad6b7169203331"
HEADER = f"00-{TRACE}-{SPAN}-01"


def test_generated_ids_have_the_w3c_widths_and_are_unique():
    traces = {new_trace_id() for _ in range(50)}
    spans = {new_span_id() for _ in range(50)}
    assert len(traces) == 50 and len(spans) == 50
    assert all(is_trace_id(value) and len(value) == 32 for value in traces)
    assert all(is_span_id(value) and len(value) == 16 for value in spans)


def test_the_all_zero_value_is_never_generated(monkeypatch):
    zeros = iter(["0" * 32, "0" * 32, TRACE])
    monkeypatch.setattr(trace_context.secrets, "token_hex", lambda size: next(zeros))
    assert new_trace_id() == TRACE
    spans = iter(["0" * 16, SPAN])
    monkeypatch.setattr(trace_context.secrets, "token_hex", lambda size: next(spans))
    assert new_span_id() == SPAN


@pytest.mark.parametrize(
    "value",
    [
        TRACE.upper(),
        TRACE[:-1],
        TRACE + "0",
        "0" * 32,
        "g" + TRACE[1:],
        "",
        None,
        1234,
        SPAN,
    ],
)
def test_is_trace_id_refuses_anything_but_32_lowercase_hex_that_is_not_zero(value):
    assert is_trace_id(value) is False


@pytest.mark.parametrize(
    "value", [SPAN.upper(), SPAN[:-1], SPAN + "0", "0" * 16, "z" + SPAN[1:], "", None, TRACE]
)
def test_is_span_id_refuses_anything_but_16_lowercase_hex_that_is_not_zero(value):
    assert is_span_id(value) is False


def test_require_helpers_name_the_field_and_bound_the_echoed_value():
    assert require_trace_id(TRACE, "trace_id") == TRACE
    assert require_span_id(SPAN, "span_id") == SPAN
    with pytest.raises(ValueError, match=r"^node_trace must be 32 lowercase hex characters"):
        require_trace_id("xyz", "node_trace")
    with pytest.raises(ValueError, match=r"^node_span must be 16 lowercase hex characters"):
        require_span_id("abc", "node_span")
    with pytest.raises(ValueError) as raised:
        require_span_id("q" * 5_000, "span_id")
    assert len(str(raised.value)) < 300 and str(raised.value).count("q") == 64


def test_format_traceparent_is_the_w3c_header_value():
    assert format_traceparent(TRACE, SPAN) == HEADER
    assert format_traceparent(TRACE, SPAN, sampled=False) == f"00-{TRACE}-{SPAN}-00"
    with pytest.raises(ValueError, match="trace_id must be 32 lowercase hex"):
        format_traceparent("bad", SPAN)
    with pytest.raises(ValueError, match="span_id must be 16 lowercase hex"):
        format_traceparent(TRACE, "bad")


def test_parse_traceparent_round_trips_and_reports_the_sampled_flag():
    assert parse_traceparent(HEADER) == TraceParent(TRACE, SPAN, True)
    assert parse_traceparent(f"00-{TRACE}-{SPAN}-00").sampled is False
    assert parse_traceparent(f"  {HEADER}  ") == TraceParent(TRACE, SPAN, True)
    assert parse_traceparent(f"00-{TRACE}-{SPAN}-03").sampled is True
    assert parse_traceparent(format_traceparent(TRACE, SPAN, sampled=False)).sampled is False


def test_a_future_version_may_carry_extra_fields_but_version_00_may_not():
    assert parse_traceparent(f"01-{TRACE}-{SPAN}-01-extra-fields").trace_id == TRACE
    with pytest.raises(ValueError, match="version 00 takes exactly four fields, got 5"):
        parse_traceparent(f"00-{TRACE}-{SPAN}-01-extra")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", r"four fields .* got 1 field\(s\)"),
        (f"00-{TRACE}-{SPAN}", r"four fields .* got 3 field\(s\)"),
        (f"0-{TRACE}-{SPAN}-01", "version must be 2 lowercase hex characters, got '0'"),
        (f"0G-{TRACE}-{SPAN}-01", "version must be 2 lowercase hex characters, got '0G'"),
        (f"ff-{TRACE}-{SPAN}-01", 'version "ff" is not allowed'),
        (f"00-{'0' * 32}-{SPAN}-01", "trace_id must be 32 lowercase hex characters and not all"),
        (f"00-{TRACE.upper()}-{SPAN}-01", "trace_id must be 32 lowercase hex characters"),
        (f"00-{TRACE}-{'0' * 16}-01", "parent_id must be 16 lowercase hex characters and not all"),
        (f"00-{TRACE}-{SPAN[:-1]}-01", "parent_id must be 16 lowercase hex characters"),
        (f"00-{TRACE}-{SPAN}-1", "flags must be 2 lowercase hex characters, got '1'"),
        (f"00-{TRACE}-{SPAN}-zz", "flags must be 2 lowercase hex characters, got 'zz'"),
    ],
)
def test_parse_traceparent_names_the_malformed_part(value, message):
    with pytest.raises(ValueError, match=message):
        parse_traceparent(value)


def test_parse_traceparent_refuses_a_non_string():
    with pytest.raises(ValueError, match="traceparent must be a string, got int"):
        parse_traceparent(7)


def test_inject_writes_the_header_and_extract_reads_it_case_insensitively():
    carrier: dict[str, str] = {}
    inject_traceparent(carrier, TRACE, SPAN)
    assert carrier == {"traceparent": HEADER}
    assert extract_traceparent(carrier) == TraceParent(TRACE, SPAN, True)
    assert extract_traceparent({"TraceParent": HEADER}) == TraceParent(TRACE, SPAN, True)


def test_extract_ignores_a_missing_or_malformed_header_instead_of_raising():
    assert extract_traceparent({}) is None
    assert extract_traceparent({"authorization": "x"}) is None
    assert extract_traceparent({"traceparent": "garbage"}) is None
    assert extract_traceparent({"traceparent": f"00-{'0' * 32}-{SPAN}-01"}) is None


def test_the_process_resource_is_stable_and_names_the_sdk(monkeypatch):
    monkeypatch.delenv(trace_context.SERVICE_NAME_VARIABLE, raising=False)
    trace_context._process_resource.cache_clear()
    try:
        first = resource_attributes()
        assert first["service.name"] == PACKAGE_NAME
        assert first["telemetry.sdk.name"] == PACKAGE_NAME
        assert first["telemetry.sdk.language"] == "python"
        assert first["telemetry.sdk.version"] == package_version()
        assert len(first["service.instance.id"]) == 32
        first["service.name"] = "mutated"
        assert resource_attributes()["service.name"] == PACKAGE_NAME
        assert resource_attributes()["service.instance.id"] == first["service.instance.id"]
    finally:
        trace_context._process_resource.cache_clear()


def test_the_service_name_follows_the_standard_environment_variable(monkeypatch):
    monkeypatch.setenv(trace_context.SERVICE_NAME_VARIABLE, "billing-agents")
    trace_context._process_resource.cache_clear()
    try:
        assert resource_attributes()["service.name"] == "billing-agents"
    finally:
        monkeypatch.delenv(trace_context.SERVICE_NAME_VARIABLE)
        trace_context._process_resource.cache_clear()


def test_a_context_accepts_w3c_ids_and_leaves_unset_ids_none():
    context = TelemetryContext(
        run_id="run-1", trace_id=TRACE, span_id=SPAN, parent_span_id=new_span_id()
    )
    assert context.trace_id == TRACE and context.span_id == SPAN
    assert TelemetryContext(run_id="run-1").trace_id is None


@pytest.mark.parametrize(
    ("field", "value", "width"),
    [
        ("trace_id", "trace-1", 32),
        ("trace_id", TRACE.upper(), 32),
        ("trace_id", "0" * 32, 32),
        ("span_id", "span-1", 16),
        ("span_id", "0" * 16, 16),
        ("parent_span_id", TRACE, 16),
    ],
)
def test_a_context_refuses_a_non_w3c_trace_or_span_id_naming_the_field(field, value, width):
    with pytest.raises(ValidationError) as raised:
        TelemetryContext(run_id="run-1", **{field: value})
    (issue,) = raised.value.errors()
    assert issue["loc"] == (field,)
    assert f"{field} must be {width} lowercase hex characters" in issue["msg"]


def test_the_ids_survive_a_store_round_trip(tmp_path):
    store = TelemetryStore(tmp_path)
    try:
        store.emit(
            "demo.event",
            TelemetryContext(run_id="run-1", trace_id=TRACE, span_id=SPAN, parent_span_id=None),
            actor=TelemetryActor(kind="test", identifier="t"),
            authority=TelemetryAuthority.SYSTEM,
            status="ok",
        )
        (event,) = store.list_events("run-1")
        assert event.context.trace_id == TRACE and event.context.span_id == SPAN
        assert store.verify_run_chain("run-1") is True
    finally:
        store.close()


def test_a_stored_event_with_a_caller_chosen_trace_id_is_still_read_and_verified(tmp_path):
    legacy = TelemetryContext.model_construct(
        run_id="run-1", trace_id="trace-1", span_id="span-1", parent_span_id=None
    )
    store = TelemetryStore(tmp_path)
    try:
        store.emit(
            "demo.event",
            legacy,
            actor=TelemetryActor(kind="test", identifier="t"),
            authority=TelemetryAuthority.SYSTEM,
            status="ok",
        )
        (listed,) = store.list_events("run-1")
        (streamed,) = list(store.iter_events("run-1"))
        assert listed.context.trace_id == streamed.context.trace_id == "trace-1"
        assert listed.context.span_id == "span-1"
        assert store.verify_run_chain("run-1") is True
    finally:
        store.close()
    with pytest.raises(ValidationError, match="trace_id must be 32 lowercase hex"):
        TelemetryContext(run_id="run-1", trace_id="trace-1")


def test_the_profiler_generates_a_trace_and_uses_w3c_span_ids():
    profiler = AgentRunProfiler()
    assert profiler.trace_id is None
    root = profiler.begin_run("run-1", "task-1", "agent")
    assert is_trace_id(profiler.trace_id) and is_span_id(root.span_id)
    profile = profiler.finish_run(ProfileSpanStatus.COMPLETED)
    assert profile.trace_id == profiler.trace_id
    assert [span.span_id for span in profile.spans] == [root.span_id]
    assert profile.spans[0].parent_span_id is None


def test_the_profiler_continues_a_supplied_trace_under_a_remote_parent():
    profiler = AgentRunProfiler()
    root = profiler.begin_run("run-1", "task-1", "agent", trace_id=TRACE, parent_span_id=SPAN)
    profile = profiler.finish_run(ProfileSpanStatus.COMPLETED)
    assert profile.trace_id == TRACE
    assert profile.spans[0].span_id == root.span_id
    assert profile.spans[0].parent_span_id == SPAN


def test_the_profiler_refuses_malformed_ids_and_stays_unstarted():
    profiler = AgentRunProfiler()
    with pytest.raises(ValueError, match="trace_id must be 32 lowercase hex"):
        profiler.begin_run("run-1", "task-1", "agent", trace_id="nope")
    with pytest.raises(ValueError, match="parent_span_id must be 16 lowercase hex"):
        profiler.begin_run("run-1", "task-1", "agent", parent_span_id="nope")
    assert profiler.trace_id is None
    profiler.begin_run("run-1", "task-1", "agent")


def test_the_profile_integrity_hash_covers_the_trace_id():
    profiler = AgentRunProfiler()
    profiler.begin_run("run-1", "task-1", "agent", trace_id=TRACE)
    profile = profiler.finish_run(ProfileSpanStatus.COMPLETED)
    payload = profile.model_dump(mode="json")
    payload["integrity_hash"] = ""
    assert canonical_hash(payload) == profile.integrity_hash
    payload["trace_id"] = new_trace_id()
    assert canonical_hash(payload) != profile.integrity_hash


def test_every_standard_metric_declares_the_instrument_its_unit_and_aggregation_imply():
    assert len(STANDARD_METRIC_DEFINITIONS) == 35
    for definition in STANDARD_METRIC_DEFINITIONS:
        assert definition.instrument == _instrument_for(definition.unit, definition.aggregation)
    by_id = {definition.metric_id: definition for definition in STANDARD_METRIC_DEFINITIONS}
    assert by_id["agent.tool_call_attempt_count"].instrument == "counter"
    assert by_id["tool.duration_ms"].instrument == "histogram"
    assert {item.instrument for item in STANDARD_METRIC_DEFINITIONS} == {
        "counter",
        "gauge",
        "histogram",
    }


@pytest.mark.parametrize(
    ("unit", "aggregation", "expected"),
    [
        ("milliseconds", "sum", "histogram"),
        ("milliseconds", "last", "histogram"),
        ("count", "sum", "counter"),
        ("tokens", "max", "gauge"),
        ("ratio", "last", "gauge"),
        ("count", "min", "gauge"),
    ],
)
def test_the_instrument_rule(unit, aggregation, expected):
    assert _instrument_for(unit, aggregation) == expected


def definition_payload(**overrides):
    return {
        "metric_id": "demo.metric",
        "name": "Demo",
        "unit": "count",
        "direction": "lower-is-better",
        "formula": "n",
        "aggregation": "sum",
        "missing_data_rule": "unavailable",
        "source_description": "tests",
        **overrides,
    }


def test_an_unset_instrument_is_allowed_and_an_unknown_one_is_refused():
    assert MetricDefinition(**definition_payload()).instrument is None
    assert MetricDefinition(**definition_payload(instrument="up_down_counter")).instrument == (
        "up_down_counter"
    )
    with pytest.raises(ValidationError) as raised:
        MetricDefinition(**definition_payload(instrument="summary"))
    assert raised.value.errors()[0]["loc"] == ("instrument",)


def test_definitions_stored_before_the_instrument_field_still_load(tmp_path):
    store = TelemetryStore(tmp_path)
    store.close()
    connection = sqlite3.connect(tmp_path / ".agent-telemetry" / "telemetry.sqlite3")
    try:
        with connection:
            connection.execute(
                "INSERT INTO metric_definitions (metric_id, definition_json) VALUES (?, ?)",
                ("demo.metric", json.dumps(definition_payload())),
            )
    finally:
        connection.close()
    reopened = TelemetryStore(tmp_path)
    try:
        (loaded,) = reopened.list_metric_definitions()
        assert loaded.metric_id == "demo.metric" and loaded.instrument is None
        reopened.register_metric_definition(
            MetricDefinition(**definition_payload(instrument="counter"))
        )
        (updated,) = reopened.list_metric_definitions()
        assert updated.instrument == "counter"
    finally:
        reopened.close()
