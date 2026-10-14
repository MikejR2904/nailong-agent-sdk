import http.server
import json
import time

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.openai_compatible import (
    OpenAICompatibleAgentModel,
    OpenAICompatibleEndpoint,
)
from tests.support.agents import FnExecutor, arun, ok, task, tool
from tests.support.model_context import FAKE_KEY


class Fake(http.server.BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        state = self.state
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        state["requests"].append(
            {"path": self.path, "headers": dict(self.headers), "json": payload}
        )
        script = state["script"]
        item = (
            script.pop(0) if script else state.get("default", {"status": 500, "body": b"no script"})
        )
        if item.get("delay"):
            time.sleep(item["delay"])
        if item.get("drop"):
            self.connection.close()
            return
        if "sse" in item:
            self.send_response(item.get("status", 200))
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for chunk in item["sse"]:
                data = chunk if isinstance(chunk, str) else "data: " + json.dumps(chunk)
                self.wfile.write((data + "\n\n").encode())
                self.wfile.flush()
            if item.get("truncate_after_sse"):
                self.connection.close()
            return
        if item.get("short_body"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "500")
            self.end_headers()
            self.wfile.write(b'{"choices": [')
            self.wfile.flush()
            self.connection.close()
            return
        data = item.get("body", b"")
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
        self.send_response(item.get("status", 200))
        self.send_header("Content-Type", item.get("content_type", "application/json"))
        for key, value in item.get("headers", {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def endpoint(url, key=FAKE_KEY, timeout=5.0):
    return OpenAICompatibleEndpoint(
        base_url=url, api_key=key, allow_insecure_http=True, timeout_seconds=timeout
    )


def model_for(url, transport=None, key=FAKE_KEY, timeout=5.0):
    return OpenAICompatibleAgentModel(
        endpoint(url, key, timeout), provider="fake", model="fake-1", transport=transport
    )


def chat(content=None, tool_calls=None, usage=None, finish="stop", extra=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    body = {
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage:
        body["usage"] = usage
    body.update(extra or {})
    return {"status": 200, "body": body}


ECHO = tool(
    "echo",
    schema={
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "additionalProperties": False,
    },
)


def run_agent(url, definition_, transport=None, **kwargs):
    executor = kwargs.pop("executor", FnExecutor(lambda t, c: ok({"echo": c.call.arguments})))
    subject = BaseAgent(definition_, model_for(url, transport), tool_executor=executor, **kwargs)
    return arun(subject.run(task()))
