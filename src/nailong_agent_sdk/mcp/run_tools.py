# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for typed-DAG run state, cancellation, approval, and resumption."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._shared import McpContext

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_run_tools(server: MCPServer, ctx: McpContext) -> None:
    @server.tool(name="get_run_state", structured_output=True)
    async def get_run_state(run_id: str) -> dict[str, Any]:
        """Return a hash-verified run record and current graph status."""

        try:
            return {
                "ok": True,
                "run": ctx.coordinator.get_run_state(run_id).model_dump(mode="json"),
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="cancel_run", structured_output=True)
    async def cancel_run(run_id: str) -> dict[str, Any]:
        """Cancel runnable nodes through deterministic typed terminal states."""

        try:
            return {"ok": True, "run": ctx.coordinator.cancel_run(run_id).model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="submit_approval", structured_output=True)
    async def submit_approval(
        run_id: str,
        approval_id: str,
        approved: bool,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Record a typed approval decision for a pending governed action."""

        try:
            approval = ctx.coordinator.submit_approval(run_id, approval_id, approved, reason)
            return {"ok": True, "approval": approval.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="resume_run", structured_output=True)
    async def resume_run(run_id: str) -> dict[str, Any]:
        """Integrity-check persisted graph state before returning it for scheduler resumption."""

        try:
            return {"ok": True, "run": ctx.coordinator.resume_run(run_id).model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}
