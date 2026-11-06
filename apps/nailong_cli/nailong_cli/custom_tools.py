"""Tools you write as Python files: drop one in a tools folder and the agent can call it."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from nailong_agent_sdk.starter import FunctionTool, tool

from .config import PROJECT_DIRECTORY

TRUST_FILE = "trusted-tools.json"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
TEMPLATE = '''"""A tool for nailong: the agent calls `{name}` and gets what it returns."""

from nailong_agent_sdk.starter import tool


@tool
def {name}(text: str) -> str:
    """Say in one sentence what this does and when to use it; the model reads this."""
    return text.upper()
'''


@dataclass
class CustomTools:
    tools: list[FunctionTool] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    untrusted: list[Path] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)


def tool_folders(project: Path, home: Path) -> list[tuple[str, Path]]:
    return [
        ("user", home / ".config" / "nailong" / "tools"),
        ("project", project / PROJECT_DIRECTORY / "tools"),
    ]


def trust_path(home: Path) -> Path:
    return home / ".config" / "nailong" / TRUST_FILE


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trusted(home: Path) -> dict[str, str]:
    path = trust_path(home)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(
            f"The trusted-tools file {path} cannot be read: {type(error).__name__}: {error}. "
            "Delete it to trust each project tool file again."
        ) from error
    return {str(key): str(value) for key, value in data.items()} if isinstance(data, dict) else {}


def trust(home: Path, path: Path) -> str:
    known = _trusted(home)
    digest = file_hash(path)
    known[str(path.resolve())] = digest
    target = trust_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(known, indent=2), encoding="utf-8")
    return digest


def _import(path: Path) -> object:
    name = f"nailong_custom_tool_{hashlib.sha256(str(path).encode()).hexdigest()[:10]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path} cannot be loaded as a Python module.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _collect(module: object, path: Path) -> list[FunctionTool]:
    found = [
        value
        for key, value in vars(module).items()
        if isinstance(value, FunctionTool) and not key.startswith("_")
    ]
    if found:
        return found
    run = getattr(module, "run", None)
    if callable(run):
        return [tool(run, name=path.stem)]
    raise ValueError(
        f"{path} defines no tool: decorate a function with @tool from "
        "nailong_agent_sdk.starter, or define run(...) with type hints and a docstring."
    )


def load_custom_tools(project: Path, home: Path) -> CustomTools:
    result = CustomTools()
    known = _trusted(home)
    taken: dict[str, Path] = {}
    for source, folder in tool_folders(project, home):
        for path in sorted(folder.glob("*.py")) if folder.is_dir() else []:
            if path.name.startswith("_"):
                continue
            if source == "project":
                digest = known.get(str(path.resolve()))
                if digest is None:
                    result.untrusted.append(path)
                    result.notices.append(
                        f"{path} is not loaded: a project tool file is code, so read it and run "
                        "/trust-tools to allow it."
                    )
                    continue
                if digest != file_hash(path):
                    result.untrusted.append(path)
                    result.notices.append(
                        f"{path} changed since you trusted it, so it is not loaded; read it and "
                        "run /trust-tools again."
                    )
                    continue
            try:
                loaded = _collect(_import(path), path)
            except BaseException as error:
                if isinstance(error, KeyboardInterrupt | SystemExit):
                    raise
                result.notices.append(
                    f"{path} could not be loaded: {type(error).__name__}: {error}"
                )
                continue
            for item in loaded:
                if not _NAME.match(item.name):
                    result.notices.append(
                        f'{path}: the tool name "{item.name}" must be letters, digits and '
                        "underscores."
                    )
                    continue
                if item.name in taken:
                    result.notices.append(
                        f'{path}: the tool "{item.name}" already comes from '
                        f"{taken[item.name]}; this one is skipped."
                    )
                    continue
                taken[item.name] = path
                guarded = (
                    item
                    if item.definition.concurrency.name == "PARALLEL_SAFE"
                    else (dataclasses.replace(item, needs_approval=True))
                )
                result.tools.append(guarded)
                result.sources[item.name] = f"{source}: {path}"
    return result


def create_tool_file(project: Path, home: Path, name: str, *, user: bool) -> Path:
    if not _NAME.match(name):
        raise ValueError(
            f'Tool name "{name}" must be letters, digits and underscores, starting with a letter.'
        )
    folder = (
        home / ".config" / "nailong" / "tools" if user else project / PROJECT_DIRECTORY / "tools"
    )
    path = folder / f"{name}.py"
    if path.exists():
        raise ValueError(f"{path} already exists; edit it or pick another name.")
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.format(name=name), encoding="utf-8")
    return path


def reload_custom_tools(state: object) -> None:
    from .turns import switch_agent

    state.parts.custom_tools = load_custom_tools(state.project, state.home)  # type: ignore[attr-defined]
    switch_agent(state)  # type: ignore[arg-type]
    state.mode_dirty = False  # type: ignore[attr-defined]
