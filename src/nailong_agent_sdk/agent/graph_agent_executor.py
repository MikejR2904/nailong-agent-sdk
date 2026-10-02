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
from ..state.graph_models import (
    GraphNode,
    GraphNodeExecutionContext,
    GraphNodeKind,
    GraphNodeResult,
    GraphNodeStatus,
    NodeExecutor,
)
from ..state.project_state_models import ProjectStateProjectionPolicy
from ..tools.tools import ToolExecutor
from .base_agent import AgentWatchdogPolicy, PostToolHook, PreToolHook
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


class GraphAgentExecutor:
    """Run registered BaseAgent bindings through one durable SDK service root."""

    def __init__(
        self,
        services: AgentRuntimeServices,
        bindings: Mapping[str, GraphAgentBinding],
    ) -> None:
        self._services = services
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

    async def execute(
        self,
        node: GraphNode,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        binding = self._bindings.get(node.node_id)
        if binding is None:
            return GraphNodeResult(
                status=GraphNodeStatus.FAILED,
                reason=f'No GraphAgentBinding is registered for node "{node.node_id}".',
            )
        try:
            task = binding.task_adapter(node, context)
            model = binding.model_factory(node, context)
            tool_executor = (
                binding.tool_executor_factory(node, context)
                if binding.tool_executor_factory is not None
                else None
            )
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
        return GraphNodeResult(
            status=status,
            output=result.output,
            reason=result.reason,
            diagnostics=[
                f"agent-status:{result.status.value}",
                f"agent-iterations:{result.iterations}",
                f"binding-hash:{binding.binding_hash}",
            ],
            provenance_hash=_hash_payload(payload),
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
