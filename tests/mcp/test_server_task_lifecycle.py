import json
import time

from tests.support.agents import definition, final, task
from tests.support.chat_provider import chat_provider, final_chat, tool_chat
from tests.support.mcp_cases import BUILTIN, live_options
from tests.support.mcp_server import Server


def scripted_run(instance, task_id):
    return instance.call(
        "run_agent_task",
        {
            "definition": definition().model_dump(mode="json"),
            "task": task(task_id).model_dump(mode="json"),
            "runtime_options": {"scripted_turns": [final()]},
        },
    )


def test_prune_runs_reports_by_default_and_deletes_only_when_told_to(tmp_path):
    from nailong_agent_sdk.agent.retention import read_tombstones

    instance = Server(tmp_path)
    try:
        for name in ("prune-a", "prune-b", "prune-c"):
            assert scripted_run(instance, name)["result"]["status"] == "completed"
        dry = instance.call("prune_runs", {"older_than_seconds": 0.001})
        assert dry["ok"] is True and dry["report"]["dry_run"] is True
        assert [run["run_id"] for run in dry["report"]["runs"]] == ["prune-a", "prune-b", "prune-c"]
        assert not (instance.run_root / ".agent-retention").exists()
        assert instance.call("get_telemetry_events", {"run_id": "prune-a"})["events"]
        applied = instance.call(
            "prune_runs", {"older_than_seconds": 0.001, "dry_run": False, "max_runs": 2}
        )
        assert applied["ok"] is True and applied["report"]["dry_run"] is False
        assert [run["run_id"] for run in applied["report"]["runs"]] == ["prune-a", "prune-b"]
        assert [run["tombstone_sequence"] for run in applied["report"]["runs"]] == [1, 2]
        assert applied["report"]["problems"] == []
        for pruned in ("prune-a", "prune-b"):
            assert instance.call("get_telemetry_events", {"run_id": pruned})["events"] == []
            assert instance.call("get_audit_log", {"run_id": pruned})["events"] == []
        assert instance.call("get_telemetry_events", {"run_id": "prune-c"})["events"]
        runs = instance.call("list_telemetry_runs")["runs"]
        assert [item["run_id"] for item in runs] == ["prune-c"]
        assert [item.run_id for item in read_tombstones(instance.run_root)] == [
            "prune-a",
            "prune-b",
        ]
        refused = instance.call("prune_runs", {"older_than_seconds": 0})
        assert refused["ok"] is False and "older_than_seconds" in json.dumps(refused["errors"])
        recent = instance.call("prune_runs", {"older_than_seconds": 3600, "dry_run": False})
        assert recent["ok"] is True and recent["report"]["runs"] == []
        assert sum(recent["report"]["kept"].values()) == 1
    finally:
        instance.stop()


def test_the_audit_handle_budget_reaches_the_service_and_its_eviction_warning_names_it(tmp_path):
    instance = Server(tmp_path, extra_environment={"AGENT_RUNTIME_AUDIT_MAX_OPEN_HANDLES": "1"})
    try:
        for name in ("evict-a", "evict-b"):
            assert scripted_run(instance, name)["result"]["status"] == "completed"
        assert instance.call("get_audit_log", {"run_id": "evict-a"})["integrity_chain_valid"]
    finally:
        instance.stop()
    log = "".join(instance.log_path.read_text("utf-8").split())
    assert "Audittranscriptstoreevicted1" in log
    assert "max_open_handles=1" in log and "AGENT_RUNTIME_AUDIT_MAX_OPEN_HANDLES" in log


WRITE_TOOL = BUILTIN["write_draft"]


def write_options(base_url, **permissions):
    return live_options(
        base_url,
        permissions={"capabilities": ["draft.write"], **permissions},
        declared_output_paths=["out.txt", "notes/b.md"],
    )


def written_files_script():
    return [
        tool_chat("c1", "write_draft", {"path": "out.txt", "content": "first line\nsecond line\n"}),
        tool_chat("c2", "write_draft", {"path": "notes/b.md", "content": "# note"}),
        final_chat(),
    ]


def test_the_files_a_task_wrote_can_be_listed_and_read_over_the_wire(server):
    with chat_provider(written_files_script()) as (base_url, _):
        run = server.call(
            "run_agent_task",
            {
                "definition": definition(tools=[WRITE_TOOL], max_iterations=6).model_dump(
                    mode="json"
                ),
                "task": task("mcp-files-1").model_dump(mode="json"),
                "runtime_options": write_options(base_url, approved_capabilities=["draft.write"]),
            },
        )
    assert run["ok"] and run["result"]["status"] == "completed", run
    listing = server.call("list_task_files", {"task_id": "mcp-files-1"})
    assert listing["ok"] is True, listing
    names = {entry["name"]: entry for entry in listing["listing"]["entries"]}
    assert names["out.txt"]["kind"] == "file" and names["notes"]["kind"] == "directory"
    nested = server.call("list_task_files", {"task_id": "mcp-files-1", "path": "notes"})
    assert [e["path"] for e in nested["listing"]["entries"]] == ["notes/b.md"]
    page = server.call(
        "read_task_file", {"task_id": "mcp-files-1", "path": "out.txt", "max_bytes": 10}
    )
    assert (
        page["ok"]
        and page["file"]["content"] == "first line\n"[:10]
        and page["file"]["next_offset"] == 10
    )
    rest = server.call(
        "read_task_file",
        {"task_id": "mcp-files-1", "path": "out.txt", "offset": page["file"]["next_offset"]},
    )
    assert page["file"]["content"] + rest["file"]["content"] == "first line\nsecond line\n"
    assert rest["file"]["next_offset"] is None
    raw = server.call(
        "read_task_file", {"task_id": "mcp-files-1", "path": "notes/b.md", "encoding": "base64"}
    )
    assert raw["file"]["content"] == "IyBub3Rl"


def test_reading_task_files_refuses_escapes_credentials_and_unknown_workspaces(server):
    workspace = server.run_root / "workspaces" / "mcp-files-2"
    workspace.mkdir(parents=True)
    (workspace / ".env").write_text("TOKEN=secret", "utf-8")
    (workspace / "ok.txt").write_text("fine", "utf-8")
    for denied in ("../mcp-files-1/out.txt", ".env", "/etc/passwd"):
        refused = server.call("read_task_file", {"task_id": "mcp-files-2", "path": denied})
        assert refused["ok"] is False
        message = refused["errors"][0]["message"]
        assert "secret" not in message and message.startswith(f'Task file path "{denied}"')
    hidden = server.call("list_task_files", {"task_id": "mcp-files-2"})
    assert [e["name"] for e in hidden["listing"]["entries"]] == ["ok.txt"]
    unknown = server.call("list_task_files", {"task_id": "no-such-task"})
    assert unknown["ok"] is False
    assert unknown["errors"][0]["message"] == (
        'Agent task "no-such-task" has no workspace directory "workspaces/no-such-task": '
        "nothing has been written there yet."
    )
    bad_workspace = server.call("list_task_files", {"task_id": "mcp-files-2", "workspace": "../x"})
    assert bad_workspace["ok"] is False and "workspace" in bad_workspace["errors"][0]["message"]


def test_a_task_that_waits_for_approval_is_paused_until_the_caller_decides_over_the_wire(server):
    from concurrent.futures import ThreadPoolExecutor

    script = [
        tool_chat("c1", "write_draft", {"path": "out.txt", "content": "approved text"}),
        final_chat(),
    ]
    with chat_provider(script) as (base_url, _):
        arguments = {
            "definition": definition(tools=[WRITE_TOOL], max_iterations=6).model_dump(mode="json"),
            "task": task("mcp-wait-1").model_dump(mode="json"),
            "runtime_options": write_options(base_url, approval_mode="wait"),
        }
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(server.call, "run_agent_task", arguments)
            pending = []
            for _ in range(200):
                listed = server.call("list_task_approvals", {"task_id": "mcp-wait-1"})
                pending = [a for a in listed.get("approvals", []) if a["status"] == "pending"]
                if pending:
                    break
                time.sleep(0.1)
            assert pending, listed
            assert not running.done()
            request = pending[0]
            assert request["capability"] == "draft.write" and request["run_id"] == "mcp-wait-1"
            wrong = server.call(
                "decide_task_approval",
                {"task_id": "mcp-wait-1", "approval_id": "approval-99", "approved": True},
            )
            assert wrong["errors"][0]["message"] == (
                'Agent task "mcp-wait-1" has no approval request "approval-99".'
            )
            decided = server.call(
                "decide_task_approval",
                {
                    "task_id": "mcp-wait-1",
                    "approval_id": request["approval_id"],
                    "approved": True,
                    "reason": "reviewed by the operator",
                },
            )
            assert decided["ok"] and decided["approval"]["status"] == "approved"
            run = running.result(60)
    assert run["ok"] and run["result"]["status"] == "completed", run
    written = server.call("read_task_file", {"task_id": "mcp-wait-1", "path": "out.txt"})
    assert written["file"]["content"] == "approved text"
    again = server.call(
        "decide_task_approval",
        {"task_id": "mcp-wait-1", "approval_id": request["approval_id"], "approved": False},
    )
    assert again["errors"][0]["message"] == 'No agent task "mcp-wait-1" is running.'


def test_cancelling_a_task_stops_a_model_call_that_is_still_waiting_for_the_provider(server):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    release = threading.Event()
    with chat_provider([final_chat()], hold=release) as (base_url, requests):
        arguments = {
            "definition": definition(max_iterations=4).model_dump(mode="json"),
            "task": task("mcp-cancel-1").model_dump(mode="json"),
            "runtime_options": live_options(base_url),
        }
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(server.call, "run_agent_task", arguments)
                for _ in range(100):
                    if requests:
                        break
                    time.sleep(0.1)
                assert requests, "the provider never received the model call"
                started = time.monotonic()
                cancelled = server.call("cancel_agent_task", {"task_id": "mcp-cancel-1"})
                assert cancelled == {"ok": True, "cancelled": True}
                run = running.result(30)
                elapsed = time.monotonic() - started
        finally:
            release.set()
    assert run["ok"] and run["result"]["status"] == "cancelled", run
    assert run["result"]["reason"] == "Execution was cancelled while the model turn was running."
    assert elapsed < 10, elapsed
    assert server.call("list_metric_definitions")["ok"] is True
