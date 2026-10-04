# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for compiling, approving, and inspecting host-configured orchestrations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..agent.orchestrator import OrchestrationPolicy, OrchestrationRequest, Orchestrator
from ..state.planning import Plan
from ._shared import McpContext, _validation_errors

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_orchestration_tools(server: MCPServer, ctx: McpContext) -> None:
    @ctx.tool(server, "validate_plan", exclusive=False)
    def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
        """Validate a PlanTask DAG by recomputing signal-derived dependencies."""

        try:
            parsed = Plan.model_validate(plan)
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        report = ctx.plan_validator.validate(parsed)
        return {"ok": True, "report": report.model_dump(mode="json")}

    @ctx.tool(server, "start_run")
    def start_run(plan: dict[str, Any]) -> dict[str, Any]:
        """Create and persist a validated typed-DAG run in the pending/runnable state."""

        try:
            parsed = Plan.model_validate(plan)
            record = ctx.coordinator.start_run(parsed)
            return {"ok": True, "run": record.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "prepare_orchestration")
    async def prepare_orchestration(
        policy: dict[str, Any],
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """Compile selected skills, models, tools, and capabilities into an approval-gated run.

        This MCP surface intentionally accepts only durable data-only user policy.
        Real model adapters, tool callbacks, and optional Jev evaluator credentials
        remain host-local and must be registered by the embedding application.
        """

        try:
            orchestrator = Orchestrator(
                ctx.run_root,
                OrchestrationPolicy.model_validate(policy),
                controller_runtime=ctx.controller_runtime,
                telemetry=ctx.telemetry,
            )
            record = await orchestrator.prepare(OrchestrationRequest.model_validate(request))
            ctx.orchestrators[record.orchestration_id] = orchestrator
            return {"ok": True, "orchestration": record.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "submit_orchestration_for_approval")
    def submit_orchestration_for_approval(orchestration_id: str) -> dict[str, Any]:
        """Bind a compiled orchestration to a controller and present it for approval."""

        try:
            record = ctx.orchestration_for(orchestration_id).submit_for_approval(orchestration_id)
            return {"ok": True, "orchestration": record.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "approve_orchestration")
    def approve_orchestration(
        orchestration_id: str,
        approved: bool,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Record the designer's plan decision for a policy-bound orchestration."""

        try:
            record = ctx.orchestration_for(orchestration_id).approve(
                orchestration_id, approved, reason
            )
            return {"ok": True, "orchestration": record.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "get_orchestration")
    def get_orchestration(orchestration_id: str) -> dict[str, Any]:
        """Return a persisted policy, routing, assignment, and approval record."""

        try:
            record = ctx.orchestration_for(orchestration_id).get(orchestration_id)
            return {"ok": True, "orchestration": record.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "cancel_orchestration")
    def cancel_orchestration(orchestration_id: str, reason: str) -> dict[str, Any]:
        """Cancel a submitted orchestration and its bound controller/graph when present."""

        try:
            record = ctx.orchestration_for(orchestration_id).cancel(orchestration_id, reason)
            return {"ok": True, "orchestration": record.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}
