# Copyright (c) 2026 David Michael Indraputra

"""The SDK's governed file tools, offered to an agent with one call."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..foundations.contracts import ToolDefinition, ToolExecutionResult
from ..tools.artifacts import ArtifactStore
from ..tools.core.definitions import core_tool_definitions
from ..tools.core.services import (
    CoreToolDispatcher,
    CoreToolServices,
    HumanQuestionResponder,
)

READ_TOOLS = ("read_file", "glob", "grep")
WRITE_TOOLS = ("write_draft", "edit_draft")
PRIMARY_ROOT = "project"


@dataclass(frozen=True)
class CoreTools:
    definitions: tuple[ToolDefinition, ...]
    dispatcher: CoreToolDispatcher
    needs_approval: bool = False

    async def run(self, name: str, call_arguments: dict[str, Any]) -> ToolExecutionResult:
        return await self.dispatcher.execute(name, call_arguments)


@dataclass(frozen=True)
class MultiRootCoreTools:
    definitions: tuple[ToolDefinition, ...]
    dispatchers: dict[str, CoreToolDispatcher]
    primary: str = PRIMARY_ROOT
    needs_approval: bool = False

    async def run(self, name: str, call_arguments: dict[str, Any]) -> ToolExecutionResult:
        arguments = dict(call_arguments)
        root = arguments.pop("root", self.primary)
        dispatcher = self.dispatchers.get(root)
        if dispatcher is None:
            return ToolExecutionResult(
                status="failed",
                error=f'There is no root named "{root}"; the roots are {sorted(self.dispatchers)}.',
            )
        return await dispatcher.execute(name, arguments)


def core_tools(
    root: Path,
    *,
    writable: Sequence[str] = (),
    only: Sequence[str] | None = None,
    needs_approval: bool = False,
    ask_human: HumanQuestionResponder | None = None,
) -> CoreTools:
    names = (
        tuple(only) if only is not None else (*READ_TOOLS, *WRITE_TOOLS) if writable else READ_TOOLS
    )
    available = {definition.name: definition for definition in core_tool_definitions()}
    return CoreTools(
        definitions=tuple(available[name] for name in names),
        dispatcher=_dispatcher(root, writable, ask_human),
        needs_approval=needs_approval,
    )


def multi_root_read_tools(roots: Mapping[str, Path]) -> MultiRootCoreTools:
    if PRIMARY_ROOT not in roots:
        raise ValueError(f'The roots need an entry named "{PRIMARY_ROOT}" for the main project.')
    available = {definition.name: definition for definition in core_tool_definitions()}
    note = (
        f" Add root to read another directory ({', '.join(sorted(roots))}); the default is "
        f"{PRIMARY_ROOT}."
    )
    definitions = []
    for name in READ_TOOLS:
        schema = copy.deepcopy(available[name].input_schema)
        schema["properties"]["root"] = {"type": "string", "enum": sorted(roots)}
        definitions.append(
            available[name].model_copy(
                update={"input_schema": schema, "description": available[name].description + note}
            )
        )
    return MultiRootCoreTools(
        definitions=tuple(definitions),
        dispatchers={name: _dispatcher(path, ()) for name, path in roots.items()},
    )


def _dispatcher(
    root: Path, writable: Sequence[str], ask_human: HumanQuestionResponder | None = None
) -> CoreToolDispatcher:
    services = CoreToolServices(
        root=root,
        artifacts=ArtifactStore(root, read_only=not writable),
        declared_output_paths=tuple(writable),
        ask_human=ask_human,
    )
    return CoreToolDispatcher(services)
