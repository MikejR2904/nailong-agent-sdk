# Copyright (c) 2026 David Michael Indraputra

"""Outbound MCP client: bounded connects and calls, concurrency, typed failures."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

from nailong_agent_sdk import (
    McpClientManager,
    McpErrorKind,
    McpServerNotConnectedError,
    McpStdioServerConfig,
    McpTimeoutError,
    McpToolError,
)
from nailong_agent_sdk.mcp.client_types import McpConnectionState

_ECHO = str(Path(__file__).parent / "support" / "mcp_echo_server.py")
_HANG = "import time; time.sleep(60)"


def _echo(name: str, **options: float) -> McpStdioServerConfig:
    return McpStdioServerConfig(name=name, command=sys.executable, args=[_ECHO], **options)


def _hanging(name: str) -> McpStdioServerConfig:
    return McpStdioServerConfig(
        name=name, command=sys.executable, args=["-c", _HANG], connect_timeout_seconds=1.0
    )


def test_calls_succeed_and_failures_are_typed() -> None:
    async def scenario() -> None:
        manager = McpClientManager([_echo("echo", call_timeout_seconds=1.0)])
        await manager.connect_all()
        try:
            assert manager.list_statuses()[0].state is McpConnectionState.CONNECTED
            assert await manager.call_tool("echo", "echo", {"text": "hi"}) == "hi"
            with pytest.raises(McpTimeoutError) as timeout:
                await manager.call_tool("echo", "slow", {"seconds": 5})
            assert timeout.value.kind is McpErrorKind.TIMEOUT
            with pytest.raises(McpToolError) as failed:
                await manager.call_tool("echo", "fail", {})
            assert failed.value.kind is McpErrorKind.TOOL_ERROR
            assert failed.value.target == "echo::fail"
            with pytest.raises(McpServerNotConnectedError):
                await manager.call_tool("missing", "echo", {})
            # The session survives a timed-out call.
            assert await manager.call_tool("echo", "echo", {"text": "again"}) == "again"
        finally:
            await manager.close()
            await manager.close()

    asyncio.run(scenario())


def test_connects_run_concurrently_and_time_out_per_server() -> None:
    async def scenario() -> None:
        hanging = [_hanging(f"hang-{index}") for index in range(4)]
        manager = McpClientManager([*hanging, _echo("echo")])
        started = time.monotonic()
        await manager.connect_all()
        elapsed = time.monotonic() - started
        try:
            statuses = {status.name: status for status in manager.list_statuses()}
            for name in (config.name for config in hanging):
                assert statuses[name].state is McpConnectionState.FAILED
                assert statuses[name].error_kind is McpErrorKind.TIMEOUT
            assert statuses["echo"].state is McpConnectionState.CONNECTED
            # Each hung server costs its 1s timeout plus stdio teardown (about 2s);
            # four in series would take at least 12s.
            assert elapsed < 6.0, elapsed
            assert await manager.call_tool("echo", "echo", {"text": "ok"}) == "ok"
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_failed_launch_is_a_transport_failure_and_reconnect_recovers() -> None:
    async def scenario() -> None:
        broken = McpStdioServerConfig(name="broken", command="/nonexistent/mcp-server")
        manager = McpClientManager([broken, _echo("echo")])
        await manager.connect_all()
        try:
            statuses = {status.name: status for status in manager.list_statuses()}
            assert statuses["broken"].error_kind is McpErrorKind.TRANSPORT
            await manager.reconnect("echo")
            assert await manager.call_tool("echo", "echo", {"text": "back"}) == "back"
        finally:
            await manager.close()

    asyncio.run(scenario())
