# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for reading telemetry, audit transcripts, and metric definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..agent.retention import RetentionPolicy
from ..observability.telemetry_models import MetricDefinition, MetricObservation
from ._shared import McpContext, _validation_errors

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_telemetry_tools(server: MCPServer, ctx: McpContext) -> None:
    @ctx.tool(server, "list_telemetry_runs", exclusive=False)
    def list_telemetry_runs(limit: int = 100) -> dict[str, Any]:
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

    @ctx.tool(server, "get_telemetry_events", exclusive=False)
    def get_telemetry_events(
        run_id: str,
        after_sequence: int = 0,
        limit: int = 250,
    ) -> dict[str, Any]:
        """Read an ordered, integrity-linked page of runtime telemetry events."""

        try:
            events = ctx.telemetry.list_events(run_id, after_sequence=after_sequence, limit=limit)
            failure = ctx.telemetry.chain_break(run_id)
            return {
                "ok": True,
                "events": [item.model_dump(mode="json") for item in events],
                "integrity_chain_valid": failure is None,
                "integrity_failure": failure.model_dump(mode="json") if failure else None,
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "get_audit_log", exclusive=False)
    def get_audit_log(run_id: str, limit: int = 1_000) -> dict[str, Any]:
        """Read bounded, redacted public interaction logs outside model working memory."""

        try:
            entries = ctx.audit_logs.list_entries(run_id, limit=limit)
            failure = ctx.audit_logs.chain_break(run_id)
            return {
                "ok": True,
                "events": [entry.model_dump(mode="json") for entry in entries],
                "integrity_chain_valid": failure is None,
                "integrity_failure": failure.model_dump(mode="json") if failure else None,
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "render_audit_transcript", exclusive=False)
    def render_audit_transcript(run_id: str) -> dict[str, Any]:
        """Render a readable Markdown review transcript from hash-linked audit entries."""

        try:
            path = ctx.audit_logs.render_markdown(run_id)
            failure = ctx.audit_logs.chain_break(run_id)
            return {
                "ok": True,
                "report": {
                    "transcript_path": str(path.relative_to(ctx.run_root.resolve())),
                    "integrity_chain_valid": failure is None,
                    "integrity_failure": failure.model_dump(mode="json") if failure else None,
                },
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "register_metric_definition", exclusive=False)
    def register_metric_definition(definition: dict[str, Any]) -> dict[str, Any]:
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

    @ctx.tool(server, "list_metric_definitions", exclusive=False)
    def list_metric_definitions() -> dict[str, Any]:
        """List metric formulas and missing-data rules known to the telemetry store."""

        return {
            "ok": True,
            "definitions": [
                item.model_dump(mode="json") for item in ctx.telemetry.list_metric_definitions()
            ],
        }

    @ctx.tool(server, "record_metric_observation", exclusive=False)
    def record_metric_observation(observation: dict[str, Any]) -> dict[str, Any]:
        """Record an observed or explicitly unavailable metric; values are never synthesized."""

        try:
            value = ctx.telemetry.record_metric(MetricObservation.model_validate(observation))
            return {"ok": True, "observation": value.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "get_telemetry_metrics", exclusive=False)
    def get_telemetry_metrics(run_id: str) -> dict[str, Any]:
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

    @ctx.tool(server, "prune_runs", exclusive=False)
    def prune_runs(
        older_than_seconds: float,
        dry_run: bool = True,
        keep_most_recent: int = 0,
        max_runs: int | None = None,
        include_unfinished: bool = False,
        export_reports: bool = False,
    ) -> dict[str, Any]:
        """Delete whole finished runs idle for older_than_seconds; defaults to a dry run.

        Each pruned run's telemetry events and metrics, audit transcript and tool-result files
        are removed after a hash-chained tombstone records their final chain heads and every
        tool-result hash. Runs with a broken chain, no `agent.terminated` event (unless
        `include_unfinished`) or recent activity are kept.
        """

        policy = RetentionPolicy(
            older_than_seconds=older_than_seconds,
            keep_most_recent=keep_most_recent,
            max_runs=max_runs,
            include_unfinished=include_unfinished,
            export_reports=export_reports,
        )
        report = ctx.retention.prune(policy, dry_run=dry_run)
        return {"ok": True, "report": report.model_dump(mode="json")}

    @ctx.tool(server, "create_telemetry_report", exclusive=False)
    def create_telemetry_report(run_id: str, include_event_hashes: bool = False) -> dict[str, Any]:
        """Create a reproducible trace and metric-completeness report from observed facts."""

        try:
            report = ctx.telemetry.create_run_report(
                run_id, include_event_hashes=include_event_hashes
            )
            return {"ok": True, "report": report}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}
