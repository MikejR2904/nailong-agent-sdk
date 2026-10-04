# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for BaseAgent contract validation, context assembly, and task execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..agent.base_agent import AgentWatchdogPolicy, BaseAgent
from ..agent.model import ScriptedModel
from ..foundations.contracts import AgentDefinition, RuntimeOptions, ScopedAgentTask
from ..memory.context import assemble_initial_context
from ..memory.context_projection import ContextProjectionPolicy
from ..observability.telemetry_models import TelemetryActor, TelemetryAuthority, TelemetryContext
from ..state.project_state_models import ProjectStateProjectionPolicy
from ..tools.tools import InMemoryTaskToolExecutor
from ._shared import McpContext, _validation_errors

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_agent_tools(server: MCPServer, ctx: McpContext) -> None:
    @ctx.tool(server, "validate_agent_definition", exclusive=False)
    def validate_agent_definition(definition: dict[str, Any]) -> dict[str, Any]:
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

    @ctx.tool(server, "assemble_initial_context", exclusive=False)
    def assemble_initial_context_tool(
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

    @ctx.tool(server, "run_agent_task", exclusive=False)
    async def run_agent_task(
        definition: dict[str, Any],
        task: dict[str, Any],
        runtime_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one scoped BaseAgent task and return its typed result.

        Until the project owner supplies a real model identifier, runtime options
        accept only deterministic scripted turns. This does not claim model reasoning.
        """

        try:
            parsed_definition = AgentDefinition.model_validate(definition)
            parsed_task = ScopedAgentTask.model_validate(task)
            options = RuntimeOptions.model_validate(runtime_options or {})
            telemetry_context = TelemetryContext(
                run_id=parsed_task.id,
                task_id=parsed_task.id,
                agent_id=parsed_definition.identity,
            )
            ctx.telemetry.emit(
                "run.created",
                telemetry_context,
                actor=TelemetryActor(kind="system", identifier="mcp-runtime", role="runtime"),
                authority=TelemetryAuthority.DETERMINISTIC,
                status="started",
                payload={
                    "mode": options.mode,
                    "model_binding": parsed_definition.model_binding.model_dump(mode="json"),
                },
            )
            agent = BaseAgent(
                definition=parsed_definition,
                model=ScriptedModel(options.scripted_turns),
                tool_executor=InMemoryTaskToolExecutor(),
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
                project_state_store=ctx.project_states,
                telemetry=ctx.telemetry,
                telemetry_context=telemetry_context,
                audit_logs=ctx.audit_logs,
            )
            result = await agent.run(parsed_task)
            ctx.telemetry.emit(
                "run.completed" if result.status.value == "completed" else "run.terminated",
                telemetry_context,
                actor=TelemetryActor(kind="system", identifier="mcp-runtime", role="runtime"),
                authority=TelemetryAuthority.DETERMINISTIC,
                status=result.status.value,
                payload={"iterations": result.iterations, "reason": result.reason},
            )
            return {"ok": True, "result": result.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}
