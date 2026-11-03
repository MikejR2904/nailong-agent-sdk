"""Settings: a user file, a project file and command-line flags, in that order of precedence."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import OPENAI_COMPATIBLE_BASE_URLS

from .network import offline_problems

PROVIDERS = ("anthropic", "openai-compatible")
SANDBOXES = ("auto", "none", "docker")
MODES = ("default", "accept-edits", "auto", "plan", "bypass")
HOOK_EVENTS = ("pre_tool", "post_tool", "session_start", "session_end", "prompt_submit", "turn_end")
EDITING_MODES = ("emacs", "vi")
SUBAGENT_TOOLS = ("read-only", "edit", "all")
REQUIRED = ("provider", "model")
DEFAULTS: dict[str, Any] = {
    "max_steps": 30,
    "context_tokens": 48_000,
    "tool_result_chars": 12_000,
    "sandbox": "auto",
    "docker_image": "python:3.12-slim",
    "timeout_seconds": 120.0,
    "invalid_output_retries": 2,
    "permission_mode": "default",
    "web": False,
    "web_search": False,
    "notify": False,
    "autocompact": False,
    "show_reasoning": True,
    "allow_insecure_http": False,
    "offline": False,
    "api_key_env": "",
    "editing_mode": "emacs",
    "theme": "default",
}
TABLES = ("hooks", "mcp", "agents", "prices", "parameters")
USER_ONLY = ("hooks", "mcp", "add_dirs", "offline")
KNOWN = frozenset(
    {*REQUIRED, *DEFAULTS, *TABLES, "base_url", "max_tokens", "instructions", "max_budget_usd"}
    | {"add_dirs"}
)
PROJECT_DIRECTORY = ".nailong"
MEMORY_FILES = ("NAILONG.md", "AGENTS.md", "CLAUDE.md")


class ConfigError(ValueError):
    pass


class MissingSettingsError(ConfigError):
    pass


@dataclass(frozen=True)
class CliConfig:
    provider: str
    model: str
    api_key_env: str
    base_url: str | None
    max_tokens: int | None
    max_steps: int
    context_tokens: int
    tool_result_chars: int
    sandbox: str
    docker_image: str
    timeout_seconds: float
    invalid_output_retries: int
    instructions: str | None
    permission_mode: str
    web: bool
    web_search: bool
    notify: bool
    autocompact: bool
    show_reasoning: bool
    allow_insecure_http: bool
    offline: bool
    editing_mode: str
    theme: str
    max_budget_usd: float | None
    project: Path
    hooks: tuple[dict[str, Any], ...] = ()
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    prices: dict[str, dict[str, Any]] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    add_dirs: tuple[str, ...] = ()
    ignored_project_keys: tuple[str, ...] = ()

    @property
    def workspace(self) -> Path:
        return self.project / PROJECT_DIRECTORY / "workspace"


def user_config_path(home: Path) -> Path:
    return home / ".config" / "nailong" / "config.toml"


def project_config_path(project: Path) -> Path:
    return project / PROJECT_DIRECTORY / "config.toml"


def load_config(project: Path, *, home: Path, overrides: dict[str, Any] | None = None) -> CliConfig:
    merged: dict[str, Any] = dict(DEFAULTS)
    tables: dict[str, dict[str, Any]] = {"agents": {}, "prices": {}, "parameters": {}}
    ignored: list[str] = []
    for path, trusted in ((user_config_path(home), True), (project_config_path(project), False)):
        data = _read(path)
        if not trusted:
            ignored = [key for key in USER_ONLY if key in data]
            for key in ignored:
                del data[key]
        for name, table in tables.items():
            table.update(data.pop(name, {}))
        merged.update(data)
    flags = dict(overrides or {})
    tables["parameters"].update(flags.pop("parameters", None) or {})
    merged.update({key: value for key, value in flags.items() if value is not None})
    missing = [key for key in REQUIRED if not merged.get(key)]
    if missing:
        raise MissingSettingsError(
            f"Missing setting(s): {', '.join(missing)}. Set them in {project_config_path(project)} "
            f"or {user_config_path(home)}, or pass them as flags."
        )
    _validate(merged, tables)
    return CliConfig(
        provider=merged["provider"],
        model=merged["model"],
        api_key_env=merged["api_key_env"],
        base_url=merged.get("base_url"),
        max_tokens=merged.get("max_tokens"),
        max_steps=merged["max_steps"],
        context_tokens=merged["context_tokens"],
        tool_result_chars=merged["tool_result_chars"],
        sandbox=merged["sandbox"],
        docker_image=merged["docker_image"],
        timeout_seconds=float(merged["timeout_seconds"]),
        invalid_output_retries=merged["invalid_output_retries"],
        instructions=merged.get("instructions"),
        permission_mode=merged["permission_mode"],
        web=bool(merged["web"]),
        web_search=bool(merged["web_search"]),
        notify=bool(merged["notify"]),
        autocompact=bool(merged["autocompact"]),
        show_reasoning=bool(merged["show_reasoning"]),
        allow_insecure_http=bool(merged["allow_insecure_http"]),
        offline=bool(merged["offline"]),
        editing_mode=merged["editing_mode"],
        theme=merged["theme"],
        max_budget_usd=merged.get("max_budget_usd"),
        project=project,
        hooks=tuple(merged.get("hooks", ())),
        mcp_servers=dict(merged.get("mcp", {})),
        agents=tables["agents"],
        prices=tables["prices"],
        parameters=tables["parameters"],
        add_dirs=tuple(merged.get("add_dirs", ())),
        ignored_project_keys=tuple(ignored),
    )


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not valid TOML: {error}") from error
    unknown = sorted(set(data) - KNOWN)
    if unknown:
        raise ConfigError(
            f"{path} has unknown setting(s) {unknown}; known settings: {sorted(KNOWN)}."
        )
    return data


def _validate(values: dict[str, Any], tables: dict[str, dict[str, Any]]) -> None:
    for key, allowed in (
        ("provider", PROVIDERS),
        ("sandbox", SANDBOXES),
        ("permission_mode", MODES),
        ("editing_mode", EDITING_MODES),
    ):
        if values[key] not in allowed:
            raise ConfigError(f'{key} must be one of {list(allowed)}, not "{values[key]}".')
    if values["provider"] == "openai-compatible" and not values.get("base_url"):
        raise ConfigError("provider openai-compatible needs base_url.")
    if values["provider"] == "anthropic" and not values.get("api_key_env"):
        raise ConfigError("provider anthropic needs api_key_env, the name of the key's variable.")
    if values["offline"]:
        problems = offline_problems(
            provider=values["provider"],
            base_url=_resolved_base_url(values.get("base_url")),
            web=bool(values["web"]),
            web_search=bool(values["web_search"]),
            mcp_servers=values.get("mcp", {}),
        )
        if problems:
            raise ConfigError("offline is on but " + "; ".join(problems) + ".")
    retries = values["invalid_output_retries"]
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ConfigError(f"invalid_output_retries must be 0 or more, not {retries!r}.")
    for key, floor, ceiling in (
        ("context_tokens", 2_000, 1_000_000),
        ("tool_result_chars", 200, 100_000),
    ):
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, int) or not floor <= value <= ceiling:
            raise ConfigError(
                f"{key} must be a whole number from {floor} to {ceiling}, not {value!r}."
            )
    for key in ("max_steps", "max_tokens"):
        value = values.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            raise ConfigError(f"{key} must be a whole number of at least 1, not {value!r}.")
    if not isinstance(values["timeout_seconds"], int | float) or values["timeout_seconds"] <= 0:
        raise ConfigError(f"timeout_seconds must be positive, not {values['timeout_seconds']!r}.")
    budget = values.get("max_budget_usd")
    if budget is not None and (not isinstance(budget, int | float) or budget <= 0):
        raise ConfigError(f"max_budget_usd must be positive, not {budget!r}.")
    for index, hook in enumerate(values.get("hooks", ())):
        if hook.get("event") not in HOOK_EVENTS or not hook.get("command"):
            raise ConfigError(
                f"hooks entry {index + 1} needs event ({list(HOOK_EVENTS)}) and a command."
            )
    for name, server in values.get("mcp", {}).items():
        if not isinstance(server, dict) or not (server.get("command") or server.get("url")):
            raise ConfigError(f'mcp server "{name}" needs a command (stdio) or a url (http).')
    for name, agent in tables["agents"].items():
        _validate_agent(name, agent)
    for model, price in tables["prices"].items():
        needed = ("input_per_million", "cached_input_per_million", "output_per_million")
        if not isinstance(price, dict) or any(key not in price for key in needed):
            raise ConfigError(f'prices "{model}" needs {", ".join(needed)}.')
    for directory in values.get("add_dirs", ()):
        if not isinstance(directory, str) or not Path(directory).is_absolute():
            raise ConfigError(f"add_dirs entries must be absolute paths, not {directory!r}.")


def _resolved_base_url(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    return OPENAI_COMPATIBLE_BASE_URLS.get(base_url, base_url)


def _validate_agent(name: str, agent: Any) -> None:
    if not isinstance(agent, dict) or not agent.get("description") or not agent.get("instructions"):
        raise ConfigError(f'agents "{name}" needs a description and instructions.')
    tools = agent.get("tools", "read-only")
    if tools not in SUBAGENT_TOOLS:
        raise ConfigError(f'agents "{name}" tools must be one of {list(SUBAGENT_TOOLS)}.')
