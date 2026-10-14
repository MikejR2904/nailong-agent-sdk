import json
import sys

from tests.support.agents import definition, final, task
from tests.support.chat_provider import chat_provider, final_chat, tool_chat
from tests.support.mcp_cases import BUILTIN, KEY, live_options
from tests.support.mcp_server import Server, raw_post


def test_a_live_run_uses_the_model_tools_and_permissions_the_caller_supplies(server):
    workspace = server.run_root / "workspaces" / "mcp-live-1"
    workspace.mkdir(parents=True)
    (workspace / "note.txt").write_text("the caller granted read access", "utf-8")
    script = [tool_chat("c1", "read_file", {"path": "note.txt"}), final_chat()]
    with chat_provider(script) as (base_url, requests):
        run = server.call(
            "run_agent_task",
            {
                "definition": definition(tools=[BUILTIN["read_file"]], max_iterations=4).model_dump(
                    mode="json"
                ),
                "task": task("mcp-live-1").model_dump(mode="json"),
                "runtime_options": live_options(
                    base_url, permissions={"capabilities": ["filesystem.read"]}
                ),
            },
        )
    assert run["ok"] and run["result"]["status"] == "completed", run
    assert requests[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert requests[0]["json"]["model"] == "fake-1"
    assert "the caller granted read access" in json.dumps(requests[1]["json"]["messages"])
    events = server.call("get_telemetry_events", {"run_id": "mcp-live-1", "limit": 500})
    assert KEY not in json.dumps([run, events])


def test_a_live_run_without_a_grant_is_blocked_with_the_missing_capability_named(server):
    script = [tool_chat("c1", "read_file", {"path": "note.txt"}), final_chat()]
    with chat_provider(script) as (base_url, _):
        run = server.call(
            "run_agent_task",
            {
                "definition": definition(tools=[BUILTIN["read_file"]], max_iterations=4).model_dump(
                    mode="json"
                ),
                "task": task("mcp-live-2").model_dump(mode="json"),
                "runtime_options": live_options(base_url),
            },
        )
    assert run["ok"] and run["result"]["status"] == "blocked"
    assert run["result"]["reason"] == 'Role "agent" lacks capability "filesystem.read".'


COMMAND = {
    "name": "greet",
    "command": [sys.executable, "-c", "print('hello over the wire')"],
    "timeout_seconds": 30,
}


def command_options(base_url):
    return live_options(
        base_url,
        builtin_tools=["run_registered_command"],
        command_templates=[COMMAND],
        permissions={
            "capabilities": ["process.execute"],
            "approved_capabilities": ["process.execute"],
        },
    )


def test_process_options_are_refused_unless_the_service_operator_enables_them(server):
    with chat_provider([]) as (base_url, requests):
        refused = server.call(
            "run_agent_task",
            {
                "definition": definition().model_dump(mode="json"),
                "task": task("mcp-live-3").model_dump(mode="json"),
                "runtime_options": command_options(base_url),
            },
        )
    assert refused["ok"] is False and requests == []
    assert refused["errors"][0]["message"].endswith(
        "Start the service with AGENT_RUNTIME_ALLOW_PROCESS_OPTIONS=1 to allow it."
    )


def test_an_operator_who_enables_process_options_lets_the_caller_declare_commands(tmp_path):
    enabled = Server(tmp_path, extra_environment={"AGENT_RUNTIME_ALLOW_PROCESS_OPTIONS": "1"})
    try:
        script = [
            tool_chat("c1", "run_registered_command", {"template_name": "greet"}),
            final_chat(),
        ]
        with chat_provider(script) as (base_url, requests):
            run = enabled.call(
                "run_agent_task",
                {
                    "definition": definition(max_iterations=4).model_dump(mode="json"),
                    "task": task("mcp-live-4").model_dump(mode="json"),
                    "runtime_options": command_options(base_url),
                },
            )
        assert run["ok"] and run["result"]["status"] == "completed", run
        assert "hello over the wire" in json.dumps(requests[1]["json"]["messages"])
    finally:
        enabled.stop()


def test_cancelling_a_task_that_is_not_running_names_it(server):
    refused = server.call("cancel_agent_task", {"task_id": "never-started"})
    assert refused["ok"] is False
    assert refused["errors"][0]["message"] == 'No agent task "never-started" is running.'


def test_options_that_contradict_each_other_are_rejected_before_anything_runs(server):
    bad = server.call(
        "run_agent_task",
        {
            "definition": definition().model_dump(mode="json"),
            "task": task("mcp-live-5").model_dump(mode="json"),
            "runtime_options": live_options("http://127.0.0.1:9/v1", scripted_turns=[final()]),
        },
    )
    assert bad["ok"] is False
    assert "model_endpoint and scripted_turns are mutually exclusive" in bad["errors"][0]["message"]


def test_hostile_scripted_output_does_not_break_the_server(server):
    deep: object = "leaf"
    for _ in range(120):
        deep = {"k": deep}
    schema = {
        "type": "object",
        "properties": {"status": {"const": "complete"}},
        "required": ["status"],
    }
    d = definition(output_schema=schema).model_dump(mode="json")
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "run_agent_task",
                "arguments": {
                    "definition": d,
                    "task": task("mcp-deep").model_dump(mode="json"),
                    "runtime_options": {
                        "scripted_turns": [
                            {"type": "final", "output": {"status": "complete", "deep": deep}}
                        ]
                    },
                },
            },
        }
    ).encode()
    status, raw = raw_post(
        server,
        body,
        {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        limit=6000,
    )
    assert status == 200 and b"Circular reference" not in raw, raw[:400]
    assert server.call("list_metric_definitions")["ok"] is True
