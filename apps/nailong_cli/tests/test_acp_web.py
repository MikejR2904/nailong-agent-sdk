from __future__ import annotations

import http.server
import io
import json
import threading
import urllib.request

from nailong_cli import server
from nailong_cli.acp import serve_acp
from nailong_cli.cli import main
from tests.conftest import call, factory, final


def runner(project, home, build):
    def run(argv, console):
        return main(
            argv,
            environ={"TEST_KEY": "k"},
            console=console,
            model_factory=build,
            home=home,
        )

    return run


def talk(project, home, build, messages, mode="plan"):
    lines = "".join(json.dumps(message) + "\n" for message in messages)
    out = io.StringIO()
    code = serve_acp(
        runner(project, home, build),
        [],
        project=project,
        mode=mode,
        stdin=io.StringIO(lines),
        stdout=out,
    )
    assert code == 0
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_an_editor_can_initialize_open_a_session_and_get_a_streamed_answer(project, home):
    build = factory(final("hello editor"))
    replies = talk(
        project,
        home,
        build,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}},
            {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": str(project)}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/prompt",
                "params": {
                    "sessionId": "nailong-acp-1",
                    "prompt": [{"type": "text", "text": "Say hello"}],
                },
            },
        ],
    )
    by_id = {item["id"]: item for item in replies if "id" in item}
    assert by_id[1]["result"]["protocolVersion"] == 1
    assert by_id[2]["result"] == {"sessionId": "nailong-acp-1"}
    assert by_id[3]["result"] == {"stopReason": "end_turn"}
    updates = [
        item["params"]["update"] for item in replies if item.get("method") == "session/update"
    ]
    chunks = [u for u in updates if u["sessionUpdate"] == "agent_message_chunk"]
    assert chunks and chunks[-1]["content"] == {"type": "text", "text": "hello editor"}


def test_tool_calls_are_reported_with_their_outcome(project, home):
    (project / "a.txt").write_text("x", encoding="utf-8")
    build = factory(call("read_file", path="a.txt"), final("done"))
    replies = talk(
        project,
        home,
        build,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/prompt",
                "params": {
                    "sessionId": "nailong-acp-1",
                    "prompt": [{"type": "text", "text": "Read"}],
                },
            },
        ],
    )
    updates = [
        item["params"]["update"] for item in replies if item.get("method") == "session/update"
    ]
    kinds = [u["sessionUpdate"] for u in updates]
    assert "tool_call" in kinds and "tool_call_update" in kinds
    assert (
        next(u for u in updates if u["sessionUpdate"] == "tool_call_update")["status"]
        == "completed"
    )


def test_bad_requests_get_specific_errors(project, home):
    replies = talk(
        project,
        home,
        factory(final("x")),
        [
            {"jsonrpc": "2.0", "id": 1, "method": "nope/method"},
            {"jsonrpc": "2.0", "id": 2, "method": "session/prompt", "params": {"sessionId": "zzz"}},
            {"jsonrpc": "2.0", "id": 3, "method": "session/new", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "session/prompt",
                "params": {"sessionId": "nailong-acp-1", "prompt": [{"type": "image"}]},
            },
            {
                "jsonrpc": "2.0",
                "method": "session/cancel",
                "params": {"sessionId": "nailong-acp-1"},
            },
        ],
    )
    errors = {item["id"]: item["error"] for item in replies if "error" in item}
    assert errors[1]["code"] == -32601 and "Method not found: nope/method" in errors[1]["message"]
    assert 'Unknown sessionId "zzz"' in errors[2]["message"]
    assert "no text block" in errors[4]["message"]


def serving(project, home):
    build = factory(final("hi"))
    service = server.AgentService(runner(project, home, build), [], project, "t" * 32)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server._handler(service, "test"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_the_service_describes_itself_in_openapi_and_shows_a_page(project, home):
    httpd, url = serving(project, home)
    try:
        with urllib.request.urlopen(url + "/openapi.json", timeout=10) as response:
            spec = json.loads(response.read())
        assert spec["openapi"].startswith("3.1") and "/run" in spec["paths"]
        assert spec["components"]["schemas"]["RunRequest"]["required"] == ["prompt"]
        with urllib.request.urlopen(url + "/", timeout=10) as response:
            page = response.read().decode()
            headers = dict(response.headers)
        assert "<title>nailong</title>" in page and "/run?stream=1" in page
        assert "default-src 'none'" in headers["Content-Security-Policy"]
        assert headers["X-Content-Type-Options"] == "nosniff"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_page_never_puts_model_output_into_html(project, home):
    from nailong_cli.web_page import PAGE

    assert "innerHTML" not in PAGE and "textContent" in PAGE and "eval(" not in PAGE
