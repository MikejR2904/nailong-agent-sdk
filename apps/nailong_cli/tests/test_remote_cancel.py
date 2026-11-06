from __future__ import annotations

import asyncio
import http.server
import io
import json
import queue
import threading
import time
import urllib.error
import urllib.request

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import ModelSetup

from nailong_cli import server
from nailong_cli.acp import serve_acp
from nailong_cli.cli import main
from tests.conftest import final

TOKEN = "t" * 32


class Slow:
    def __init__(self) -> None:
        self.inner = ScriptedModel([final("too late")])

    async def next_turn(self, context):
        await asyncio.sleep(30)
        return await self.inner.next_turn(context)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def slow_runner(home):
    setup = ModelSetup(Slow(), "scripted", "scripted-1")

    def run(argv, console):
        return main(
            argv,
            environ={"TEST_KEY": "k"},
            console=console,
            model_factory=lambda config, environ: setup,
            home=home,
        )

    return run


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


def test_post_cancel_stops_a_running_request_and_says_so(project, home):
    service = server.AgentService(slow_runner(home), [], project, TOKEN)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server._handler(service, "test"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        _, status = post(url, "/cancel", {})
        assert status == 409
        response, _ = post(url, "/run?stream=1", {"prompt": "wait"})
        started = time.monotonic()
        time.sleep(1.0)
        _, status = post(url, "/cancel", {})
        assert status == 200
        events = [
            json.loads(raw.decode().strip()[6:])
            for raw in response
            if raw.decode().startswith("data: ")
        ]
        assert time.monotonic() - started < 20
        result = [event for event in events if event["type"] == "result"]
        assert result and result[0]["status"] != "completed"
    finally:
        httpd.shutdown()
        httpd.server_close()


class Feeder:
    def __init__(self) -> None:
        self.lines: queue.Queue[str | None] = queue.Queue()

    def __iter__(self):
        while (line := self.lines.get()) is not None:
            yield line


class Editor(io.StringIO):
    def __init__(self, feeder: Feeder) -> None:
        super().__init__()
        self.feeder = feeder
        self.done = threading.Event()

    def write(self, text: str) -> int:
        count = super().write(text)
        if '"id": 3' in text and '"result"' in text:
            self.done.set()
            self.feeder.lines.put(None)
        return count


def test_session_cancel_ends_the_prompt_with_the_cancelled_stop_reason(project, home):
    feeder = Feeder()
    editor = Editor(feeder)
    for message in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": str(project)}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {"sessionId": "nailong-acp-1", "prompt": [{"type": "text", "text": "wait"}]},
        },
    ):
        feeder.lines.put(json.dumps(message) + "\n")

    def cancel_later() -> None:
        time.sleep(1.5)
        feeder.lines.put(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "session/cancel",
                    "params": {"sessionId": "nailong-acp-1"},
                }
            )
            + "\n"
        )

    threading.Thread(target=cancel_later, daemon=True).start()
    started = time.monotonic()
    code = serve_acp(
        slow_runner(home), [], project=project, mode="plan", stdin=feeder, stdout=editor
    )
    assert code == 0 and time.monotonic() - started < 25
    replies = [json.loads(line) for line in editor.getvalue().splitlines()]
    by_id = {item["id"]: item for item in replies if "id" in item}
    assert by_id[3]["result"] == {"stopReason": "cancelled"}
