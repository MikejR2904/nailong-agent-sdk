import http.server
import json
import threading
from contextlib import contextmanager


def chat(content=None, tool_calls=None, usage=None, finish="stop"):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    body = {
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage:
        body["usage"] = usage
    return body


def fn_call(call_id, name, arguments):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def final_chat(output=None):
    return chat(content=json.dumps({"type": "final", "output": output or {"status": "complete"}}))


def tool_chat(call_id, name, arguments):
    return chat(tool_calls=[fn_call(call_id, name, arguments)], finish="tool_calls")


class ScriptedChatTransport:
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.requests = []

    def post_json(self, url, *, headers, payload, timeout_seconds):
        self.requests.append({"url": url, "headers": dict(headers), "payload": payload})
        if not self.bodies:
            raise AssertionError("the scripted chat transport has no response left")
        body = self.bodies.pop(0)
        if isinstance(body, Exception):
            raise body
        return body


@contextmanager
def chat_provider(bodies, hold=None):
    scripted = list(bodies)
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"null")
            requests.append({"path": self.path, "headers": dict(self.headers), "json": payload})
            if hold is not None:
                hold.wait(60)
            body = json.dumps(scripted.pop(0) if scripted else chat(content="{}")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/v1", requests
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)
