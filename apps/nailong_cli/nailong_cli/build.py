"""Turning settings into the pieces an agent needs: model, sandbox, project root."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import (
    OPENAI_COMPATIBLE_BASE_URLS,
    ModelSetup,
    anthropic_messages,
    openai_chat,
)
from nailong_agent_sdk.tools.os_sandbox import (
    BubblewrapSandbox,
    OsSandboxOptions,
    SeatbeltSandbox,
    default_os_sandbox,
)
from nailong_agent_sdk.tools.sandbox import DockerSandbox
from nailong_agent_sdk.tools.sandbox_models import DockerSandboxOptions

from . import credentials
from .approvals import NOT_SANDBOXED
from .config import CliConfig
from .network import host_of, is_loopback

EFFORT_LEVELS = ("low", "medium", "high")


class MissingKeyError(RuntimeError):
    pass


def effort_parameters(provider: str, level: str) -> dict[str, Any]:
    if level not in EFFORT_LEVELS:
        raise ValueError(f'effort must be one of {list(EFFORT_LEVELS)}, not "{level}".')
    if provider == "anthropic":
        return {"output_config": {"effort": level}}
    return {"reasoning_effort": level}


def build_model(config: CliConfig, environ: Mapping[str, str]) -> ModelSetup:
    key = None
    if config.api_key_env:
        key = environ.get(config.api_key_env, "").strip() or credentials.lookup(config.api_key_env)
        if not key:
            raise MissingKeyError(
                f'The environment variable "{config.api_key_env}" named by api_key_env is not set '
                "or is empty, and no key is saved for it (nailong --setup can save one)."
            )
    if config.provider == "anthropic":
        return anthropic_messages(
            api_key=key,
            model=config.model,
            **({"base_url": config.base_url} if config.base_url else {}),
            **({"max_tokens": config.max_tokens} if config.max_tokens else {}),
            timeout_seconds=config.timeout_seconds,
            parameters=dict(config.parameters) or None,
        )
    base_url = OPENAI_COMPATIBLE_BASE_URLS.get(config.base_url or "", config.base_url)
    parameters = dict(config.parameters)
    if config.max_tokens:
        parameters["max_tokens"] = config.max_tokens
    return openai_chat(
        base_url=str(base_url),
        api_key=key,
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        allow_insecure_http=config.allow_insecure_http or is_loopback(host_of(str(base_url))),
        parameters=parameters or None,
        tool_mode=config.tool_mode,
    )


def choose_sandbox(
    config: CliConfig, extra_dirs: Mapping[str, Path] | None = None
) -> tuple[dict[str, Any], str]:
    extra = dict(extra_dirs or {})
    if config.sandbox == "docker":
        binds = [f"{path}:/extra/{name}" for name, path in extra.items()]
        if not binds:
            return {"docker_image": config.docker_image}, f"docker {config.docker_image}"
        options = DockerSandboxOptions(image=config.docker_image, extra_binds=binds)
        return {"sandbox": DockerSandbox(options)}, f"docker {config.docker_image}"
    if config.sandbox == "auto":
        options = OsSandboxOptions(extra_writable=[path.as_posix() for path in extra.values()])
        backend = default_os_sandbox(options)
        if isinstance(backend, BubblewrapSandbox):
            return {"sandbox": backend}, "bubblewrap"
        if isinstance(backend, SeatbeltSandbox):
            return {"sandbox": backend}, "seatbelt"
    return {}, NOT_SANDBOXED


def project_root(path: Path) -> Path:
    root = path.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"The project directory {root} does not exist.")
    return root
