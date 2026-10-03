# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for BaseAgent contract validation, context assembly, and task execution."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..agent.base_agent import AgentWatchdogPolicy, BaseAgent
from ..foundations.contracts import AgentDefinition, RuntimeOptions, ScopedAgentTask
from ..foundations.errors import AgentSdkError
from ..memory.context import assemble_initial_context
from ..memory.context_projection import ContextProjectionPolicy, FileToolResultJournal
from ..observability.telemetry_models import TelemetryActor, TelemetryAuthority, TelemetryContext
from ..state.planning import ModelTier, PlanTask
from ..state.project_state_models import ProjectStateProjectionPolicy
from ..tools.artifacts import ArtifactStore
from ..tools.registry import HarnessExecutionContext, HarnessToolExecutor, HarnessToolRegistry
from ._shared import McpContext, _validation_errors

if TYPE_CHECKING:
    from mcp.server import MCPServer

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def register_agent_tools(server: MCPServer, ctx: McpContext) -> None:
    @server.tool(name="validate_agent_definition", structured_output=True)
    async def validate_agent_definition(definition: dict[str, Any]) -> dict[str, Any]:
        """Validate the serializable BaseAgent contract and return field-level errors."""

        try:
            parsed = AgentDefinition.model_validate(definition)
        except ValidationError as error:
            return {"ok": True, "valid": False, "errors": _validation_errors(error)}
        return {
            "ok": True,
            "valid": True,
            "definition": parsed.model_dump(mode="json"),
            "tool_names": [tool.name for tool in parsed.tools],
        }

    @server.tool(name="assemble_initial_context", structured_output=True)
    async def assemble_initial_context_tool(
        definition: dict[str, Any],
        task: dict[str, Any],
    ) -> dict[str, Any]:
        """Assemble context in deterministic BaseAgent order without model judgment."""

        try:
            parsed_definition = AgentDefinition.model_validate(definition)
            parsed_task = ScopedAgentTask.model_validate(task)
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        context = assemble_initial_context(parsed_definition, parsed_task)
        return {"ok": True, "context": context.model_dump(mode="json")}

    @server.tool(name="run_agent_task", structured_output=True)
    async def run_agent_task(
        definition: dict[str, Any],
        task: dict[str, Any],
        runtime_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one scoped BaseAgent task on its declared model and return the typed result.

        The model is the definition's ``model_binding`` (with fallbacks), built from
        the providers this server's operator configured. Tools run through the
        capability-governed harness in a workspace of their own; an approval-gated
        tool returns ``blocked`` with an ``approval_id`` to decide through
        ``submit_agent_task_approval`` and pass back in ``runtime_options``.
        """

        try:
            parsed_definition = AgentDefinition.model_validate(definition)
            parsed_task = ScopedAgentTask.model_validate(task)
            options = RuntimeOptions.model_validate(runtime_options or {})
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        try:
            model = ctx.model_resolver.resolve(parsed_definition.model_binding)
        except AgentSdkError as error:
            return {"ok": False, "errors": [{"code": error.code, "message": error.message}]}
        try:
            workspace = _task_workspace(ctx.run_root, parsed_task.id)
            journal = FileToolResultJournal(workspace)
            telemetry_context = TelemetryContext(
                run_id=parsed_task.id,
                task_id=parsed_task.id,
                agent_id=parsed_definition.identity,
            )
            actor = TelemetryActor(kind="system", identifier="mcp-runtime", role="runtime")
            ctx.telemetry.emit(
                "run.created",
                telemetry_context,
                actor=actor,
                authority=TelemetryAuthority.DETERMINISTIC,
                status="started",
                payload={
                    "role": options.role,
                    "model_binding": parsed_definition.model_binding.model_dump(mode="json"),
                },
            )
            executor = HarnessToolExecutor(
                HarnessToolRegistry(),
                HarnessExecutionContext(
                    run_id=parsed_task.id,
                    node_id=parsed_task.id,
                    role=options.role,
                    plan_task=_plan_task_for(parsed_task),
                    run_root=workspace,
                    artifacts=ArtifactStore(workspace),
                    policy=ctx.capability_policy,
                    approvals=ctx.agent_task_approvals,
                    supervisor=ctx.supervisor,
                    declared_output_paths=tuple(options.declared_output_paths),
                    approval_ids_by_capability=dict(options.approval_ids),
                    result_journal=journal,
                    search_client=ctx.search_client,
                ),
            )
            agent = BaseAgent(
                definition=parsed_definition,
                model=model,
                tool_executor=executor,
                watchdog_policy=AgentWatchdogPolicy(
                    run_deadline_seconds=options.run_deadline_seconds,
                    model_turn_timeout_seconds=options.model_turn_timeout_seconds,
                    tool_call_timeout_seconds=options.tool_call_timeout_seconds,
                    verification_timeout_seconds=options.verification_timeout_seconds,
                ),
                context_projection_policy=ContextProjectionPolicy(
                    **{
                        key: value
                        for key, value in {
                            "context_token_budget": options.context_token_budget,
                            "episode_token_budget": options.episode_token_budget,
                            "tool_result_preview_chars": options.tool_result_preview_chars,
                        }.items()
                        if value is not None
                    }
                ),
                project_state_projection_policy=ProjectStateProjectionPolicy(
                    **(
                        {"token_budget": options.project_state_token_budget}
                        if options.project_state_token_budget is not None
                        else {}
                    )
                ),
                result_journal=journal,
                project_state_store=ctx.project_states,
                telemetry=ctx.telemetry,
                telemetry_context=telemetry_context,
                audit_logs=ctx.audit_logs,
            )
            result = await agent.run(parsed_task)
            ctx.telemetry.emit(
                "run.completed" if result.status.value == "completed" else "run.terminated",
                telemetry_context,
                actor=actor,
                authority=TelemetryAuthority.DETERMINISTIC,
                status=result.status.value,
                payload={"iterations": result.iterations, "reason": result.reason},
            )
            return {
                "ok": True,
                "result": result.model_dump(mode="json"),
                "workspace": str(workspace),
            }
        except AgentSdkError as error:
            return {"ok": False, "errors": [{"code": error.code, "message": error.message}]}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="list_agent_task_approvals", structured_output=True)
    async def list_agent_task_approvals(task_id: str | None = None) -> dict[str, Any]:
        """List approval requests raised by agent tasks (all, or one task's)."""

        requests = ctx.agent_task_approvals.list(task_id)
        return {"ok": True, "approvals": [item.model_dump(mode="json") for item in requests]}

    @server.tool(name="submit_agent_task_approval", structured_output=True)
    async def submit_agent_task_approval(
        approval_id: str, approved: bool, reason: str | None = None
    ) -> dict[str, Any]:
        """Record an operator decision on an agent-task approval request."""

        try:
            decided = ctx.agent_task_approvals.submit(approval_id, approved, reason)
        except ValueError as error:
            return {"ok": False, "errors": [{"message": str(error)}]}
        return {"ok": True, "approval": decided.model_dump(mode="json")}


def _task_workspace(run_root: Path, task_id: str) -> Path:
    if not _SAFE_ID.fullmatch(task_id):
        raise AgentSdkError(
            "TASK_ID_INVALID",
            "Task ids used as workspace names may contain only letters, digits, '.', '_' "
            f"and '-' (got {task_id!r}).",
        )
    workspace = (run_root / "agent-tasks" / task_id).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _plan_task_for(task: ScopedAgentTask) -> PlanTask:
    locked = task.locked_interface
    return PlanTask(
        task_id=task.id,
        scope=task.scope.label,
        locked_interface=locked if isinstance(locked, dict) else {"value": locked},
        instructions=task.instructions,
        acceptance_criteria="; ".join(task.acceptance_criteria),
        model_tier=ModelTier.STANDARD,
    )
