"""--mcp-serve: offer the project's file and command tools to another program over MCP (stdio)."""

from __future__ import annotations

import json
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from nailong_agent_sdk.foundations.contracts import ToolDefinition, ToolExecutionResult
from nailong_agent_sdk.starter import dev_tools, notebook_tools

from .approvals import NOT_SANDBOXED
from .factory import PROTECTED

READ_ONLY_MODES = frozenset({"default", "plan"})
EDIT_MODES = frozenset({"accept-edits"})
COMMAND_MODES = frozenset({"auto", "bypass"})
SERVER_NAME = "nailong"

ToolRun = Callable[[dict[str, Any]], Awaitable[ToolExecutionResult]]


@dataclass(frozen=True)
class OfferedTool:
    definition: ToolDefinition
    run: ToolRun


def offered_tools(
    project: Path, mode: str, sandbox_kwargs: Mapping[str, Any], extra_dirs: Mapping[str, Path]
) -> dict[str, OfferedTool]:
    edits = mode in EDIT_MODES | COMMAND_MODES
    commands = mode in COMMAND_MODES
    groups: list[Any] = list(
        dev_tools(
            project,
            allow_writes=edits,
            approve_writes=False,
            allow_commands=commands,
            approve_commands=False,
            protected=PROTECTED,
            extra_roots=dict(extra_dirs) or None,
            **sandbox_kwargs,
        )
    )
    if edits:
        groups.append(notebook_tools(project, protected=PROTECTED, needs_approval=False))
    table: dict[str, OfferedTool] = {}
    for group in groups:
        definitions = getattr(group, "definitions", None)
        if definitions is not None:
            for definition in definitions:
                table[definition.name] = OfferedTool(definition, _bound(group.run, definition.name))
        else:
            table[group.name] = OfferedTool(group.definition, group.run)
    return table


def _bound(
    run: Callable[[str, dict[str, Any]], Awaitable[ToolExecutionResult]], name: str
) -> ToolRun:
    async def call(arguments: dict[str, Any]) -> ToolExecutionResult:
        return await run(name, arguments)

    return call


def refusal(mode: str, sandbox_label: str, allow_unsandboxed: bool) -> str | None:
    if mode in COMMAND_MODES and sandbox_label == NOT_SANDBOXED and not allow_unsandboxed:
        return (
            f"--permission-mode {mode} would let whoever connects run commands on this machine, "
            "and commands are not sandboxed here. Set up a sandbox, pass --allow-unsandboxed, or "
            "use --permission-mode accept-edits to offer file edits without commands."
        )
    return None


def describe(mode: str, table: Mapping[str, OfferedTool]) -> str:
    kind = (
        "reading, editing and running commands"
        if mode in COMMAND_MODES
        else "reading and editing"
        if mode in EDIT_MODES
        else "reading only"
    )
    return f"nailong MCP server: {len(table)} tool(s), {kind}: {', '.join(sorted(table))}"


def _text(result: ToolExecutionResult) -> str:
    if result.status != "succeeded":
        return str(result.error or result.status)
    output = result.output
    return output if isinstance(output, str) else json.dumps(output, indent=2, default=str)


async def serve_stdio(table: Mapping[str, OfferedTool], *, version: str, project: Path) -> None:
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    async def list_tools(context: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=item.definition.name,
                    description=item.definition.description,
                    inputSchema=item.definition.input_schema,
                )
                for item in table.values()
            ]
        )

    async def call_tool(context: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        item = table.get(params.name)
        if item is None:
            known = ", ".join(sorted(table))
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text", text=f'No tool "{params.name}"; the tools are {known}.'
                    )
                ],
                isError=True,
            )
        result = await item.run(dict(params.arguments or {}))
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=_text(result))],
            isError=result.status != "succeeded",
        )

    server = Server(
        SERVER_NAME,
        version=version,
        instructions=f"File and command tools for the project at {project}.",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def announce(text: str, stream: TextIO | None = None) -> None:
    print(text, file=stream or sys.stderr, flush=True)
