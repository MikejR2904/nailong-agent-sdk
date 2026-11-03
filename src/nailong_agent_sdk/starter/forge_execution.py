# Copyright (c) 2026 David Michael Indraputra

"""Run a forged tool's source in a disposable subprocess and read back its JSON result."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..tools.sandbox import SandboxBackend
from ..tools.sandbox_models import (
    DockerSandboxOptions,
    EnvironmentPolicy,
    default_native_environment,
)
from ..tools.supervisor import CommandTemplate, ProcessExecutionRecord
from .forge_runner import MARKER

RUNNER = Path(__file__).with_name("forge_runner.py")
RUNNER_DIRECTORY = "runner"
MAX_ARGUMENT_CHARS = 16_000
MAX_OUTPUT_BYTES = 65_536
CONTAINER_MOUNT = "/forge"
DEFAULT_IMAGE = "python:3.12-slim"
DEFAULT_MEMORY_BYTES = 256 * 1024 * 1024
_TAIL_CHARS = 500
_UNPRIVILEGED_USER = "65534:65534"
_PROCESS_LIMIT = 64


@dataclass(frozen=True)
class ExecutionTarget:
    python: str
    runner: str
    environment: EnvironmentPolicy | None
    mount: Path | None = None

    def locate(self, source_path: Path) -> str:
        if self.mount is None:
            return str(source_path)
        return f"{CONTAINER_MOUNT}/{source_path.relative_to(self.mount).as_posix()}"


def native_target() -> ExecutionTarget:
    return ExecutionTarget(sys.executable, str(RUNNER), default_native_environment())


def container_target(forge_directory: Path) -> ExecutionTarget:
    runner_copy = forge_directory / RUNNER_DIRECTORY / RUNNER.name
    runner_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(RUNNER, runner_copy)
    return ExecutionTarget(
        "python",
        f"{CONTAINER_MOUNT}/{RUNNER_DIRECTORY}/{RUNNER.name}",
        None,
        forge_directory,
    )


def container_options(
    forge_directory: Path, image: str, memory_bytes: int, cpu_limit: float
) -> DockerSandboxOptions:
    return DockerSandboxOptions(
        image=image,
        network_disabled=True,
        read_only_root=True,
        memory_bytes=memory_bytes,
        cpu_limit=cpu_limit,
        extra_binds=[f"{forge_directory}:{CONTAINER_MOUNT}:ro"],
        drop_capabilities=True,
        no_new_privileges=True,
        pids_limit=_PROCESS_LIMIT,
        user=_UNPRIVILEGED_USER,
    )


async def run_source(
    backend: SandboxBackend,
    scratch_parent: Path,
    target: ExecutionTarget,
    source_path: Path,
    arguments: dict[str, Any],
    timeout_seconds: float,
) -> tuple[bool, Any]:
    arguments_json = json.dumps(arguments, allow_nan=False)
    if len(arguments_json) > MAX_ARGUMENT_CHARS:
        raise ValueError(
            f"The arguments are {len(arguments_json)} characters of JSON; the limit is "
            f"{MAX_ARGUMENT_CHARS}."
        )
    scratch_parent.mkdir(parents=True, exist_ok=True)
    template = CommandTemplate(
        name="forged-tool",
        command=[
            target.python,
            "-I",
            target.runner,
            target.locate(source_path),
            arguments_json,
        ],
        timeout_seconds=timeout_seconds,
        max_output_bytes=MAX_OUTPUT_BYTES,
    )
    workdir = Path(tempfile.mkdtemp(prefix="run-", dir=scratch_parent))
    workdir.chmod(0o755)
    try:
        record = await backend.run(template, cwd=workdir, environment=target.environment)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return _outcome(record)


def _outcome(record: ProcessExecutionRecord) -> tuple[bool, Any]:
    if record.successful:
        for line in reversed(record.output.splitlines()):
            if line.startswith(MARKER):
                try:
                    return True, json.loads(line[len(MARKER) :])
                except json.JSONDecodeError:
                    break
        return False, (
            "the tool finished but its result was missing or cut off; results over "
            f"{MAX_OUTPUT_BYTES} bytes of output are truncated"
        )
    tail = record.output.strip()[-_TAIL_CHARS:]
    return False, f"{record.exit_kind.value} (exit code {record.return_code}): {tail}"
