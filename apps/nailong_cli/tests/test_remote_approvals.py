from __future__ import annotations

import http.server
import io
import json
import queue
import threading
import urllib.error
import urllib.request

import pytest

from nailong_cli import server
from nailong_cli.acp import serve_acp
from nailong_cli.cli import main
from tests.conftest import call, factory, final

TOKEN = "t" * 32


def runner(home, build):
    def run(argv, console):
        return main(
            argv, environ={"TEST_KEY": "k"}, console=console, model_factory=build, home=home
        )

    return run


@pytest.fixture
def running(project, home):
    def start(*turns):
        service = server.AgentService(runner(home, factory(*turns)), [], project, TOKEN)
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server._handler(service, "test"))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    started: list[http.server.ThreadingHTTPServer] = []
    yield start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def post(url, path, body):
    request = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=60)
        return response, response.status
    except urllib.error.HTTPError as error:
        return error, error.code


def run_with_answer(url, allow):
    response, status = post(
        url,
        "/run?stream=1",
        {"prompt": "write it", "mode": "default", "approvals": True},
    )
    assert status == 200
    events = []
    for raw in response:
        text = raw.decode().strip()
        if not text.startswith("data: "):
            continue
        event = json.loads(text[6:])
        events.append(event)
        if event["type"] == "approval_request":
            _, answered = post(url, "/approve", {"id": event["id"], "allow": allow})
            assert answered == 200
    return events


def test_a_streamed_run_asks_the_client_and_goes_ahead_on_yes(project, running):
    url = running(call("write_file", path="x.txt", content="hi"), final("done"))
    events = run_with_answer(url, True)
    asked = [event for event in events if event["type"] == "approval_request"]
    assert len(asked) == 1 and asked[0]["tool"] == "write_file"
    assert "x.txt" in asked[0]["subject"] or "x.txt" in asked[0]["description"]
    assert (project / "x.txt").read_text(encoding="utf-8") == "hi"


def test_a_no_leaves_the_file_unwritten(project, running):
    url = running(call("write_file", path="x.txt", content="hi"), final("done"))
    events = run_with_answer(url, False)
    assert any(event["type"] == "approval_request" for event in events)
    assert not (project / "x.txt").exists()


def test_the_default_mode_needs_approvals_and_approvals_need_a_stream(running):
    url = running(final("x"))
    body, status = post(url, "/run", {"prompt": "hi", "mode": "default"})
    assert status == 400 and "approvals" in json.loads(body.read())["error"]
    body, status = post(url, "/run", {"prompt": "hi", "mode": "default", "approvals": True})
    assert status == 400 and "stream=1" in json.loads(body.read())["error"]
    body, status = post(url, "/run", {"prompt": "hi", "approvals": "yes"})
    assert status == 400 and '"approvals" must be true or false' in json.loads(body.read())["error"]


def test_an_answer_for_an_unknown_approval_is_refused_by_name(running):
    url = running(final("x"))
    body, status = post(url, "/approve", {"id": "nope", "allow": True})
    assert status == 404 and 'No approval "nope"' in json.loads(body.read())["error"]
    body, status = post(url, "/approve", {"id": "nope"})
    assert status == 400


class Feeder:
    def __init__(self) -> None:
        self.lines: queue.Queue[str | None] = queue.Queue()

    def __iter__(self):
        while (line := self.lines.get()) is not None:
            yield line


class Editor(io.StringIO):
    def __init__(self, feeder: Feeder, answer: str) -> None:
        super().__init__()
        self.feeder = feeder
        self.answer = answer
        self.requests: list[dict] = []

    def write(self, text: str) -> int:
        count = super().write(text)
        for line in text.splitlines():
            if not line.strip():
                continue
            message = json.loads(line)
            if message.get("method") == "session/request_permission":
                self.requests.append(message)
                reply = {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"outcome": {"outcome": "selected", "optionId": self.answer}},
                }
                self.feeder.lines.put(json.dumps(reply) + "\n")
            elif message.get("id") == 3 and "result" in message:
                self.feeder.lines.put(None)
        return count


def drive_editor(project, home, answer):
    feeder = Feeder()
    editor = Editor(feeder, answer)
    for message in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": str(project)}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {
                "sessionId": "nailong-acp-1",
                "prompt": [{"type": "text", "text": "write it"}],
            },
        },
    ):
        feeder.lines.put(json.dumps(message) + "\n")
    build = factory(call("write_file", path="x.txt", content="hi"), final("done"))
    code = serve_acp(
        runner(home, build), [], project=project, mode="default", stdin=feeder, stdout=editor
    )
    assert code == 0
    return editor


def test_acp_asks_the_editor_for_permission_and_writes_on_allow(project, home):
    editor = drive_editor(project, home, "allow")
    assert len(editor.requests) == 1
    params = editor.requests[0]["params"]
    assert params["sessionId"] == "nailong-acp-1" and params["toolCall"]["title"].startswith(
        "write_file"
    )
    assert [option["kind"] for option in params["options"]] == ["allow_once", "reject_once"]
    assert (project / "x.txt").read_text(encoding="utf-8") == "hi"


def test_acp_does_not_write_when_the_editor_rejects(project, home):
    editor = drive_editor(project, home, "reject")
    assert len(editor.requests) == 1
    assert not (project / "x.txt").exists()
