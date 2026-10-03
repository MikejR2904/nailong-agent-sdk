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
from typing import Any

from pydantic import Field, model_validator

from ..foundations.contracts import StrictModel


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
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} entries must be unique")
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
