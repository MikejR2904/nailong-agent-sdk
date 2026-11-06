# Copyright (c) 2026 David Michael Indraputra

"""Turn a plain Python function into a tool the SDK can declare, validate and run."""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model
from pydantic_core import to_jsonable_python

from ..foundations.contracts import (
    EpisodeKind,
    ToolConcurrency,
    ToolDefinition,
    ToolExecutionResult,
)
from ..foundations.detached import abandoned_calls, run_detached_tracked

_VALUE_KEYS = frozenset({"default", "const", "enum", "examples"})


@dataclass(frozen=True)
class FunctionTool:
    definition: ToolDefinition
    function: Callable[..., Any]
    arguments: type[BaseModel]
    needs_approval: bool

    @property
    def name(self) -> str:
        return self.definition.name

    async def run(self, call_arguments: dict[str, Any]) -> ToolExecutionResult:
        blocking = abandoned_calls()
        if blocking:
            return ToolExecutionResult(
                status="failed",
                error=(
                    f'Tool "{self.name}" was not started: an earlier call to '
                    f"{', '.join(blocking)} was cancelled or timed out but its function is still "
                    "running in the background and cannot be stopped. Wait for it to finish."
                ),
            )
        try:
            parsed = self.arguments.model_validate(call_arguments)
            values = {field: getattr(parsed, field) for field in self.arguments.model_fields}
            if inspect.iscoroutinefunction(self.function):
                returned = await self.function(**values)
            else:
                returned = await run_detached_tracked(self.name, self.function, **values)
            return ToolExecutionResult(status="succeeded", output=to_jsonable_python(returned))
        except Exception as error:
            message = f'Tool "{self.name}" raised {type(error).__name__}: {error}'
            return ToolExecutionResult(status="failed", error=message)


def tool(
    function: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    read_only: bool = False,
    needs_approval: bool = False,
) -> Any:
    def build(target: Callable[..., Any]) -> FunctionTool:
        tool_name = name or target.__name__
        text = description or _first_paragraph(inspect.getdoc(target))
        if not text:
            raise ValueError(
                f'Tool function "{tool_name}" needs a docstring or description=: the model reads '
                "it to decide when to call the tool."
            )
        arguments = _argument_model(target, tool_name)
        definition = ToolDefinition(
            name=tool_name,
            description=text,
            input_schema=_without_titles(arguments.model_json_schema()),
            episode_kind=EpisodeKind.EXPLORATORY if read_only else EpisodeKind.ACTION,
            concurrency=ToolConcurrency.PARALLEL_SAFE if read_only else ToolConcurrency.SERIAL,
        )
        return FunctionTool(definition, target, arguments, needs_approval)

    return build if function is None else build(function)


def _first_paragraph(docstring: str | None) -> str:
    if not docstring:
        return ""
    return " ".join(docstring.strip().split("\n\n")[0].split())


def _argument_model(function: Callable[..., Any], tool_name: str) -> type[BaseModel]:
    hints = typing.get_type_hints(function)
    fields: dict[str, Any] = {}
    for parameter_name, parameter in inspect.signature(function).parameters.items():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise ValueError(
                f'Tool function "{tool_name}" has the variadic parameter "{parameter_name}"; '
                "declare each argument by name so the model can be told its type."
            )
        default = ... if parameter.default is parameter.empty else parameter.default
        fields[parameter_name] = (hints.get(parameter_name, Any), default)
    return create_model(f"{tool_name}_arguments", __config__=ConfigDict(extra="forbid"), **fields)


def _without_titles(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_without_titles(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: value if key in _VALUE_KEYS else _without_titles(value)
        for key, value in schema.items()
        if not (key == "title" and isinstance(value, str))
    }
