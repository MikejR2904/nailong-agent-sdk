# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for the deterministic vertical controller: dispatch, results, and provenance."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..state.elastic import MAX_ELASTIC_DEPTH_LIMIT, MAX_ELASTIC_NODES_LIMIT
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
    @ctx.tool(server, "create_controller")
    def create_controller(
        snapshot: dict[str, Any],
        profile: dict[str, Any],
        routing_rules: dict[str, Any],
        gap_metadata: dict[str, Any],
        max_repair_attempts: int = 1,
        elastic_depth_ceiling: int = MAX_ELASTIC_DEPTH_LIMIT,
        elastic_nodes_ceiling: int = MAX_ELASTIC_NODES_LIMIT,
    ) -> dict[str, Any]:
        """Create a deterministic vertical controller bound to a read-only source snapshot."""

        try:
            record = ctx.controller_runtime.create_controller(
                SharedSubstrateSnapshot.model_validate(snapshot),
                SkillToolProfile.model_validate(profile),
                ComplexityRoutingRules.model_validate(routing_rules),
                GapMetadata.model_validate(gap_metadata),
                max_repair_attempts=max_repair_attempts,
                elastic_depth_ceiling=elastic_depth_ceiling,
                elastic_nodes_ceiling=elastic_nodes_ceiling,
            )
            return {"ok": True, "controller": record.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "submit_controller_plan")
    def submit_controller_plan(controller_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        """Present a deterministically validated plan upward for designer approval."""

        try:
            record = ctx.controller_runtime.submit_plan(controller_id, Plan.model_validate(plan))
            return {"ok": True, "controller": record.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "approve_controller_plan")
    def approve_controller_plan(
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

    @ctx.tool(server, "dispatch_controller")
    def dispatch_controller(controller_id: str) -> dict[str, Any]:
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

    @ctx.tool(server, "record_controller_node_result")
    def record_controller_node_result(
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

    @ctx.tool(server, "publish_exploratory_discovery")
    def publish_exploratory_discovery(
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

    @ctx.tool(server, "request_lateral_dependency")
    def request_lateral_dependency(
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

    @ctx.tool(server, "grant_elastic_capacity")
    def grant_elastic_capacity(
        controller_id: str,
        reason: str,
        max_elastic_depth: int | None = None,
        max_elastic_nodes: int | None = None,
    ) -> dict[str, Any]:
        """Raise a run's elastic caps by a recorded decision and run deferred requests that fit."""

        try:
            run = ctx.controller_runtime.grant_elastic_capacity(
                controller_id,
                max_elastic_depth=max_elastic_depth,
                max_elastic_nodes=max_elastic_nodes,
                reason=reason,
            )
            return {"ok": True, "run": run.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "decline_elastic_requests")
    def decline_elastic_requests(
        controller_id: str,
        parent_node_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Discard one node's deferred elastic requests and release the dependents they held."""

        try:
            run = ctx.controller_runtime.decline_elastic_requests(
                controller_id, parent_node_id, reason
            )
            return {"ok": True, "run": run.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "get_controller_state")
    def get_controller_state(controller_id: str) -> dict[str, Any]:
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

    @ctx.tool(server, "get_shared_state")
    def get_shared_state(controller_id: str) -> dict[str, Any]:
        """Return typed lateral discoveries and writes without worker transcripts."""

        try:
            return {"ok": True, "shared_state": ctx.controller_runtime.shared_state(controller_id)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "verify_provenance_contract")
    def verify_provenance_contract(
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

    @ctx.tool(server, "record_controller_stage_failure")
    def record_controller_stage_failure(
        controller_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Request one bounded repair or escalate after the configured repair cap."""

        try:
            record = ctx.controller_runtime.record_stage_failure(controller_id, reason)
            return {"ok": True, "controller": record.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "complete_controller")
    def complete_controller(controller_id: str) -> dict[str, Any]:
        """Record the typed completion of a vertical controller run."""

        try:
            record = ctx.controller_runtime.complete(controller_id)
            return {"ok": True, "controller": record.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "reconcile_controller")
    def reconcile_controller(controller_id: str) -> dict[str, Any]:
        """Re-derive the project state from the controller's graph run results and cancel the
        controller if its run was cancelled, after an operation that failed between writes."""

        try:
            report = ctx.controller_runtime.reconcile(controller_id)
            return {"ok": True, "report": report.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "cancel_controller")
    def cancel_controller(controller_id: str, reason: str) -> dict[str, Any]:
        """Cancel the active graph run and its vertical controller record."""

        try:
            record = ctx.controller_runtime.cancel(controller_id, reason)
            return {"ok": True, "controller": record.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}
