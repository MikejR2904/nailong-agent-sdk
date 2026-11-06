"""--acp: speak the Agent Client Protocol on stdio so an editor can drive the agent.

Written from the protocol's published shape (JSON-RPC 2.0, one message per line): `initialize`,
`session/new`, `session/prompt` with `session/update` notifications, and
`session/request_permission` sent to the editor when the mode is `default`. Cancelling a prompt
while it runs is not supported, and no editor has been run against it.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from .console import Console
from .server import REMOTE_MODES, AgentService, _Slot

PROTOCOL_VERSION = 1
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
PARSE_ERROR = -32700
PERMISSION_SECONDS = 300.0
ACP_MODES = (*REMOTE_MODES, "default")


class AcpAgent:
    def __init__(
        self,
        run: Callable[[list[str], Console], int],
        base_argv: list[str],
        project: Path,
        mode: str,
        out: IO[str],
    ) -> None:
        self._service = AgentService(run, base_argv, project, "unused")
        self._mode = mode if mode in ACP_MODES else "plan"
        self._waiting: dict[str, _Slot] = {}
        self._asked = 0
        self._prompts: list[threading.Thread] = []
        self._cancels: dict[str, threading.Event] = {}
        self._out = out
        self._sessions: dict[str, str | None] = {}
        self._lock = threading.Lock()

    def _send(self, message: dict[str, Any]) -> None:
        with self._lock:
            self._out.write(json.dumps(message) + "\n")
            self._out.flush()

    def _reply(self, ident: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": ident, "result": result})

    def _fail(self, ident: Any, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}})

    def _update(self, session: str, update: dict[str, Any]) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": session, "update": update},
            }
        )

    def _ask_client(self, session: str, request: dict[str, str]) -> bool:
        with self._lock:
            self._asked += 1
            ident = f"permission-{self._asked}"
            slot = _Slot()
            self._waiting[ident] = slot
        self._send(
            {
                "jsonrpc": "2.0",
                "id": ident,
                "method": "session/request_permission",
                "params": {
                    "sessionId": session,
                    "toolCall": {
                        "toolCallId": ident,
                        "title": f"{request['tool']}: {request['subject']}"[:200],
                        "kind": "other",
                        "status": "pending",
                    },
                    "options": [
                        {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
                        {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
                    ],
                },
            }
        )
        try:
            if not slot.event.wait(PERMISSION_SECONDS):
                raise TimeoutError(f"the editor did not answer in {PERMISSION_SECONDS:g} seconds")
            return slot.allow
        finally:
            with self._lock:
                self._waiting.pop(ident, None)

    def _answered(self, ident: Any, message: dict[str, Any]) -> None:
        with self._lock:
            slot = self._waiting.get(str(ident))
        if slot is None:
            return
        outcome = (message.get("result") or {}).get("outcome") or {}
        slot.allow = outcome.get("outcome") == "selected" and outcome.get("optionId") == "allow"
        slot.event.set()

    def _cancel(self, session: str) -> None:
        with self._lock:
            event = self._cancels.get(session)
            waiting = list(self._waiting.values())
        if event is None:
            return
        event.set()
        for slot in waiting:
            slot.event.set()

    def finish(self) -> None:
        for worker in self._prompts:
            worker.join()

    def handle(self, message: dict[str, Any]) -> None:
        ident, method = message.get("id"), message.get("method")
        params = message.get("params") or {}
        if method is None and ident is not None:
            self._answered(ident, message)
            return
        if method == "session/cancel":
            self._cancel(str(params.get("sessionId")))
            return
        if method == "initialize":
            self._reply(
                ident,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "agentCapabilities": {
                        "loadSession": False,
                        "promptCapabilities": {"image": False, "audio": False},
                    },
                    "authMethods": [],
                },
            )
        elif method == "session/new":
            session = f"nailong-acp-{len(self._sessions) + 1}"
            self._sessions[session] = None
            self._reply(ident, {"sessionId": session})
        elif method == "session/prompt":
            worker = threading.Thread(target=self._prompt, args=(ident, params), daemon=True)
            self._prompts.append(worker)
            worker.start()
        elif ident is not None:
            self._fail(ident, METHOD_NOT_FOUND, f"Method not found: {method}")

    def _prompt(self, ident: Any, params: dict[str, Any]) -> None:
        session = params.get("sessionId")
        if session not in self._sessions:
            self._fail(
                ident, INVALID_PARAMS, f'Unknown sessionId "{session}"; call session/new first.'
            )
            return
        blocks = params.get("prompt") or []
        text = "\n".join(
            str(block.get("text", "")) for block in blocks if block.get("type") == "text"
        ).strip()
        if not text:
            self._fail(ident, INVALID_PARAMS, "The prompt has no text block.")
            return
        asker = (
            (lambda request: self._ask_client(session, request))
            if self._mode == "default"
            else None
        )
        cancel = threading.Event()
        with self._lock:
            self._cancels[session] = cancel
        lines = self._service.start(
            text, self._mode, self._sessions[session], approve=asker, cancel=cancel
        )
        streamed = False
        while (line := lines.get()) is not None:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "session":
                self._sessions[session] = event.get("id")
            elif kind == "text":
                streamed = True
                self._update(session, _chunk(str(event.get("delta", ""))))
            elif kind == "tool_use":
                self._update(
                    session,
                    {
                        "sessionUpdate": "tool_call",
                        "toolCallId": _call_id(event),
                        "title": f"{event.get('tool')} {json.dumps(event.get('arguments'))[:80]}",
                        "kind": "other",
                        "status": "in_progress",
                    },
                )
            elif kind == "tool_result":
                self._update(
                    session,
                    {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": _call_id(event),
                        "status": "completed" if event.get("status") == "succeeded" else "failed",
                    },
                )
            elif kind == "result":
                if not streamed:
                    text_out = (
                        event.get("answer") or f"The run did not complete: {event.get('reason')}"
                    )
                    self._update(session, _chunk(str(text_out)))
            elif kind in {"error", "exit"}:
                self._update(
                    session, _chunk(str(event.get("message") or f"exit {event.get('code')}"))
                )
        with self._lock:
            self._cancels.pop(session, None)
        self._reply(ident, {"stopReason": "cancelled" if cancel.is_set() else "end_turn"})


def _chunk(text: str) -> dict[str, Any]:
    return {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}


def _call_id(event: dict[str, Any]) -> str:
    return f"call-{event.get('tool')}"


def serve_acp(
    run: Callable[[list[str], Console], int],
    argv: list[str],
    *,
    project: Path,
    mode: str,
    stdin: IO[str],
    stdout: IO[str],
) -> int:
    agent = AcpAgent(run, argv, project, mode, stdout)
    inbox: queue.Queue[str | None] = queue.Queue()

    def read() -> None:
        for line in stdin:
            inbox.put(line)
        inbox.put(None)

    threading.Thread(target=read, daemon=True).start()
    while (line := inbox.get()) is not None:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            agent._fail(None, PARSE_ERROR, "That line is not valid JSON.")
            continue
        if isinstance(message, dict):
            agent.handle(message)
    agent.finish()
    return 0
