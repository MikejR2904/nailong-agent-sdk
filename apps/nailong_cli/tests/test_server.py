from __future__ import annotations

import http.server
import json
import threading
import urllib.error
import urllib.request

import pytest

from nailong_cli import server
from nailong_cli.cli import main
from tests.conftest import factory, final, make_console

TOKEN = "t" * 32


@pytest.fixture
def running(project, home):
    build = factory(final("hello from the agent"), final("second answer"))

    def run(argv, console):
        return main(
            argv,
            environ={"TEST_KEY": "k"},
            console=console,
            model_factory=build,
            home=home,
        )

    service = server.AgentService(run, [], project, TOKEN)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server._handler(service, "test"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield url
    httpd.shutdown()
    httpd.server_close()


def post(url, body, token=TOKEN, path="/run"):
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def test_health_needs_no_token(running):
    with urllib.request.urlopen(running + "/health", timeout=10) as response:
        assert json.loads(response.read()) == {"ok": True, "version": "test"}


def test_a_run_needs_the_right_token(running):
    assert post(running, {"prompt": "hi"}, token=None)[0] == 401
    status, body = post(running, {"prompt": "hi"}, token="x" * 32)
    assert status == 401 and "NAILONG_SERVER_TOKEN" in body


def test_a_run_returns_the_answer_and_a_session_id(running):
    status, body = post(running, {"prompt": "Say hello"})
    result = json.loads(body)
    assert status == 200 and result["status"] == "completed"
    assert result["answer"] == "hello from the agent" and result["session"]


def test_a_later_request_can_continue_the_same_session(running):
    first = json.loads(post(running, {"prompt": "One"})[1])
    second = json.loads(post(running, {"prompt": "Two", "session": first["session"]})[1])
    assert second["session"] == first["session"] and second["status"] == "completed"


def test_streaming_sends_events_as_they_happen(running):
    status, body = post(running, {"prompt": "Say hello"}, path="/run?stream=1")
    events = [json.loads(line[6:]) for line in body.split("\n\n") if line.startswith("data: ")]
    kinds = [event["type"] for event in events]
    assert status == 200 and kinds[0] == "session" and kinds[-1] == "result"
    assert events[-1]["answer"] == "hello from the agent"


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"prompt": "  "}, "must be non-empty text"),
        ({"prompt": "x" * 100_001}, "the limit is 100000"),
        ({"prompt": "hi", "mode": "bypass"}, "nobody is at the server to approve"),
        ({"prompt": "hi", "session": "../../etc"}, "must be a session id"),
        ([1, 2], "must be a JSON object"),
    ],
)
def test_bad_requests_are_refused_with_the_reason(running, body, fragment):
    status, text = post(running, body)
    assert status == 400 and fragment in text


def test_unknown_pages_are_explained(running):
    assert post(running, {}, path="/nope")[0] == 404


def test_the_attach_client_prints_the_stream_and_remembers_the_session(running):
    console, out, err = make_console()
    code = server.attach(
        running, "Say hello", environ={server.TOKEN_VARIABLE: TOKEN}, console=console
    )
    assert code == 0 and "hello from the agent" in out.getvalue()
    assert "completed" in out.getvalue()


def test_attach_explains_a_wrong_token_and_a_missing_one_and_unsafe_addresses(running):
    console, _, err = make_console()
    assert (
        server.attach(running, "hi", environ={server.TOKEN_VARIABLE: "w" * 32}, console=console)
        == 1
    )
    assert "refused the request (HTTP 401)" in err.getvalue()
    console, _, err = make_console()
    assert server.attach(running, "hi", environ={}, console=console) == 64
    assert "Set NAILONG_SERVER_TOKEN" in err.getvalue()
    console, _, err = make_console()
    assert (
        server.attach(
            "http://example.com:4096", "hi", environ={server.TOKEN_VARIABLE: TOKEN}, console=console
        )
        == 64
    )
    assert "use https" in err.getvalue()
    console, _, err = make_console()
    assert server.attach("ftp://x", "hi", environ={}, console=console) == 64


def test_serving_refuses_a_short_token_and_a_public_address_without_your_own_token(project):
    console, _, err = make_console()
    assert (
        server.serve(
            lambda a, c: 0,
            [],
            project=project,
            hostname="127.0.0.1",
            port=0,
            environ={server.TOKEN_VARIABLE: "short"},
            console=console,
            version="t",
        )
        == 64
    )
    assert "at least 24 characters" in err.getvalue()
    console, _, err = make_console()
    assert (
        server.serve(
            lambda a, c: 0,
            [],
            project=project,
            hostname="0.0.0.0",
            port=0,
            environ={},
            console=console,
            version="t",
        )
        == 64
    )
    assert "set NAILONG_SERVER_TOKEN yourself" in err.getvalue()


def test_the_server_flags_are_removed_before_the_rest_are_passed_to_each_run():
    argv = [
        "--serve",
        "--port",
        "5000",
        "--hostname=0.0.0.0",
        "--model",
        "m",
        "--attach",
        "u",
        "-n",
    ]
    assert server.strip_server_flags(argv) == ["--model", "m", "-n"]
