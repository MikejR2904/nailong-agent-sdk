# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for typed-DAG run state, cancellation, approval, and resumption."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._shared import McpContext
from .authority import McpOperation, approval_request_digest

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_run_tools(server: MCPServer, ctx: McpContext) -> None:
    @ctx.tool(server, "get_run_state", operation=McpOperation.READ)
    def get_run_state(run_id: str) -> dict[str, Any]:
        """Return a hash-verified run record and current graph status."""

        try:
            return {
                "ok": True,
                "run": ctx.coordinator.get_run_state(run_id).model_dump(mode="json"),
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "cancel_run")
    def cancel_run(run_id: str) -> dict[str, Any]:
        """Cancel runnable nodes through deterministic typed terminal states."""

        try:
            return {"ok": True, "run": ctx.coordinator.cancel_run(run_id).model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "list_run_approvals", operation=McpOperation.READ)
    def list_run_approvals(run_id: str) -> dict[str, Any]:
        """List the approval requests a graph run raised, each with the request_hash that
        an approver binds a decision to."""

        try:
            requests = ctx.coordinator.approvals(run_id).list(run_id)
            return {
                "ok": True,
                "approvals": [
                    {**item.model_dump(mode="json"), "request_hash": approval_request_digest(item)}
                    for item in requests
                ],
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "submit_approval", operation=McpOperation.APPROVAL)
    def submit_approval(
        run_id: str,
        approval_id: str,
        approved: bool,
        reason: str | None = None,
        request_hash: str | None = None,
        issued_at_utc: str | None = None,
        approver_label: str | None = None,
    ) -> dict[str, Any]:
        """Record a typed approval decision for a pending governed action.

        An approver credential must send `request_hash` (from `list_run_approvals`) and
        `issued_at_utc` (the instant the approver decided); `approver_label` is the
        caller's unverified note on who decided and is only recorded."""

        def current_digest() -> str | None:
            request = ctx.coordinator.approvals(run_id).get(approval_id)
            if request is None or request.run_id != run_id:
                return None
            return approval_request_digest(request)

        try:
            with ctx.gate.decide(
                "submit_approval",
                subject=f'approval request "{approval_id}" of run "{run_id}"',
                approved=approved,
                binding_field="request_hash",
                expected=request_hash,
                actual=current_digest,
                issued_at_utc=issued_at_utc,
                label=approver_label,
                reason=reason,
            ) as receipt:
                approval = ctx.coordinator.submit_approval(
                    run_id, approval_id, approved, reason, source=receipt.source()
                )
            return {
                "ok": True,
                "approval": approval.model_dump(mode="json"),
                **receipt.fields(),
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "revoke_run_approval", operation=McpOperation.APPROVAL)
    def revoke_run_approval(
        run_id: str,
        approval_id: str,
        reason: str,
        request_hash: str | None = None,
        issued_at_utc: str | None = None,
        approver_label: str | None = None,
    ) -> dict[str, Any]:
        """Withdraw a pending or approved approval request of a graph run so that no later call
        is authorized by it.

        An approver credential must send `request_hash` (from `list_run_approvals`) and
        `issued_at_utc`; the host credential cannot revoke. The revocation is recorded in the
        decision stream and as an `approval.revoked` event of the run."""

        def current_digest() -> str | None:
            request = ctx.coordinator.approvals(run_id).get(approval_id)
            if request is None or request.run_id != run_id:
                return None
            return approval_request_digest(request)

        try:
            with ctx.gate.decide(
                "revoke_run_approval",
                subject=f'approval request "{approval_id}" of run "{run_id}"',
                approved=False,
                binding_field="request_hash",
                expected=request_hash,
                actual=current_digest,
                issued_at_utc=issued_at_utc,
                label=approver_label,
                reason=reason,
            ) as receipt:
                approval = ctx.coordinator.revoke_approval(
                    run_id, approval_id, reason, source=receipt.source()
                )
            return {
                "ok": True,
                "approval": approval.model_dump(mode="json"),
                **receipt.fields(),
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "resume_run")
    def resume_run(run_id: str) -> dict[str, Any]:
        """Integrity-check persisted graph state before returning it for scheduler resumption."""

        try:
            return {"ok": True, "run": ctx.coordinator.resume_run(run_id).model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}
