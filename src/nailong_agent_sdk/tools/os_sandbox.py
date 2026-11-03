# Copyright (c) 2026 David Michael Indraputra

"""Sandbox backends built on the operating system's own tools: bubblewrap and Seatbelt.

Each backend wraps a registered command template in the OS tool and runs it through the same
``ProcessSupervisor`` as every other backend, so timeouts, output caps and process-tree
termination are unchanged. Unlike ``NativeSandbox`` they run in the working directory they are
given, so a coding tool sees the project; the sandbox then limits what that command can reach.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from pydantic import Field

from ..foundations.contracts import StrictModel
from .sandbox import SandboxBackend
from .sandbox_models import EnvironmentPolicy, default_native_environment
from .supervisor import CommandTemplate, ProcessExecutionRecord, ProcessSupervisor

_SEATBELT_TEMP_PATHS = ("/private/tmp", "/private/var/folders")


class OsSandboxUnavailableError(RuntimeError):
    pass


class OsSandboxOptions(StrictModel):
    network: bool = False
    hide_paths: list[str] = Field(default_factory=lambda: [str(Path.home())])
    extra_read_only: list[str] = Field(default_factory=list)
    extra_writable: list[str] = Field(default_factory=list)


class BubblewrapSandbox:
    def __init__(
        self, options: OsSandboxOptions | None = None, *, bwrap_binary: str = "bwrap"
    ) -> None:
        self._options = options or OsSandboxOptions()
        self._binary = bwrap_binary

    @staticmethod
    def available(bwrap_binary: str = "bwrap") -> bool:
        return sys.platform.startswith("linux") and shutil.which(bwrap_binary) is not None

    async def run(
        self,
        template: CommandTemplate,
        *,
        cwd: Path,
        environment: EnvironmentPolicy | None = None,
    ) -> ProcessExecutionRecord:
        if not sys.platform.startswith("linux"):
            raise OsSandboxUnavailableError(
                f"Bubblewrap sandboxing works only on Linux; this platform is {sys.platform}."
            )
        binary = shutil.which(self._binary)
        if binary is None:
            raise OsSandboxUnavailableError(
                f'Bubblewrap ("{self._binary}") was not found on PATH; install the bubblewrap '
                "package or choose another sandbox."
            )
        env = (environment or default_native_environment()).resolve()
        argv = bubblewrap_argv(binary, self._options, template, cwd, env)
        return await _execute(template, argv, cwd, env)


class SeatbeltSandbox:
    def __init__(
        self, options: OsSandboxOptions | None = None, *, sandbox_binary: str = "sandbox-exec"
    ) -> None:
        self._options = options or OsSandboxOptions()
        self._binary = sandbox_binary

    @staticmethod
    def available(sandbox_binary: str = "sandbox-exec") -> bool:
        return sys.platform == "darwin" and shutil.which(sandbox_binary) is not None

    async def run(
        self,
        template: CommandTemplate,
        *,
        cwd: Path,
        environment: EnvironmentPolicy | None = None,
    ) -> ProcessExecutionRecord:
        if sys.platform != "darwin":
            raise OsSandboxUnavailableError(
                f"Seatbelt sandboxing works only on macOS; this platform is {sys.platform}."
            )
        binary = shutil.which(self._binary)
        if binary is None:
            raise OsSandboxUnavailableError(
                f'Seatbelt ("{self._binary}") was not found on PATH; it ships with macOS, so '
                "check that PATH includes /usr/bin."
            )
        env = (environment or default_native_environment()).resolve()
        argv = [binary, "-p", seatbelt_profile(self._options, cwd), *template.command]
        return await _execute(template, argv, cwd, env)


def default_os_sandbox(options: OsSandboxOptions | None = None) -> SandboxBackend | None:
    if BubblewrapSandbox.available():
        return BubblewrapSandbox(options)
    if SeatbeltSandbox.available():
        return SeatbeltSandbox(options)
    return None


def bubblewrap_argv(
    binary: str,
    options: OsSandboxOptions,
    template: CommandTemplate,
    cwd: Path,
    environment: dict[str, str],
) -> list[str]:
    project = cwd.resolve().as_posix()
    argv = [binary, "--die-with-parent", "--new-session"]
    argv += ["--unshare-pid", "--unshare-ipc", "--unshare-uts"]
    if not options.network:
        argv.append("--unshare-net")
    argv += ["--ro-bind", "/", "/"]
    for path in options.hide_paths:
        argv += ["--tmpfs", _absolute(path)]
    argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    for path in options.extra_read_only:
        argv += ["--ro-bind", _absolute(path), _absolute(path)]
    for path in [project, *options.extra_writable]:
        argv += ["--bind", _absolute(path), _absolute(path)]
    argv += ["--chdir", project, "--clearenv"]
    for name, value in sorted({"HOME": "/tmp", **environment}.items()):
        argv += ["--setenv", name, value]
    return [*argv, "--", *template.command]


def seatbelt_profile(options: OsSandboxOptions, cwd: Path) -> str:
    project = cwd.resolve().as_posix()
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow signal (target self))",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "(allow file-read*)",
        '(allow file-write* (literal "/dev/null") (literal "/dev/tty"))',
    ]
    writable = [project, *_SEATBELT_TEMP_PATHS, *options.extra_writable]
    rules.append(
        "(allow file-write* " + " ".join(f"(subpath {_sbpl(_absolute(p))})" for p in writable) + ")"
    )
    for path in options.hide_paths:
        rules.append(f"(deny file-read* file-write* (subpath {_sbpl(_absolute(path))}))")
    for path in options.extra_read_only:
        rules.append(f"(allow file-read* (subpath {_sbpl(_absolute(path))}))")
    if _inside_any(project, options.hide_paths):
        rules.append(f"(allow file-read* file-write* (subpath {_sbpl(project)}))")
    if options.network:
        rules.append("(allow network*)")
    return "\n".join(rules)


def _inside_any(project: str, hidden: list[str]) -> bool:
    return any(project == path or project.startswith(path.rstrip("/") + "/") for path in hidden)


async def _execute(
    template: CommandTemplate, argv: list[str], cwd: Path, environment: dict[str, str]
) -> ProcessExecutionRecord:
    wrapped = template.model_copy(update={"command": argv, "environment": None})
    supervisor = ProcessSupervisor([wrapped])
    return await supervisor.execute(wrapped.name, cwd=cwd, env=environment)


def _absolute(path: str) -> str:
    if not path.startswith("/") or "\x00" in path:
        raise ValueError(f'Sandbox path "{path}" must be absolute and contain no NUL byte.')
    return path


def _sbpl(path: str) -> str:
    if any(ord(character) < 32 for character in path):
        raise ValueError("A sandbox path must not contain control characters.")
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
