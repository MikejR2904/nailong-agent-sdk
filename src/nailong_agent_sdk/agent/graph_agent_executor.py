# Copyright (c) 2026 David Michael Indraputra

"""Host-injected BaseAgent execution for typed graph agent nodes.

This adapter is intentionally narrow.  The host owns model, tool, verification,
and task-adaptation decisions; the graph only provides declared predecessor
results and a frozen graph-state view.  No worker transcript is copied into
another worker's context.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..foundations.contracts import AgentDefinition, AgentRunStatus, ScopedAgentTask
from ..memory.context_projection import ContextProjectionPolicy
from ..memory.episode_store import InMemoryEpisodeStore
from ..state.elastic import ELASTIC_REQUEST_TOOL_NAME
from ..state.graph_models import (
    GraphNode,
    GraphNodeExecutionContext,
    GraphNodeKind,
    GraphNodeResult,
    GraphNodeStatus,
    NodeExecutor,
)
from ..state.project_state_models import ProjectStateProjectionPolicy
from ..tools.elastic_requests import ElasticRequestBuffer, ElasticRequestToolExecutor
from ..tools.tools import ToolExecutor
from .base_agent import AgentWatchdogPolicy, PostToolHook, PreToolHook
from .elastic_context import render_elastic_instructions
from .model import AgentModel
from .runtime import AgentRuntimeServices
from .verification import VerificationGateRegistry

GraphTaskAdapter = Callable[[GraphNode, GraphNodeExecutionContext], ScopedAgentTask]
GraphModelFactory = Callable[[GraphNode, GraphNodeExecutionContext], AgentModel]
GraphToolExecutorFactory = Callable[[GraphNode, GraphNodeExecutionContext], ToolExecutor | None]
GraphContextProjectionPolicyFactory = Callable[
    [GraphNode, GraphNodeExecutionContext], ContextProjectionPolicy | None
]


@dataclass(frozen=True)
class GraphAgentBinding:
    """Host-owned binding for a graph node identity.

    Runtime factories are deliberately not serialized into graph snapshots.  A
    restarted host must re-register the same binding version and its digest is
    included in node provenance for auditability.
    """

    node_id: str
    definition: AgentDefinition
    task_adapter: GraphTaskAdapter
    model_factory: GraphModelFactory
    tool_executor_factory: GraphToolExecutorFactory | None = None
    verification_gates: VerificationGateRegistry | None = None
    pre_tool_hooks: tuple[PreToolHook, ...] = ()
    post_tool_hooks: tuple[PostToolHook, ...] = ()
    binding_version: str = "v1"
    idempotent: bool = False
    watchdog_policy: AgentWatchdogPolicy | None = None
    context_projection_policy: (
        ContextProjectionPolicy | GraphContextProjectionPolicyFactory | None
    ) = None
    project_state_projection_policy: ProjectStateProjectionPolicy | None = None
    episode_store_factory: Callable[[], InMemoryEpisodeStore] | None = None
    escalate_elastic_overflow: bool = False

    @property
    def binding_hash(self) -> str:
        payload = {
            "node_id": self.node_id,
            "identity": self.definition.identity,
            "instructions_version": self.definition.instructions.version,
            "binding_version": self.binding_version,
            "idempotent": self.idempotent,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


ElasticBindingFactory = Callable[[GraphNode, GraphNodeExecutionContext], GraphAgentBinding]


class GraphAgentExecutor:
    """Run registered BaseAgent bindings through one durable SDK service root."""

    def __init__(
        self,
        services: AgentRuntimeServices,
        bindings: Mapping[str, GraphAgentBinding],
        *,
        elastic_binding_factory: ElasticBindingFactory | None = None,
    ) -> None:
        self._services = services
        self._elastic_binding_factory = elastic_binding_factory
        self._bindings = dict(bindings)
        if len(self._bindings) != len(bindings):
            raise ValueError("Graph agent bindings must use unique node IDs")
        for node_id, binding in self._bindings.items():
            if node_id != binding.node_id:
                raise ValueError("Graph agent binding map keys must equal binding.node_id")

    def executors(self) -> dict[GraphNodeKind, NodeExecutor]:
        """Return the executor mapping for ordinary and elastic agent nodes."""

        return {
            GraphNodeKind.AGENT: self.execute,
            GraphNodeKind.ELASTIC: self.execute,
        }

    def idempotent_node_ids(self) -> set[str]:
        """Return only explicitly replay-safe bindings for recovery policy."""

        return {node_id for node_id, binding in self._bindings.items() if binding.idempotent}

    def is_replayable(self, node: GraphNode, context: GraphNodeExecutionContext) -> bool:
        """Return whether an interrupted node's binding is declared replay-safe.

        An elastic node has no binding until it first runs, so its binding is built
        from the factory exactly as execution would build it.
        """

        binding = self._bindings.get(node.node_id)
        if binding is None and node.kind is GraphNodeKind.ELASTIC:
            if self._elastic_binding_factory is None:
                return False
            binding = self._elastic_binding_factory(node, context)
            mismatch = _binding_mismatch(binding, node)
            if mismatch is not None:
                raise ValueError(mismatch)
            self._bindings[node.node_id] = binding
        return binding is not None and binding.idempotent

    async def execute(
        self,
        node: GraphNode,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        binding = self._bindings.get(node.node_id)
        if binding is None:
            resolved = self._resolve_missing_binding(node, context)
            if isinstance(resolved, GraphNodeResult):
                return resolved
            binding = resolved
        buffer: ElasticRequestBuffer | None = None
        try:
            task = binding.task_adapter(node, context)
            if node.kind is GraphNodeKind.ELASTIC:
                elastic_text = render_elastic_instructions(node, context)
                task = task.model_copy(
                    update={"instructions": f"{task.instructions}\n\n{elastic_text}"}
                )
            model = binding.model_factory(node, context)
            tool_executor = (
                binding.tool_executor_factory(node, context)
                if binding.tool_executor_factory is not None
                else None
            )
            if any(tool.name == ELASTIC_REQUEST_TOOL_NAME for tool in binding.definition.tools):
                buffer = ElasticRequestBuffer(
                    node,
                    context.elastic_capacity,
                    set(context.dependencies),
                    escalate_overflow=binding.escalate_elastic_overflow,
                    reservation=context.elastic_reservation,
                )
                tool_executor = ElasticRequestToolExecutor(tool_executor, buffer)
            projection_policy = (
                binding.context_projection_policy(node, context)
                if callable(binding.context_projection_policy)
                else binding.context_projection_policy
            )
            agent = self._services.create_agent(
                binding.definition,
                model,
                tool_executor=tool_executor,
                verification_gates=binding.verification_gates,
                pre_tool_hooks=binding.pre_tool_hooks,
                post_tool_hooks=binding.post_tool_hooks,
                watchdog_policy=binding.watchdog_policy,
                context_projection_policy=projection_policy,
                project_state_projection_policy=binding.project_state_projection_policy,
                episode_store_factory=binding.episode_store_factory,
            )
            result = await agent.run(task)
        except Exception as error:
            return GraphNodeResult(
                status=GraphNodeStatus.FAILED,
                reason=f"Graph agent invocation raised {type(error).__name__}: {error}",
                diagnostics=[f"error-type:{type(error).__name__}"],
            )
        status = _graph_status(result.status)
        queued = buffer.requests if buffer is not None else []
        spawn_requests = queued if status is GraphNodeStatus.COMPLETED else []
        payload = {
            "agent_identity": binding.definition.identity,
            "binding_hash": binding.binding_hash,
            "task_id": result.task_id,
            "status": result.status.value,
            "output": result.output,
            "reason": result.reason,
            "escalation": result.escalation.model_dump(mode="json")
            if result.escalation is not None
            else None,
            "project_state_hash": _hash_payload(result.project_state),
        }
        diagnostics = [
            f"agent-status:{result.status.value}",
            f"agent-iterations:{result.iterations}",
            f"binding-hash:{binding.binding_hash}",
        ]
        if spawn_requests:
            payload["elastic_request_ids"] = [request.request_id for request in spawn_requests]
            diagnostics.append(f"elastic-requests:{len(spawn_requests)}")
        elif queued:
            diagnostics.append(f"elastic-requests-dropped:{len(queued)}")
        return GraphNodeResult(
            status=status,
            output=result.output,
            reason=result.reason,
            diagnostics=diagnostics,
            provenance_hash=_hash_payload(payload),
            spawn_requests=spawn_requests,
        )

    def _resolve_missing_binding(
        self, node: GraphNode, context: GraphNodeExecutionContext
    ) -> GraphAgentBinding | GraphNodeResult:
        reason = f'No GraphAgentBinding is registered for node "{node.node_id}".'
        if node.kind is not GraphNodeKind.ELASTIC:
            return GraphNodeResult(status=GraphNodeStatus.FAILED, reason=reason)
        if self._elastic_binding_factory is None:
            return GraphNodeResult(
                status=GraphNodeStatus.FAILED,
                reason=f"{reason} Elastic nodes are created while the run executes, so pass "
                "elastic_binding_factory to GraphAgentExecutor to build their bindings.",
            )
        try:
            binding = self._elastic_binding_factory(node, context)
        except Exception as error:
            return GraphNodeResult(
                status=GraphNodeStatus.FAILED,
                reason=f"Elastic binding factory raised {type(error).__name__} for node "
                f'"{node.node_id}": {error}',
                diagnostics=[f"error-type:{type(error).__name__}"],
            )
        mismatch = _binding_mismatch(binding, node)
        if mismatch is not None:
            return GraphNodeResult(status=GraphNodeStatus.FAILED, reason=mismatch)
        self._bindings[node.node_id] = binding
        return binding


def _binding_mismatch(binding: GraphAgentBinding, node: GraphNode) -> str | None:
    if binding.node_id == node.node_id:
        return None
    return (
        "The elastic binding factory returned a binding for node "
        f'"{binding.node_id}" instead of "{node.node_id}".'
    )


def _graph_status(status: AgentRunStatus) -> GraphNodeStatus:
    return {
        AgentRunStatus.COMPLETED: GraphNodeStatus.COMPLETED,
        AgentRunStatus.BLOCKED: GraphNodeStatus.BLOCKED,
        AgentRunStatus.FAILED: GraphNodeStatus.FAILED,
        AgentRunStatus.CANCELLED: GraphNodeStatus.CANCELLED,
    }[status]


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
