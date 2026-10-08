# Copyright (c) 2026 David Michael Indraputra

"""Typed lateral-state payloads and provenance gates.

`StateGraph` owns the authoritative run-scoped substrate as
``GraphSharedState``. This module retains serializable payload types and the
legacy standalone container only for compatibility with pre-0.10 consumers.
Workers never exchange conversation; a consuming action may reference only a
closed exploratory discovery published to graph state (systems-design
framework, updated PDF, pp. 60 and 63).
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from ..foundations.atomic_io import replace_atomic, unique_temporary_path
from ..foundations.contracts import StrictModel
from ..foundations.identifiers import require_unique, validate_identifier
from ..foundations.json_limits import assert_json_depth


class DiscoveryKind(StrEnum):
    EXPLORATORY = "exploratory"


class SharedSubstrateSnapshot(StrictModel):
    snapshot_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    artifact_ids: list[str] = Field(default_factory=list)
    read_only: bool = True


class DiscoveryRoutingRefs(StrictModel):
    """Exact, source-backed identifiers eligible for deterministic routing."""

    requirement_ids: list[str] = Field(default_factory=list)
    signal_ids: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    schema_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> DiscoveryRoutingRefs:
        for field_name in ("requirement_ids", "signal_ids", "task_ids", "schema_ids"):
            values = getattr(self, field_name)
            if any(not value.strip() for value in values):
                raise ValueError(f"{field_name} entries must be non-empty")
            require_unique(values, f"{field_name} entries")
        return self

    def any_identifiers(self) -> set[str]:
        return set().union(
            self.requirement_ids,
            self.signal_ids,
            self.task_ids,
            self.schema_ids,
        )


class DiscoveryRouteStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    LATE_DISCOVERY = "late-discovery"


class DiscoveryRouteDecision(StrictModel):
    discovery_episode_id: str = Field(min_length=1)
    consumer_node_id: str = Field(min_length=1)
    status: DiscoveryRouteStatus
    reason: str = Field(min_length=1)
    routing_snapshot_id: str = Field(min_length=1)
    routing_policy_version: str = Field(min_length=1)


class DiscoveryRoutingIndex(StrictModel):
    """Persisted exact-reference postings for the current graph revision."""

    requirement_postings: dict[str, list[str]] = Field(default_factory=dict)
    signal_postings: dict[str, list[str]] = Field(default_factory=dict)
    task_postings: dict[str, list[str]] = Field(default_factory=dict)
    schema_postings: dict[str, list[str]] = Field(default_factory=dict)


class ExploratoryDiscovery(StrictModel):
    episode_id: str = Field(min_length=1)
    producer_node_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    snapshot_version: str = Field(min_length=1)
    source_spans: list[str] = Field(min_length=1)
    description: str = Field(min_length=1)
    payload: dict[str, Any]
    provenance_hash: str = Field(min_length=1)
    affected_refs: DiscoveryRoutingRefs = Field(default_factory=DiscoveryRoutingRefs)
    closed: bool = True
    kind: DiscoveryKind = DiscoveryKind.EXPLORATORY

    @field_validator("payload")
    @classmethod
    def payload_is_bounded(cls, payload: dict[str, Any]) -> dict[str, Any]:
        return assert_json_depth(payload, "discovery payload")


class LateralDependencyRequest(StrictModel):
    consumer_node_id: str = Field(min_length=1)
    consumer_action_id: str = Field(min_length=1)
    discovery_episode_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class SharedStateWrite(StrictModel):
    schema_version: str = "shared-state-v1"
    producer_node_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    value: dict[str, Any]
    provenance_hash: str = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def value_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return assert_json_depth(value, "shared state value")


class ProvenanceRecord(StrictModel):
    schema_version: str = "provenance-v1"
    node_id: str = Field(min_length=1)
    input_hashes: list[str] = Field(default_factory=list)
    source_snapshot_ids: list[str] = Field(default_factory=list)
    source_spans: list[str] = Field(default_factory=list)
    tool_record_hashes: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    result_schema_version: str = Field(min_length=1)
    result_hash: str = Field(min_length=1)
    hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def hash_matches_payload(self) -> ProvenanceRecord:
        expected = provenance_hash(
            self.node_id,
            self.input_hashes,
            self.source_snapshot_ids,
            self.source_spans,
            self.tool_record_hashes,
            self.artifact_ids,
            self.result_schema_version,
            self.result_hash,
        )
        if self.hash != expected:
            raise ValueError("provenance hash does not match canonical fields")
        return self


class ProvenanceGateDecision(StrictModel):
    accepted: bool
    reasons: list[str] = Field(default_factory=list)


class ProvenanceContractGate:
    """Mechanically accept only compatible result provenance at a fan-in boundary."""

    def verify(
        self,
        records: list[ProvenanceRecord],
        *,
        expected_snapshot_ids: set[str],
        required_schema_version: str,
    ) -> ProvenanceGateDecision:
        reasons: list[str] = []
        node_ids = [record.node_id for record in records]
        if len(node_ids) != len(set(node_ids)):
            reasons.append("Duplicate producer node IDs are not permitted at contract fan-in.")
        for record in records:
            if record.result_schema_version != required_schema_version:
                reasons.append(f'Node "{record.node_id}" has an unexpected result schema version.')
            if not expected_snapshot_ids.issubset(set(record.source_snapshot_ids)):
                reasons.append(
                    f'Node "{record.node_id}" lacks an expected source snapshot reference.'
                )
        return ProvenanceGateDecision(accepted=not reasons, reasons=reasons)


class RunSharedState:
    """Legacy standalone substrate; new runtimes must use ``GraphSharedState``.

    Retained for source compatibility only. It is no longer created by
    ``ControllerRuntime`` or persisted by ``HarnessCoordinator``.
    """

    def __init__(self, snapshot: SharedSubstrateSnapshot) -> None:
        self.snapshot = snapshot
        self._discoveries: dict[str, ExploratoryDiscovery] = {}
        self._writes: dict[str, SharedStateWrite] = {}
        self._consumers: dict[str, list[LateralDependencyRequest]] = {}

    def publish_discovery(self, discovery: ExploratoryDiscovery) -> None:
        if discovery.snapshot_id != self.snapshot.snapshot_id:
            raise ValueError("Discovery references an unexpected shared substrate snapshot.")
        if discovery.snapshot_version != self.snapshot.version:
            raise ValueError("Discovery references an unexpected shared substrate version.")
        if not discovery.closed:
            raise ValueError("Only closed exploratory discoveries may enter shared state.")
        if discovery.episode_id in self._discoveries:
            raise ValueError(f'Discovery episode "{discovery.episode_id}" already exists.')
        self._discoveries[discovery.episode_id] = discovery

    def request_lateral_dependency(self, request: LateralDependencyRequest) -> ExploratoryDiscovery:
        discovery = self._discoveries.get(request.discovery_episode_id)
        if discovery is None:
            raise ValueError("Lateral dependency must cite a published exploratory discovery.")
        if discovery.producer_node_id == request.consumer_node_id:
            raise ValueError("Lateral dependency must cross distinct graph nodes.")
        self._consumers.setdefault(discovery.episode_id, []).append(request)
        return discovery

    def write(self, state_write: SharedStateWrite) -> None:
        if state_write.key in self._writes:
            raise ValueError(f'Shared-state key "{state_write.key}" is immutable once written.')
        self._writes[state_write.key] = state_write

    def get_discovery(self, episode_id: str) -> ExploratoryDiscovery | None:
        return self._discoveries.get(episode_id)

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "substrate": self.snapshot.model_dump(mode="json"),
            "discoveries": [
                self._discoveries[key].model_dump(mode="json") for key in sorted(self._discoveries)
            ],
            "writes": [self._writes[key].model_dump(mode="json") for key in sorted(self._writes)],
            "lateral_dependencies": {
                key: [item.model_dump(mode="json") for item in self._consumers[key]]
                for key in sorted(self._consumers)
            },
        }

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any]) -> RunSharedState:
        state = cls(SharedSubstrateSnapshot.model_validate(payload["substrate"]))
        for discovery in payload.get("discoveries", []):
            state.publish_discovery(ExploratoryDiscovery.model_validate(discovery))
        for state_write in payload.get("writes", []):
            state.write(SharedStateWrite.model_validate(state_write))
        for requests in payload.get("lateral_dependencies", {}).values():
            for request in requests:
                state.request_lateral_dependency(LateralDependencyRequest.model_validate(request))
        return state


class SharedStateStore:
    """Legacy persistence adapter; graph snapshots now carry the substrate."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve() / ".agent-shared-state"
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, run_id: str, state: RunSharedState) -> None:
        target = self._root / f"{validate_identifier(run_id, 'Run id')}.json"
        temporary = unique_temporary_path(target)
        temporary.write_text(
            json.dumps(state.snapshot_state(), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        replace_atomic(temporary, target)

    def load(self, run_id: str) -> RunSharedState:
        target = self._root / f"{validate_identifier(run_id, 'Run id')}.json"
        if not target.is_file():
            raise ValueError(f'Shared state for run "{run_id}" is unknown.')
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f'Shared state for run "{run_id}" is malformed.')
        return RunSharedState.from_snapshot(payload)


def make_provenance_record(
    *,
    node_id: str,
    input_hashes: list[str],
    source_snapshot_ids: list[str],
    source_spans: list[str],
    tool_record_hashes: list[str],
    artifact_ids: list[str],
    result_schema_version: str,
    result: Any,
) -> ProvenanceRecord:
    result_hash = canonical_hash(result)
    digest = provenance_hash(
        node_id,
        input_hashes,
        source_snapshot_ids,
        source_spans,
        tool_record_hashes,
        artifact_ids,
        result_schema_version,
        result_hash,
    )
    return ProvenanceRecord(
        node_id=node_id,
        input_hashes=input_hashes,
        source_snapshot_ids=source_snapshot_ids,
        source_spans=source_spans,
        tool_record_hashes=tool_record_hashes,
        artifact_ids=artifact_ids,
        result_schema_version=result_schema_version,
        result_hash=result_hash,
        hash=digest,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def provenance_hash(
    node_id: str,
    input_hashes: list[str],
    source_snapshot_ids: list[str],
    source_spans: list[str],
    tool_record_hashes: list[str],
    artifact_ids: list[str],
    result_schema_version: str,
    result_hash: str,
) -> str:
    return canonical_hash(
        {
            "node_id": node_id,
            "input_hashes": sorted(input_hashes),
            "source_snapshot_ids": sorted(source_snapshot_ids),
            "source_spans": sorted(source_spans),
            "tool_record_hashes": sorted(tool_record_hashes),
            "artifact_ids": sorted(artifact_ids),
            "result_schema_version": result_schema_version,
            "result_hash": result_hash,
        }
    )
