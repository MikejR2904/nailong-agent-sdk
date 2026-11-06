"""--serve and --attach: run the agent behind a small HTTP service, and talk to one."""

from __future__ import annotations

import hmac
import http.server
import io
import json
import queue
import re
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO, cast

from .console import Console
from .network import host_of, is_loopback
from .web_page import CONTENT_SECURITY_POLICY, PAGE, openapi

DEFAULT_PORT = 4096
DEFAULT_HOST = "127.0.0.1"
TOKEN_VARIABLE = "NAILONG_SERVER_TOKEN"
MIN_TOKEN_CHARS = 24
MAX_BODY_BYTES = 1_000_000
MAX_PROMPT_CHARS = 100_000
REMOTE_MODES = ("plan", "accept-edits")
_SESSION = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SERVER_FLAGS = {"--serve": 0, "--port": 1, "--hostname": 1, "--attach": 1}


class _LineSink(io.TextIOBase):
    def __init__(self, lines: queue.Queue[str | None]) -> None:
        self._lines = lines
        self._pending = ""

    def write(self, text: str) -> int:
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line.strip():
                self._lines.put(
                    line if line.startswith("{") else json.dumps({"type": "notice", "text": line})
                )
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


def strip_server_flags(argv: Sequence[str]) -> list[str]:
    kept: list[str] = []
    skip = 0
    for item in argv:
        if skip:
            skip -= 1
            continue
        name = item.split("=", 1)[0]
        if name in _SERVER_FLAGS:
            skip = 0 if "=" in item else _SERVER_FLAGS[name]
            continue
        kept.append(item)
    return kept


APPROVAL_SECONDS = 300.0


class _Slot:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.allow = False


class Approvals:
    def __init__(self, timeout: float = APPROVAL_SECONDS) -> None:
        self.timeout = timeout
        self._pending: dict[str, _Slot] = {}
        self._lock = threading.Lock()

    def requester(self, lines: queue.Queue[str | None]) -> Callable[[dict[str, str]], bool]:
        def ask(request: dict[str, str]) -> bool:
            ident = secrets.token_hex(8)
            slot = _Slot()
            with self._lock:
                self._pending[ident] = slot
            lines.put(json.dumps({"type": "approval_request", "id": ident, **request}))
            try:
                if not slot.event.wait(self.timeout):
                    raise TimeoutError(
                        f"approval {ident} got no answer in {self.timeout:g} seconds; "
                        "POST /approve with its id"
                    )
                return slot.allow
            finally:
                with self._lock:
                    self._pending.pop(ident, None)

        return ask

    def answer(self, ident: str, allow: bool) -> bool:
        with self._lock:
            slot = self._pending.get(ident)
        if slot is None:
            return False
        slot.allow = allow
        slot.event.set()
        return True


class AgentService:
    def __init__(
        self,
        run: Callable[[list[str], Console], int],
        base_argv: list[str],
        project: Path,
        token: str,
    ) -> None:
        self._run = run
        self._base = base_argv
        self.project = project
        self._token = token
        self._lock = threading.Lock()
        self.approvals = Approvals()
        self.running: threading.Event | None = None

    def authorized(self, header: str | None) -> bool:
        expected = f"Bearer {self._token}"
        return header is not None and hmac.compare_digest(header, expected)

    def validate(self, body: Any) -> tuple[str, str, str | None]:
        if not isinstance(body, dict):
            raise ValueError('The request body must be a JSON object like {"prompt": "..."}.')
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('"prompt" must be non-empty text.')
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError(
                f'"prompt" has {len(prompt)} characters; the limit is {MAX_PROMPT_CHARS}.'
            )
        mode = body.get("mode", "plan")
        approvals = body.get("approvals", False)
        if not isinstance(approvals, bool):
            raise ValueError('"approvals" must be true or false.')
        allowed = [*REMOTE_MODES, *(["default"] if approvals else [])]
        if mode not in allowed:
            raise ValueError(
                f'"mode" must be one of {allowed}: nobody is at the server to approve anything '
                'else. Send "approvals": true and stream the run to answer each question with '
                "POST /approve."
            )
        session = body.get("session")
        if session is not None and (not isinstance(session, str) or not _SESSION.match(session)):
            raise ValueError('"session" must be a session id: letters, digits, "-", "_" or ".".')
        return prompt, mode, session

    def start(
        self,
        prompt: str,
        mode: str,
        session: str | None,
        *,
        approve: Callable[[dict[str, str]], bool] | None = None,
        lines: queue.Queue[str | None] | None = None,
        cancel: threading.Event | None = None,
    ) -> queue.Queue[str | None]:
        lines = lines if lines is not None else queue.Queue()
        ask = approve

        def work() -> None:
            sink = _LineSink(lines)
            console = Console(
                io.StringIO(), cast(TextIO, sink), io.StringIO(), color=False, interactive=False
            )
            console.remote_approval = ask
            console.remote_cancel = cancel
            argv = [
                *self._base,
                "-p",
                prompt,
                "--output",
                "stream-json",
                "--project",
                str(self.project),
                "--permission-mode",
                mode,
                *(["-r", session] if session else []),
            ]
            try:
                with self._lock:
                    code = self._run(argv, console)
                if code != 0:
                    lines.put(json.dumps({"type": "exit", "code": code}))
            except Exception as error:
                lines.put(
                    json.dumps({"type": "error", "message": f"{type(error).__name__}: {error}"})
                )
            finally:
                lines.put(None)

        threading.Thread(target=work, daemon=True).start()
        return lines


def _handler(service: AgentService, version: str) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = f"nailong/{version}"

        def log_message(self, format: str, *args: object) -> None:
            return None

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/health":
                self._json(200, {"ok": True, "version": version})
            elif path == "/openapi.json":
                self._json(200, openapi(version))
            elif path == "/":
                data = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json(
                    404, {"error": f"No such page {self.path}; use GET /health or POST /run."}
                )

        def do_POST(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._json(413, {"error": f"The body is over {MAX_BODY_BYTES} bytes."})
                return
            raw = self.rfile.read(length)
            if parsed.path not in {"/run", "/approve", "/cancel"}:
                self._json(
                    404,
                    {"error": f"No such page {parsed.path}; use POST /run, /approve or /cancel."},
                )
                return
            if not service.authorized(self.headers.get("Authorization")):
                self._json(401, {"error": f"Send Authorization: Bearer <{TOKEN_VARIABLE}>."})
                return
            if parsed.path == "/approve":
                self._approve(raw)
                return
            if parsed.path == "/cancel":
                running = service.running
                if running is None or running.is_set():
                    self._json(409, {"error": "No run is in progress."})
                    return
                running.set()
                self._json(200, {"ok": True})
                return
            try:
                body = json.loads(raw or b"null")
                prompt, mode, session = service.validate(body)
            except (ValueError, json.JSONDecodeError) as error:
                self._json(400, {"error": str(error)})
                return
            approvals = isinstance(body, dict) and body.get("approvals") is True
            if approvals and "stream=1" not in parsed.query:
                self._json(
                    400,
                    {
                        "error": "Approvals need the run streamed: POST /run?stream=1 "
                        'with "approvals": true.'
                    },
                )
                return
            lines: queue.Queue[str | None] = queue.Queue()
            asker = service.approvals.requester(lines) if approvals else None
            cancel = threading.Event()
            service.running = cancel
            service.start(prompt, mode, session, approve=asker, lines=lines, cancel=cancel)
            if "stream=1" in parsed.query:
                self._stream(lines)
            else:
                self._collect(lines)

        def _approve(self, raw: bytes) -> None:
            try:
                body = json.loads(raw or b"null")
            except json.JSONDecodeError as error:
                self._json(400, {"error": f"The body is not JSON: {error}"})
                return
            ident = body.get("id") if isinstance(body, dict) else None
            allow = body.get("allow") if isinstance(body, dict) else None
            if not isinstance(ident, str) or not isinstance(allow, bool):
                self._json(400, {"error": 'Send {"id": "<approval id>", "allow": true|false}.'})
                return
            if not service.approvals.answer(ident, allow):
                self._json(
                    404, {"error": f'No approval "{ident}" is waiting; it may have timed out.'}
                )
                return
            self._json(200, {"ok": True})

        def _stream(self, lines: queue.Queue[str | None]) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            while (line := lines.get()) is not None:
                self.wfile.write(f"data: {line}\n\n".encode())
                self.wfile.flush()

        def _collect(self, lines: queue.Queue[str | None]) -> None:
            result: dict[str, Any] = {}
            session: str | None = None
            problems: list[str] = []
            while (line := lines.get()) is not None:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "session":
                    session = event.get("id")
                elif event.get("type") == "result":
                    result = event
                elif event.get("type") in {"error", "exit"}:
                    problems.append(str(event.get("message") or f"exit code {event.get('code')}"))
            if not result:
                self._json(500, {"error": "The run produced no result.", "details": problems})
                return
            self._json(
                200, {**{k: v for k, v in result.items() if k != "type"}, "session": session}
            )

    return Handler


def serve(
    run: Callable[[list[str], Console], int],
    argv: Sequence[str],
    *,
    project: Path,
    hostname: str,
    port: int,
    environ: Mapping[str, str],
    console: Console,
    version: str,
) -> int:
    configured = environ.get(TOKEN_VARIABLE, "")
    if configured and len(configured) < MIN_TOKEN_CHARS:
        console.error(f"{TOKEN_VARIABLE} must be at least {MIN_TOKEN_CHARS} characters.")
        return 64
    if not configured and not is_loopback(hostname):
        console.error(
            f"Serving on {hostname} reaches other machines, so set {TOKEN_VARIABLE} yourself "
            "instead of using a generated one."
        )
        return 64
    token = configured or secrets.token_urlsafe(32)
    service = AgentService(run, strip_server_flags(argv), project, token)
    try:
        server = http.server.ThreadingHTTPServer((hostname, port), _handler(service, version))
    except OSError as error:
        console.error(f"Cannot listen on {hostname}:{port}: {error}")
        return 64
    address = f"http://{hostname}:{server.server_address[1]}"
    print(f"nailong serving {project} at {address}", file=console.stderr, flush=True)
    if not configured:
        print(f"token: {token}", file=console.stderr, flush=True)
    print("Runs are read-only (plan) unless a request asks for accept-edits.", file=console.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def attach(
    url: str,
    prompt: str | None,
    *,
    environ: Mapping[str, str],
    console: Console,
    mode: str = "plan",
) -> int:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        console.error(f"--attach needs an http(s) address like http://127.0.0.1:4096, not {url!r}.")
        return 64
    if parsed.scheme == "http" and not is_loopback(host_of(url)):
        console.error(
            "--attach sends your token in clear over plain http to another machine; use https."
        )
        return 64
    token = environ.get(TOKEN_VARIABLE, "")
    if not token:
        console.error(f"Set {TOKEN_VARIABLE} to the token the server printed.")
        return 64
    session: str | None = None

    def one(text: str) -> int:
        nonlocal session
        body = json.dumps(
            {"prompt": text, "mode": mode, **({"session": session} if session else {})}
        )
        request = urllib.request.Request(
            url.rstrip("/") + "/run?stream=1",
            data=body.encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        shown = {"text": False}
        try:
            with urllib.request.urlopen(request, timeout=3600) as response:
                for raw in response:
                    text_line = raw.decode("utf-8").strip()
                    if not text_line.startswith("data:"):
                        continue
                    status = _show(console, json.loads(text_line[5:]), shown)
                    if status is not None:
                        session = status or session
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            console.error(f"The server refused the request (HTTP {error.code}): {detail}")
            return 1
        except (urllib.error.URLError, OSError) as error:
            console.error(f"Cannot reach {url}: {error}")
            return 1
        return 0

    if prompt is not None:
        return one(prompt)
    while True:
        try:
            line = console.ask("attached> ")
        except EOFError:
            return 0
        if line in {"/exit", "/quit"}:
            return 0
        if line:
            one(line)


def _show(console: Console, event: dict[str, Any], shown: dict[str, bool]) -> str | None:
    kind = event.get("type")
    if kind == "session":
        return str(event.get("id"))
    if kind == "text":
        shown["text"] = True
        console.write(str(event.get("delta", "")))
    elif kind == "tool_use":
        console.line(
            console.style(
                f"\n● {event.get('tool')} {json.dumps(event.get('arguments'))[:100]}", "bold"
            )
        )
    elif kind == "tool_result":
        console.line(console.style(f"  ⎿ {event.get('status')}", "dim"))
    elif kind == "result":
        if not shown["text"] and event.get("answer"):
            console.line(str(event["answer"]))
        console.line(
            console.style(f"\n[{event.get('status')}] {event.get('steps')} step(s)", "dim")
        )
    elif kind in {"error", "exit"}:
        console.error(
            str(event.get("message") or f"the run ended with exit code {event.get('code')}")
        )
    return None
