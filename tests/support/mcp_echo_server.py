# Copyright (c) 2026 David Michael Indraputra

"""A small real stdio MCP server the client tests connect to."""

from __future__ import annotations

import asyncio

from mcp.server import MCPServer

server = MCPServer(name="echo-test", version="1")


@server.tool(name="echo")
def echo(text: str) -> str:
    return text


@server.tool(name="slow")
async def slow(seconds: float) -> str:
    await asyncio.sleep(seconds)
    return "done"


@server.tool(name="fail")
def fail() -> str:
    raise ValueError("deliberate tool failure")


if __name__ == "__main__":
    server.run(transport="stdio")
