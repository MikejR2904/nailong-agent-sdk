import json

import pytest

from nailong_agent_sdk.state.shared_state import (
    LateralDependencyRequest,
    ProvenanceContractGate,
    ProvenanceRecord,
    RunSharedState,
    SharedStateStore,
    SharedStateWrite,
    make_provenance_record,
    provenance_hash,
)
from tests.support.elastic import SUBSTRATE, discovery


def lateral(consumer="consumer", episode="d1", action="act"):
    return LateralDependencyRequest(
        consumer_node_id=consumer,
        consumer_action_id=action,
        discovery_episode_id=episode,
        reason="needs the finding",
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


def test_the_legacy_substrate_enforces_the_publication_rules():
    state = RunSharedState(SUBSTRATE)
    with pytest.raises(ValueError, match="unexpected shared substrate snapshot"):
        state.publish_discovery(discovery().model_copy(update={"snapshot_id": "other"}))
    with pytest.raises(ValueError, match="unexpected shared substrate version"):
        state.publish_discovery(discovery().model_copy(update={"snapshot_version": "2"}))
    with pytest.raises(ValueError, match="Only closed exploratory discoveries"):
        state.publish_discovery(discovery().model_copy(update={"closed": False}))
    state.publish_discovery(discovery())
    with pytest.raises(ValueError, match='Discovery episode "d1" already exists'):
        state.publish_discovery(discovery())
    assert state.get_discovery("d1") == discovery() and state.get_discovery("nope") is None


def test_the_legacy_substrate_refuses_unknown_and_self_dependencies_and_rewrites():
    state = RunSharedState(SUBSTRATE)
    state.publish_discovery(discovery(producer="producer"))
    with pytest.raises(ValueError, match="must cite a published exploratory discovery"):
        state.request_lateral_dependency(lateral(episode="missing"))
    with pytest.raises(ValueError, match="must cross distinct graph nodes"):
        state.request_lateral_dependency(lateral(consumer="producer"))
    assert state.request_lateral_dependency(lateral()) == discovery(producer="producer")
    write = SharedStateWrite(
        producer_node_id="producer", key="k", value={"a": 1}, provenance_hash="h"
    )
    state.write(write)
    with pytest.raises(ValueError, match='Shared-state key "k" is immutable once written'):
        state.write(write)


def test_the_legacy_substrate_round_trips_through_its_store(tmp_path):
    state = RunSharedState(SUBSTRATE)
    state.publish_discovery(discovery(producer="producer"))
    state.request_lateral_dependency(lateral())
    state.write(
        SharedStateWrite(producer_node_id="producer", key="k", value={"a": 1}, provenance_hash="h")
    )
    store = SharedStateStore(tmp_path)
    store.save("run-1", state)
    loaded = store.load("run-1")
    assert loaded.snapshot_state() == state.snapshot_state()
    assert state.snapshot_state()["lateral_dependencies"]["d1"][0]["consumer_node_id"] == "consumer"


def test_the_legacy_store_names_unknown_malformed_and_unsafe_runs(tmp_path):
    store = SharedStateStore(tmp_path)
    with pytest.raises(ValueError, match='Shared state for run "run-9" is unknown'):
        store.load("run-9")
    (tmp_path / ".agent-shared-state" / "run-2.json").write_text(json.dumps([1]), encoding="utf-8")
    with pytest.raises(ValueError, match='Shared state for run "run-2" is malformed'):
        store.load("run-2")
    with pytest.raises(ValueError, match="Run id"):
        store.save("../escape", RunSharedState(SUBSTRATE))
