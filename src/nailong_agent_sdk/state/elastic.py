# Copyright (c) 2026 David Michael Indraputra

"""Typed elastic-node contracts: spawn requests, node specs, spawn records, and admission checks."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field, ValidationInfo, field_validator, model_validator

from ..foundations.contracts import StrictModel
from ..foundations.identifiers import require_unique, validate_identifier
from .shared_state import DiscoveryRoutingRefs

ELASTIC_REQUEST_TOOL_NAME = "request_elastic_node"
MAX_ELASTIC_DEPTH_LIMIT = 8
MAX_ELASTIC_NODES_LIMIT = 256
DEFAULT_MAX_ELASTIC_DEPTH = 1
DEFAULT_MAX_ELASTIC_NODES = 3
MAX_ELASTIC_REQUESTS_PER_RESULT = 32
MAX_ELASTIC_DEPENDENCIES = 64
ELASTIC_JOIN_COST = 1


class ElasticRefusalCode(StrEnum):
    REQUEST_ID_DUPLICATE = "ELASTIC_REQUEST_ID_DUPLICATE"
    DEPENDENCY_NOT_VISIBLE = "ELASTIC_DEPENDENCY_NOT_VISIBLE"
    ROUTING_REFS_NOT_INHERITED = "ELASTIC_ROUTING_REFS_NOT_INHERITED"
    NODE_ID_EXISTS = "ELASTIC_NODE_ID_EXISTS"
    NODE_CAP_REACHED = "ELASTIC_NODE_CAP_REACHED"
    DEPTH_CAP_REACHED = "ELASTIC_DEPTH_CAP_REACHED"
    NODE_CEILING_REACHED = "ELASTIC_NODE_CEILING_REACHED"
    DEPTH_CEILING_REACHED = "ELASTIC_DEPTH_CEILING_REACHED"
    BATCH_DECLINED = "ELASTIC_BATCH_DECLINED"
    REQUEST_LIMIT_REACHED = "ELASTIC_REQUEST_LIMIT_REACHED"


class ElasticSpawnRequest(StrictModel):
    request_id: str
    scope: str = Field(min_length=1, max_length=2_000)
    instructions: str = Field(min_length=1, max_length=8_000)
    reason: str = Field(min_length=1, max_length=2_000)
    dependencies: list[str] = Field(default_factory=list, max_length=MAX_ELASTIC_DEPENDENCIES)
    routing_refs: DiscoveryRoutingRefs = Field(default_factory=DiscoveryRoutingRefs)

    @field_validator("request_id")
    @classmethod
    def request_id_is_an_identifier(cls, value: str) -> str:
        return validate_identifier(value, "Elastic request_id")

    @field_validator("scope", "instructions", "reason")
    @classmethod
    def text_is_not_blank(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return value

    @field_validator("dependencies")
    @classmethod
    def dependencies_are_unique_and_named(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("dependencies entries must be non-empty")
        require_unique(value, "dependencies")
        return value


class ElasticNodeRole(StrEnum):
    CHILD = "child"
    JOIN = "join"


class ElasticNodeSpec(StrictModel):
    role: ElasticNodeRole
    parent_node_id: str = Field(min_length=1)
    root_node_id: str = Field(min_length=1)
    request: ElasticSpawnRequest | None = None
    joins: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def spec_matches_role(self) -> ElasticNodeSpec:
        require_unique(self.joins, "joins")
        if self.role is ElasticNodeRole.CHILD:
            if self.request is None:
                raise ValueError("a child node spec requires the request that created it")
            if self.joins:
                raise ValueError("a child node spec cannot list joined nodes")
        elif self.request is not None:
            raise ValueError("a join node spec cannot carry a request")
        return self


class GraphSpawnStatus(StrEnum):
    ACCEPTED = "accepted"
    REFUSED = "refused"
    DEFERRED = "deferred"
    DISCARDED = "discarded"


class GraphSpawnRecord(StrictModel):
    sequence: int = Field(ge=1)
    event_sequence: int = Field(ge=0)
    parent_node_id: str = Field(min_length=1)
    request: ElasticSpawnRequest
    status: GraphSpawnStatus
    depth: int = Field(ge=1)
    child_node_id: str | None = None
    join_node_id: str | None = None
    code: ElasticRefusalCode | None = None
    reason: str | None = None
    grant_sequence: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def fields_match_status(self) -> GraphSpawnRecord:
        has_outcome = self.code is not None and bool(self.reason)
        if self.status is GraphSpawnStatus.ACCEPTED:
            if self.child_node_id is None or self.join_node_id is None:
                raise ValueError("an accepted spawn record requires child_node_id and join_node_id")
            if self.code is not None or self.reason is not None:
                raise ValueError("an accepted spawn record cannot carry a refusal code or reason")
        elif self.status is GraphSpawnStatus.REFUSED:
            if not has_outcome:
                raise ValueError("a refused spawn record requires a code and a reason")
            if self.child_node_id is not None or self.join_node_id is not None:
                raise ValueError("a refused spawn record cannot name a child or join node")
        else:
            label = self.status.value
            if not has_outcome:
                raise ValueError(f"a {label} spawn record requires a code and a reason")
            if self.join_node_id is None:
                raise ValueError(f"a {label} spawn record requires the held join_node_id")
            if self.child_node_id is not None:
                raise ValueError(f"a {label} spawn record cannot name a child node")
        return self


class GraphCapacityGrant(StrictModel):
    sequence: int = Field(ge=1)
    previous_max_depth: int = Field(ge=0)
    max_depth: int = Field(ge=0)
    previous_max_nodes: int = Field(ge=0)
    max_nodes: int = Field(ge=0)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def grant_only_raises(self) -> GraphCapacityGrant:
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        if self.max_depth < self.previous_max_depth or self.max_nodes < self.previous_max_nodes:
            raise ValueError(
                "a capacity grant cannot lower a cap: max_elastic_depth "
                f"{self.previous_max_depth} to {self.max_depth}, max_elastic_nodes "
                f"{self.previous_max_nodes} to {self.max_nodes}"
            )
        if self.max_depth == self.previous_max_depth and self.max_nodes == self.previous_max_nodes:
            raise ValueError(
                "a capacity grant must raise at least one cap, but both stay at "
                f"max_elastic_depth {self.max_depth} and max_elastic_nodes {self.max_nodes}"
            )
        return self


class ElasticCapacity(StrictModel):
    node_depth: int = Field(ge=0)
    max_depth: int = Field(ge=0)
    nodes_used: int = Field(ge=0)
    max_nodes: int = Field(ge=0)
    ceiling_depth: int = Field(default=MAX_ELASTIC_DEPTH_LIMIT, ge=0, le=MAX_ELASTIC_DEPTH_LIMIT)
    ceiling_nodes: int = Field(default=MAX_ELASTIC_NODES_LIMIT, ge=0, le=MAX_ELASTIC_NODES_LIMIT)

    @property
    def remaining_nodes(self) -> int:
        return max(0, self.max_nodes - self.nodes_used)

    @property
    def remaining_requests(self) -> int:
        return max(0, self.remaining_nodes - ELASTIC_JOIN_COST)

    @property
    def depth_available(self) -> bool:
        return self.node_depth + 1 <= self.max_depth


@dataclass(frozen=True)
class ElasticProblem:
    code: ElasticRefusalCode
    message: str
    grantable: bool = True


@dataclass(frozen=True)
class ElasticReservation:
    reserve: Callable[[int], ElasticProblem | None]
    release: Callable[[], None]
    remaining: Callable[[], int]


def elastic_child_id(parent_node_id: str, request_id: str) -> str:
    return f"elastic:{parent_node_id}:{request_id}"


def elastic_join_id(parent_node_id: str) -> str:
    return f"join:{parent_node_id}"


def validate_elastic_caps(
    max_depth: int,
    max_nodes: int,
    ceiling_depth: int = MAX_ELASTIC_DEPTH_LIMIT,
    ceiling_nodes: int = MAX_ELASTIC_NODES_LIMIT,
) -> None:
    if not 0 <= ceiling_depth <= MAX_ELASTIC_DEPTH_LIMIT:
        raise ValueError(
            f"elastic_depth_ceiling must be between 0 and {MAX_ELASTIC_DEPTH_LIMIT}, "
            f"got {ceiling_depth}."
        )
    if not 0 <= ceiling_nodes <= MAX_ELASTIC_NODES_LIMIT:
        raise ValueError(
            f"elastic_nodes_ceiling must be between 0 and {MAX_ELASTIC_NODES_LIMIT}, "
            f"got {ceiling_nodes}."
        )
    if not 0 <= max_depth <= MAX_ELASTIC_DEPTH_LIMIT:
        raise ValueError(
            f"max_elastic_depth must be between 0 and {MAX_ELASTIC_DEPTH_LIMIT}, got {max_depth}."
        )
    if not 0 <= max_nodes <= MAX_ELASTIC_NODES_LIMIT:
        raise ValueError(
            f"max_elastic_nodes must be between 0 and {MAX_ELASTIC_NODES_LIMIT}, got {max_nodes}."
        )
    if max_depth > ceiling_depth:
        raise ValueError(
            f"max_elastic_depth {max_depth} is above the ceiling {ceiling_depth} of this run."
        )
    if max_nodes > ceiling_nodes:
        raise ValueError(
            f"max_elastic_nodes {max_nodes} is above the ceiling {ceiling_nodes} of this run."
        )


def _quoted(values: Collection[str]) -> str:
    return ", ".join(f'"{value}"' for value in sorted(values))


def _widened_refs(
    claimed: DiscoveryRoutingRefs, held: DiscoveryRoutingRefs
) -> dict[str, list[str]]:
    widened: dict[str, list[str]] = {}
    for field_name in ("requirement_ids", "signal_ids", "task_ids", "schema_ids"):
        extra = sorted(set(getattr(claimed, field_name)) - set(getattr(held, field_name)))
        if extra:
            widened[field_name] = extra
    return widened


def check_spawn_request(
    request: ElasticSpawnRequest,
    *,
    parent_node_id: str,
    parent_routing_refs: DiscoveryRoutingRefs,
    visible_dependencies: Collection[str],
    taken_request_ids: Collection[str],
) -> ElasticProblem | None:
    if request.request_id in taken_request_ids:
        return ElasticProblem(
            ElasticRefusalCode.REQUEST_ID_DUPLICATE,
            f'Request id "{request.request_id}" was already used by an earlier request of node '
            f'"{parent_node_id}".',
        )
    visible = set(visible_dependencies) | {parent_node_id}
    hidden = set(request.dependencies) - visible
    if hidden:
        known = set(visible_dependencies)
        seen = f"can see {_quoted(known)}" if known else "can see no dependencies"
        return ElasticProblem(
            ElasticRefusalCode.DEPENDENCY_NOT_VISIBLE,
            f'Request "{request.request_id}" gives its child dependencies {_quoted(hidden)}, but '
            f'node "{parent_node_id}" {seen}; a child may only depend on what its parent can '
            "already see.",
        )
    widened = _widened_refs(request.routing_refs, parent_routing_refs)
    if widened:
        detail = "; ".join(f"{name} {', '.join(values)}" for name, values in widened.items())
        return ElasticProblem(
            ElasticRefusalCode.ROUTING_REFS_NOT_INHERITED,
            f'Request "{request.request_id}" claims routing references that node '
            f'"{parent_node_id}" does not hold ({detail}); a child may only narrow its '
            "parent's references.",
        )
    return None


def capacity_problem(
    *,
    node_id: str,
    node_depth: int,
    max_depth: int,
    nodes_used: int,
    max_nodes: int,
    requested: int,
    reserved: int = 0,
    ceiling_depth: int = MAX_ELASTIC_DEPTH_LIMIT,
    ceiling_nodes: int = MAX_ELASTIC_NODES_LIMIT,
) -> ElasticProblem | None:
    child_depth = node_depth + 1
    if child_depth > ceiling_depth:
        return ElasticProblem(
            ElasticRefusalCode.DEPTH_CEILING_REACHED,
            f'Node "{node_id}" is at elastic depth {node_depth}, so its children would be at '
            f"depth {child_depth}, above the ceiling max_elastic_depth {ceiling_depth}, which "
            "no capacity grant can raise.",
            grantable=False,
        )
    if child_depth > max_depth:
        return ElasticProblem(
            ElasticRefusalCode.DEPTH_CAP_REACHED,
            f'Node "{node_id}" is at elastic depth {node_depth} and the plan allows '
            f"max_elastic_depth {max_depth}, so its children would be at depth {child_depth}.",
        )
    needed = requested + ELASTIC_JOIN_COST
    if nodes_used + needed > ceiling_nodes:
        return ElasticProblem(
            ElasticRefusalCode.NODE_CEILING_REACHED,
            f'Elastic node capacity for node "{node_id}" would exceed the ceiling '
            f"max_elastic_nodes {ceiling_nodes}, which no capacity grant can raise: {requested} "
            f"requested plus the join node, {nodes_used} already used.",
            grantable=False,
        )
    if nodes_used + reserved + needed > max_nodes:
        held = f", {reserved} of them held by tasks running at the same time" if reserved else ""
        return ElasticProblem(
            ElasticRefusalCode.NODE_CAP_REACHED,
            f'Elastic node capacity is exhausted for node "{node_id}": {requested} requested '
            f"plus the join node, {nodes_used + reserved} already used{held}, "
            f"max_elastic_nodes {max_nodes}.",
        )
    return None
