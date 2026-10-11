# Copyright (c) 2026 David Michael Indraputra

"""Agent-facing queue and tool executor for elastic spawn requests."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from pydantic import ValidationError

from ..foundations.contracts import AgentFailure, ToolDefinition, ToolExecutionResult
from ..state.elastic import (
    ELASTIC_JOIN_COST,
    ELASTIC_REQUEST_TOOL_NAME,
    MAX_ELASTIC_REQUESTS_PER_RESULT,
    ElasticCapacity,
    ElasticProblem,
    ElasticRefusalCode,
    ElasticReservation,
    ElasticSpawnRequest,
    capacity_problem,
    check_spawn_request,
)
from ..state.graph_models import GraphNode
from .tools import ToolExecutor, ToolInvocationContext


class ElasticRequestRejected(Exception):
    def __init__(self, problem: ElasticProblem) -> None:
        super().__init__(problem.message)
        self.code = problem.code


class ElasticRequestBuffer:
    def __init__(
        self,
        node: GraphNode,
        capacity: ElasticCapacity | None,
        visible_dependencies: Collection[str],
        *,
        limit: int = MAX_ELASTIC_REQUESTS_PER_RESULT,
        escalate_overflow: bool = False,
        reservation: ElasticReservation | None = None,
    ) -> None:
        self._node = node
        self._capacity = capacity
        self._visible = frozenset(visible_dependencies)
        self._limit = limit
        self._escalate_overflow = escalate_overflow
        self._reservation = reservation
        self._requests: list[ElasticSpawnRequest] = []

    @property
    def requests(self) -> list[ElasticSpawnRequest]:
        return list(self._requests)

    def add(self, request: ElasticSpawnRequest) -> dict[str, Any]:
        node_id = self._node.node_id
        if len(self._requests) >= self._limit:
            raise ElasticRequestRejected(
                ElasticProblem(
                    ElasticRefusalCode.REQUEST_LIMIT_REACHED,
                    f'Node "{node_id}" already queued {len(self._requests)} elastic requests, '
                    f"which is the per-task limit of {self._limit}.",
                )
            )
        problem = check_spawn_request(
            request,
            parent_node_id=node_id,
            parent_routing_refs=self._node.routing_refs,
            visible_dependencies=self._visible,
            taken_request_ids={item.request_id for item in self._requests},
            handoff_chars_used=sum(len(item.handoff or "") for item in self._requests),
        )
        if problem is not None:
            raise ElasticRequestRejected(problem)
        overflow = self._overflow(len(self._requests) + 1)
        if overflow is not None:
            if not overflow.grantable or not self._escalate_overflow:
                raise ElasticRequestRejected(overflow)
            if self._reservation is not None:
                self._reservation.release()
        self._requests.append(request)
        return {
            "request_id": request.request_id,
            "status": "queued",
            "queued_requests": len(self._requests),
            "child_depth": self._node.elastic_depth + 1,
            "elastic_nodes_remaining_after_queue": self._remaining_after_queue(),
            "capacity_decision_required": overflow is not None,
            "runs": "after this task completes; a join node then resumes this task with the "
            "findings"
            if overflow is None
            else "only after the controller grants more elastic capacity or declines; every "
            "request this task queued and this task's dependents wait for that decision",
        }

    def _remaining_after_queue(self) -> int | None:
        if self._reservation is not None:
            return self._reservation.remaining()
        if self._capacity is None:
            return None
        return max(0, self._capacity.remaining_nodes - len(self._requests) - ELASTIC_JOIN_COST)

    def _overflow(self, requested: int) -> ElasticProblem | None:
        if self._reservation is not None:
            return self._reservation.reserve(requested)
        if self._capacity is None:
            return None
        return capacity_problem(
            node_id=self._node.node_id,
            node_depth=self._capacity.node_depth,
            max_depth=self._capacity.max_depth,
            nodes_used=self._capacity.nodes_used,
            max_nodes=self._capacity.max_nodes,
            requested=requested,
            ceiling_depth=self._capacity.ceiling_depth,
            ceiling_nodes=self._capacity.ceiling_nodes,
        )


class ElasticRequestToolExecutor:
    def __init__(self, inner: ToolExecutor | None, buffer: ElasticRequestBuffer) -> None:
        self._inner = inner
        self._buffer = buffer

    async def execute(
        self, tool: ToolDefinition, context: ToolInvocationContext
    ) -> ToolExecutionResult:
        if tool.name != ELASTIC_REQUEST_TOOL_NAME:
            if self._inner is None:
                return ToolExecutionResult(
                    status="failed",
                    error=f'No tool executor is configured for tool "{tool.name}".',
                )
            return await self._inner.execute(tool, context)
        try:
            request = ElasticSpawnRequest.model_validate(context.call.arguments)
        except ValidationError as error:
            detail = "; ".join(
                f"{'.'.join(str(part) for part in issue['loc']) or '(root)'}: {issue['msg']}"
                for issue in error.errors()
            )
            failure = AgentFailure(
                code="ELASTIC_REQUEST_INVALID",
                message=f"{ELASTIC_REQUEST_TOOL_NAME} arguments are invalid: {detail}",
                details={"tool": ELASTIC_REQUEST_TOOL_NAME},
            )
            return ToolExecutionResult(status="failed", error=failure.message, failure=failure)
        try:
            receipt = self._buffer.add(request)
        except ElasticRequestRejected as rejected:
            failure = AgentFailure(
                code=rejected.code.value,
                message=str(rejected),
                details={"tool": ELASTIC_REQUEST_TOOL_NAME, "request_id": request.request_id},
            )
            return ToolExecutionResult(status="failed", error=failure.message, failure=failure)
        return ToolExecutionResult(status="succeeded", output=receipt)
