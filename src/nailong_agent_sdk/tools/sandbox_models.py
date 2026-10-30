# Copyright (c) 2026 David Michael Indraputra

"""Typed configuration for the native and Docker sandbox execution backends."""

from __future__ import annotations

import os
from enum import StrEnum

from pydantic import Field

from ..foundations.contracts import StrictModel


class SandboxKind(StrEnum):
    NATIVE = "native"
    DOCKER = "docker"


class EnvironmentPolicy(StrictModel):
    """An explicit environment allowlist; nothing outside it reaches the child.

    The host process's full environment is never inherited implicitly. Only
    the named variables (if present) and any literal overrides are passed to
    the sandboxed command, so host secrets sitting in unrelated environment
    variables cannot leak into a tool subprocess.
    """

    allowed_variable_names: list[str] = Field(default_factory=list)
    literal_variables: dict[str, str] = Field(default_factory=dict)

    def resolve(self) -> dict[str, str]:
        """Return only the host variables this policy names, plus its literal overrides."""

        scrubbed = {
            name: os.environ[name] for name in self.allowed_variable_names if name in os.environ
        }
        scrubbed.update(self.literal_variables)
        return scrubbed


def default_native_environment() -> EnvironmentPolicy:
    names = (
        ["SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP"]
        if os.name == "nt"
        else ["PATH", "LANG", "LC_ALL", "TMPDIR"]
    )
    return EnvironmentPolicy(allowed_variable_names=names)


class DockerSandboxOptions(StrictModel):
    """Per-template container settings for :class:`DockerSandbox`."""

    image: str = Field(min_length=1)
    network_disabled: bool = True
    read_only_root: bool = True
    memory_bytes: int | None = Field(default=None, ge=1)
    cpu_limit: float | None = Field(default=None, gt=0)
    extra_binds: list[str] = Field(default_factory=list)
