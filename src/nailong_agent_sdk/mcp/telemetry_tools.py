# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for reading telemetry, audit transcripts, and metric definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..observability.telemetry_models import MetricDefinition, MetricObservation
from ._shared import McpContext, _validation_errors

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_telemetry_tools(server: MCPServer, ctx: McpContext) -> None:
    @server.tool(name="list_telemetry_runs", structured_output=True)
    async def list_telemetry_runs(limit: int = 100) -> dict[str, Any]:
        """List durable Python runtime telemetry runs without exposing hidden reasoning."""

        try:
            return {
                "ok": True,
                "runs": [
                    item.model_dump(mode="json") for item in ctx.telemetry.list_runs(limit=limit)
                ],
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="get_telemetry_events", structured_output=True)
    async def get_telemetry_events(
        run_id: str,
        after_sequence: int = 0,
        limit: int = 250,
    ) -> dict[str, Any]:
        """Read an ordered, integrity-linked page of runtime telemetry events."""

        try:
            events = ctx.telemetry.list_events(run_id, after_sequence=after_sequence, limit=limit)
            return {
                "ok": True,
                "events": [item.model_dump(mode="json") for item in events],
                "integrity_chain_valid": ctx.telemetry.verify_run_chain(run_id),
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="get_audit_log", structured_output=True)
    async def get_audit_log(run_id: str, limit: int = 1_000) -> dict[str, Any]:
        """Read bounded, redacted public interaction logs outside model working memory."""

        try:
            entries = ctx.audit_logs.list_entries(run_id, limit=limit)
            return {
                "ok": True,
                "events": [entry.model_dump(mode="json") for entry in entries],
                "integrity_chain_valid": ctx.audit_logs.verify(run_id),
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="render_audit_transcript", structured_output=True)
    async def render_audit_transcript(run_id: str) -> dict[str, Any]:
        """Render a readable Markdown review transcript from hash-linked audit entries."""

        try:
            path = ctx.audit_logs.render_markdown(run_id)
            return {
                "ok": True,
                "report": {
                    "transcript_path": str(path.relative_to(ctx.run_root.resolve())),
                    "integrity_chain_valid": ctx.audit_logs.verify(run_id),
                },
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="register_metric_definition", structured_output=True)
    async def register_metric_definition(definition: dict[str, Any]) -> dict[str, Any]:
        """Register a versioned metric formula before observations are interpreted."""

        try:
            value = ctx.telemetry.register_metric_definition(
                MetricDefinition.model_validate(definition)
            )
            return {"ok": True, "definition": value.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="list_metric_definitions", structured_output=True)
    async def list_metric_definitions() -> dict[str, Any]:
        """List metric formulas and missing-data rules known to the telemetry store."""

        return {
            "ok": True,
            "definitions": [
                item.model_dump(mode="json") for item in ctx.telemetry.list_metric_definitions()
            ],
        }

    @server.tool(name="record_metric_observation", structured_output=True)
    async def record_metric_observation(observation: dict[str, Any]) -> dict[str, Any]:
        """Record an observed or explicitly unavailable metric; values are never synthesized."""

        try:
            value = ctx.telemetry.record_metric(MetricObservation.model_validate(observation))
            return {"ok": True, "observation": value.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="get_telemetry_metrics", structured_output=True)
    async def get_telemetry_metrics(run_id: str) -> dict[str, Any]:
        """Return recorded metric observations and their explicit availability states."""

        try:
            return {
                "ok": True,
                "metrics": [
                    item.model_dump(mode="json") for item in ctx.telemetry.list_metrics(run_id)
                ],
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="create_telemetry_report", structured_output=True)
    async def create_telemetry_report(run_id: str) -> dict[str, Any]:
        """Create a reproducible trace and metric-completeness report from observed facts."""

        try:
            return {"ok": True, "report": ctx.telemetry.create_run_report(run_id)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}
