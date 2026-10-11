import json
import sys
import threading

import pytest
from pydantic import ValidationError

from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskOptionError, TaskRunOptions
from nailong_agent_sdk.foundations.contracts import (
    AgentRunStatus,
    FallbackModelBinding,
    ModelBinding,
)
from nailong_agent_sdk.mcp.client_types import McpStdioServerConfig
from nailong_agent_sdk.tools.core import core_tool_definitions
from tests.support.agents import arun, definition, final, task, tool, tool_call
from tests.support.chat_provider import ScriptedChatTransport, final_chat, tool_chat
from tests.support.extended_paths import resolve_files_in_extended_spelling, windows_only
from tests.support.mcp_stdio import STDIO_SERVER

KEY = "sk-live-0123456789abcdef"
ENDPOINT = {"base_url": "http://127.0.0.1:9/v1", "api_key": KEY, "allow_insecure_http": True}
BUILTIN = {tool_.name: tool_ for tool_ in core_tool_definitions()}


def options(**changes):
    return TaskRunOptions.model_validate({"model_endpoint": ENDPOINT, **changes})


def runner(tmp_path, bodies, **settings):
    services = AgentRuntimeServices.open(tmp_path / "service")
    transport = ScriptedChatTransport(bodies)
    return AgentTaskRunner(services, transport=transport, **settings), services, transport


def workspace_of(services, task_id="t1"):
    return services.run_root / "workspaces" / task_id


def seen_by_the_model(transport, request_index):
    return json.dumps(transport.requests[request_index]["payload"]["messages"])


def test_a_live_run_reaches_the_endpoint_the_caller_names_with_the_callers_key(tmp_path):
    subject, services, transport = runner(tmp_path, [final_chat()])
    result = arun(subject.run(definition(), task(), options()))
    assert result.status is AgentRunStatus.COMPLETED
    (request,) = transport.requests
    assert request["url"] == "http://127.0.0.1:9/v1/chat/completions"
    assert request["headers"]["Authorization"] == f"Bearer {KEY}"
    assert request["payload"]["model"] == "fake-1"
    events = services.telemetry.list_events("t1", limit=500)
    recorded = json.dumps(
        [result.model_dump(mode="json"), [event.model_dump(mode="json") for event in events]]
    )
    assert KEY not in recorded
    assert "run.created" in {event.event_type for event in events}


def test_the_key_never_appears_in_a_dump_of_the_options():
    parsed = options()
    assert KEY not in repr(parsed) and KEY not in json.dumps(parsed.model_dump(mode="json"))


def test_a_tool_is_blocked_until_the_caller_grants_its_capability(tmp_path):
    read = definition(tools=[BUILTIN["read_file"]], max_iterations=4)
    subject, _, _ = runner(
        tmp_path / "denied", [tool_chat("c1", "read_file", {"path": "note.txt"}), final_chat()]
    )
    blocked = arun(subject.run(read, task(), options()))
    assert blocked.status is AgentRunStatus.BLOCKED
    assert blocked.reason == 'Role "agent" lacks capability "filesystem.read".'

    subject, services, transport = runner(
        tmp_path / "granted", [tool_chat("c1", "read_file", {"path": "note.txt"}), final_chat()]
    )
    workspace_of(services).mkdir(parents=True)
    (workspace_of(services) / "note.txt").write_text("the grant opened this file", "utf-8")
    granted = arun(
        subject.run(read, task(), options(permissions={"capabilities": ["filesystem.read"]}))
    )
    assert granted.status is AgentRunStatus.COMPLETED
    assert "the grant opened this file" in seen_by_the_model(transport, 1)


def test_builtin_tools_named_in_the_options_are_offered_to_the_model(tmp_path):
    subject, services, transport = runner(tmp_path, [final_chat()])
    arun(
        subject.run(
            definition(),
            task(),
            options(
                builtin_tools=["read_file", "glob"],
                permissions={"capabilities": ["filesystem.read"]},
            ),
        )
    )
    offered = [item["function"]["name"] for item in transport.requests[0]["payload"]["tools"]]
    assert offered == ["read_file", "glob"]


def test_a_mutating_capability_runs_only_when_the_caller_approves_it_in_advance(tmp_path):
    write = definition(tools=[BUILTIN["write_draft"]], max_iterations=4)
    call = tool_chat("c1", "write_draft", {"path": "out.txt", "content": "drafted"})
    permissions = {"capabilities": ["draft.write"]}

    subject, services, _ = runner(tmp_path / "unapproved", [call, final_chat()])
    unapproved = arun(
        subject.run(
            write, task(), options(permissions=permissions, declared_output_paths=["out.txt"])
        )
    )
    assert unapproved.status is AgentRunStatus.BLOCKED
    assert unapproved.reason == 'Capability "draft.write" requires a typed approval decision.'
    assert not (workspace_of(services) / "out.txt").exists()

    subject, services, _ = runner(tmp_path / "approved", [call, final_chat()])
    approved = arun(
        subject.run(
            write,
            task(),
            options(
                permissions={**permissions, "approved_capabilities": ["draft.write"]},
                declared_output_paths=["out.txt"],
            ),
        )
    )
    assert approved.status is AgentRunStatus.COMPLETED
    assert (workspace_of(services) / "out.txt").read_text("utf-8") == "drafted"


def test_a_custom_workspace_inside_the_service_root_is_used(tmp_path):
    subject, services, _ = runner(
        tmp_path,
        [tool_chat("c1", "write_draft", {"path": "out.txt", "content": "here"}), final_chat()],
    )
    arun(
        subject.run(
            definition(tools=[BUILTIN["write_draft"]], max_iterations=4),
            task(),
            options(
                permissions={
                    "capabilities": ["draft.write"],
                    "approved_capabilities": ["draft.write"],
                },
                declared_output_paths=["out.txt"],
                workspace="jobs/alpha",
            ),
        )
    )
    assert (services.run_root / "jobs" / "alpha" / "out.txt").read_text("utf-8") == "here"


@windows_only
def test_a_workspace_resolving_in_the_extended_spelling_is_still_judged_by_its_location(
    tmp_path, monkeypatch
):
    subject, services, _ = runner(
        tmp_path,
        [tool_chat("c1", "write_draft", {"path": "out.txt", "content": "here"}), final_chat()],
    )
    resolve_files_in_extended_spelling(monkeypatch)
    permissions = {
        "capabilities": ["draft.write"],
        "approved_capabilities": ["draft.write"],
    }
    result = arun(
        subject.run(
            definition(tools=[BUILTIN["write_draft"]], max_iterations=4),
            task(),
            options(permissions=permissions, declared_output_paths=["out.txt"], workspace="jobs/a"),
        )
    )
    assert result.status is AgentRunStatus.COMPLETED
    assert (services.run_root / "jobs" / "a" / "out.txt").read_text("utf-8") == "here"
    for bad in ("../escape", ".", "jobs/..", ".agent-runs"):
        with pytest.raises(TaskOptionError, match="workspace"):
            arun(subject.run(definition(), task(), options(workspace=bad)))


@pytest.mark.parametrize(
    "bad",
    [
        "../escape",
        "..\\escape",
        "/absolute",
        "C:\\absolute",
        "C:drive",
        " ",
        ".",
        "jobs/..",
        ".agent-runs",
        ".agent-telemetry/inside",
    ],
)
def test_a_workspace_outside_the_service_root_or_over_its_records_is_refused(tmp_path, bad):
    subject, _, transport = runner(tmp_path, [])
    with pytest.raises(TaskOptionError) as raised:
        arun(subject.run(definition(), task(), options(workspace=bad)))
    assert "workspace" in str(raised.value) and transport.requests == []


COMMAND = {
    "name": "greet",
    "command": [sys.executable, "-c", "print('hello from the template')"],
    "timeout_seconds": 30,
}
PROCESS_PERMISSIONS = {
    "capabilities": ["process.execute"],
    "approved_capabilities": ["process.execute"],
}


def test_a_registered_command_the_caller_declares_can_be_run(tmp_path):
    subject, _, transport = runner(
        tmp_path,
        [tool_chat("c1", "run_registered_command", {"template_name": "greet"}), final_chat()],
    )
    result = arun(
        subject.run(
            definition(max_iterations=4),
            task(),
            options(
                builtin_tools=["run_registered_command"],
                command_templates=[COMMAND],
                permissions=PROCESS_PERMISSIONS,
            ),
        )
    )
    assert result.status is AgentRunStatus.COMPLETED
    assert "hello from the template" in seen_by_the_model(transport, 1)


def test_process_options_are_refused_when_the_runner_does_not_allow_them(tmp_path):
    subject, _, transport = runner(
        tmp_path, [], allow_process_options=False, process_options_hint="Flip the switch."
    )
    with pytest.raises(TaskOptionError) as raised:
        arun(
            subject.run(
                definition(),
                task(),
                options(
                    builtin_tools=["run_registered_command"],
                    command_templates=[COMMAND],
                    permissions=PROCESS_PERMISSIONS,
                ),
            )
        )
    assert str(raised.value) == (
        "command_templates would start processes on the host running this agent, and this "
        "runner does not allow that. Flip the switch."
    )
    assert transport.requests == []


def stdio_server(tmp_path):
    script = tmp_path / "stdio_server.py"
    script.write_text(STDIO_SERVER, "utf-8")
    return McpStdioServerConfig(name="demo", command=sys.executable, args=[str(script)])


def test_tools_of_an_mcp_server_the_caller_names_are_offered_and_callable(tmp_path):
    subject, _, transport = runner(
        tmp_path,
        [tool_chat("c1", "mcp__demo__echo", {"text": "hi"}), final_chat()],
    )
    result = arun(
        subject.run(
            definition(max_iterations=4),
            task(),
            options(
                mcp_servers=[stdio_server(tmp_path)],
                permissions={"capabilities": ["mcp.demo"], "approved_capabilities": ["mcp.demo"]},
            ),
        ),
        timeout=120,
    )
    assert result.status is AgentRunStatus.COMPLETED
    offered = {item["function"]["name"] for item in transport.requests[0]["payload"]["tools"]}
    assert {"mcp__demo__echo", "mcp__demo__explode"} <= offered
    assert "echo:hi" in seen_by_the_model(transport, 1)


def test_an_mcp_server_that_cannot_start_is_reported_by_name(tmp_path):
    subject, _, transport = runner(tmp_path, [])
    missing = McpStdioServerConfig(name="ghost", command="definitely-not-a-command-xyz")
    with pytest.raises(TaskOptionError) as raised:
        arun(
            subject.run(
                definition(),
                task(),
                options(
                    mcp_servers=[missing],
                    permissions={"capabilities": ["mcp.ghost"]},
                ),
            ),
            timeout=120,
        )
    assert str(raised.value).startswith('MCP servers did not connect: "ghost": ')
    assert transport.requests == []


def test_a_tool_the_definition_declares_must_have_an_implementation(tmp_path):
    subject, _, transport = runner(tmp_path, [])
    with pytest.raises(TaskOptionError) as raised:
        arun(
            subject.run(
                definition(tools=[tool("teleport")]),
                task(),
                options(permissions={"capabilities": []}),
            )
        )
    assert str(raised.value).startswith("Tools ['teleport'] have no implementation")
    assert transport.requests == []


def test_an_unknown_builtin_tool_name_lists_the_real_ones(tmp_path):
    subject, _, _ = runner(tmp_path, [])
    with pytest.raises(TaskOptionError) as raised:
        arun(
            subject.run(
                definition(),
                task(),
                options(builtin_tools=["teleport"], permissions={"capabilities": []}),
            )
        )
    assert str(raised.value).startswith("builtin_tools ['teleport'] are not built-in tools")
    assert "read_file" in str(raised.value)


def test_an_endpoint_that_is_not_https_is_refused_unless_the_caller_allows_it(tmp_path):
    subject, _, _ = runner(tmp_path, [])
    insecure = {"base_url": "http://example.test/v1", "api_key": KEY}
    with pytest.raises(TaskOptionError) as raised:
        arun(subject.run(definition(), task(), options(model_endpoint=insecure)))
    assert str(raised.value) == (
        "model_endpoint is invalid: OpenAI-compatible base_url must use HTTPS unless "
        "allow_insecure_http is explicit."
    )


def test_a_fallback_model_receives_its_own_binding(tmp_path):
    binding = ModelBinding(
        provider="fake",
        model="primary-1",
        fallbacks=[
            FallbackModelBinding(provider="fake", model="backup-2", parameters={"top_p": 0.5})
        ],
    )
    chosen = definition().model_copy(update={"model_binding": binding})
    subject, _, transport = runner(tmp_path, [RuntimeError("primary is down"), final_chat()])
    result = arun(subject.run(chosen, task(), options()))
    assert result.status is AgentRunStatus.COMPLETED
    assert [request["payload"]["model"] for request in transport.requests] == [
        "primary-1",
        "backup-2",
    ]
    assert transport.requests[1]["payload"]["top_p"] == 0.5


def test_a_run_without_an_endpoint_replays_its_scripted_turns(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "service")
    subject = AgentTaskRunner(services)
    scripted = TaskRunOptions.model_validate({"scripted_turns": [final()]})
    result = arun(subject.run(definition(), task(), scripted))
    assert result.status is AgentRunStatus.COMPLETED
    events = [e.event_type for e in services.telemetry.list_events("t1", limit=500)]
    assert "run.created" in events and "run.completed" in events
    created = next(
        e for e in services.telemetry.list_events("t1", limit=500) if e.event_type == "run.created"
    )
    assert created.payload["mode"] == "deterministic"


def test_scripted_tool_calls_still_use_the_deterministic_echo_executor(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "service")
    subject = AgentTaskRunner(services)
    scripted = TaskRunOptions.model_validate(
        {"scripted_turns": [tool_call("c1", "echo", {"a": 1}), final()]}
    )
    echoing = definition(tools=[tool("echo")], max_iterations=4)
    assert arun(subject.run(echoing, task(), scripted)).status is AgentRunStatus.COMPLETED


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"scripted_turns": [final()]},
            "model_endpoint and scripted_turns are mutually exclusive",
        ),
        ({"mode": "deterministic"}, 'mode "deterministic" conflicts with model_endpoint'),
        (
            {"builtin_tools": ["read_file"]},
            "builtin_tools require permissions: without a capability grant",
        ),
        (
            {"declared_output_paths": ["a"], "web_search": "duckduckgo-html"},
            "declared_output_paths, web_search require permissions",
        ),
        (
            {"permissions": {"capabilities": []}, "builtin_tools": ["a", "a"]},
            'builtin_tools must be unique; repeated: "a"',
        ),
        (
            {"permissions": {"capabilities": ["a"], "approved_capabilities": ["b"]}},
            "permissions.approved_capabilities ['b'] must also be listed",
        ),
        (
            {
                "permissions": {"capabilities": []},
                "mcp_servers": [{"name": "demo", "url": "http://127.0.0.1:1/mcp"}],
            },
            'mcp_servers lists "demo" but permissions.capabilities does not grant "mcp.demo"',
        ),
    ],
)
def test_inconsistent_options_are_rejected_with_the_field_that_is_wrong(changes, message):
    with pytest.raises(ValidationError) as raised:
        options(**changes)
    assert message in str(raised.value)


class BlockingTransport(ScriptedChatTransport):
    def __init__(self, bodies):
        super().__init__(bodies)
        self.started = threading.Event()
        self.release = threading.Event()

    def post_json(self, url, *, headers, payload, timeout_seconds):
        self.started.set()
        assert self.release.wait(30), "the test never released the model call"
        return super().post_json(
            url, headers=headers, payload=payload, timeout_seconds=timeout_seconds
        )


def start_blocked_run(tmp_path, bodies):
    services = AgentRuntimeServices.open(tmp_path / "service")
    transport = BlockingTransport(bodies)
    subject = AgentTaskRunner(services, transport=transport)
    outcome = {}

    def work():
        try:
            outcome["result"] = arun(
                subject.run(
                    definition(tools=[BUILTIN["brief"]], max_iterations=6),
                    task(),
                    options(permissions={"capabilities": ["utility.brief"]}),
                )
            )
        except BaseException as error:
            outcome["error"] = error

    thread = threading.Thread(target=work)
    thread.start()
    assert transport.started.wait(30), "the run never reached the model"
    return subject, transport, thread, outcome


def test_a_running_task_can_be_cancelled_by_id(tmp_path):
    subject, transport, thread, outcome = start_blocked_run(
        tmp_path, [tool_chat("c1", "brief", {"text": "hi"}), final_chat()]
    )
    assert subject.cancel("t1") is True
    transport.release.set()
    thread.join(60)
    assert outcome["result"].status is AgentRunStatus.CANCELLED
    assert subject.cancel("t1") is False


def test_a_task_id_that_is_already_running_is_refused(tmp_path):
    subject, transport, thread, outcome = start_blocked_run(tmp_path, [final_chat()])
    try:
        with pytest.raises(TaskOptionError) as raised:
            arun(subject.run(definition(), task(), options()))
        assert str(raised.value) == 'Agent task "t1" is already running.'
    finally:
        transport.release.set()
        thread.join(60)
    assert outcome["result"].status is AgentRunStatus.COMPLETED


def test_cancelling_a_task_that_is_not_running_reports_false(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "service")
    assert AgentTaskRunner(services).cancel("t1") is False
