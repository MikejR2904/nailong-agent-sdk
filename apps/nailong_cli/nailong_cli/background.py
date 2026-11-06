"""Commands that keep running while the agent does other things: start, read output, stop."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import FunctionTool, tool
from nailong_agent_sdk.starter.dev_tools import shell_argv
from nailong_agent_sdk.tools.os_sandbox import (
    BubblewrapSandbox,
    SeatbeltSandbox,
    bubblewrap_argv,
    seatbelt_profile,
)
from nailong_agent_sdk.tools.process_freeze import FREEZER
from nailong_agent_sdk.tools.sandbox_models import default_native_environment
from nailong_agent_sdk.tools.supervisor import CommandTemplate

MAX_JOBS = 5
MAX_BUFFER_CHARS = 1_000_000
MAX_READ_CHARS = 20_000


@dataclass
class Job:
    id: str
    command: str
    process: asyncio.subprocess.Process
    script: Path | None
    buffer: list[str] = field(default_factory=list)
    size: int = 0
    dropped: int = 0
    reader: asyncio.Task[None] | None = None


class BackgroundJobs:
    def __init__(self, project: Path, sandbox: Any | None, unsupported_reason: str | None) -> None:
        self._project = project
        self._sandbox = sandbox
        self._unsupported = unsupported_reason
        self._jobs: dict[str, Job] = {}
        self._counter = 0

    def tools(self) -> list[FunctionTool]:
        async def start_background(command: str) -> dict[str, Any]:
            """Start a long-running shell command (a server, a watcher) and return at once."""
            return await self.start(command)

        async def background_output(job: str, since: int = 0) -> dict[str, Any]:
            """Read what a background command has printed since a character offset."""
            return self.output(job, since)

        async def background_stop(job: str) -> dict[str, Any]:
            """Stop a background command and everything it started."""
            return await self.stop(job)

        return [
            tool(start_background, needs_approval=True),
            tool(background_output, read_only=True),
            tool(background_stop),
        ]

    async def start(self, command: str) -> dict[str, Any]:
        if self._unsupported:
            raise ValueError(self._unsupported)
        if not command.strip():
            raise ValueError("command must not be empty.")
        running = [job for job in self._jobs.values() if job.process.returncode is None]
        if len(running) >= MAX_JOBS:
            raise ValueError(f"{MAX_JOBS} background commands are already running; stop one first.")
        argv, script = self._argv(command)
        self._counter += 1
        job_id = f"job-{self._counter}"
        env = default_native_environment().resolve()
        options: dict[str, Any] = (
            {"start_new_session": True}
            if sys.platform != "win32"
            else {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        )
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self._project,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **options,
        )
        job = Job(job_id, command, process, script)
        FREEZER.register(process.pid, job_id, foreground=False)
        job.reader = asyncio.create_task(self._read(job))
        self._jobs[job_id] = job
        return {"job": job_id, "pid": process.pid, "note": "Use background_output to read it."}

    def output(self, job_id: str, since: int = 0) -> dict[str, Any]:
        job = self._job(job_id)
        text = "".join(job.buffer)
        offset = max(since - job.dropped, 0)
        chunk = text[offset : offset + MAX_READ_CHARS]
        return {
            "running": job.process.returncode is None,
            "exit_code": job.process.returncode,
            "output": chunk,
            "next_offset": job.dropped + offset + len(chunk),
        }

    async def stop(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        await self._terminate(job)
        return {"job": job_id, "exit_code": job.process.returncode}

    def listing(self) -> list[str]:
        paused = {entry.label for entry in FREEZER.entries() if entry.frozen}

        def state(job: Job) -> str:
            code = job.process.returncode
            if code is not None:
                return f"exit {code}"
            return "paused" if job.id in paused else "running"

        return [f"{job.id}: {state(job)}  {job.command}" for job in self._jobs.values()]

    async def close(self) -> None:
        for job in self._jobs.values():
            await self._terminate(job)

    def _argv(self, command: str) -> tuple[list[str], Path | None]:
        argv, script = shell_argv(command, containerized=False)
        template = CommandTemplate(name="background", command=argv, timeout_seconds=86_400)
        if isinstance(self._sandbox, BubblewrapSandbox):
            binary = _which("bwrap")
            wrapped = bubblewrap_argv(
                binary,
                self._sandbox._options,
                template,
                self._project,
                default_native_environment().resolve(),
            )
            return wrapped, script
        if isinstance(self._sandbox, SeatbeltSandbox):
            profile = seatbelt_profile(self._sandbox._options, self._project)
            return [_which("sandbox-exec"), "-p", profile, *argv], script
        return argv, script

    async def _read(self, job: Job) -> None:
        assert job.process.stdout is not None
        while True:
            data = await job.process.stdout.read(4096)
            if not data:
                break
            text = data.decode("utf-8", "replace")
            job.buffer.append(text)
            job.size += len(text)
            while job.size > MAX_BUFFER_CHARS and job.buffer:
                removed = job.buffer.pop(0)
                job.size -= len(removed)
                job.dropped += len(removed)
        await job.process.wait()
        FREEZER.unregister(job.process.pid)
        if job.script is not None:
            job.script.unlink(missing_ok=True)

    async def _terminate(self, job: Job) -> None:
        if job.process.returncode is None:
            FREEZER.thaw_pid(job.process.pid)
            _kill_tree(job.process.pid)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(job.process.wait(), 5)
        if job.reader is not None:
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(job.reader, 5)
        if job.script is not None:
            job.script.unlink(missing_ok=True)

    def _job(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError(f'There is no background job "{job_id}"; jobs: {sorted(self._jobs)}.')
        return job


def make_background(sandbox_kwargs: dict[str, Any], project: Path) -> BackgroundJobs:
    if "docker_image" in sandbox_kwargs:
        return BackgroundJobs(
            project,
            None,
            "Background commands are not supported with the Docker sandbox; use run_command.",
        )
    sandbox = sandbox_kwargs.get("sandbox")
    return BackgroundJobs(project, sandbox, None)


def _which(binary: str) -> str:
    import shutil

    found = shutil.which(binary)
    if found is None:
        raise ValueError(f'"{binary}" was not found on PATH.')
    return found


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        return
    if sys.platform == "win32":
        return
    import signal

    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)
