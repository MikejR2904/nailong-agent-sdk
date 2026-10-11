import asyncio
import re
import sys
import textwrap
import time

import pytest

from nailong_agent_sdk.mcp.client import (
    McpClientManager,
    McpServerNotConnectedError,
    McpToolCallError,
)
from nailong_agent_sdk.mcp.client_bridge import mcp_tools_as_extensions, registered_tool_name
from nailong_agent_sdk.mcp.client_types import (
    McpConnectionState,
    McpHttpServerConfig,
    McpStdioServerConfig,
    McpToolInfo,
)
from nailong_agent_sdk.tools.registry import HarnessToolRegistry
from tests.support.mcp_server import Server
from tests.support.mcp_stdio import STDIO_SERVER
from tests.support.processes import kill_process_tree, process_alive
from tests.support.tools import build
from tests.support.tools import call as call_tool

HANGING_SERVER = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path
    Path(sys.argv[1]).write_text(str(os.getpid()))
    time.sleep(600)
    """
)


@pytest.fixture
def stdio_config(tmp_path):
    script = tmp_path / "stdio_server.py"
    script.write_text(STDIO_SERVER, "utf-8")
    return McpStdioServerConfig(name="demo", command=sys.executable, args=[str(script)])


def arun(coro, timeout=120):
    async def guarded():
        return await asyncio.wait_for(coro, timeout)

    return asyncio.run(guarded())


def test_http_connection_to_the_sdk_server_works_with_the_installed_mcp(tmp_path):
    server = Server(tmp_path)
    try:

        async def scenario():
            manager = McpClientManager(
                [
                    McpHttpServerConfig(name="sdk", url=server.url, headers=server.auth_headers),
                    McpHttpServerConfig(name="anonymous", url=server.url),
                ]
            )
            await manager.connect_all()
            statuses = {status.name: status for status in manager.list_statuses()}
            reply = await manager.call_tool("sdk", "list_metric_definitions", {})
            await manager.close()
            return statuses, reply

        statuses, reply = arun(scenario())
        sdk = statuses["sdk"]
        assert sdk.state is McpConnectionState.CONNECTED and len(sdk.tools) == 53, sdk.detail
        assert sdk.auth_configured is True and '"ok"' in reply
        anonymous = statuses["anonymous"]
        assert anonymous.state is McpConnectionState.FAILED and anonymous.auth_configured is False
        assert "the endpoint answered HTTP 401" in anonymous.detail
        assert server.token not in anonymous.detail
    finally:
        server.stop()


def test_stdio_roundtrip_errors_reconnect_and_close(stdio_config):
    async def scenario():
        manager = McpClientManager([stdio_config])
        try:
            await manager.connect_all()
            status = manager.list_statuses()[0]
            assert status.state is McpConnectionState.CONNECTED and {
                t.name for t in status.tools
            } == {"echo", "explode", "sleepy", "die"}
            assert status.auth_configured is False and status.resources == []
            assert await manager.call_tool("demo", "echo", {"text": "hi"}) == "echo:hi"
            with pytest.raises(McpToolCallError) as boom:
                await manager.call_tool("demo", "explode", {})
            assert "Error executing tool explode" in str(boom.value) and "demo::explode" in str(
                boom.value
            )
            with pytest.raises(McpToolCallError) as bad_args:
                await manager.call_tool("demo", "echo", {"nope": 1})
            message = str(bad_args.value)
            await manager.reconnect("demo")
            assert await manager.call_tool("demo", "echo", {"text": "again"}) == "echo:again"
        finally:
            await manager.close()
        await manager.close()
        assert manager.list_statuses()[0].state is McpConnectionState.CLOSED
        assert manager.list_tools() == []
        with pytest.raises(McpServerNotConnectedError, match="is not connected"):
            await manager.call_tool("demo", "echo", {"text": "x"})
        with pytest.raises(McpServerNotConnectedError, match="is not connected"):
            await manager.read_resource("unknown", "file://x")
        with pytest.raises(ValueError, match='No MCP server named "ghost"'):
            await manager.reconnect("ghost")
        return message

    arun(scenario(), timeout=90)


def test_duplicate_server_names_and_failed_connections(tmp_path):
    with pytest.raises(ValueError, match="names must be unique"):
        McpClientManager(
            [
                McpStdioServerConfig(name="a", command="x"),
                McpHttpServerConfig(name="a", url="http://x"),
            ]
        )

    async def scenario():
        manager = McpClientManager(
            [
                McpStdioServerConfig(name="missing-binary", command="definitely-not-a-binary-xyz"),
                McpHttpServerConfig(name="dead-http", url="http://127.0.0.1:9/mcp"),
            ]
        )
        await manager.connect_all()
        return manager.list_statuses()

    statuses = {status.name: status for status in arun(scenario(), timeout=180)}
    assert all(s.state is McpConnectionState.FAILED for s in statuses.values())
    missing = statuses["missing-binary"].detail
    assert 'stdio command "definitely-not-a-binary-xyz"' in missing
    assert "FileNotFoundError" in missing
    unreachable = statuses["dead-http"].detail
    assert 'HTTP endpoint "http://127.0.0.1:9/mcp"' in unreachable
    assert "ConnectError" in unreachable
    assert "unpack" not in unreachable


def test_a_hanging_server_fails_after_the_configured_handshake_timeout(tmp_path):
    script = tmp_path / "hang.py"
    pid_file = tmp_path / "pid.txt"
    script.write_text(HANGING_SERVER, "utf-8")
    manager = McpClientManager(
        [
            McpStdioServerConfig(
                name="hang",
                command=sys.executable,
                args=[str(script), str(pid_file)],
                connect_timeout_seconds=3,
            )
        ]
    )

    async def scenario():
        started = time.monotonic()
        await asyncio.wait_for(manager.connect_all(), 25)
        return time.monotonic() - started

    elapsed = asyncio.run(scenario())
    alive = None
    if pid_file.exists():
        pid = int(pid_file.read_text())
        alive = process_alive(pid)
        if alive:
            kill_process_tree(pid)
    status = manager.list_statuses()[0]
    assert status.state is McpConnectionState.FAILED
    assert "TimeoutError: no initialize response within 3s" in status.detail
    assert 3 <= elapsed < 15, elapsed
    assert alive is False


def test_connect_timeout_defaults_to_a_finite_bound_and_rejects_nonsense():
    assert McpStdioServerConfig(name="a", command="b").connect_timeout_seconds == 30.0
    assert McpHttpServerConfig(name="a", url="http://x").connect_timeout_seconds == 30.0
    for value in (0, -1, 100_000, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="connect_timeout_seconds"):
            McpStdioServerConfig(name="a", command="b", connect_timeout_seconds=value)


def test_a_server_that_dies_mid_call_is_reported_as_not_connected_and_can_be_reconnected(
    stdio_config,
):
    async def scenario():
        manager = McpClientManager([stdio_config])
        try:
            await manager.connect_all()
            with pytest.raises(McpServerNotConnectedError, match="closed its connection"):
                await manager.call_tool("demo", "die", {})
            status = manager.list_statuses()[0]
            with pytest.raises(McpServerNotConnectedError, match="is not connected"):
                await manager.call_tool("demo", "echo", {"text": "x"})
            await manager.reconnect("demo")
            return status, await manager.call_tool("demo", "echo", {"text": "back"})
        finally:
            await manager.close()

    status, reply = arun(scenario(), timeout=90)
    assert status.state is McpConnectionState.FAILED and "connection closed" in status.detail
    assert status.tools == []
    assert reply == "echo:back"


def test_mcp_extensions_are_gated_and_results_are_marked_untrusted(stdio_config, tmp_path):
    from nailong_agent_sdk.tools.policy import CapabilityGrant

    async def scenario():
        manager = McpClientManager([stdio_config])
        try:
            await manager.connect_all()
            extensions, handlers = mcp_tools_as_extensions(manager)
            names = sorted(t.name for t in extensions)
            assert names == [
                "mcp__demo__die",
                "mcp__demo__echo",
                "mcp__demo__explode",
                "mcp__demo__sleepy",
            ]
            assert {t.capability for t in extensions} == {"mcp.demo"} and {
                t.side_effect.value for t in extensions
            } == {"process"}
            registry = HarnessToolRegistry.with_extensions(extensions, handlers=handlers)
            executor, _ = build(tmp_path, registry=registry, approve=())
            denied = await call_tool(executor, "mcp__demo__echo", text="hi")
            assert denied.status == "blocked" and 'lacks capability "mcp.demo"' in denied.error
            grant = [CapabilityGrant(role="worker", capabilities=["mcp.demo"], allowed_paths=["."])]
            executor2, _ = build(tmp_path / "g", registry=registry, grants=grant, approve=())
            needs_approval = await call_tool(executor2, "mcp__demo__echo", text="hi")
            assert (
                needs_approval.status == "blocked"
                and needs_approval.output["capability"] == "mcp.demo"
            )
            executor3, _ = build(
                tmp_path / "h", registry=registry, grants=grant, approve=("mcp.demo",)
            )
            allowed = await call_tool(executor3, "mcp__demo__echo", text="hi")
            failed = await call_tool(executor3, "mcp__demo__explode")
            return allowed, failed
        finally:
            await manager.close()

    allowed, failed = arun(scenario(), timeout=90)
    assert allowed.status == "succeeded"
    assert allowed.output["content"] == "echo:hi"
    assert allowed.output["untrusted_content"] is True
    assert allowed.output["source"] == "mcp:demo::echo"
    assert "untrusted evidence" in allowed.output["safety_notice"]
    assert failed.status == "failed" and "Error executing tool explode" in failed.error


def test_registered_tool_names_are_valid_llm_function_names():
    pattern = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
    risky = [
        registered_tool_name("my server", "read file"),
        registered_tool_name("a.b", "c"),
        registered_tool_name("x" * 40, "y" * 40),
    ]
    invalid = [name for name in risky if not pattern.match(name)]
    assert invalid == [], invalid
    assert registered_tool_name("demo", "echo") == "mcp__demo__echo"
    assert len(set(risky)) == 3
    assert registered_tool_name("a.b", "c") != registered_tool_name("a_b", "c")
    assert registered_tool_name("x" * 40, "y" * 40) != registered_tool_name("x" * 40, "y" * 41)


class _ListedTools:
    def __init__(self, *pairs):
        self._tools = [
            McpToolInfo(server_name=server, name=tool, description="", input_schema={})
            for server, tool in pairs
        ]

    def list_tools(self):
        return self._tools


def test_two_tools_that_map_to_one_registered_name_are_refused_naming_both():
    with pytest.raises(ValueError) as excinfo:
        mcp_tools_as_extensions(_ListedTools(("a", "b__c"), ("a__b", "c")))
    message = str(excinfo.value)
    assert '"a::b__c" and "a__b::c"' in message and "mcp__a__b__c" in message
    with pytest.raises(ValueError, match='lists the tool "echo" more than once'):
        mcp_tools_as_extensions(_ListedTools(("demo", "echo"), ("demo", "echo")))
    extensions, handlers = mcp_tools_as_extensions(_ListedTools(("a.b", "c"), ("a_b", "c")))
    assert len({tool.name for tool in extensions}) == 2 and set(handlers) == {
        tool.name for tool in extensions
    }


def test_a_cancelled_connect_does_not_leave_the_child_process_running(tmp_path):
    script = tmp_path / "hang.py"
    pid_file = tmp_path / "pid.txt"
    script.write_text(HANGING_SERVER, "utf-8")

    async def scenario():
        manager = McpClientManager(
            [
                McpStdioServerConfig(
                    name="hang", command=sys.executable, args=[str(script), str(pid_file)]
                )
            ]
        )
        try:
            await asyncio.wait_for(manager.connect_all(), 5)
            outcome = "connected"
        except TimeoutError:
            outcome = "timed-out"
        started = time.monotonic()
        total_awaits = 0
        cancelled_awaits = 0
        first_cancel = None
        while time.monotonic() - started < 4:
            total_awaits += 1
            try:
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                cancelled_awaits += 1
                if first_cancel is None:
                    first_cancel = time.monotonic() - started
        pid = int(pid_file.read_text())
        return (
            outcome,
            total_awaits,
            cancelled_awaits,
            first_cancel,
            pid,
            process_alive(pid),
            manager.list_statuses()[0].state.value,
        )

    outcome, total_awaits, cancelled_awaits, first_cancel, pid, alive, state = asyncio.run(
        scenario()
    )
    if alive:
        kill_process_tree(pid)
    assert outcome == "timed-out"
    assert cancelled_awaits == 0, (
        f"{cancelled_awaits} of {total_awaits} later awaits of the caller were cancelled"
    )
    assert not alive


def test_a_cancelled_tool_call_leaves_the_session_usable(stdio_config):
    async def scenario():
        manager = McpClientManager([stdio_config])
        try:
            await manager.connect_all()
            started = time.monotonic()
            try:
                await asyncio.wait_for(manager.call_tool("demo", "sleepy", {"seconds": 20}), 2)
            except TimeoutError:
                pass
            cancelled_after = time.monotonic() - started
            started = time.monotonic()
            reply = await asyncio.wait_for(
                manager.call_tool("demo", "echo", {"text": "after-cancel"}), 25
            )
            return cancelled_after, reply, time.monotonic() - started
        finally:
            await manager.close()

    cancelled_after, reply, second_call_seconds = arun(scenario(), timeout=90)
    assert reply == "echo:after-cancel"
    assert second_call_seconds < 5, (
        f"the follow-up call waited {second_call_seconds:.1f}s behind the cancelled one"
    )


def test_a_slow_tool_call_has_no_client_side_timeout(stdio_config):
    async def scenario():
        manager = McpClientManager([stdio_config])
        try:
            await manager.connect_all()
            started = time.monotonic()
            text = await manager.call_tool("demo", "sleepy", {"seconds": 7})
            return text, time.monotonic() - started
        finally:
            await manager.close()

    text, elapsed = arun(scenario(), timeout=60)
    assert text == "done"


PID_LINE = "Path(os.environ['PID_DIR'], f'{os.getpid()}.pid').write_text('x')"
PID_SERVER = STDIO_SERVER.replace(
    'server = MCPServer(name="demo")', PID_LINE + '\nserver = MCPServer(name="demo")'
)


def test_reconnect_and_close_from_a_different_task_clean_up_the_old_child(tmp_path):
    script = tmp_path / "pid_server.py"
    script.write_text(PID_SERVER, "utf-8")
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    config = McpStdioServerConfig(
        name="demo", command=sys.executable, args=[str(script)], env={"PID_DIR": str(pid_dir)}
    )

    def pids():
        return sorted(int(p.stem) for p in pid_dir.glob("*.pid"))

    async def scenario():
        manager = McpClientManager([config])
        await asyncio.create_task(manager.connect_all())
        first = pids()
        await asyncio.create_task(manager.reconnect("demo"))
        await asyncio.sleep(1)
        second = pids()
        old_alive_after_reconnect = [pid for pid in first if process_alive(pid)]
        working_after_reconnect = await manager.call_tool("demo", "echo", {"text": "r"})
        await asyncio.create_task(manager.close())
        await asyncio.sleep(2)
        alive_after_close = [pid for pid in second if process_alive(pid)]
        return first, second, old_alive_after_reconnect, working_after_reconnect, alive_after_close

    first, second, old_alive, working, alive_after_close = asyncio.run(scenario())
    for pid in set(old_alive) | set(alive_after_close):
        kill_process_tree(pid)
    assert working == "echo:r"
    assert old_alive == [], (
        f"reconnect from another task left the previous server process {old_alive} running"
    )
    assert alive_after_close == [], f"close from another task left {alive_after_close} running"
