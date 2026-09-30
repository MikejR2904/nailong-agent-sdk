# Copyright (c) 2026 David Michael Indraputra

"""Optional LangGraph adapters that preserve SDK-owned authority and evidence.

LangGraph may orchestrate sanitized transitions or run inside a declared SDK graph
node.  It never receives SDK capabilities, raw audit transcripts, or authority
to dispatch a governed tool.  The host ledger remains the canonical audit record.

LangGraph documents graph persistence and interrupt/resume semantics at:
https://docs.langchain.com/oss/python/langgraph/persistence and
https://docs.langchain.com/oss/python/langgraph/interrupts
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import Field

from ..agent.base_agent import PostToolHook, PreToolHook
from ..agent.model import AgentModel
from ..agent.runtime import AgentRuntimeServices
from ..agent.verification import VerificationGateRegistry
from ..foundations.contracts import AgentDefinition, AgentRunStatus, ScopedAgentTask, StrictModel
from ..state.graph_models import GraphNode, GraphNodeExecutionContext, GraphNodeResult, NodeExecutor
from ..tools.tools import ToolExecutor
from ._utils import canonical_digest, require_optional_module
from .contracts import (
    InteropOperationStatus,
    InteropReceipt,
    InteropRunEnvelope,
    assert_sanitized_interop_value,
)
from .receipts import InteropReceiptSink

LangGraphTaskAdapter = Callable[[InteropRunEnvelope], ScopedAgentTask]
LangGraphModelFactory = Callable[[InteropRunEnvelope], AgentModel]
LangGraphToolExecutorFactory = Callable[[InteropRunEnvelope], ToolExecutor | None]


@dataclass(frozen=True)
class LangGraphSdkNodeBinding:
    """Host-owned BaseAgent binding for exactly one LangGraph node."""

    node_name: str
    definition: AgentDefinition
    task_adapter: LangGraphTaskAdapter
    model_factory: LangGraphModelFactory
    tool_executor_factory: LangGraphToolExecutorFactory | None = None
    verification_gates: VerificationGateRegistry | None = None
    pre_tool_hooks: tuple[PreToolHook, ...] = ()
    post_tool_hooks: tuple[PostToolHook, ...] = ()
    binding_version: str = "v1"

    @property
    def binding_digest(self) -> str:
        return canonical_digest(
            {
                "node_name": self.node_name,
                "agent_identity": self.definition.identity,
                "instruction_version": self.definition.instructions.version,
                "binding_version": self.binding_version,
            }
        )


class LangGraphSdkNode:
    """One callable LangGraph node that invokes a bounded host-owned SDK agent.

    The caller injects an ``envelope_factory`` rather than allowing this adapter to
    serialize the full LangGraph state.  Its return value is deliberately a compact
    transition receipt suitable for a LangGraph reducer, not an AgentResult transcript.
    """

    def __init__(
        self,
        services: AgentRuntimeServices,
        binding: LangGraphSdkNodeBinding,
        envelope_factory: Callable[[Mapping[str, Any]], InteropRunEnvelope],
        *,
        receipt_sink: InteropReceiptSink,
    ) -> None:
        self._services = services
        self._binding = binding
        self._envelope_factory = envelope_factory
        self._receipt_sink = receipt_sink

    async def __call__(self, state: Mapping[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        envelope = self._envelope_factory(state)
        self._validate_envelope(envelope)
        try:
            task = self._binding.task_adapter(envelope)
            model = self._binding.model_factory(envelope)
            tool_executor = (
                self._binding.tool_executor_factory(envelope)
                if self._binding.tool_executor_factory is not None
                else None
            )
            agent = self._services.create_agent(
                self._binding.definition,
                model,
                tool_executor=tool_executor,
                verification_gates=self._binding.verification_gates,
                pre_tool_hooks=self._binding.pre_tool_hooks,
                post_tool_hooks=self._binding.post_tool_hooks,
            )
            result = await agent.run(task)
            result_digest = canonical_digest(
                {
                    "status": result.status.value,
                    "reason": result.reason,
                    "output": result.output,
                    "project_state": result.project_state,
                }
            )
            status = _interop_status(result.status)
            transition = {
                "schema_version": "agent-sdk-langgraph-transition-v1",
                "run_id": envelope.run_id,
                "node_name": self._binding.node_name,
                "status": result.status.value,
                "remaining_turn_budget": max(0, envelope.remaining_turn_budget - result.iterations),
                "binding_digest": self._binding.binding_digest,
                "result_digest": result_digest,
                "project_state_digest": canonical_digest(result.project_state or {}),
            }
            await self._emit_receipt(
                envelope,
                status,
                result_digest=result_digest,
                duration_ms=_duration_ms(started),
            )
            return {"agent_sdk_transition": transition}
        except Exception as error:
            result_digest = canonical_digest({"error_type": type(error).__name__})
            await self._emit_receipt(
                envelope,
                InteropOperationStatus.FAILED,
                result_digest=result_digest,
                duration_ms=_duration_ms(started),
                detail_code=f"sdk-node-{type(error).__name__.lower()}",
            )
            raise

    def _validate_envelope(self, envelope: InteropRunEnvelope) -> None:
        if envelope.remaining_turn_budget <= 0:
            raise RuntimeError(
                "Host turn budget is exhausted before LangGraph SDK node invocation."
            )
        assert_sanitized_interop_value(envelope.projection)

    async def _emit_receipt(
        self,
        envelope: InteropRunEnvelope,
        status: InteropOperationStatus,
        *,
        result_digest: str,
        duration_ms: float,
        detail_code: str | None = None,
    ) -> None:
        receipt = InteropReceipt(
            provider="langgraph",
            operation="sdk-agent-node",
            status=status,
            run_id=envelope.run_id,
            projection_digest=envelope.projection_digest,
            result_digest=result_digest,
            external_checkpoint_id=_optional_string(envelope.projection.get("checkpoint_id")),
            external_parent_checkpoint_id=_optional_string(
                envelope.projection.get("parent_checkpoint_id")
            ),
            duration_ms=duration_ms,
            detail_code=detail_code,
        )
        emitted = self._receipt_sink(receipt)
        if inspect.isawaitable(emitted):
            await emitted


class LangGraphApprovalChallenge(StrictModel):
    """Display-safe, resumable approval payload; not an approval decision itself."""

    challenge_id: str = Field(min_length=1)
    action_digest: str = Field(min_length=64, max_length=64)
    policy_version: str = Field(min_length=1)
    expires_at_utc: str = Field(min_length=1)
    display_summary: str = Field(min_length=1, max_length=2_000)
    idempotency_key: str = Field(min_length=1)


def langgraph_interrupt_payload(challenge: LangGraphApprovalChallenge) -> dict[str, Any]:
    """Return the only payload safe to pass to LangGraph ``interrupt``.

    The application must independently validate the challenge, expiry, action digest,
    actor, and idempotency key in the SDK approval registry after resume.  The helper
    intentionally cannot execute or approve an action.
    """

    return challenge.model_dump(mode="json")


class AsyncLangGraphRunnable(Protocol):
    """Small structural protocol shared by a compiled LangGraph graph/runnable."""

    async def ainvoke(
        self,
        input: Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
    ) -> Any: ...


LangGraphInputProjector = Callable[[GraphNode, GraphNodeExecutionContext], Mapping[str, Any]]
LangGraphOutputReducer = Callable[[Any, GraphNode, GraphNodeExecutionContext], GraphNodeResult]


class LangGraphNodeExecutor:
    """Adapt a host-owned compiled LangGraph runnable to the SDK ``NodeExecutor`` protocol.

    The projector owns the projection.  The reducer owns output validation and only
    returns a typed SDK graph result.  This prevents arbitrary framework state from
    entering the SDK graph or bypassing its graph-state authority.
    """

    def __init__(
        self,
        runnable: AsyncLangGraphRunnable,
        input_projector: LangGraphInputProjector,
        output_reducer: LangGraphOutputReducer,
        *,
        config_factory: (
            Callable[[GraphNode, GraphNodeExecutionContext], Mapping[str, Any] | None] | None
        ) = None,
    ) -> None:
        self._runnable = runnable
        self._input_projector = input_projector
        self._output_reducer = output_reducer
        self._config_factory = config_factory

    async def execute(
        self,
        node: GraphNode,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        projected = dict(self._input_projector(node, context))
        assert_sanitized_interop_value(projected)
        configuration = self._config_factory(node, context) if self._config_factory else None
        if configuration is not None:
            assert_sanitized_interop_value(dict(configuration))
        output = await self._runnable.ainvoke(projected, configuration)
        result = self._output_reducer(output, node, context)
        if not isinstance(result, GraphNodeResult):
            raise TypeError("LangGraph output reducer must return GraphNodeResult.")
        return result

    def as_node_executor(self) -> NodeExecutor:
        return self.execute


def build_langgraph_state_graph(
    state_schema: type[Any],
    node_name: str,
    sdk_node: LangGraphSdkNode,
) -> Any:
    """Build the smallest optional LangGraph graph around one SDK node.

    Import is lazy so base SDK users do not install LangGraph.  Applications needing
    richer graph topology should compose ``LangGraphSdkNode`` into their own graph and
    retain the same sanitized-envelope rule.
    """

    module = require_optional_module("langgraph.graph", "langgraph")
    builder = module.StateGraph(state_schema)
    builder.add_node(node_name, sdk_node)
    builder.add_edge(module.START, node_name)
    builder.add_edge(node_name, module.END)
    return builder.compile()


def _interop_status(status: AgentRunStatus) -> InteropOperationStatus:
    return {
        AgentRunStatus.COMPLETED: InteropOperationStatus.SUCCEEDED,
        AgentRunStatus.BLOCKED: InteropOperationStatus.ESCALATED,
        AgentRunStatus.FAILED: InteropOperationStatus.FAILED,
        AgentRunStatus.CANCELLED: InteropOperationStatus.REJECTED,
    }[status]


def _duration_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1_000


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
