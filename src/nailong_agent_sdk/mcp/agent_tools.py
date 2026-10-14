# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for BaseAgent contract validation, context assembly, and task execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import Context
from pydantic import ValidationError

from ..agent import task_files
from ..agent.task_runner import TaskRunOptions
from ..foundations.contracts import AgentDefinition, ScopedAgentTask
from ..memory.context import assemble_initial_context
from ..observability.trace_context import TRACEPARENT_HEADER, extract_traceparent
from ._shared import McpContext, _validation_errors
from .authority import McpOperation, approval_request_digest

if TYPE_CHECKING:
    from mcp.server import MCPServer


def _inbound_traceparent(request: Context | None) -> str | None:
    try:
        meta = request.request_context.meta if request is not None else None
    except ValueError:
        return None
    value = meta.get(TRACEPARENT_HEADER) if meta else None
    if not isinstance(value, str) or extract_traceparent({TRACEPARENT_HEADER: value}) is None:
        return None
    return value


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
        request: Context | None = None,
    ) -> dict[str, Any]:
        """Run one scoped agent task with the model, tools, permissions and limits supplied here.

        `runtime_options` carries `model_endpoint` (base_url, api_key), `permissions`
        (role, capabilities, allowed_paths, approved_capabilities, approval_mode,
        approval_timeout_seconds), `builtin_tools`, `workspace`, `declared_output_paths`,
        `command_templates`, `mcp_servers`, `web_search` and the limits. Without
        `model_endpoint` the run replays `scripted_turns`. With `approval_mode` "wait" a tool
        call that needs approval pauses the run until `decide_task_approval` answers it or
        `approval_timeout_seconds` passes; read the files it wrote with `list_task_files` and
        `read_task_file`. `traceparent` (a W3C header value; the request's `_meta.traceparent`
        when the option is absent) continues the caller's trace: the run's events and profile
        spans carry its trace id, and `model_endpoint`'s `propagate_trace_context` also sends
        it to the provider.
        """

        try:
            parsed_definition = AgentDefinition.model_validate(definition)
            parsed_task = ScopedAgentTask.model_validate(task)
            options = TaskRunOptions.model_validate(runtime_options or {})
            if options.traceparent is None and (inbound := _inbound_traceparent(request)):
                options = options.model_copy(update={"traceparent": inbound})
            result = await ctx.task_runner.run(parsed_definition, parsed_task, options)
            return {"ok": True, "result": result.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "cancel_agent_task", exclusive=False)
    def cancel_agent_task(task_id: str) -> dict[str, Any]:
        """Stop a running agent task, interrupting its model or tool call; it ends cancelled."""

        if ctx.task_runner.cancel(task_id):
            return {"ok": True, "cancelled": True}
        return {
            "ok": False,
            "errors": [{"message": f'No agent task "{task_id}" is running.', "type": "ValueError"}],
        }

    @ctx.tool(server, "list_task_approvals", exclusive=False, operation=McpOperation.READ)
    def list_task_approvals(task_id: str) -> dict[str, Any]:
        """List the approval requests a running agent task has raised, with their decisions
        and the request_hash an approver binds a decision to."""

        try:
            approvals = ctx.task_runner.approvals(task_id)
            return {
                "ok": True,
                "approvals": [
                    {**item.model_dump(mode="json"), "request_hash": approval_request_digest(item)}
                    for item in approvals
                ],
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "decide_task_approval", exclusive=False, operation=McpOperation.APPROVAL)
    def decide_task_approval(
        task_id: str,
        approval_id: str,
        approved: bool,
        reason: str | None = None,
        request_hash: str | None = None,
        issued_at_utc: str | None = None,
        approver_label: str | None = None,
    ) -> dict[str, Any]:
        """Approve or reject one pending approval request of a running agent task.

        An approver credential must send `request_hash` (from `list_task_approvals`) and
        `issued_at_utc` (the instant the approver decided); `approver_label` is the
        caller's unverified note on who decided and is only recorded."""

        def current_digest() -> str | None:
            for item in ctx.task_runner.approvals(task_id):
                if item.approval_id == approval_id:
                    return approval_request_digest(item)
            return None

        try:
            with ctx.gate.decide(
                "decide_task_approval",
                subject=f'approval request "{approval_id}" of task "{task_id}"',
                approved=approved,
                binding_field="request_hash",
                expected=request_hash,
                actual=current_digest,
                issued_at_utc=issued_at_utc,
                label=approver_label,
                reason=reason,
            ) as receipt:
                request = ctx.task_runner.decide_approval(task_id, approval_id, approved, reason)
            return {
                "ok": True,
                "approval": request.model_dump(mode="json"),
                **receipt.fields(),
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "list_task_files", exclusive=False, operation=McpOperation.READ)
    def list_task_files(
        task_id: str,
        path: str = ".",
        workspace: str | None = None,
        offset: int = 0,
        limit: int = task_files.DEFAULT_LISTING_LIMIT,
    ) -> dict[str, Any]:
        """List one directory of the workspace an agent task wrote to, one bounded page at a time.

        `workspace` is the value given in the task's `runtime_options`; omit it for the default
        workspace of `task_id`. Credential files and SDK-internal state are never listed.
        """

        try:
            root = ctx.task_runner.workspace_for(task_id, workspace)
            listing = task_files.list_task_files(root, path, offset=offset, limit=limit)
            return {"ok": True, "listing": listing.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "read_task_file", exclusive=False, operation=McpOperation.READ)
    def read_task_file(
        task_id: str,
        path: str,
        workspace: str | None = None,
        offset: int = 0,
        max_bytes: int = task_files.DEFAULT_READ_BYTES,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Read one page of a file in an agent task's workspace as UTF-8 text or base64 bytes.

        Follow `next_offset` until it is null to read the whole file. Credential files, SDK-internal
        state and anything outside the workspace are refused.
        """

        try:
            root = ctx.task_runner.workspace_for(task_id, workspace)
            chunk = task_files.read_task_file(
                root, path, offset=offset, max_bytes=max_bytes, encoding=encoding
            )
            return {"ok": True, "file": chunk.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}
