"""A small language-server client: ask a real server what is wrong with a file the agent edited."""

from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUEST_SECONDS = 20.0
DIAGNOSTIC_WAIT_SECONDS = 6.0
MAX_REPORTED = 20
SEVERITIES = {1: "error", 2: "warning", 3: "info", 4: "hint"}
LANGUAGE_IDS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".cpp": "cpp",
    ".java": "java",
    ".rb": "ruby",
    ".json": "json",
}


@dataclass(frozen=True)
class ServerSpec:
    name: str
    command: tuple[str, ...]
    extensions: tuple[str, ...]


BUILTIN_SERVERS = (
    ServerSpec("pyright", ("pyright-langserver", "--stdio"), (".py",)),
    ServerSpec(
        "typescript", ("typescript-language-server", "--stdio"), (".ts", ".tsx", ".js", ".jsx")
    ),
    ServerSpec("gopls", ("gopls",), (".go",)),
    ServerSpec("rust-analyzer", ("rust-analyzer",), (".rs",)),
)


def specs_from_table(table: dict[str, dict[str, Any]]) -> list[ServerSpec]:
    found = []
    for name, entry in table.items():
        command, extensions = entry.get("command"), entry.get("extensions")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(x, str) for x in command)
        ):
            raise ValueError(f'lsp.{name} needs command = ["server", "--stdio"].')
        if not isinstance(extensions, list) or not extensions:
            raise ValueError(f'lsp.{name} needs extensions = [".py", ...].')
        if entry.get("disabled"):
            continue
        found.append(ServerSpec(name, tuple(command), tuple(str(x) for x in extensions)))
    return found


NAVIGATION_METHODS = {
    "definition": "textDocument/definition",
    "references": "textDocument/references",
    "hover": "textDocument/hover",
}
MAX_LOCATIONS = 50


def _hover_text(result: Any) -> str:
    contents = (result or {}).get("contents") if isinstance(result, dict) else None
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return str(contents.get("value", ""))
    if isinstance(contents, list):
        return "\n".join(
            str(item.get("value", "")) if isinstance(item, dict) else str(item) for item in contents
        )
    return ""


class LspError(RuntimeError):
    pass


class LspClient:
    def __init__(self, spec: ServerSpec, root: Path) -> None:
        self.spec = spec
        self._root = root.resolve()
        self._process: subprocess.Popen[bytes] | None = None
        self._next_id = 0
        self._replies: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._fresh: dict[str, threading.Event] = {}
        self._versions: dict[str, int] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()

    def _start(self) -> None:
        try:
            self._process = subprocess.Popen(
                list(self.spec.command),
                cwd=self._root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            raise LspError(
                f"{self.spec.name} could not start ({self.spec.command[0]}): "
                f"{type(error).__name__}: {error}"
            ) from error
        threading.Thread(target=self._read_loop, daemon=True).start()
        reply = self._request(
            "initialize",
            {
                "processId": None,
                "rootUri": self._root.as_uri(),
                "capabilities": {
                    "textDocument": {"publishDiagnostics": {"relatedInformation": False}}
                },
            },
        )
        if "error" in reply:
            raise LspError(f"{self.spec.name} refused to initialize: {reply['error']}")
        self._notify("initialized", {})

    def _send(self, message: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        body = json.dumps(message).encode("utf-8")
        with self._write_lock:
            self._process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            self._process.stdin.flush()

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            ident = self._next_id
            waiting: queue.Queue[dict[str, Any]] = queue.Queue()
            self._replies[ident] = waiting
        self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        try:
            return waiting.get(timeout=REQUEST_SECONDS)
        except queue.Empty:
            raise LspError(
                f"{self.spec.name} did not answer {method} within {REQUEST_SECONDS:g}s"
            ) from None

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stream = self._process.stdout
        while True:
            length = 0
            while True:
                line = stream.readline()
                if not line:
                    return
                if line in (b"\r\n", b"\n"):
                    break
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1])
            if not length:
                continue
            try:
                message = json.loads(stream.read(length))
            except ValueError:
                continue
            self._handle(message)

    def _handle(self, message: dict[str, Any]) -> None:
        if "id" in message and "method" not in message:
            waiting = self._replies.pop(message["id"], None)
            if waiting is not None:
                waiting.put(message)
        elif message.get("method") == "textDocument/publishDiagnostics":
            params = message.get("params", {})
            uri = params.get("uri", "")
            self._diagnostics[uri] = params.get("diagnostics", [])
            self._fresh.setdefault(uri, threading.Event()).set()
        elif "id" in message and "method" in message:
            self._send({"jsonrpc": "2.0", "id": message["id"], "result": None})

    def _sync(self, path: Path, text: str) -> tuple[str, threading.Event]:
        if self._process is None:
            self._start()
        uri = path.resolve().as_uri()
        fresh = self._fresh.setdefault(uri, threading.Event())
        fresh.clear()
        version = self._versions.get(uri, 0) + 1
        self._versions[uri] = version
        if version == 1:
            self._notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": LANGUAGE_IDS.get(path.suffix, path.suffix.lstrip(".")),
                        "version": version,
                        "text": text,
                    }
                },
            )
        else:
            self._notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": text}],
                },
            )
        return uri, fresh

    def locate(
        self, path: Path, text: str, line: int, character: int, kind: str
    ) -> list[dict[str, Any]] | str:
        uri, _ = self._sync(path, text)
        params: dict[str, Any] = {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": character - 1},
        }
        if kind == "references":
            params["context"] = {"includeDeclaration": True}
        reply = self._request(NAVIGATION_METHODS[kind], params)
        if "error" in reply:
            raise LspError(f"{self.spec.name} could not answer {kind}: {reply['error']}")
        result = reply.get("result")
        if kind == "hover":
            return _hover_text(result)
        if result is None:
            return []
        return result if isinstance(result, list) else [result]

    def diagnose(self, path: Path, text: str) -> list[str]:
        uri, fresh = self._sync(path, text)
        deadline = time.monotonic() + DIAGNOSTIC_WAIT_SECONDS
        fresh.wait(max(0.0, deadline - time.monotonic()))
        lines = []
        for item in self._diagnostics.get(uri, []):
            start = item.get("range", {}).get("start", {})
            severity = SEVERITIES.get(item.get("severity", 1), "error")
            if severity in {"error", "warning"}:
                lines.append(
                    f"{path.name}:{start.get('line', 0) + 1}:{start.get('character', 0) + 1} "
                    f"{severity}: {item.get('message', '')}"
                )
        return lines[:MAX_REPORTED]

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            self._request("shutdown", {})
            self._notify("exit", {})
        except (LspError, OSError):
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
        self._process = None


class LspManager:
    def __init__(self, project: Path, specs: list[ServerSpec], *, builtin: bool) -> None:
        self._project = project.resolve()
        chosen = list(specs)
        if builtin:
            chosen = [
                *(item for item in BUILTIN_SERVERS if shutil.which(item.command[0])),
                *chosen,
            ]
        self._specs = chosen
        self._clients: dict[str, LspClient] = {}
        self._failed: set[str] = set()

    @property
    def active(self) -> bool:
        return bool(self._specs)

    def describe(self) -> str:
        return ", ".join(spec.name for spec in self._specs) or "none"

    def diagnose(self, path: Path) -> list[str]:
        for spec in reversed(self._specs):
            if path.suffix not in spec.extensions or spec.name in self._failed:
                continue
            client = self._clients.setdefault(spec.name, LspClient(spec, self._project))
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                found = client.diagnose(path, text)
            except LspError as error:
                self._failed.add(spec.name)
                return [f"(language server {spec.name} stopped: {error})"]
            return [f"{spec.name} (language server):\n" + "\n".join(found)] if found else []
        return []

    def navigate(self, path: Path, line: int, character: int, kind: str) -> list[str]:
        if kind not in NAVIGATION_METHODS:
            raise ValueError(f"kind must be one of {sorted(NAVIGATION_METHODS)}, not {kind!r}.")
        resolved = path.resolve()
        if not resolved.is_file():
            raise ValueError(f"{path} is not a file.")
        for spec in reversed(self._specs):
            if path.suffix not in spec.extensions or spec.name in self._failed:
                continue
            client = self._clients.setdefault(spec.name, LspClient(spec, self._project))
            try:
                found = client.locate(
                    resolved,
                    resolved.read_text(encoding="utf-8", errors="replace"),
                    line,
                    character,
                    kind,
                )
            except LspError as error:
                self._failed.add(spec.name)
                raise ValueError(f"language server {spec.name} stopped: {error}") from None
            if isinstance(found, str):
                return [found] if found.strip() else []
            return [self._describe(item) for item in found[:MAX_LOCATIONS]]
        raise ValueError(
            f"No language server handles {path.suffix or path.name}; configured: {self.describe()}."
        )

    def _describe(self, item: dict[str, Any]) -> str:
        uri = item.get("uri") or item.get("targetUri") or ""
        span = item.get("range") or item.get("targetSelectionRange") or {}
        start = span.get("start", {})
        where = urllib.parse.unquote(urllib.parse.urlparse(uri).path)
        if re.match(r"^/[A-Za-z]:", where):
            where = where[1:]
        try:
            where = str(Path(where).resolve().relative_to(self._project))
        except ValueError:
            where = f"(outside the project) {where}"
        return f"{where}:{start.get('line', 0) + 1}:{start.get('character', 0) + 1}"

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()


def navigation_tools(manager: LspManager, project: Path) -> list[Any]:
    from nailong_agent_sdk.starter import tool
    from nailong_agent_sdk.tools.policy import sensitive_pattern_for

    root = project.resolve()

    def code_navigate(action: str, path: str, line: int, character: int = 1) -> dict[str, Any]:
        """Ask the language server about a symbol: action is definition, references or hover;
        line and character are 1-based positions in the file."""
        target = (root / path).resolve()
        try:
            relative = target.relative_to(root)
        except ValueError:
            raise ValueError(f"{path} is outside the project.") from None
        if sensitive_pattern_for(relative.as_posix()) is not None:
            raise ValueError(f"{path} is a credential-style path and is not read.")
        if line < 1 or character < 1:
            raise ValueError("line and character are 1-based and must be 1 or more.")
        found = manager.navigate(target, line, character, action)
        return {"action": action, "path": relative.as_posix(), "results": found}

    return [tool(code_navigate, read_only=True)]
