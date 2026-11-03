# Copyright (c) 2026 David Michael Indraputra

"""Map Anthropic Messages API responses into untrusted SDK agent turns."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

from ...foundations.contracts import AgentTurn, ModelBinding
from ...foundations.detached import run_detached
from ...foundations.errors import AgentSdkError, InvalidModelOutputError
from ..model import (
    AgentModel,
    ModelCancellationOutcome,
    ModelContext,
    ModelStreamCompleted,
    ModelStreamListener,
    ModelTurnResponse,
    ProviderContinuation,
    ProviderToolResult,
    ProviderUsage,
    TransportCancellation,
)
from ..openai_compatible.transport import (
    AsyncJsonHttpTransport,
    HttpxStreamingJsonTransport,
    JsonHttpTransport,
    StreamingJsonHttpTransport,
    UrlLibJsonTransport,
)
from ..provider_context import (
    SYSTEM_PROMPT,
    attached_images,
    declared_tools,
    safe_parameters,
    stable_context,
    volatile_context,
)
from .endpoint import AnthropicEndpoint
from .stream import StreamAccumulator

_AGENT_TURN_ADAPTER: TypeAdapter[AgentTurn] = TypeAdapter(AgentTurn)
_FENCED_JSON = re.compile(r"\A\s*```(?:json)?[ \t]*\n(.*)\n```\s*\Z", re.DOTALL)
_TRUNCATED_STOP_REASONS = frozenset({"max_tokens", "model_context_window_exceeded"})
DEFAULT_MAX_TOKENS = 8192
_OWN_PARAMETERS = frozenset({"system", "stream", "stop_sequences"})


class AnthropicMessagesAgentModel(AgentModel):
    def __init__(
        self,
        endpoint: AnthropicEndpoint,
        *,
        provider: str,
        model: str,
        default_max_tokens: int = DEFAULT_MAX_TOKENS,
        cache_stable_prefix: bool = True,
        transport: JsonHttpTransport | AsyncJsonHttpTransport | None = None,
        streaming_transport: StreamingJsonHttpTransport | None = None,
    ) -> None:
        if not provider.strip():
            raise ValueError("Anthropic provider identifier must be non-empty.")
        if not model.strip():
            raise ValueError("Anthropic model identifier must be non-empty.")
        if default_max_tokens < 1:
            raise ValueError("Anthropic default_max_tokens must be at least 1.")
        self._endpoint = endpoint
        self._provider = provider
        self._model = model
        self._default_max_tokens = default_max_tokens
        self._cache_stable_prefix = cache_stable_prefix
        self._transport = transport or UrlLibJsonTransport()
        self._streaming_transport = streaming_transport or HttpxStreamingJsonTransport()
        self._cancellations: dict[str, ModelCancellationOutcome] = {}

    async def next_turn(self, context: ModelContext) -> ModelTurnResponse:
        binding = self._require_binding(context)
        payload = self._payload(context, binding)
        headers = self._endpoint.request_headers(context.trace_parent)
        timeout = self._endpoint.timeout_seconds
        url = self._endpoint.url_for("/v1/messages")
        aborts_on_cancel = isinstance(self._transport, AsyncJsonHttpTransport)
        try:
            if isinstance(self._transport, AsyncJsonHttpTransport):
                response = await self._transport.apost_json(
                    url, headers=headers, payload=payload, timeout_seconds=timeout
                )
            else:
                response = await run_detached(
                    self._transport.post_json,
                    url,
                    headers=headers,
                    payload=payload,
                    timeout_seconds=timeout,
                )
        except asyncio.CancelledError:
            self._cancellations[context.task.id] = self._cancellation(aborts_on_cancel)
            raise
        return self._turn_response_from(response)

    async def stream_turn(
        self, context: ModelContext, on_delta: ModelStreamListener
    ) -> ModelTurnResponse:
        binding = self._require_binding(context)
        payload = {**self._payload(context, binding), "stream": True}
        accumulator = StreamAccumulator()
        try:
            async for chunk in self._streaming_transport.stream_json(
                self._endpoint.url_for("/v1/messages"),
                headers=self._endpoint.request_headers(context.trace_parent),
                payload=payload,
                timeout_seconds=self._endpoint.timeout_seconds,
            ):
                for event in accumulator.consume(chunk):
                    outcome = on_delta(event)
                    if inspect.isawaitable(outcome):
                        await outcome
        except asyncio.CancelledError:
            self._cancellations[context.task.id] = self._cancellation(True)
            raise
        result = self._turn_response_from(accumulator.finalize())
        outcome = on_delta(ModelStreamCompleted(usage=result.usage))
        if inspect.isawaitable(outcome):
            await outcome
        return result

    def cancellation_outcome(self, task_id: str) -> ModelCancellationOutcome | None:
        return self._cancellations.pop(task_id, None)

    async def accept_tool_results(
        self, continuation: ProviderContinuation, results: Sequence[ProviderToolResult]
    ) -> ProviderContinuation:
        if continuation.provider != self._provider:
            raise AgentSdkError(
                "MODEL_CONTINUATION_INVALID",
                "Provider continuation belongs to a different model adapter.",
            )
        state = dict(continuation.state)
        content = state.get("assistant_content")
        issued = (
            {
                block.get("id")
                for block in content
                if isinstance(block, Mapping) and block.get("type") == "tool_use"
            }
            if isinstance(content, list)
            else set()
        )
        returned = [item.call_id for item in results]
        if not issued or set(returned) != issued or len(returned) != len(issued):
            raise AgentSdkError(
                "MODEL_CONTINUATION_INVALID",
                "Tool result IDs must exactly match the tool_use blocks the provider issued.",
            )
        state["tool_results"] = [
            {
                "type": "tool_result",
                "tool_use_id": item.call_id,
                "content": item.content,
                **({"is_error": True} if item.status != "succeeded" else {}),
            }
            for item in results
        ]
        return ProviderContinuation(provider=self._provider, state=state)

    def _cancellation(self, connection_closed: bool) -> ModelCancellationOutcome:
        limit = f"{self._endpoint.timeout_seconds:g}s"
        transport: TransportCancellation
        if connection_closed:
            transport, detail = "connection-closed", "the connection to the provider was closed"
        else:
            transport = "detached"
            detail = (
                "the blocking request keeps running on a detached thread until the provider "
                f"answers or {limit} pass, and its response is discarded"
            )
        return ModelCancellationOutcome(
            transport=transport,
            provider="unsupported",
            detail=(
                f"{detail}; the Messages API has no operation that stops a request, so the "
                "provider may finish generating it and bill for it"
            ),
        )

    def _require_binding(self, context: ModelContext) -> ModelBinding:
        binding = context.model_binding
        if binding is None:
            raise AgentSdkError(
                "MODEL_BINDING_REQUIRED",
                "Anthropic model calls require the declared model binding.",
            )
        if binding.provider != self._provider or binding.model != self._model:
            raise AgentSdkError(
                "MODEL_BINDING_MISMATCH",
                "Anthropic adapter does not match the declared provider/model binding.",
                {
                    "adapter_provider": self._provider,
                    "adapter_model": self._model,
                    "binding_provider": binding.provider,
                    "binding_model": binding.model,
                },
            )
        return binding

    def _payload(self, context: ModelContext, binding: ModelBinding) -> dict[str, Any]:
        parameters = safe_parameters(binding.parameters, _OWN_PARAMETERS)
        tools = declared_tools(context)
        stable_block: dict[str, Any] = {
            "type": "text",
            "text": json.dumps(
                stable_context(context, [tool["name"] for tool in tools]), separators=(",", ":")
            ),
        }
        if self._cache_stable_prefix:
            stable_block["cache_control"] = {"type": "ephemeral"}
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    stable_block,
                    *(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": image["media_type"],
                                "data": image["data"],
                            },
                        }
                        for image in attached_images(context)
                    ),
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(volatile_context(context), separators=(",", ":")),
                    }
                ],
            },
        ]
        if context.continuation is not None:
            messages.extend(self._continuation_messages(context.continuation))
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._default_max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": messages,
            **parameters,
        }
        if tools:
            payload["tools"] = tools
        return payload

    def _continuation_messages(self, continuation: ProviderContinuation) -> list[dict[str, Any]]:
        if continuation.provider != self._provider:
            raise AgentSdkError(
                "MODEL_CONTINUATION_INVALID",
                "Anthropic adapter received a continuation from another provider.",
            )
        content = continuation.state.get("assistant_content")
        results = continuation.state.get("tool_results")
        if not isinstance(content, list) or not isinstance(results, list):
            raise AgentSdkError(
                "MODEL_CONTINUATION_INVALID", "Anthropic continuation has an invalid state shape."
            )
        if not results:
            raise AgentSdkError(
                "MODEL_CONTINUATION_UNRESOLVED",
                "Provider continuation requires tool results before the next model turn.",
            )
        for item in results:
            if (
                not isinstance(item, Mapping)
                or item.get("type") != "tool_result"
                or not isinstance(item.get("tool_use_id"), str)
                or not isinstance(item.get("content"), str)
            ):
                raise AgentSdkError(
                    "MODEL_CONTINUATION_INVALID",
                    "Anthropic continuation contains malformed tool results.",
                )
        return [
            {"role": "assistant", "content": content},
            {"role": "user", "content": [dict(item) for item in results]},
        ]

    def _turn_response_from(self, response: Mapping[str, Any]) -> ModelTurnResponse:
        content = _content_blocks(response)
        turn = _turn_from_content(content)
        continuation = None
        if turn.type in {"tool-call", "tool-batch"}:
            continuation = ProviderContinuation(
                provider=self._provider,
                state={"assistant_content": content, "tool_results": []},
            )
        return ModelTurnResponse(turn=turn, continuation=continuation, usage=_usage(response))


def _content_blocks(response: Mapping[str, Any]) -> list[Any]:
    if response.get("type") == "error":
        error = response.get("error")
        detail = error.get("message") if isinstance(error, Mapping) else None
        raise AgentSdkError(
            "ANTHROPIC_MESSAGES_PROVIDER_ERROR",
            f"Provider returned an error instead of a message: {detail or 'no message given'}",
        )
    stop_reason = response.get("stop_reason")
    if stop_reason in _TRUNCATED_STOP_REASONS:
        raise AgentSdkError(
            "ANTHROPIC_MESSAGES_RESPONSE_TRUNCATED",
            f'The provider stopped at its limit (stop_reason "{stop_reason}") before the turn '
            "was complete; raise max_tokens in the model binding parameters or ask for less "
            "output per turn.",
        )
    if stop_reason == "refusal":
        raise AgentSdkError(
            "ANTHROPIC_MESSAGES_RESPONSE_REFUSED",
            'The provider declined to respond (stop_reason "refusal"); details: '
            f"{json.dumps(response.get('stop_details'), default=str)}",
        )
    if stop_reason == "pause_turn":
        raise AgentSdkError(
            "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
            'The provider paused the turn (stop_reason "pause_turn"), which only happens with '
            "server-side tools this adapter does not declare.",
        )
    content = response.get("content")
    if not isinstance(content, list) or not content:
        raise AgentSdkError(
            "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
            "Messages response must contain a non-empty content list.",
        )
    return content


def _turn_from_content(content: Sequence[Any]) -> AgentTurn:
    tool_uses = [
        block for block in content if isinstance(block, Mapping) and block.get("type") == "tool_use"
    ]
    if tool_uses:
        raw_turn: dict[str, Any] = {"type": "tool-batch", "calls": [_call(b) for b in tool_uses]}
    else:
        raw_turn = _json_turn(_text(content))
    try:
        return _AGENT_TURN_ADAPTER.validate_python(raw_turn)
    except ValidationError as error:
        field_errors = [
            f"{'.'.join(str(part) for part in issue['loc']) or '(root)'}: {issue['msg']}"
            for issue in error.errors()
        ]
        raise InvalidModelOutputError(
            "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
            "Provider response does not match the AgentTurn contract: "
            + "; ".join(field_errors[:5])
            + ".",
            {"field_errors": field_errors, "validation_error": str(error)},
        ) from error


def _call(block: Mapping[str, Any]) -> dict[str, Any]:
    call_id, name, arguments = block.get("id"), block.get("name"), block.get("input")
    if not isinstance(call_id, str) or not call_id or not isinstance(name, str):
        raise InvalidModelOutputError(
            "ANTHROPIC_MESSAGES_RESPONSE_INVALID", "A tool_use block lacks an id or a name."
        )
    if not isinstance(arguments, dict):
        raise InvalidModelOutputError(
            "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
            f'The tool_use block "{call_id}" for "{name}" has input that is not a JSON object.',
        )
    return {"id": call_id, "name": name, "arguments": arguments, "depends_on_call_ids": []}


def _text(content: Sequence[Any]) -> str:
    text = "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, Mapping)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )
    if not text.strip():
        raise InvalidModelOutputError(
            "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
            "Messages response without tool_use blocks must contain non-empty text.",
        )
    return text


def _json_turn(text: str) -> dict[str, Any]:
    fenced = _FENCED_JSON.match(text)
    try:
        decoded = json.loads(fenced.group(1) if fenced else text)
    except json.JSONDecodeError as error:
        raise InvalidModelOutputError(
            "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
            "Model response without tool calls must be a JSON AgentTurn object.",
        ) from error
    if not isinstance(decoded, dict):
        raise InvalidModelOutputError(
            "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
            "Model response without tool calls must be a JSON AgentTurn object.",
        )
    return decoded


def _usage(response: Mapping[str, Any]) -> ProviderUsage | None:
    raw = response.get("usage")
    if not isinstance(raw, Mapping):
        return None
    fresh = _counter(raw, "input_tokens")
    cache_read = _counter(raw, "cache_read_input_tokens")
    cache_write = _counter(raw, "cache_creation_input_tokens")
    total_input = (
        None
        if fresh is None and cache_read is None and cache_write is None
        else (fresh or 0) + (cache_read or 0) + (cache_write or 0)
    )
    request_id = response.get("id")
    return ProviderUsage(
        input_tokens=total_input,
        output_tokens=_counter(raw, "output_tokens"),
        cached_input_tokens=cache_read,
        request_id=request_id if isinstance(request_id, str) else None,
    )


def _counter(raw: Mapping[str, Any], name: str) -> int | None:
    value = raw.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentSdkError(
            "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
            f'Provider usage field "{name}" must be a non-negative integer when present.',
        )
    return value
