"""Running a command the user (not the agent) asked for, with the user's own environment."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from nailong_agent_sdk.starter.dev_tools import shell_argv
from nailong_agent_sdk.tools.supervisor import (
    CommandTemplate,
    ProcessExecutionRecord,
    ProcessSupervisor,
)

DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_OUTPUT_BYTES = 65_536


async def run_shell(
    command: str,
    cwd: Path,
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ProcessExecutionRecord:
    argv, script = shell_argv(command, containerized=False)
    template = CommandTemplate(
        name="user-shell",
        command=argv,
        timeout_seconds=timeout_seconds,
        max_output_bytes=MAX_OUTPUT_BYTES,
    )
    try:
        supervisor = ProcessSupervisor([template])
        return await supervisor.execute("user-shell", cwd=cwd, env=dict(env) if env else None)
    finally:
        if script is not None:
            script.unlink(missing_ok=True)
