# Copyright (c) 2026 David Michael Indraputra

"""Map OpenAI Chat Completions responses into untrusted SDK agent turns."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

from ...foundations.contracts import AgentTurn, ModelBinding
from ...foundations.errors import AgentSdkError
from ..model import (
    AgentModel,
    ModelContext,
    ModelStreamCompleted,
    ModelStreamListener,
    ModelTextDelta,
    ModelToolCallDelta,
    ModelTurnResponse,
    ProviderContinuation,
    ProviderToolResult,
    ProviderUsage,
)
from .transport import (
    HttpxStreamingJsonTransport,
    JsonHttpTransport,
    OpenAICompatibleEndpoint,
    StreamingJsonHttpTransport,
    UrlLibJsonTransport,
)

_AGENT_TURN_ADAPTER: TypeAdapter[AgentTurn] = TypeAdapter(AgentTurn)


class OpenAICompatibleAgentModel(AgentModel):
    """Map OpenAI Chat Completions responses into untrusted SDK agent turns.

    The host owns the endpoint, credential, and model binding. Tool calls are mapped to a
    dependency-free SDK batch because the Chat Completions protocol does not supply tool-call
    dependency edges; the existing scheduler still validates and authorizes each call.
    """

    def __init__(
        self,
        endpoint: OpenAICompatibleEndpoint,
        *,
        provider: str,
        model: str,
        transport: JsonHttpTransport | None = None,
        streaming_transport: StreamingJsonHttpTransport | None = None,
    ) -> None:
        if not provider.strip():
            raise ValueError("OpenAI-compatible provider identifier must be non-empty.")
        if not model.strip():
            raise ValueError("OpenAI-compatible model identifier must be non-empty.")
        self._endpoint = endpoint
        self._provider = provider
        self._model = model
        self._transport = transport or UrlLibJsonTransport()
        self._streaming_transport = streaming_transport or HttpxStreamingJsonTransport()

    async def next_turn(self, context: ModelContext) -> ModelTurnResponse:
        binding = self._require_binding(context)
        payload = self._chat_payload(context, binding)
        response = await asyncio.to_thread(
            self._transport.post_json,
            self._endpoint.url_for("/chat/completions"),
            headers=self._endpoint.headers,
            payload=payload,
            timeout_seconds=self._endpoint.timeout_seconds,
        )
        return self._turn_response_from(response)

    async def stream_turn(
        self, context: ModelContext, on_delta: ModelStreamListener
    ) -> ModelTurnResponse:
        """Stream incremental text/tool-call deltas while still returning the full turn.

        Chunks are accumulated into the same response shape ``next_turn``
        already parses, so both paths share every validation and mapping
        rule below instead of maintaining two independent readings of the
        wire format.
        """

        binding = self._require_binding(context)
        payload = {**self._chat_payload(context, binding), "stream": True}
        payload["stream_options"] = {"include_usage": True}
        accumulator = _ChatStreamAccumulator()
        async for chunk in self._streaming_transport.stream_json(
            self._endpoint.url_for("/chat/completions"),
            headers=self._endpoint.headers,
            payload=payload,
            timeout_seconds=self._endpoint.timeout_seconds,
        ):
            for event in accumulator.consume(chunk):
                await _emit(on_delta, event)
        response = accumulator.finalize()
        result = self._turn_response_from(response)
        await _emit(on_delta, ModelStreamCompleted(usage=result.usage))
        return result

    def _require_binding(self, context: ModelContext) -> ModelBinding:
        binding = context.model_binding
        if binding is None:
            raise AgentSdkError(
                "MODEL_BINDING_REQUIRED",
                "OpenAI-compatible model calls require the declared model binding.",
            )
        self._validate_binding(binding)
        return binding

    def _turn_response_from(self, response: Mapping[str, Any]) -> ModelTurnResponse:
        turn = _agent_turn_from_chat_response(response)
        return ModelTurnResponse(
            turn=turn,
            continuation=self._continuation_from_response(response, turn),
            usage=_provider_usage(response),
        )

    async def accept_tool_results(
        self,
        continuation: ProviderContinuation,
        results: Sequence[ProviderToolResult],
    ) -> ProviderContinuation:
        """Bind bounded SDK results to the exact tool calls in a provider continuation."""

        if continuation.provider != self._provider:
            raise AgentSdkError(
                "MODEL_CONTINUATION_INVALID",
                "Provider continuation belongs to a different model adapter.",
            )
        state = dict(continuation.state)
        raw_calls = state.get("assistant_tool_calls")
        if not isinstance(raw_calls, list):
            raise AgentSdkError(
                "MODEL_CONTINUATION_INVALID",
                "OpenAI-compatible continuation lacks assistant tool calls.",
            )
        expected_ids = {
            item.get("id")
            for item in raw_calls
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }
        returned_ids = [item.call_id for item in results]
        if (
            not expected_ids
            or set(returned_ids) != expected_ids
            or len(returned_ids) != len(expected_ids)
        ):
            raise AgentSdkError(
                "MODEL_CONTINUATION_INVALID",
                "Tool result IDs must exactly match the provider-issued tool calls.",
            )
        state["tool_results"] = [
            {
                "role": "tool",
                "tool_call_id": item.call_id,
                "content": item.content,
            }
            for item in results
        ]
        return ProviderContinuation(provider=self._provider, state=state)

    def _validate_binding(self, binding: ModelBinding) -> None:
        if binding.provider != self._provider or binding.model != self._model:
            raise AgentSdkError(
                "MODEL_BINDING_MISMATCH",
                "OpenAI-compatible adapter does not match the declared provider/model binding.",
                {
                    "adapter_provider": self._provider,
                    "adapter_model": self._model,
                    "binding_provider": binding.provider,
                    "binding_model": binding.model,
                },
            )

    def _chat_payload(self, context: ModelContext, binding: ModelBinding) -> dict[str, Any]:
        parameters = _safe_parameters(binding.parameters)
        output_schema = context.output_schema
        if output_schema is None:
            raise AgentSdkError(
                "MODEL_OUTPUT_SCHEMA_REQUIRED",
                "OpenAI-compatible model calls require the declared output schema.",
            )
        # Split by iteration-stability, not by topic: everything here is byte-identical
        # on every call for this task, so it stays in its own leading message and the
        # provider's automatic prompt-cache can match this whole prefix turn after
        # turn. ``project_state``/``iteration`` change every call and must never be
        # merged into this message, or they would invalidate the cached prefix.
        stable_context = {
            "task_id": context.task.id,
            "task_input": context.task.input,
            "prompt": context.prompt.model_dump(mode="json"),
            "output_schema": output_schema,
        }
        volatile_context = {
            "project_state": context.project_state.model_dump(mode="json"),
            "iteration": context.iteration,
            "recent_observations": [
                observation.model_dump(mode="json") for observation in context.observations
            ],
            "episode_summaries": [episode.model_dump(mode="json") for episode in context.episodes],
            "compacted_episodes": [
                reference.model_dump(mode="json") for reference in context.compacted_episodes
            ],
            "omitted_compacted_count": (
                context.projection.omitted_compacted_count if context.projection else 0
            ),
        }
        system = (
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
            "your next action so you do not repeat a call whose outcome you already have."
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(stable_context, separators=(",", ":"))},
            {"role": "user", "content": json.dumps(volatile_context, separators=(",", ":"))},
        ]
        if context.continuation is not None:
            messages.extend(self._continuation_messages(context.continuation))
        tools = _chat_tools(context)
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            **parameters,
        }
        if not tools:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _continuation_from_response(
        self,
        response: Mapping[str, Any],
        turn: AgentTurn,
    ) -> ProviderContinuation | None:
        if turn.type not in {"tool-call", "tool-batch"}:
            return None
        raw_calls = _chat_message(response).get("tool_calls")
        if not isinstance(raw_calls, list) or not raw_calls:
            raise AgentSdkError(
                "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                "Provider tool turn lacks the raw tool-call payload needed for continuation.",
            )
        return ProviderContinuation(
            provider=self._provider,
            state={"assistant_tool_calls": raw_calls, "tool_results": []},
        )

    def _continuation_messages(self, continuation: ProviderContinuation) -> list[dict[str, Any]]:
        if continuation.provider != self._provider:
            raise AgentSdkError(
                "MODEL_CONTINUATION_INVALID",
                "OpenAI-compatible adapter received a continuation from another provider.",
            )
        raw_calls = continuation.state.get("assistant_tool_calls")
        tool_results = continuation.state.get("tool_results")
        if not isinstance(raw_calls, list) or not isinstance(tool_results, list):
            raise AgentSdkError(
                "MODEL_CONTINUATION_INVALID",
                "OpenAI-compatible continuation has an invalid state shape.",
            )
        if not tool_results:
            raise AgentSdkError(
                "MODEL_CONTINUATION_UNRESOLVED",
                "Provider continuation requires tool results before the next model turn.",
            )
        messages: list[dict[str, Any]] = [
            {"role": "assistant", "content": None, "tool_calls": raw_calls}
        ]
        for item in tool_results:
            if (
                not isinstance(item, Mapping)
                or item.get("role") != "tool"
                or not isinstance(item.get("tool_call_id"), str)
                or not isinstance(item.get("content"), str)
            ):
                raise AgentSdkError(
                    "MODEL_CONTINUATION_INVALID",
                    "OpenAI-compatible continuation contains malformed tool results.",
                )
            messages.append(dict(item))
        return messages


def _safe_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(dict(parameters), allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise AgentSdkError(
            "MODEL_PARAMETERS_INVALID",
            f"Model binding parameters must be JSON-compatible finite values: {error}.",
        ) from error
    forbidden = {
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
    if forbidden & {str(key).lower() for key in decoded}:
        raise AgentSdkError(
            "MODEL_PARAMETERS_INVALID",
            "Credentials and endpoint fields must not appear in model binding parameters.",
        )
    return decoded


def _chat_tools(context: ModelContext) -> list[dict[str, Any]]:
    tools_section = next((item for item in context.prompt.sections if item.kind == "tools"), None)
    if tools_section is None or not isinstance(tools_section.value, list):
        return []
    tools: list[dict[str, Any]] = []
    for raw_tool in tools_section.value:
        if not isinstance(raw_tool, Mapping):
            continue
        name = raw_tool.get("name")
        description = raw_tool.get("description")
        parameters = raw_tool.get("input_schema")
        if (
            not isinstance(name, str)
            or not isinstance(description, str)
            or not isinstance(parameters, dict)
        ):
            continue
        tools.append(
            {
                "type": "function",
                "function": {"name": name, "description": description, "parameters": parameters},
            }
        )
    return tools


def _agent_turn_from_chat_response(response: Mapping[str, Any]) -> AgentTurn:
    message = _chat_message(response)
    raw_tool_calls = message.get("tool_calls")
    if isinstance(raw_tool_calls, list) and raw_tool_calls:
        calls = []
        for raw_call in raw_tool_calls:
            if not isinstance(raw_call, Mapping):
                raise AgentSdkError(
                    "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                    "Tool calls must be JSON objects.",
                )
            call_id = raw_call.get("id")
            function = raw_call.get("function")
            if not isinstance(call_id, str) or not call_id or not isinstance(function, Mapping):
                raise AgentSdkError(
                    "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                    "Tool call lacks an ID or function payload.",
                )
            name = function.get("name")
            arguments = function.get("arguments")
            if not isinstance(name, str) or not isinstance(arguments, str):
                raise AgentSdkError(
                    "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                    "Tool call function must include string name and arguments.",
                )
            try:
                decoded_arguments = json.loads(arguments)
            except json.JSONDecodeError as error:
                raise AgentSdkError(
                    "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                    "Tool call arguments must be a JSON object.",
                ) from error
            if not isinstance(decoded_arguments, dict):
                raise AgentSdkError(
                    "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                    "Tool call arguments must be a JSON object.",
                )
            calls.append(
                {
                    "id": call_id,
                    "name": name,
                    "arguments": decoded_arguments,
                    "depends_on_call_ids": [],
                }
            )
        raw_turn: dict[str, Any] = {"type": "tool-batch", "calls": calls}
    else:
        try:
            raw_turn = json.loads(_chat_content(response))
        except json.JSONDecodeError as error:
            raise AgentSdkError(
                "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                "Model response without tool calls must be a JSON AgentTurn object.",
            ) from error
    try:
        return _AGENT_TURN_ADAPTER.validate_python(raw_turn)
    except ValidationError as error:
        field_errors = [
            f"{'.'.join(str(part) for part in issue['loc']) or '(root)'}: {issue['msg']}"
            for issue in error.errors()
        ]
        raise AgentSdkError(
            "OPENAI_COMPATIBLE_RESPONSE_INVALID",
            "Provider response does not match the AgentTurn contract: "
            + "; ".join(field_errors[:5])
            + ".",
            {"field_errors": field_errors, "validation_error": str(error)},
        ) from error


def _chat_message(response: Mapping[str, Any]) -> Mapping[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise AgentSdkError(
            "OPENAI_COMPATIBLE_RESPONSE_INVALID",
            "Chat response must contain a first choice.",
        )
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise AgentSdkError(
            "OPENAI_COMPATIBLE_RESPONSE_INVALID",
            "Chat response choice must contain a message object.",
        )
    return message


def _chat_content(response: Mapping[str, Any]) -> str:
    content = _chat_message(response).get("content")
    if not isinstance(content, str) or not content.strip():
        raise AgentSdkError(
            "OPENAI_COMPATIBLE_RESPONSE_INVALID",
            "Chat response message must contain non-empty text content.",
        )
    return content


def _provider_usage(response: Mapping[str, Any]) -> ProviderUsage | None:
    raw_usage = response.get("usage")
    if not isinstance(raw_usage, Mapping):
        return None
    prompt_details = raw_usage.get("prompt_tokens_details")
    completion_details = raw_usage.get("completion_tokens_details")
    return ProviderUsage(
        input_tokens=_optional_nonnegative_int(raw_usage.get("prompt_tokens")),
        output_tokens=_optional_nonnegative_int(raw_usage.get("completion_tokens")),
        cached_input_tokens=(
            _optional_nonnegative_int(prompt_details.get("cached_tokens"))
            if isinstance(prompt_details, Mapping)
            else None
        ),
        reasoning_tokens=(
            _optional_nonnegative_int(completion_details.get("reasoning_tokens"))
            if isinstance(completion_details, Mapping)
            else None
        ),
        request_id=response.get("id") if isinstance(response.get("id"), str) else None,
    )


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentSdkError(
            "OPENAI_COMPATIBLE_RESPONSE_INVALID",
            "Provider token counters must be non-negative integers when present.",
        )
    return value


class _ChatStreamAccumulator:
    """Fold streamed chat-completion chunks into the one non-streaming response shape.

    Tool-call argument fragments arrive keyed by ``index`` and must be
    concatenated in arrival order; ``id``/``function.name`` typically appear
    only on that index's first chunk, so later chunks only ever refine them.
    """

    def __init__(self) -> None:
        self._response_id: str | None = None
        self._content_parts: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}
        self._usage: dict[str, Any] | None = None

    def consume(self, chunk: Mapping[str, Any]) -> list[ModelTextDelta | ModelToolCallDelta]:
        events: list[ModelTextDelta | ModelToolCallDelta] = []
        if isinstance(chunk.get("id"), str):
            self._response_id = chunk["id"]
        raw_usage = chunk.get("usage")
        if isinstance(raw_usage, Mapping):
            self._usage = dict(raw_usage)
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return events
        delta = choices[0].get("delta")
        if not isinstance(delta, Mapping):
            return events
        content = delta.get("content")
        if isinstance(content, str) and content:
            self._content_parts.append(content)
            events.append(ModelTextDelta(text=content))
        raw_tool_calls = delta.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            events.extend(self._consume_tool_call_deltas(raw_tool_calls))
        return events

    def _consume_tool_call_deltas(self, raw_tool_calls: list[Any]) -> list[ModelToolCallDelta]:
        events: list[ModelToolCallDelta] = []
        for raw_call in raw_tool_calls:
            if not isinstance(raw_call, Mapping) or not isinstance(raw_call.get("index"), int):
                continue
            index = raw_call["index"]
            entry = self._tool_calls.setdefault(
                index,
                {"id": None, "type": "function", "function": {"name": None, "arguments": ""}},
            )
            if isinstance(raw_call.get("id"), str):
                entry["id"] = raw_call["id"]
            arguments_delta = ""
            function = raw_call.get("function")
            if isinstance(function, Mapping):
                if isinstance(function.get("name"), str):
                    entry["function"]["name"] = function["name"]
                if isinstance(function.get("arguments"), str):
                    arguments_delta = function["arguments"]
                    entry["function"]["arguments"] += arguments_delta
            events.append(
                ModelToolCallDelta(
                    index=index,
                    call_id=entry["id"],
                    name=entry["function"]["name"],
                    arguments_delta=arguments_delta,
                )
            )
        return events

    def finalize(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant"}
        if self._tool_calls:
            message["tool_calls"] = [self._tool_calls[index] for index in sorted(self._tool_calls)]
        else:
            message["content"] = "".join(self._content_parts)
        response: dict[str, Any] = {"choices": [{"message": message}]}
        if self._response_id is not None:
            response["id"] = self._response_id
        if self._usage is not None:
            response["usage"] = self._usage
        return response


async def _emit(on_delta: ModelStreamListener, event: Any) -> None:
    result = on_delta(event)
    if inspect.isawaitable(result):
        await result
