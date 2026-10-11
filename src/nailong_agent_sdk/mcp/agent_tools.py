# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for BaseAgent contract validation, context assembly, and task execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..agent import task_files
from ..agent.task_runner import TaskRunOptions
from ..foundations.contracts import AgentDefinition, ScopedAgentTask
from ..memory.context import assemble_initial_context
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
        """Run one scoped agent task with the model, tools, permissions and limits supplied here.

        `runtime_options` carries `model_endpoint` (base_url, api_key), `permissions`
        (role, capabilities, allowed_paths, approved_capabilities, approval_mode,
        approval_timeout_seconds), `builtin_tools`, `workspace`, `declared_output_paths`,
        `command_templates`, `mcp_servers`, `web_search` and the limits. Without
        `model_endpoint` the run replays `scripted_turns`. With `approval_mode` "wait" a tool
        call that needs approval pauses the run until `decide_task_approval` answers it or
        `approval_timeout_seconds` passes; read the files it wrote with `list_task_files` and
        `read_task_file`. `traceparent` (a W3C header value) continues the caller's trace: the
        run's events and profile spans carry its trace id, and `model_endpoint`'s
        `propagate_trace_context` also sends it to the provider.
        """

        try:
            parsed_definition = AgentDefinition.model_validate(definition)
            parsed_task = ScopedAgentTask.model_validate(task)
            options = TaskRunOptions.model_validate(runtime_options or {})
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

    @ctx.tool(server, "list_task_approvals", exclusive=False)
    def list_task_approvals(task_id: str) -> dict[str, Any]:
        """List the approval requests a running agent task has raised, with their decisions."""

        try:
            approvals = ctx.task_runner.approvals(task_id)
            return {"ok": True, "approvals": [item.model_dump(mode="json") for item in approvals]}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "decide_task_approval", exclusive=False)
    def decide_task_approval(
        task_id: str, approval_id: str, approved: bool, reason: str | None = None
    ) -> dict[str, Any]:
        """Approve or reject one pending approval request of a running agent task."""

        try:
            request = ctx.task_runner.decide_approval(task_id, approval_id, approved, reason)
            return {"ok": True, "approval": request.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @ctx.tool(server, "list_task_files", exclusive=False)
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

    @ctx.tool(server, "read_task_file", exclusive=False)
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
