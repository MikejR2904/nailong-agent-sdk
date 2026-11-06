"""Settings: a user file, a project file and command-line flags, in that order of precedence."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from nailong_agent_sdk.starter import OPENAI_COMPATIBLE_BASE_URLS

from .network import offline_problems
from .rules import rule_from_table

PROVIDERS = ("anthropic", "openai-compatible")
SANDBOXES = ("auto", "none", "docker")
MODES = ("default", "accept-edits", "auto", "plan", "bypass")
HOOK_EVENTS = (
    "pre_tool",
    "post_tool",
    "session_start",
    "session_end",
    "prompt_submit",
    "turn_end",
    "file_edited",
    "tool_failed",
    "assistant_message",
    "permission_asked",
    "compacted",
)
EDITING_MODES = ("emacs", "vi")
TOOL_MODES = ("auto", "native", "prompted")
SUBAGENT_TOOLS = ("read-only", "edit", "all")
REQUIRED = ("provider", "model")
DEFAULTS: dict[str, Any] = {
    "max_steps": 30,
    "task_attempts": 2,
    "team_parallel": 1,
    "context_tokens": 48_000,
    "tool_result_chars": 12_000,
    "sandbox": "auto",
    "docker_image": "python:3.12-slim",
    "timeout_seconds": 120.0,
    "invalid_output_retries": 2,
    "model_timeout_seconds": 300.0,
    "tool_timeout_seconds": 600.0,
    "run_deadline_seconds": 0,
    "permission_mode": "default",
    "web": False,
    "web_search": False,
    "notify": False,
    "autocompact": True,
    "show_reasoning": True,
    "allow_insecure_http": False,
    "require_sandbox": False,
    "embedding_model": "",
    "embedding_base_url": "",
    "tool_mode": "auto",
    "fallback_models": (),
    "otlp_include": ("telemetry", "audit", "integrity"),
    "ssh_hosts": (),
    "hook_env": (),
    "format_on_edit": False,
    "diagnostics_on_edit": False,
    "lsp_on_edit": False,
    "instruction_files": (),
    "small_model": "",
    "offline": False,
    "api_key_env": "",
    "editing_mode": "emacs",
    "theme": "default",
}
TABLES = ("hooks", "mcp", "agents", "prices", "parameters", "variants", "keybinds")
USER_ONLY = (
    "hooks",
    "mcp",
    "add_dirs",
    "offline",
    "otlp_endpoint",
    "otlp_include",
    "ssh_hosts",
    "hook_env",
    "require_sandbox",
    "embedding_model",
    "embedding_base_url",
    "format_on_edit",
    "diagnostics_on_edit",
    "formatters",
    "diagnostics",
    "lsp_on_edit",
    "lsp",
)
OTLP_PARTS = ("telemetry", "audit", "integrity")
KNOWN = frozenset(
    {
        *REQUIRED,
        *DEFAULTS,
        *TABLES,
        "base_url",
        "max_tokens",
        "instructions",
        "max_budget_usd",
        "otlp_endpoint",
        "rules",
        "formatters",
        "diagnostics",
        "lsp",
    }
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
    task_attempts: int
    team_parallel: int
    context_tokens: int
    tool_result_chars: int
    sandbox: str
    docker_image: str
    timeout_seconds: float
    invalid_output_retries: int
    model_timeout_seconds: float
    tool_timeout_seconds: float
    run_deadline_seconds: float
    instructions: str | None
    permission_mode: str
    web: bool
    web_search: bool
    notify: bool
    autocompact: bool
    show_reasoning: bool
    allow_insecure_http: bool
    require_sandbox: bool
    embedding_model: str
    embedding_base_url: str
    tool_mode: str
    fallback_models: tuple[str, ...]
    otlp_include: tuple[str, ...]
    ssh_hosts: tuple[str, ...]
    hook_env: tuple[str, ...]
    format_on_edit: bool
    diagnostics_on_edit: bool
    lsp_on_edit: bool
    instruction_files: tuple[str, ...]
    small_model: str
    otlp_endpoint: str | None
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
    formatters: dict[str, Any] = field(default_factory=dict)
    variants: dict[str, Any] = field(default_factory=dict)
    keybinds: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    lsp: dict[str, Any] = field(default_factory=dict)
    rules: tuple[dict[str, Any], ...] = ()

    @property
    def workspace(self) -> Path:
        return self.project / PROJECT_DIRECTORY / "workspace"


def user_config_path(home: Path) -> Path:
    return home / ".config" / "nailong" / "config.toml"


def project_config_path(project: Path) -> Path:
    return project / PROJECT_DIRECTORY / "config.toml"


def load_config(
    project: Path,
    *,
    home: Path,
    overrides: dict[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> CliConfig:
    merged: dict[str, Any] = dict(DEFAULTS)
    tables: dict[str, dict[str, Any]] = {
        "agents": {},
        "prices": {},
        "parameters": {},
        "variants": {},
        "keybinds": {},
    }
    ignored: list[str] = []
    rule_entries: list[dict[str, Any]] = []
    for path, trusted in ((user_config_path(home), True), (project_config_path(project), False)):
        data = _read(path, (environ if environ is not None else os.environ) if trusted else None)
        if not trusted:
            ignored = [key for key in USER_ONLY if key in data]
            for key in ignored:
                del data[key]
        for name, table in tables.items():
            table.update(data.pop(name, {}))
        entries = data.pop("rules", [])
        if not trusted and any(
            isinstance(item, dict) and item.get("action") == "allow" for item in entries
        ):
            ignored.append("rules (allow entries)")
            entries = [
                item
                for item in entries
                if not (isinstance(item, dict) and item.get("action") == "allow")
            ]
        rule_entries.extend(
            {**item, "source": "user" if trusted else "project"}
            for item in entries
            if isinstance(item, dict)
        )
        if any(not isinstance(item, dict) for item in entries):
            raise ConfigError(f"rules in {path} must be a list of tables ([[rules]]).")
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
    for position, entry in enumerate(rule_entries, 1):
        try:
            rule_from_table(entry, entry["source"], position)
        except ValueError as error:
            raise ConfigError(str(error)) from error
    return CliConfig(
        provider=merged["provider"],
        model=merged["model"],
        api_key_env=merged["api_key_env"],
        base_url=merged.get("base_url"),
        max_tokens=merged.get("max_tokens"),
        max_steps=merged["max_steps"],
        task_attempts=merged["task_attempts"],
        team_parallel=merged["team_parallel"],
        context_tokens=merged["context_tokens"],
        tool_result_chars=merged["tool_result_chars"],
        sandbox=merged["sandbox"],
        docker_image=merged["docker_image"],
        timeout_seconds=float(merged["timeout_seconds"]),
        invalid_output_retries=merged["invalid_output_retries"],
        model_timeout_seconds=float(merged["model_timeout_seconds"]),
        tool_timeout_seconds=float(merged["tool_timeout_seconds"]),
        run_deadline_seconds=float(merged["run_deadline_seconds"]),
        instructions=merged.get("instructions"),
        permission_mode=merged["permission_mode"],
        web=bool(merged["web"]),
        web_search=bool(merged["web_search"]),
        notify=bool(merged["notify"]),
        autocompact=bool(merged["autocompact"]),
        show_reasoning=bool(merged["show_reasoning"]),
        allow_insecure_http=bool(merged["allow_insecure_http"]),
        require_sandbox=bool(merged["require_sandbox"]),
        embedding_model=str(merged["embedding_model"]),
        embedding_base_url=str(merged["embedding_base_url"]),
        tool_mode=merged["tool_mode"],
        fallback_models=tuple(merged["fallback_models"]),
        otlp_include=tuple(merged["otlp_include"]),
        ssh_hosts=tuple(merged["ssh_hosts"]),
        hook_env=tuple(merged["hook_env"]),
        format_on_edit=bool(merged["format_on_edit"]),
        diagnostics_on_edit=bool(merged["diagnostics_on_edit"]),
        lsp_on_edit=bool(merged["lsp_on_edit"]),
        lsp=dict(merged.get("lsp", {})),
        instruction_files=tuple(merged["instruction_files"]),
        small_model=str(merged["small_model"]),
        variants=tables["variants"],
        keybinds=tables["keybinds"],
        formatters=dict(merged.get("formatters", {})),
        diagnostics=dict(merged.get("diagnostics", {})),
        otlp_endpoint=merged.get("otlp_endpoint"),
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
        rules=tuple(rule_entries),
    )


def _read(path: Path, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not valid TOML: {error}") from error
    if environ is not None:
        data = _substituted(data, path, environ)
    unknown = sorted(set(data) - KNOWN)
    if unknown:
        raise ConfigError(
            f"{path} has unknown setting(s) {unknown}; known settings: {sorted(KNOWN)}."
        )
    return data


_REFERENCE = re.compile(r"\{(env|file):([^{}]+)\}")


def _substituted(value: Any, path: Path, environ: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _substituted(item, path, environ) for key, item in value.items()}
    if isinstance(value, list):
        return [_substituted(item, path, environ) for item in value]
    if not isinstance(value, str):
        return value

    def fill(found: re.Match[str]) -> str:
        kind, name = found.group(1), found.group(2).strip()
        if kind == "env":
            return environ.get(name, "")
        target = Path(name).expanduser()
        target = target if target.is_absolute() else path.parent / target
        try:
            return target.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as error:
            raise ConfigError(
                f"{path}: {{file:{name}}} cannot be read ({type(error).__name__}: {error})."
            ) from error

    return _REFERENCE.sub(fill, value)


def _validate(values: dict[str, Any], tables: dict[str, dict[str, Any]]) -> None:
    for key, allowed in (
        ("provider", PROVIDERS),
        ("sandbox", SANDBOXES),
        ("permission_mode", MODES),
        ("editing_mode", EDITING_MODES),
        ("tool_mode", TOOL_MODES),
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
            otlp_endpoint=values.get("otlp_endpoint"),
        )
        if problems:
            raise ConfigError("offline is on but " + "; ".join(problems) + ".")
    _validate_fallbacks(values)
    _validate_endpoint(values.get("otlp_endpoint"))
    _validate_include(values["otlp_include"])
    _validate_hosts(values["ssh_hosts"])
    _validate_hook_env(values["hook_env"])
    _validate_checkers(values)
    listed = values["instruction_files"]
    if not isinstance(listed, list | tuple) or any(not isinstance(item, str) for item in listed):
        raise ConfigError(
            f"instruction_files must be a list of file names or globs, not {listed!r}."
        )
    for name in ("model_timeout_seconds", "tool_timeout_seconds", "run_deadline_seconds"):
        seconds = values[name]
        if isinstance(seconds, bool) or not isinstance(seconds, int | float) or seconds < 0:
            raise ConfigError(f"{name} must be 0 (no limit) or more seconds, not {seconds!r}.")
    retries = values["invalid_output_retries"]
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ConfigError(f"invalid_output_retries must be 0 or more, not {retries!r}.")
    for key, floor, ceiling in (
        ("context_tokens", 2_000, 1_000_000),
        ("tool_result_chars", 200, 100_000),
        ("task_attempts", 1, 5),
        ("team_parallel", 1, 4),
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
        if hook.get("on_failure", "block") not in {"block", "warn"}:
            raise ConfigError(
                f'hooks entry {index + 1} on_failure must be "block" (a failing hook refuses the '
                f'action) or "warn" (it is reported and the action goes ahead), not '
                f"{hook['on_failure']!r}."
            )
    for name, server in values.get("mcp", {}).items():
        if not isinstance(server, dict) or not (server.get("command") or server.get("url")):
            raise ConfigError(f'mcp server "{name}" needs a command (stdio) or a url (http).')
        for key in ("allow_tools", "deny_tools"):
            listed = server.get(key, [])
            if not isinstance(listed, list) or any(not isinstance(item, str) for item in listed):
                raise ConfigError(f'mcp server "{name}" {key} must be a list of tool names.')
    for name, agent in tables["agents"].items():
        _validate_agent(name, agent)
    unknown_actions = sorted(
        set(tables["keybinds"]) - {"cycle_mode", "expand_output", "newline", "expand_paste"}
    )
    if unknown_actions:
        raise ConfigError(
            f"keybinds has unknown action(s) {unknown_actions}; the actions are cycle_mode, "
            "expand_output, newline and expand_paste."
        )
    for variant, settings in tables["variants"].items():
        if not isinstance(settings, dict) or not settings:
            raise ConfigError(
                f'variants "{variant}" needs model parameters such as effort = "high".'
            )
    for model, price in tables["prices"].items():
        needed = ("input_per_million", "cached_input_per_million", "output_per_million")
        if not isinstance(price, dict) or any(key not in price for key in needed):
            raise ConfigError(f'prices "{model}" needs {", ".join(needed)}.')
    for directory in values.get("add_dirs", ()):
        if not isinstance(directory, str) or not Path(directory).is_absolute():
            raise ConfigError(f"add_dirs entries must be absolute paths, not {directory!r}.")


def _validate_fallbacks(values: dict[str, Any]) -> None:
    names = values["fallback_models"]
    if not isinstance(names, list | tuple) or any(
        not isinstance(name, str) or not name.strip() for name in names
    ):
        raise ConfigError(f"fallback_models must be a list of model names, not {names!r}.")
    if values["model"] in names:
        raise ConfigError(f'fallback_models lists "{values["model"]}", which is already the model.')
    if len(set(names)) != len(names):
        raise ConfigError(f"fallback_models lists a model twice: {list(names)}.")


def _validate_checkers(values: dict[str, Any]) -> None:
    from .lsp import specs_from_table
    from .post_edit import checkers_from_table

    try:
        specs_from_table(values.get("lsp", {}) if isinstance(values.get("lsp", {}), dict) else {})
    except (ValueError, AttributeError) as error:
        raise ConfigError(str(error)) from error
    for kind in ("formatters", "diagnostics"):
        table = values.get(kind, {})
        if not isinstance(table, dict) or any(
            not isinstance(item, dict) for item in table.values()
        ):
            raise ConfigError(f"{kind} must be tables such as [{kind}.mine].")
        try:
            checkers_from_table(table, kind)
        except ValueError as error:
            raise ConfigError(str(error)) from error


def _validate_hosts(hosts: Any) -> None:
    if not isinstance(hosts, list | tuple) or any(
        not isinstance(host, str)
        or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:\[\]-]{0,252}", host)
        for host in hosts
    ):
        raise ConfigError(
            "ssh_hosts must be a list of host names such as admin@server1 (letters, digits and "
            f". _ @ : - only, not starting with a dash), not {hosts!r}."
        )


def _validate_include(parts: Any) -> None:
    if (
        not isinstance(parts, list | tuple)
        or not parts
        or any(part not in OTLP_PARTS for part in parts)
        or len(set(parts)) != len(parts)
    ):
        raise ConfigError(
            f"otlp_include must list some of {list(OTLP_PARTS)} once each, not {parts!r}."
        )


def _validate_endpoint(endpoint: Any) -> None:
    if endpoint is None:
        return
    parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
    if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"otlp_endpoint must be an http(s) URL, not {endpoint!r}.")
    if parsed.username or parsed.password:
        raise ConfigError(
            "otlp_endpoint must not contain credentials; use OTEL_EXPORTER_OTLP_HEADERS."
        )


def _resolved_base_url(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    return OPENAI_COMPATIBLE_BASE_URLS.get(base_url, base_url)


def _validate_hook_env(names: Any) -> None:
    if not isinstance(names, list | tuple) or any(
        not isinstance(item, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item)
        for item in names
    ):
        raise ConfigError(
            'hook_env must be a list of environment variable names such as ["JIRA_URL"].'
        )


def _validate_agent(name: str, agent: Any) -> None:
    if not isinstance(agent, dict) or not agent.get("description") or not agent.get("instructions"):
        raise ConfigError(f'agents "{name}" needs a description and instructions.')
    tools = agent.get("tools", "read-only")
    if tools not in SUBAGENT_TOOLS:
        raise ConfigError(f'agents "{name}" tools must be one of {list(SUBAGENT_TOOLS)}.')
    temperature, steps, deny = agent.get("temperature"), agent.get("steps"), agent.get("deny", [])
    if temperature is not None and (
        isinstance(temperature, bool) or not isinstance(temperature, int | float)
    ):
        raise ConfigError(f'agents "{name}" temperature must be a number, not {temperature!r}.')
    if steps is not None and (isinstance(steps, bool) or not isinstance(steps, int) or steps < 1):
        raise ConfigError(f'agents "{name}" steps must be a whole number of 1 or more.')
    if not isinstance(deny, list) or any(not isinstance(item, str) for item in deny):
        raise ConfigError(f'agents "{name}" deny must be a list such as ["run_command:rm *"].')
    model = agent.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ConfigError(f'agents "{name}" model must be a model name, not {model!r}.')
