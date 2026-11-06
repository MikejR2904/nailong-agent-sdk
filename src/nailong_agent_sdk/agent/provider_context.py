# Copyright (c) 2026 David Michael Indraputra

"""The provider-neutral request context every model adapter builds its messages from."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..foundations.errors import AgentSdkError
from .model import ModelContext

ATTACHED_IMAGES_KEY = "attached_images"
FORBIDDEN_PARAMETERS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "authorization_header",
        "base_url",
        "model",
        "messages",
        "tools",
        "tool_choice",
        "response_format",
    }
)

SYSTEM_PROMPT = (
    "You are an untrusted proposal component in a governed agent runtime. "
    "Use only declared function tools when a tool is needed. If no tool is needed, "
    "return exactly one JSON AgentTurn object: either "
    '{"type":"final","output":...} matching output_schema or '
    '{"type":"blocked","reason":"..."}. Do not emit prose outside JSON. '
    "The second user message's recent_observations lists your own recent tool calls "
    "this task, oldest first, each tagged with the iteration it happened on, and "
    "episode_summaries gives a one-line summary of each call still in memory. "
    "compacted_episodes lists earlier calls whose full results were dropped to save "
    "space; each keeps a one-line summary, its status, and a handle_id, and if a "
    "stored-result reader such as get_tool_result is available you can fetch the "
    "full result by passing it that handle_id. omitted_compacted_count is how many "
    "still older compacted calls are not listed. Check all of these before choosing "
    "your next action so you do not repeat a call whose outcome you already have. "
    "semantic_summaries, when present, are verified digests of groups of earlier "
    "calls that are no longer listed individually: they carry facts, decisions and "
    "unresolved_issues with the episode ids they came from. A digest is derived, "
    "not evidence; fetch a source handle in source_handles when its detail matters. "
    "An observation of kind user-message is something the user told you while you were "
    "working: read it and follow it from your next step. "
    "The volatile message lists slowly changing sections first and project_state and "
    "iteration last."
)


_REASONING_TAGS = {
    "<think>": "</think>",
    "<thinking>": "</thinking>",
    "<reasoning>": "</reasoning>",
}


class ReasoningSplitter:
    """Separates streamed reasoning tags from answer text, even when a tag spans chunks."""

    def __init__(self) -> None:
        self._closing: str | None = None
        self._held = ""

    def feed(self, text: str) -> list[tuple[bool, str]]:
        buffer = self._held + text
        self._held = ""
        parts: list[tuple[bool, str]] = []
        while buffer:
            if self._closing is not None:
                end = buffer.find(self._closing)
                if end == -1:
                    keep = _partial_suffix(buffer, (self._closing,))
                    self._emit(parts, True, buffer[: len(buffer) - keep])
                    self._held = buffer[len(buffer) - keep :]
                    return parts
                self._emit(parts, True, buffer[:end])
                buffer = buffer[end + len(self._closing) :]
                self._closing = None
                continue
            start, tag = min(
                ((buffer.find(tag), tag) for tag in _REASONING_TAGS if tag in buffer),
                default=(-1, ""),
            )
            if start == -1:
                keep = _partial_suffix(buffer, tuple(_REASONING_TAGS))
                self._emit(parts, False, buffer[: len(buffer) - keep])
                self._held = buffer[len(buffer) - keep :]
                return parts
            self._emit(parts, False, buffer[:start])
            buffer = buffer[start + len(tag) :]
            self._closing = _REASONING_TAGS[tag]
        return parts

    def flush(self) -> list[tuple[bool, str]]:
        parts: list[tuple[bool, str]] = []
        self._emit(parts, self._closing is not None, self._held)
        self._held = ""
        return parts

    @staticmethod
    def _emit(parts: list[tuple[bool, str]], reasoning: bool, text: str) -> None:
        if text:
            parts.append((reasoning, text))


def _partial_suffix(text: str, tags: tuple[str, ...]) -> int:
    longest = 0
    for tag in tags:
        for size in range(min(len(tag) - 1, len(text)), 0, -1):
            if text.endswith(tag[:size]):
                longest = max(longest, size)
                break
    return longest


def prompted_tool_instructions(tools: Sequence[Mapping[str, Any]]) -> str:
    listing = "\n".join(
        f"- {tool['name']}: {tool['description']} input_schema="
        + json.dumps(tool["input_schema"], separators=(",", ":"))
        for tool in tools
    )
    return (
        "This model has no native tool calling, so call tools by replying with ONLY one JSON "
        'object and no other text: {"type":"tool-batch","calls":[{"id":"1","name":"<tool name>",'
        '"arguments":{...}}]}. Put several independent calls in "calls" when you need them. '
        "The results will be sent back in the next message; never write a tool result yourself. "
        'When you are done, reply with {"type":"final","output":...} matching output_schema, or '
        '{"type":"blocked","reason":"..."}.\nAvailable tools (name: description input_schema):\n'
        + listing
    )


def declared_tools(context: ModelContext) -> list[dict[str, Any]]:
    tools_section = next((item for item in context.prompt.sections if item.kind == "tools"), None)
    if tools_section is None or not isinstance(tools_section.value, list):
        return []
    tools: list[dict[str, Any]] = []
    for raw_tool in tools_section.value:
        if not isinstance(raw_tool, Mapping):
            continue
        name = raw_tool.get("name")
        description = raw_tool.get("description")
        schema = raw_tool.get("input_schema")
        if (
            not isinstance(name, str)
            or not isinstance(description, str)
            or not isinstance(schema, dict)
        ):
            continue
        tools.append({"name": name, "description": description, "input_schema": schema})
    return tools


def stable_context(context: ModelContext, tool_names: Sequence[str]) -> dict[str, Any]:
    output_schema = context.output_schema
    if output_schema is None:
        raise AgentSdkError(
            "MODEL_OUTPUT_SCHEMA_REQUIRED",
            "Model calls require the declared output schema.",
        )
    images = attached_images(context)
    stable = {
        "task_id": context.task.id,
        "task_input": {
            key: value for key, value in context.task.input.items() if key != ATTACHED_IMAGES_KEY
        },
        "prompt": prompt_without_duplicate_tools(context, tool_names),
        "output_schema": output_schema,
    }
    if images:
        stable["attached_images"] = f"{len(images)} image(s) are attached to this message."
    return stable


def attached_images(context: ModelContext) -> list[dict[str, str]]:
    raw = context.task.input.get(ATTACHED_IMAGES_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(
        isinstance(item, Mapping)
        and isinstance(item.get("media_type"), str)
        and isinstance(item.get("data"), str)
        for item in raw
    ):
        raise AgentSdkError(
            "MODEL_IMAGES_INVALID",
            f'The task input "{ATTACHED_IMAGES_KEY}" must be a list of objects with a string '
            "media_type and base64 data.",
        )
    return [{"media_type": item["media_type"], "data": item["data"]} for item in raw]


def volatile_context(context: ModelContext) -> dict[str, Any]:
    volatile: dict[str, Any] = {}
    if context.semantic_summaries:
        volatile["semantic_summaries"] = [
            item.model_dump(mode="json") for item in context.semantic_summaries
        ]
        volatile["omitted_summary_count"] = (
            context.projection.omitted_summary_count if context.projection else 0
        )
    volatile["compacted_episodes"] = [
        stub.model_dump(mode="json") for stub in context.compacted_episodes
    ]
    volatile["omitted_compacted_count"] = (
        context.projection.omitted_compacted_count if context.projection else 0
    )
    volatile["recent_observations"] = [
        observation.model_dump(mode="json") for observation in context.observations
    ]
    volatile["episode_summaries"] = [
        episode.model_dump(mode="json") for episode in context.episodes
    ]
    volatile["project_state"] = context.project_state.model_dump(mode="json")
    volatile["iteration"] = context.iteration
    return volatile


def prompt_without_duplicate_tools(
    context: ModelContext, tool_names: Sequence[str]
) -> dict[str, Any]:
    dump = context.prompt.model_dump(mode="json")
    declared = list(tool_names)
    if not declared:
        return dump
    for section in dump["sections"]:
        if section["kind"] == "tools" and isinstance(section["value"], list):
            section["value"] = {
                "function_tools": declared,
                "other": [
                    item
                    for item in section["value"]
                    if not (isinstance(item, Mapping) and item.get("name") in declared)
                ],
            }
    return dump


def safe_parameters(
    parameters: Mapping[str, Any], extra_forbidden: frozenset[str] = frozenset()
) -> dict[str, Any]:
    try:
        encoded = json.dumps(dict(parameters), allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise AgentSdkError(
            "MODEL_PARAMETERS_INVALID",
            f"Model binding parameters must be JSON-compatible finite values: {error}.",
        ) from error
    if (FORBIDDEN_PARAMETERS | extra_forbidden) & {str(key).lower() for key in decoded}:
        raise AgentSdkError(
            "MODEL_PARAMETERS_INVALID",
            "Credentials and endpoint fields must not appear in model binding parameters.",
        )
    return decoded
