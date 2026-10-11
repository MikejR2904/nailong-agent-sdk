import pytest

from nailong_agent_sdk.state.shared_state import (
    ProvenanceContractGate,
    ProvenanceRecord,
    make_provenance_record,
    provenance_hash,
)


def record(node_id="producer", spans=("a", "b"), schema="result-v1", snapshots=("s",)):
    return make_provenance_record(
        node_id=node_id,
        input_hashes=["h2", "h1"],
        source_snapshot_ids=list(snapshots),
        source_spans=list(spans),
        tool_record_hashes=["t1"],
        artifact_ids=["sha256:x"],
        result_schema_version=schema,
        result={"ok": True},
    )


def test_a_provenance_record_hashes_its_fields_without_depending_on_list_order():
    first = record(spans=("a", "b"))
    reordered = record(spans=("b", "a"))
    assert first.hash == reordered.hash
    assert first.hash == provenance_hash(
        "producer",
        ["h2", "h1"],
        ["s"],
        ["a", "b"],
        ["t1"],
        ["sha256:x"],
        "result-v1",
        first.result_hash,
    )


def test_a_provenance_record_whose_fields_were_altered_is_refused():
    payload = record().model_dump(mode="json")
    payload["source_spans"] = ["forged"]
    with pytest.raises(ValueError, match="provenance hash does not match canonical fields"):
        ProvenanceRecord.model_validate(payload)


def test_the_fan_in_gate_names_every_mismatch():
    gate = ProvenanceContractGate()
    accepted = gate.verify(
        [record("a"), record("b")],
        expected_snapshot_ids={"s"},
        required_schema_version="result-v1",
    )
    assert accepted.accepted is True and accepted.reasons == []
    rejected = gate.verify(
        [record("a"), record("a"), record("c", schema="result-v2"), record("d", snapshots=())],
        expected_snapshot_ids={"s"},
        required_schema_version="result-v1",
    )
    assert rejected.accepted is False
    assert rejected.reasons == [
        "Duplicate producer node IDs are not permitted at contract fan-in.",
        'Node "c" has an unexpected result schema version.',
        'Node "d" lacks an expected source snapshot reference.',
    ]
