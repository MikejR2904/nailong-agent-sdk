import asyncio
import sys
import time

import pytest

from nailong_agent_sdk.mcp.client import (
    McpCallTimeoutError,
    McpClientManager,
    McpResourceReadError,
    McpServerNotConnectedError,
    McpToolCallError,
)
from nailong_agent_sdk.mcp.client_types import (
    McpConnectionState,
    McpHttpServerConfig,
    McpStdioServerConfig,
)
from tests.support.mcp_server import Server
from tests.support.mcp_stdio import STDIO_SERVER
from tests.support.processes import kill_process_tree, process_alive

LIVENESS_SERVER = STDIO_SERVER.replace(
    'if __name__ == "__main__":',
    "@server.tool(name='pid')\n"
    "async def pid() -> str:\n"
    "    return str(os.getpid())\n"
    "\n"
    "@server.tool(name='freeze')\n"
    "async def freeze() -> str:\n"
    "    time.sleep(600)\n"
    "    return 'unfrozen'\n"
    "\n"
    "@server.resource('demo://slow')\n"
    "async def slow_resource() -> str:\n"
    "    await asyncio.sleep(20)\n"
    "    return 'late'\n"
    "\n"
    'if __name__ == "__main__":',
)


@pytest.fixture
def config_for(tmp_path):
    script = tmp_path / "liveness_server.py"
    script.write_text(LIVENESS_SERVER, "utf-8")

    def make(**options):
        return McpStdioServerConfig(
            name="demo", command=sys.executable, args=[str(script)], **options
        )

    return make


def arun(coro, timeout=120):
    async def guarded():
        return await asyncio.wait_for(coro, timeout)

    return asyncio.run(guarded())


async def wait_until(condition, seconds, what):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = condition()
        if found:
            return found
        await asyncio.sleep(0.1)
    raise AssertionError(f"{what} did not happen within {seconds:g}s")


def status_of(manager):
    return manager.list_statuses()[0]


def test_deadlines_and_pings_are_off_by_default_and_validated():
    config = McpStdioServerConfig(name="a", command="b")
    assert config.call_timeout_seconds is None and config.ping_interval_seconds is None
    assert config.ping_timeout_seconds == 10.0 and config.reconnect_on_ping_failure is False
    http = McpHttpServerConfig(name="a", url="http://localhost:1/mcp")
    assert http.call_timeout_seconds is None and http.ping_interval_seconds is None
    for field, bad in (
        ("call_timeout_seconds", 0),
        ("call_timeout_seconds", -1),
        ("call_timeout_seconds", 86_401),
        ("ping_interval_seconds", 0),
        ("ping_interval_seconds", 3_601),
        ("ping_timeout_seconds", 0),
        ("ping_timeout_seconds", 3_601),
    ):
        with pytest.raises(ValueError, match=field):
            McpStdioServerConfig(name="a", command="b", **{field: bad})
        with pytest.raises(ValueError, match=field):
            McpHttpServerConfig(name="a", url="http://localhost:1/mcp", **{field: bad})


def test_a_configured_call_timeout_ends_a_slow_tool_call_and_leaves_the_session_usable(
    config_for,
):
    async def scenario():
        manager = McpClientManager([config_for(call_timeout_seconds=1.0)])
        try:
            await manager.connect_all()
            started = time.monotonic()
            with pytest.raises(McpCallTimeoutError) as slow:
                await manager.call_tool("demo", "sleepy", {"seconds": 20})
            waited = time.monotonic() - started
            reply = await manager.call_tool("demo", "echo", {"text": "after"})
            return slow.value, waited, reply, status_of(manager).state
        finally:
            await manager.close()

    error, waited, reply, state = arun(scenario(), 90)
    assert isinstance(error, McpToolCallError) and isinstance(error, McpResourceReadError)
    assert 'MCP tool call to "demo::sleepy" timed out after 1s' in str(error)
    assert (error.server_name, error.seconds) == ("demo", 1.0)
    assert 0.9 <= waited < 8
    assert reply == "echo:after" and state is McpConnectionState.CONNECTED


def test_a_per_call_timeout_overrides_the_configured_one_in_both_directions(config_for):
    async def scenario():
        manager = McpClientManager([config_for(call_timeout_seconds=1.0)])
        try:
            await manager.connect_all()
            patient = await manager.call_tool("demo", "sleepy", {"seconds": 2}, timeout_seconds=15)
            with pytest.raises(McpCallTimeoutError, match="timed out after 0.5s"):
                await manager.call_tool("demo", "sleepy", {"seconds": 20}, timeout_seconds=0.5)
            return patient
        finally:
            await manager.close()

    assert arun(scenario(), 90) == "done"


def test_without_any_timeout_a_slow_call_still_waits_for_the_server(config_for):
    async def scenario():
        manager = McpClientManager([config_for()])
        try:
            await manager.connect_all()
            return await manager.call_tool("demo", "sleepy", {"seconds": 2})
        finally:
            await manager.close()

    assert arun(scenario(), 60) == "done"


def test_a_timeout_that_is_not_positive_is_refused_naming_the_argument(config_for):
    async def scenario():
        manager = McpClientManager([config_for()])
        try:
            await manager.connect_all()
            for bad in (0, -2.5):
                with pytest.raises(ValueError, match="timeout_seconds must be positive, got"):
                    await manager.call_tool("demo", "echo", {"text": "x"}, timeout_seconds=bad)
                with pytest.raises(ValueError, match="timeout_seconds must be positive, got"):
                    await manager.read_resource("demo", "demo://slow", timeout_seconds=bad)
        finally:
            await manager.close()

    arun(scenario(), 60)


def test_a_resource_read_honours_the_same_deadlines(config_for):
    async def scenario():
        manager = McpClientManager([config_for(call_timeout_seconds=1.0)])
        try:
            await manager.connect_all()
            with pytest.raises(McpCallTimeoutError) as slow:
                await manager.read_resource("demo", "demo://slow")
            reply = await manager.call_tool("demo", "echo", {"text": "after"})
            return slow.value, reply
        finally:
            await manager.close()

    error, reply = arun(scenario(), 90)
    assert 'MCP resource read "demo::demo://slow" timed out after 1s' in str(error)
    assert isinstance(error, McpResourceReadError) and reply == "echo:after"


def test_a_server_killed_between_calls_is_marked_failed_by_the_ping(config_for):
    async def scenario():
        config = config_for(ping_interval_seconds=0.3, ping_timeout_seconds=2)
        manager = McpClientManager([config])
        try:
            await manager.connect_all()
            kill_process_tree(int(await manager.call_tool("demo", "pid", {})))
            status = await wait_until(
                lambda: (
                    status_of(manager)
                    if status_of(manager).state is McpConnectionState.FAILED
                    else None
                ),
                20,
                "the ping marking the dead server failed",
            )
            with pytest.raises(McpServerNotConnectedError, match="is not connected"):
                await manager.call_tool("demo", "echo", {"text": "x"})
            return status
        finally:
            await manager.close()

    status = arun(scenario(), 90)
    assert 'stdio command "' in status.detail and "stopped answering" in status.detail
    assert status.tools == [] and status.resources == []


def test_a_server_that_stops_answering_is_failed_released_and_its_pending_call_ends(config_for):
    async def scenario():
        config = config_for(ping_interval_seconds=0.3, ping_timeout_seconds=1)
        manager = McpClientManager([config])
        try:
            await manager.connect_all()
            pid = int(await manager.call_tool("demo", "pid", {}))
            hung = asyncio.create_task(manager.call_tool("demo", "freeze", {}))
            status = await wait_until(
                lambda: (
                    status_of(manager)
                    if status_of(manager).state is McpConnectionState.FAILED
                    else None
                ),
                30,
                "the ping marking the frozen server failed",
            )
            ended = await asyncio.wait_for(asyncio.gather(hung, return_exceptions=True), 30)
            await wait_until(lambda: not process_alive(pid), 30, "the frozen child being stopped")
            return status, ended[0]
        finally:
            await manager.close()

    status, ended = arun(scenario(), 120)
    assert "no answer to a ping within 1s" in status.detail
    assert isinstance(ended, McpServerNotConnectedError)


def test_with_reconnect_enabled_a_killed_server_comes_back_without_a_call(config_for):
    async def scenario():
        config = config_for(
            ping_interval_seconds=0.3, ping_timeout_seconds=2, reconnect_on_ping_failure=True
        )
        manager = McpClientManager([config])
        try:
            await manager.connect_all()
            first = int(await manager.call_tool("demo", "pid", {}))
            kill_process_tree(first)

            async def second_pid():
                try:
                    return int(await manager.call_tool("demo", "pid", {}))
                except (McpServerNotConnectedError, McpToolCallError):
                    return None

            deadline = time.monotonic() + 40
            second = None
            while time.monotonic() < deadline:
                found = await second_pid()
                if found is not None and found != first:
                    second = found
                    break
                await asyncio.sleep(0.3)
            echoed = await manager.call_tool("demo", "echo", {"text": "revived"})
            return first, second, echoed, status_of(manager).state
        finally:
            await manager.close()

    first, second, echoed, state = arun(scenario(), 120)
    assert second is not None and second != first
    assert echoed == "echo:revived" and state is McpConnectionState.CONNECTED


def test_a_healthy_server_is_never_marked_failed_by_the_ping(config_for):
    async def scenario():
        manager = McpClientManager([config_for(ping_interval_seconds=0.2, ping_timeout_seconds=2)])
        try:
            await manager.connect_all()
            states = set()
            for _ in range(12):
                states.add(status_of(manager).state)
                await manager.call_tool("demo", "echo", {"text": "alive"})
                await asyncio.sleep(0.2)
            return states
        finally:
            await manager.close()

    assert arun(scenario(), 60) == {McpConnectionState.CONNECTED}


def test_closing_the_manager_stops_the_pings(config_for):
    async def scenario():
        manager = McpClientManager([config_for(ping_interval_seconds=0.2)])
        await manager.connect_all()
        monitors = [
            task for task in asyncio.all_tasks() if (task.get_name() or "").startswith("mcp-ping-")
        ]
        await manager.close()
        await asyncio.sleep(0.5)
        return len(monitors), [task.done() for task in monitors]

    count, done = arun(scenario(), 60)
    assert count == 1 and done == [True]


def test_a_reconnect_by_the_host_restarts_the_ping(config_for):
    async def scenario():
        config = config_for(ping_interval_seconds=0.3, ping_timeout_seconds=2)
        manager = McpClientManager([config])
        try:
            await manager.connect_all()
            await manager.reconnect("demo")
            kill_process_tree(int(await manager.call_tool("demo", "pid", {})))
            return await wait_until(
                lambda: (
                    status_of(manager)
                    if status_of(manager).state is McpConnectionState.FAILED
                    else None
                ),
                20,
                "the ping after a reconnect marking the dead server failed",
            )
        finally:
            await manager.close()

    assert "stopped answering" in arun(scenario(), 90).detail


def test_an_http_server_that_goes_away_is_marked_failed_by_the_ping(tmp_path):
    server = Server(tmp_path)
    try:

        async def scenario():
            config = McpHttpServerConfig(
                name="sdk",
                url=server.url,
                headers=server.auth_headers,
                ping_interval_seconds=0.3,
                ping_timeout_seconds=2,
            )
            manager = McpClientManager([config])
            try:
                await manager.connect_all()
                assert status_of(manager).state is McpConnectionState.CONNECTED
                server.stop()
                return await wait_until(
                    lambda: (
                        status_of(manager)
                        if status_of(manager).state is McpConnectionState.FAILED
                        else None
                    ),
                    40,
                    "the ping marking the stopped HTTP server failed",
                )
            finally:
                await manager.close()

        status = arun(scenario(), 120)
        assert 'HTTP endpoint "http://127.0.0.1:' in status.detail
        assert "stopped answering" in status.detail
        assert server.token not in status.detail
    finally:
        server.stop()


def test_the_first_report_of_a_closed_connection_keeps_its_more_specific_detail(config_for):
    async def scenario():
        config = config_for(ping_interval_seconds=0.3, ping_timeout_seconds=1)
        manager = McpClientManager([config])
        try:
            await manager.connect_all()
            hung = asyncio.create_task(manager.call_tool("demo", "freeze", {}))
            await wait_until(
                lambda: status_of(manager).state is McpConnectionState.FAILED, 30, "the failure"
            )
            await asyncio.gather(hung, return_exceptions=True)
            return status_of(manager)
        finally:
            await manager.close()

    assert "no answer to a ping" in arun(scenario(), 120).detail
