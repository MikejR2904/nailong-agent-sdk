import textwrap

STDIO_SERVER = textwrap.dedent(
    """
    import asyncio, os, sys, time
    from pathlib import Path
    from mcp.server import MCPServer

    server = MCPServer(name="demo")

    @server.tool(name="echo")
    async def echo(text: str) -> str:
        return f"echo:{text}"

    @server.tool(name="explode")
    async def explode() -> str:
        raise RuntimeError("tool exploded on purpose")

    @server.tool(name="sleepy")
    async def sleepy(seconds: float) -> str:
        await asyncio.sleep(seconds)
        return "done"

    @server.tool(name="die")
    async def die() -> str:
        os._exit(3)

    if __name__ == "__main__":
        server.run(transport="stdio")
    """
)
