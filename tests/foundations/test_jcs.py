import json

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.foundations.hashing import canonical_hash
from nailong_agent_sdk.foundations.jcs import (
    jcs_bytes,
    jcs_hash,
    jcs_json,
    number_text,
    schema_hash,
    schema_message,
    uses_jcs,
)
from nailong_agent_sdk.observability.audit_log import (
    AuditLogEntry,
    AuditTranscriptStore,
    audit_entry_hash,
)
from nailong_agent_sdk.observability.telemetry_store import event_hash
from tests.support.observability_cases import emit


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, "0"),
        (-0.0, "0"),
        (1.0, "1"),
        (10.0, "10"),
        (4.5, "4.5"),
        (-4.5, "-4.5"),
        (5e-324, "5e-324"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
        (9007199254740992.0, "9007199254740992"),
        (295147905179352830000.0, "295147905179352830000"),
        (1e21, "1e+21"),
        (1e20, "100000000000000000000"),
        (1e23, "1e+23"),
        (9.999999999999997e22, "9.999999999999997e+22"),
        (0.000001, "0.000001"),
        (9.999999999999997e-7, "9.999999999999997e-7"),
        (1e-7, "1e-7"),
        (123456.789, "123456.789"),
        (1.5e-10, "1.5e-10"),
        (-1e21, "-1e+21"),
    ],
)
def test_numbers_are_written_as_ecmascript_writes_them(value, expected):
    assert number_text(value) == expected
    assert jcs_json(value) == expected


def test_a_float_with_no_fraction_hashes_like_the_integer():
    assert jcs_hash({"a": 1.0}) == jcs_hash({"a": 1})


def test_object_keys_sort_by_utf16_code_units_as_in_the_rfc_example():
    keys = ["€", "\r", "דּ", "1", "\U0001f600", "\u0080", "ö"]
    ordered = list(json.loads(jcs_json({key: 1 for key in keys})))
    assert ordered == ["\r", "1", "\u0080", "ö", "€", "\U0001f600", "דּ"]


def test_strings_keep_non_ascii_literal_and_escape_only_what_json_requires():
    text = 'café "q" \\ \n \x1f \x7f   \U0001f600'
    assert jcs_json(text) == '"café \\"q\\" \\\\ \\n \\u001f \x7f   \U0001f600"'
    assert jcs_bytes("é") == b'"\xc3\xa9"'


def test_the_rfc_8785_structure_example_is_reproduced():
    document = {
        "numbers": [333333333.33333329, 1e30, 4.5, 2e-3, 0.000000000000000000000000001],
        "string": '€$\x0f\nA\'B"\\\\"/',
        "literals": [None, True, False],
    }
    assert jcs_json(document) == (
        '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )


def test_whitespace_and_insertion_order_never_change_the_bytes():
    assert jcs_json({"b": [1, {"d": 1, "c": 2}], "a": None}) == '{"a":null,"b":[1,{"c":2,"d":1}]}'


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        (float("nan"), "$ is nan"),
        ({"a": [float("inf")]}, "$.a[0] is inf"),
        ({"a": {1, 2}}, "set at $.a has no JSON form"),
        ({1: "x"}, "has a int key 1"),
        ({"k": "\ud800"}, "$.k holds a lone surrogate at index 0"),
        (object(), "object at $ has no JSON form"),
    ],
)
def test_a_value_with_no_json_form_is_refused_naming_where_it_is(value, fragment):
    with pytest.raises((TypeError, ValueError)) as failure:
        jcs_json(value)
    assert fragment in str(failure.value)


def test_a_schema_ending_in_v2_hashes_with_jcs_and_older_ones_keep_the_legacy_form():
    content = {"name": "café", "ratio": 1.0}
    assert uses_jcs("x-v2") and not uses_jcs("x-v1")
    assert schema_hash("x-v2", content) == jcs_hash(content)
    assert schema_hash("x-v1", content) == canonical_hash(content)
    assert schema_hash("x-v1", content) != schema_hash("x-v2", content)
    assert schema_message("x-v2", content) == jcs_bytes(content)


def test_an_audit_entry_written_before_jcs_still_verifies_and_the_chain_continues_in_v2(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    legacy = AuditLogEntry(
        schema_version="agent-audit-log-v1",
        sequence=1,
        event_type="old",
        occurred_at_utc="2026-01-01T00:00:00+00:00",
        run_id="r1",
        payload={"name": "café", "ratio": 1.0},
    )
    legacy = legacy.model_copy(update={"integrity_hash": audit_entry_hash(legacy)})
    assert legacy.integrity_hash == canonical_hash(
        legacy.model_copy(update={"integrity_hash": ""}).model_dump(mode="json")
    )
    path = tmp_path / ".agent-audit-logs" / "r1.jsonl"
    path.write_text(legacy.model_dump_json() + "\n", encoding="utf-8")
    current = store.append("r1", "new", {"name": "café"})
    assert current.schema_version == "agent-audit-log-v2" and current.sequence == 2
    assert current.previous_hash == legacy.integrity_hash
    assert store.chain_break("r1") is None
    store.close()


def test_an_audit_entry_with_an_unknown_schema_is_refused_by_name_not_silently_rehashed(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    store.append("r1", "one", {})
    path = tmp_path / ".agent-audit-logs" / "r1.jsonl"
    path.write_text(path.read_text("utf-8").replace("agent-audit-log-v2", "agent-audit-log-v9"))
    with pytest.raises(AgentSdkError) as failure:
        store.chain_break("r1")
    assert failure.value.code == "AUDIT_TRANSCRIPT_CORRUPT"
    assert 'schema_version "agent-audit-log-v9" is not one of' in failure.value.message
    store.close()


def test_a_telemetry_event_hash_follows_its_schema_version(tmp_path):
    from nailong_agent_sdk.observability.telemetry_store import TelemetryStore

    store = TelemetryStore(tmp_path)
    stored = emit(store, run_id="r1")
    assert stored.schema_version == "telemetry-event-v2"
    assert event_hash(stored) == stored.integrity_hash
    older = stored.model_copy(update={"schema_version": "telemetry-event-v1"})
    assert event_hash(older) == canonical_hash(
        older.model_copy(update={"integrity_hash": ""}).model_dump(mode="json")
    )
    store.close()
