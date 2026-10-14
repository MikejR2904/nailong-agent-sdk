import asyncio
import dataclasses
import json
import sys
import textwrap

import pytest

from nailong_agent_sdk.mcp.client import McpClientManager
from nailong_agent_sdk.mcp.client_bridge import mcp_tools_as_extensions
from nailong_agent_sdk.mcp.client_types import McpHttpServerConfig, McpStdioServerConfig
from nailong_agent_sdk.tools.policy import CapabilityGrant
from nailong_agent_sdk.tools.registry import HarnessToolRegistry
from tests.support.agents import definition, final, task
from tests.support.mcp_server import Server
from tests.support.tools import build, invocation, tool_def

TRACE = "0af7651916cd43dd8448eb211c80319c"
SPAN = "b7ad6b7169203331"
HEADER = f"00-{TRACE}-{SPAN}-01"

META_SERVER = textwrap.dedent(
    """
    import json
    from mcp.server import MCPServer
    from mcp.server.mcpserver import Context

    server = MCPServer(name="meta")

    @server.tool(name="seen_meta")
    async def seen_meta(ctx: Context) -> str:
        meta = ctx.request_context.meta or {}
        return json.dumps({key: meta.get(key) for key in ("traceparent", "tracestate")})

    if __name__ == "__main__":
        server.run(transport="stdio")
    """
)


def arun(coro, timeout=120):
    async def guarded():
        return await asyncio.wait_for(coro, timeout)

    return asyncio.run(guarded())


@pytest.fixture
def script(tmp_path):
    path = tmp_path / "meta_server.py"
    path.write_text(META_SERVER, "utf-8")
    return path


def config(script, **options):
    return McpStdioServerConfig(name="meta", command=sys.executable, args=[str(script)], **options)


def test_the_option_is_off_by_default_and_a_plain_setting():
    assert McpStdioServerConfig(name="x", command="python").propagate_trace_context is False
    assert McpHttpServerConfig(name="x", url="http://127.0.0.1:1").propagate_trace_context is False
    assert (
        McpStdioServerConfig(
            name="x", command="python", propagate_trace_context=True
        ).propagate_trace_context
        is True
    )


def test_a_server_that_opted_in_receives_the_traceparent_in_request_meta(script):
    async def scenario():
        manager = McpClientManager([config(script, propagate_trace_context=True)])
        try:
            await manager.connect_all()
            sent = json.loads(await manager.call_tool("meta", "seen_meta", {}, trace_parent=HEADER))
            absent = json.loads(await manager.call_tool("meta", "seen_meta", {}))
            return sent, absent
        finally:
            await manager.close()

    sent, absent = arun(scenario())
    assert sent == {"traceparent": HEADER, "tracestate": None}
    assert absent == {"traceparent": None, "tracestate": None}


def test_a_server_that_did_not_opt_in_never_receives_it(script):
    async def scenario():
        manager = McpClientManager([config(script)])
        try:
            await manager.connect_all()
            return json.loads(await manager.call_tool("meta", "seen_meta", {}, trace_parent=HEADER))
        finally:
            await manager.close()

    assert arun(scenario()) == {"traceparent": None, "tracestate": None}


def test_the_harness_hands_each_call_its_own_trace_parent_to_an_mcp_tool(script, tmp_path):
    other = f"00-{TRACE}-{'1' * 16}-01"

    async def scenario():
        manager = McpClientManager([config(script, propagate_trace_context=True)])
        try:
            await manager.connect_all()
            extensions, handlers = mcp_tools_as_extensions(manager)
            registry = HarnessToolRegistry.with_extensions(extensions, handlers=handlers)
            grant = [CapabilityGrant(role="worker", capabilities=["mcp.meta"], allowed_paths=["."])]
            executor, _ = build(tmp_path, registry=registry, grants=grant, approve=("mcp.meta",))
            name = "mcp__meta__seen_meta"
            seen = []
            for parent in (HEADER, other, None):
                call = dataclasses.replace(invocation(name, {}), trace_parent=parent)
                result = await executor.execute(tool_def(name), call)
                assert result.status == "succeeded", result.error
                seen.append(json.loads(result.output["content"])["traceparent"])
            return seen
        finally:
            await manager.close()

    assert arun(scenario()) == [HEADER, other, None]


@pytest.fixture(scope="module")
def sdk_server(tmp_path_factory):
    instance = Server(tmp_path_factory.mktemp("meta-sdk"))
    yield instance
    instance.stop()


def run_with_meta(server, meta, options=None, task_id="meta-run"):
    arguments = {
        "definition": definition().model_dump(mode="json"),
        "task": task(task_id).model_dump(mode="json"),
        "runtime_options": {"scripted_turns": [final()], **(options or {})},
    }

    async def scenario():
        async with server.session() as session:
            result = await session.call_tool("run_agent_task", arguments, meta=meta)
            return server.structured(result)

    return arun(scenario())


def test_run_agent_task_continues_the_trace_in_the_requests_meta(sdk_server):
    reply = run_with_meta(sdk_server, {"traceparent": HEADER}, task_id="meta-1")
    assert reply["ok"] and reply["result"]["profile"]["trace_id"] == TRACE
    root = next(s for s in reply["result"]["profile"]["spans"] if s["kind"] == "run")
    assert root["parent_span_id"] == SPAN


def test_an_explicit_traceparent_option_wins_over_the_meta(sdk_server):
    explicit = f"00-{'a' * 32}-{'b' * 16}-01"
    reply = run_with_meta(
        sdk_server, {"traceparent": HEADER}, {"traceparent": explicit}, task_id="meta-2"
    )
    assert reply["result"]["profile"]["trace_id"] == "a" * 32


def test_a_malformed_meta_traceparent_is_ignored_instead_of_failing_the_run(sdk_server):
    reply = run_with_meta(sdk_server, {"traceparent": "not-a-header"}, task_id="meta-3")
    assert reply["ok"] and reply["result"]["status"] == "completed"
    assert reply["result"]["profile"]["trace_id"] != TRACE
    without = run_with_meta(sdk_server, None, task_id="meta-4")
    assert without["ok"] and len(without["result"]["profile"]["trace_id"]) == 32


def test_the_injected_request_context_is_not_part_of_the_tool_schema(sdk_server):
    async def listing():
        async with sdk_server.session() as session:
            return (await session.list_tools()).tools

    run_tool = next(t for t in arun(listing()) if t.name == "run_agent_task")
    assert set(run_tool.input_schema["properties"]) == {"definition", "task", "runtime_options"}


def test_a_malformed_traceparent_option_is_a_validation_error_naming_the_field(sdk_server):
    reply = run_with_meta(sdk_server, None, {"traceparent": "nope"}, task_id="meta-5")
    assert reply["ok"] is False
    assert any("traceparent" in str(error) for error in reply["errors"])
