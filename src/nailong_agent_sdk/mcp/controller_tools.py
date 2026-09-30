# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for the deterministic vertical controller: dispatch, results, and provenance."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..state.graph_models import GraphNodeResult
from ..state.orchestration_models import ComplexityRoutingRules, GapMetadata, SkillToolProfile
from ..state.planning import Plan
from ..state.shared_state import (
    ExploratoryDiscovery,
    LateralDependencyRequest,
    ProvenanceRecord,
    SharedSubstrateSnapshot,
)
from ._shared import McpContext, _validation_errors

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_controller_tools(server: MCPServer, ctx: McpContext) -> None:
    @server.tool(name="create_controller", structured_output=True)
    async def create_controller(
        snapshot: dict[str, Any],
        profile: dict[str, Any],
        routing_rules: dict[str, Any],
        gap_metadata: dict[str, Any],
        max_repair_attempts: int = 1,
    ) -> dict[str, Any]:
        """Create a deterministic vertical controller bound to a read-only source snapshot."""

        try:
            record = ctx.controller_runtime.create_controller(
                SharedSubstrateSnapshot.model_validate(snapshot),
                SkillToolProfile.model_validate(profile),
                ComplexityRoutingRules.model_validate(routing_rules),
                GapMetadata.model_validate(gap_metadata),
                max_repair_attempts=max_repair_attempts,
            )
            return {"ok": True, "controller": record.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="submit_controller_plan", structured_output=True)
    async def submit_controller_plan(controller_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        """Present a deterministically validated plan upward for designer approval."""

        try:
            record = ctx.controller_runtime.submit_plan(controller_id, Plan.model_validate(plan))
            return {"ok": True, "controller": record.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="approve_controller_plan", structured_output=True)
    async def approve_controller_plan(
        controller_id: str,
        approved: bool,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Record the designer's explicit plan decision before downward dispatch."""

        try:
            record = ctx.controller_runtime.approve_plan(controller_id, approved, reason)
            return {"ok": True, "controller": record.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="dispatch_controller", structured_output=True)
    async def dispatch_controller(controller_id: str) -> dict[str, Any]:
        """Create the approved graph run and immutable lateral shared substrate."""

        try:
            controller, run = ctx.controller_runtime.dispatch(controller_id)
            return {
                "ok": True,
                "controller": controller.model_dump(mode="json"),
                "run": run.model_dump(mode="json"),
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="record_controller_node_result", structured_output=True)
    async def record_controller_node_result(
        controller_id: str,
        node_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit a typed worker result into the controller-owned graph state."""

        try:
            run = ctx.controller_runtime.record_node_result(
                controller_id, node_id, GraphNodeResult.model_validate(result)
            )
            return {"ok": True, "run": run.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="publish_exploratory_discovery", structured_output=True)
    async def publish_exploratory_discovery(
        controller_id: str,
        discovery: dict[str, Any],
    ) -> dict[str, Any]:
        """Publish one closed exploratory discovery to the run-scoped substrate."""

        try:
            record = ctx.controller_runtime.publish_discovery(
                controller_id, ExploratoryDiscovery.model_validate(discovery)
            )
            return {"ok": True, "controller": record.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="request_lateral_dependency", structured_output=True)
    async def request_lateral_dependency(
        controller_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach a consumer action to a producer's closed exploratory discovery."""

        try:
            run = ctx.controller_runtime.request_lateral_dependency(
                controller_id, LateralDependencyRequest.model_validate(request)
            )
            return {"ok": True, "run": run.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="get_controller_state", structured_output=True)
    async def get_controller_state(controller_id: str) -> dict[str, Any]:
        """Return the durable user-visible vertical controller state and event log."""

        try:
            return {
                "ok": True,
                "controller": ctx.controller_runtime.get_controller(controller_id).model_dump(
                    mode="json"
                ),
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="get_shared_state", structured_output=True)
    async def get_shared_state(controller_id: str) -> dict[str, Any]:
        """Return typed lateral discoveries and writes without worker transcripts."""

        try:
            return {"ok": True, "shared_state": ctx.controller_runtime.shared_state(controller_id)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="verify_provenance_contract", structured_output=True)
    async def verify_provenance_contract(
        controller_id: str,
        records: list[dict[str, Any]],
        required_schema_version: str,
    ) -> dict[str, Any]:
        """Mechanically check typed producer provenance at a graph fan-in boundary."""

        try:
            decision = ctx.controller_runtime.verify_provenance_contract(
                controller_id,
                [ProvenanceRecord.model_validate(record) for record in records],
                required_schema_version,
            )
            return {"ok": True, "decision": decision.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="record_controller_stage_failure", structured_output=True)
    async def record_controller_stage_failure(
        controller_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Request one bounded repair or escalate after the configured repair cap."""

        try:
            record = ctx.controller_runtime.record_stage_failure(controller_id, reason)
            return {"ok": True, "controller": record.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="complete_controller", structured_output=True)
    async def complete_controller(controller_id: str) -> dict[str, Any]:
        """Record the typed completion of a vertical controller run."""

        try:
            record = ctx.controller_runtime.complete(controller_id)
            return {"ok": True, "controller": record.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="cancel_controller", structured_output=True)
    async def cancel_controller(controller_id: str, reason: str) -> dict[str, Any]:
        """Cancel the active graph run and its vertical controller record."""

        try:
            record = ctx.controller_runtime.cancel(controller_id, reason)
            return {"ok": True, "controller": record.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}
