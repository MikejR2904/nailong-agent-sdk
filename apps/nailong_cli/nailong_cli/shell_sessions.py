"""Persistent shells: one process keeps its directory, variables and logins between commands."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import FunctionTool, tool
from nailong_agent_sdk.tools.os_sandbox import (
    BubblewrapSandbox,
    SeatbeltSandbox,
    bubblewrap_argv,
    seatbelt_profile,
)
from nailong_agent_sdk.tools.process_freeze import FREEZER, FrozenCommand
from nailong_agent_sdk.tools.sandbox_models import default_native_environment
from nailong_agent_sdk.tools.supervisor import CommandTemplate, command_output_listener

from .background import _kill_tree

MAX_SESSIONS = 5
MAX_OUTPUT_BYTES = 65_536
DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_TIMEOUT_SECONDS = 3_600.0
READ_TICK_SECONDS = 0.05
PROMPT_MARKER = "@@NLP@@"
_HOST = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@:\[\]-]{0,252}$")
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_SSH_PASSTHROUGH = ("SSH_AUTH_SOCK", "HOME", "USERPROFILE", "USER", "USERNAME", "SSH_ASKPASS")


@dataclass
class Shell:
    name: str
    host: str | None
    process: asyncio.subprocess.Process
    frozen: FrozenCommand
    cwd: str = ""
    busy: bool = False
    commands: int = 0
    opened: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    leftover: str = ""


class ShellSessions:
    def __init__(
        self,
        project: Path,
        sandbox: Any | None,
        unsupported_reason: str | None,
        allowed_hosts: tuple[str, ...] = (),
    ) -> None:
        self._project = project
        self._sandbox = sandbox
        self._unsupported = unsupported_reason
        self._allowed_hosts = allowed_hosts
        self._shells: dict[str, Shell] = {}
        self._control_directory: Path | None = None

    def tools(self) -> list[FunctionTool]:
        async def shell_open(name: str = "shell", host: str | None = None) -> dict[str, Any]:
            """Open a persistent shell that keeps its directory, variables and logins.

            Give host to work on a remote machine over one reused SSH connection (only hosts the
            user listed in ssh_hosts; keys come from their ssh-agent, never a password prompt).
            """
            return await self.open(name, host)

        async def shell_run(
            session: str, command: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
        ) -> dict[str, Any]:
            """Run one command in an open shell and return its output, exit code and directory."""
            return await self.run(session, command, timeout_seconds)

        async def shell_close(session: str) -> dict[str, Any]:
            """Close a persistent shell and everything it started."""
            return await self.close_one(session)

        def shell_list() -> list[str]:
            """List the open persistent shells."""
            return self.listing()

        return [
            tool(shell_open, needs_approval=True),
            tool(shell_run, needs_approval=True),
            tool(shell_close),
            tool(shell_list, read_only=True),
        ]

    async def open(self, name: str, host: str | None) -> dict[str, Any]:
        if self._unsupported:
            raise ValueError(self._unsupported)
        if not _NAME.match(name):
            raise ValueError(
                f'Shell name "{name}" must be 1 to 32 letters, digits, "-" or "_" characters.'
            )
        if name in self._shells and self._shells[name].process.returncode is None:
            raise ValueError(
                f'A shell named "{name}" is already open; close it or pick another name.'
            )
        if (
            sum(1 for shell in self._shells.values() if shell.process.returncode is None)
            >= MAX_SESSIONS
        ):
            raise ValueError(f"{MAX_SESSIONS} shells are already open; close one first.")
        argv, env = self._launch(host)
        options: dict[str, Any] = (
            {"start_new_session": True}
            if sys.platform != "win32"
            else {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        )
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self._project,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **options,
        )
        entry = FREEZER.register(process.pid, f"shell:{name}", foreground=False)
        shell = Shell(name, host, process, entry)
        self._shells[name] = shell
        if host is None and os.name == "nt":
            await self._send(shell, f"prompt {PROMPT_MARKER}")
        result = await self._execute(shell, "echo ready" if os.name == "nt" else "true", 30.0)
        return {
            "session": name,
            "host": host,
            "directory": result["directory"],
            "note": "Use shell_run to run commands here; the directory and variables persist.",
        }

    async def run(self, name: str, command: str, timeout_seconds: float) -> dict[str, Any]:
        shell = self._shell(name)
        if not command.strip():
            raise ValueError("command must not be empty.")
        seconds = min(max(timeout_seconds, 1.0), MAX_TIMEOUT_SECONDS)
        if shell.lock.locked():
            raise ValueError(f'Shell "{name}" is still running a command; wait for it to finish.')
        async with shell.lock:
            return await self._execute(shell, command, seconds)

    async def close_one(self, name: str) -> dict[str, Any]:
        shell = self._shell(name)
        await self._terminate(shell)
        self._shells.pop(name, None)
        return {"session": name, "closed": True, "exit_code": shell.process.returncode}

    async def close(self) -> None:
        for shell in list(self._shells.values()):
            await self._terminate(shell)
        self._shells.clear()
        if self._control_directory is not None:
            shutil.rmtree(self._control_directory, ignore_errors=True)

    def listing(self) -> list[str]:
        lines = []
        for shell in self._shells.values():
            if shell.process.returncode is not None:
                state = f"ended (exit {shell.process.returncode})"
            elif shell.frozen.frozen:
                state = "paused"
            else:
                state = "running a command" if shell.busy else "idle"
            where = f"ssh {shell.host}" if shell.host else "local"
            lines.append(f"shell:{shell.name}: {state}  {where}  {shell.cwd}")
        return lines

    def is_shell(self, label: str) -> bool:
        return label.removeprefix("shell:") in self._shells

    async def stop_named(self, label: str) -> dict[str, Any]:
        return await self.close_one(label.removeprefix("shell:"))

    def _shell(self, name: str) -> Shell:
        shell = self._shells.get(name)
        if shell is None:
            raise ValueError(f'There is no shell "{name}"; open shells: {sorted(self._shells)}.')
        if shell.process.returncode is not None:
            raise ValueError(
                f'Shell "{name}" ended (exit {shell.process.returncode}); its directory and '
                "variables are gone. Close it and open a new one."
            )
        return shell

    def _launch(self, host: str | None) -> tuple[list[str], dict[str, str]]:
        if host is not None:
            return self._ssh_launch(host)
        env = default_native_environment().resolve()
        if os.name == "nt":
            return ["cmd.exe", "/d", "/q"], env
        argv = ["/bin/sh"]
        if isinstance(self._sandbox, BubblewrapSandbox):
            template = CommandTemplate(name="shell", command=argv, timeout_seconds=86_400)
            return (
                bubblewrap_argv(
                    _which("bwrap"), self._sandbox._options, template, self._project, env
                ),
                env,
            )
        if isinstance(self._sandbox, SeatbeltSandbox):
            profile = seatbelt_profile(self._sandbox._options, self._project)
            return [_which("sandbox-exec"), "-p", profile, *argv], env
        return argv, env

    def _ssh_launch(self, host: str) -> tuple[list[str], dict[str, str]]:
        if not self._allowed_hosts:
            raise ValueError(
                "Remote shells are off. List the hosts you allow in ssh_hosts in your user "
                'settings, for example ssh_hosts = ["admin@server1"].'
            )
        if not _HOST.match(host) or host not in self._allowed_hosts:
            raise ValueError(
                f'Host "{host}" is not in ssh_hosts ({", ".join(self._allowed_hosts)}).'
            )
        ssh = _which("ssh")
        argv = [ssh, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
        if os.name != "nt":
            if self._control_directory is None:
                self._control_directory = Path(tempfile.mkdtemp(prefix="nailong-ssh-"))
            argv += [
                "-o",
                "ControlMaster=auto",
                "-o",
                f"ControlPath={self._control_directory}/%C",
                "-o",
                "ControlPersist=10m",
            ]
        argv += ["--", host]
        env = default_native_environment().resolve()
        env.update({name: os.environ[name] for name in _SSH_PASSTHROUGH if name in os.environ})
        return argv, env

    async def _send(self, shell: Shell, text: str) -> None:
        assert shell.process.stdin is not None
        newline = "\r\n" if os.name == "nt" and shell.host is None else "\n"
        shell.process.stdin.write((text + newline).encode("utf-8"))
        await shell.process.stdin.drain()

    def _sentinel(self, shell: Shell, marker: str) -> str:
        if os.name == "nt" and shell.host is None:
            return f"echo {marker}^|%errorlevel%^|%cd%^|"
        return f'printf \'\\n{marker}|%d|%s|\\n\' "$?" "$PWD"'

    async def _execute(self, shell: Shell, command: str, seconds: float) -> dict[str, Any]:
        marker = f"__NL_{uuid.uuid4().hex[:12]}__"
        shell.busy = True
        shell.frozen.foreground = True
        started = time.monotonic()
        try:
            await self._send(shell, command)
            await self._send(shell, self._sentinel(shell, marker))
            text, code, cwd, cut = await self._collect(shell, marker, seconds)
        except TimeoutError:
            await self._terminate(shell)
            raise ValueError(
                f'The command did not finish within {seconds:g}s. Shell "{shell.name}" was closed, '
                "so its directory and variables are lost; for work that takes long use "
                "start_background."
            ) from None
        except (BrokenPipeError, ConnectionResetError):
            raise ValueError(
                f'Shell "{shell.name}" ended while the command ran (exit '
                f"{shell.process.returncode}); its state is lost."
            ) from None
        finally:
            shell.busy = False
            shell.frozen.foreground = False
        shell.cwd = cwd
        shell.commands += 1
        return {
            "exit_code": code,
            "output": text,
            "output_truncated_bytes": cut,
            "directory": cwd,
            "seconds": round(time.monotonic() - started, 3),
        }

    async def _collect(
        self, shell: Shell, marker: str, seconds: float
    ) -> tuple[str, int, str, int]:
        assert shell.process.stdout is not None
        listener = command_output_listener.get()
        pattern = re.compile(re.escape(marker) + r"\|(-?\d+)\|(.*?)\|\r?\n")
        buffer = shell.leftover
        shell.leftover = ""
        emitted = 0
        remaining = seconds
        last = time.monotonic()
        while True:
            found = pattern.search(buffer)
            if found is not None:
                break
            safe = max(emitted, buffer.rfind("\n") + 1)
            if listener is not None and safe > emitted:
                self._announce(listener, _stream(buffer[emitted:safe]))
                emitted = safe
            if remaining <= 0:
                raise TimeoutError
            try:
                chunk = await asyncio.wait_for(shell.process.stdout.read(4096), READ_TICK_SECONDS)
            except TimeoutError:
                chunk = None
            now = time.monotonic()
            if not shell.frozen.frozen:
                remaining -= now - last
            last = now
            if chunk == b"":
                raise BrokenPipeError
            if chunk:
                buffer += chunk.decode("utf-8", "replace")
        before = buffer[: found.start()]
        shell.leftover = buffer[found.end() :]
        if listener is not None and len(before) > emitted:
            self._announce(listener, _stream(before[emitted:]))
        text = _clean(before)
        data = text.encode("utf-8")
        cut = max(0, len(data) - MAX_OUTPUT_BYTES)
        if cut:
            text = data[:MAX_OUTPUT_BYTES].decode("utf-8", "ignore")
        return text, int(found.group(1)), found.group(2), cut

    @staticmethod
    def _announce(listener: Any, text: str) -> None:
        if text:
            with contextlib.suppress(Exception):
                listener(text)

    async def _terminate(self, shell: Shell) -> None:
        FREEZER.thaw_pid(shell.process.pid)
        if shell.process.returncode is None:
            _kill_tree(shell.process.pid)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shell.process.wait(), 5)
        FREEZER.unregister(shell.process.pid)


def _stream(text: str) -> str:
    return text.replace("\r\n", "\n").replace(PROMPT_MARKER, "")


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace(PROMPT_MARKER, "")
    return text.strip("\n") if text.strip() else ""


def _which(binary: str) -> str:
    found = shutil.which(binary)
    if found is None:
        raise ValueError(f'"{binary}" was not found on PATH.')
    return found


def make_shell_sessions(
    sandbox_kwargs: dict[str, Any], project: Path, allowed_hosts: tuple[str, ...]
) -> ShellSessions:
    if "docker_image" in sandbox_kwargs:
        return ShellSessions(
            project,
            None,
            "Persistent shells are not supported with the Docker sandbox; use run_command.",
            allowed_hosts,
        )
    return ShellSessions(project, sandbox_kwargs.get("sandbox"), None, allowed_hosts)
