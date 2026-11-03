# Copyright (c) 2026 David Michael Indraputra

"""Map OpenAI Chat Completions responses into untrusted SDK agent turns."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter, ValidationError

from ...foundations.contracts import AgentTurn, ModelBinding
from ...foundations.detached import run_detached
from ...foundations.errors import AgentSdkError, InvalidModelOutputError
from ...foundations.hashing import estimate_tokens
from ..cost_model import shared_prefix_tokens
from ..model import (
    AgentModel,
    ModelCancellationOutcome,
    ModelContext,
    ModelReasoningDelta,
    ModelStreamCompleted,
    ModelStreamListener,
    ModelTextDelta,
    ModelToolCallDelta,
    ModelTurnResponse,
    ProviderCancellation,
    ProviderContinuation,
    ProviderToolResult,
    ProviderUsage,
    RequestDiagnostics,
    TransportCancellation,
)
from ..provider_context import (
    SYSTEM_PROMPT,
    ReasoningSplitter,
    attached_images,
    declared_tools,
    safe_parameters,
    stable_context,
    volatile_context,
)
from .transport import (
    AsyncJsonHttpTransport,
    HttpxStreamingJsonTransport,
    JsonHttpTransport,
    OpenAICompatibleEndpoint,
    StreamingJsonHttpTransport,
    UrlLibJsonTransport,
    provider_error_detail,
)

_AGENT_TURN_ADAPTER: TypeAdapter[AgentTurn] = TypeAdapter(AgentTurn)
_REASONING_BLOCK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
PROVIDER_CANCEL_TIMEOUT_SECONDS = 5.0
_DIAGNOSTIC_TASK_LIMIT = 128


@dataclass(frozen=True)
class ProviderCancelRequest:
    task_id: str
    request_id: str
    provider: str
    model: str
    streamed: bool


ProviderCancel = Callable[[ProviderCancelRequest], Awaitable[bool]]


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
        transport: JsonHttpTransport | AsyncJsonHttpTransport | None = None,
        streaming_transport: StreamingJsonHttpTransport | None = None,
        provider_cancel: ProviderCancel | None = None,
        json_mode: bool = True,
        text_answer: Callable[[str], dict[str, Any]] | None = None,
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
        self._provider_cancel = provider_cancel
        self._json_mode = json_mode
        self._text_answer = text_answer
        self._cancellations: dict[str, ModelCancellationOutcome] = {}
        self._last_requests: OrderedDict[str, str] = OrderedDict()
        self._diagnostics: OrderedDict[str, RequestDiagnostics] = OrderedDict()

    async def next_turn(self, context: ModelContext) -> ModelTurnResponse:
        binding = self._require_binding(context)
        payload = self._chat_payload(context, binding)
        request = {
            "headers": self._endpoint.request_headers(context.trace_parent),
            "payload": payload,
            "timeout_seconds": self._endpoint.timeout_seconds,
        }
        url = self._endpoint.url_for("/chat/completions")
        aborts_on_cancel = isinstance(self._transport, AsyncJsonHttpTransport)
        try:
            if isinstance(self._transport, AsyncJsonHttpTransport):
                response = await self._transport.apost_json(url, **request)
            else:
                response = await run_detached(self._transport.post_json, url, **request)
        except asyncio.CancelledError:
            await self._record_cancellation(
                context.task.id, binding, aborts_on_cancel, request_id=None, streamed=False
            )
            raise
        return self._turn_response_from(response)

    def cancellation_outcome(self, task_id: str) -> ModelCancellationOutcome | None:
        return self._cancellations.pop(task_id, None)

    async def _record_cancellation(
        self,
        task_id: str,
        binding: ModelBinding,
        connection_closed: bool,
        *,
        request_id: str | None,
        streamed: bool,
    ) -> None:
        provider: ProviderCancellation = "unsupported"
        detail = (
            "the Chat Completions protocol has no operation that stops a request, so the "
            "provider may finish generating it and bill for it"
        )
        if self._provider_cancel is not None and request_id is not None:
            provider, detail = await self._ask_provider_to_cancel(
                task_id, binding, request_id, streamed
            )
        elif self._provider_cancel is not None:
            detail = (
                "a provider cancellation is configured but the provider had not named the "
                "request yet, so there was nothing to cancel by id"
            )
        transport: TransportCancellation
        if connection_closed:
            transport = "connection-closed"
            detail = f"the connection to the provider was closed; {detail}"
        else:
            transport = "detached"
            detail = (
                "the blocking request keeps running on a detached thread until the provider "
                f"answers or {self._endpoint.timeout_seconds:g}s pass, and its response is "
                f"discarded; {detail}"
            )
        self._cancellations[task_id] = ModelCancellationOutcome(
            transport=transport,
            provider=provider,
            request_id=request_id,
            detail=detail,
        )

    async def _ask_provider_to_cancel(
        self, task_id: str, binding: ModelBinding, request_id: str, streamed: bool
    ) -> tuple[ProviderCancellation, str]:
        assert self._provider_cancel is not None
        request = ProviderCancelRequest(
            task_id=task_id,
            request_id=request_id,
            provider=binding.provider,
            model=binding.model,
            streamed=streamed,
        )
        try:
            confirmed = await asyncio.wait_for(
                self._provider_cancel(request), PROVIDER_CANCEL_TIMEOUT_SECONDS
            )
        except TimeoutError:
            return "requested", (
                f"the provider cancellation for request {request_id} did not answer within "
                f"{PROVIDER_CANCEL_TIMEOUT_SECONDS:g}s"
            )
        except Exception as error:
            return "requested", (
                f"the provider cancellation for request {request_id} failed: "
                f"{type(error).__name__}: {error}"
            )
        if confirmed is True:
            return "confirmed", f"the provider confirmed cancelling request {request_id}"
        return "requested", f"the provider did not confirm cancelling request {request_id}"

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
        try:
            async for chunk in self._streaming_transport.stream_json(
                self._endpoint.url_for("/chat/completions"),
                headers=self._endpoint.request_headers(context.trace_parent),
                payload=payload,
                timeout_seconds=self._endpoint.timeout_seconds,
            ):
                for event in accumulator.consume(chunk):
                    await _emit(on_delta, event)
            for event in accumulator.flush():
                await _emit(on_delta, event)
        except asyncio.CancelledError:
            await self._record_cancellation(
                context.task.id,
                binding,
                True,
                request_id=accumulator.response_id,
                streamed=True,
            )
            raise
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
        turn = _agent_turn_from_chat_response(response, self._text_answer)
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
        parameters = safe_parameters(binding.parameters)
        tool_entries = declared_tools(context)
        tools = _chat_tools(tool_entries)
        stable = stable_context(context, [entry["name"] for entry in tool_entries])
        volatile = volatile_context(context)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _stable_content(stable, attached_images(context))},
            {"role": "user", "content": json.dumps(volatile, separators=(",", ":"))},
        ]
        if context.continuation is not None:
            messages.extend(self._continuation_messages(context.continuation))
        payload: dict[str, Any] = {"model": self._model, "messages": messages, **parameters}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        elif self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        self._observe_request(context.task.id, payload, tools)
        return payload

    def request_diagnostics(self, task_id: str) -> RequestDiagnostics | None:
        return self._diagnostics.get(task_id)

    def _observe_request(
        self, task_id: str, payload: Mapping[str, Any], tools: Sequence[Mapping[str, Any]]
    ) -> None:
        messages = payload["messages"]
        text = json.dumps({"tools": list(tools), "messages": messages}, separators=(",", ":"))
        stable = json.dumps({"tools": list(tools), "messages": messages[:2]}, separators=(",", ":"))
        previous = self._last_requests.get(task_id)
        shared = shared_prefix_tokens(previous, text) if previous is not None else 0
        self._diagnostics[task_id] = RequestDiagnostics(
            estimated_request_tokens=estimate_tokens(text, str),
            estimated_stable_prefix_tokens=estimate_tokens(stable, str),
            estimated_shared_prefix_tokens=shared,
            previous_request_tokens=(
                estimate_tokens(previous, str) if previous is not None else None
            ),
        )
        self._last_requests[task_id] = text
        for registry in (self._last_requests, self._diagnostics):
            registry.move_to_end(task_id)
            while len(registry) > _DIAGNOSTIC_TASK_LIMIT:
                registry.popitem(last=False)

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
            state={
                "assistant_tool_calls": raw_calls,
                "assistant_extra": _assistant_extra(_chat_message(response)),
                "tool_results": [],
            },
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
        extra = continuation.state.get("assistant_extra")
        assistant: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": raw_calls}
        if isinstance(extra, Mapping):
            assistant.update(extra)
        messages: list[dict[str, Any]] = [assistant]
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


def _stable_content(stable: Mapping[str, Any], images: Sequence[Mapping[str, str]]) -> Any:
    text = json.dumps(stable, separators=(",", ":"))
    if not images:
        return text
    return [
        {"type": "text", "text": text},
        *(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image['media_type']};base64,{image['data']}"},
            }
            for image in images
        ),
    ]


def _chat_tools(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": entry["name"],
                "description": entry["description"],
                "parameters": entry["input_schema"],
            },
        }
        for entry in entries
    ]


_REASONING_FIELDS = ("reasoning_content", "reasoning")


def _assistant_extra(message: Mapping[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    spoken = message.get("content")
    if isinstance(spoken, str):
        visible = _REASONING_BLOCK.sub("", spoken).strip()
        if visible:
            extra["content"] = visible
    for field_name in _REASONING_FIELDS:
        value = message.get(field_name)
        if isinstance(value, str) and value:
            extra[field_name] = value
    details = message.get("reasoning_details")
    if isinstance(details, list) and details:
        extra["reasoning_details"] = details
    return extra


def _text_event(reasoning: bool, text: str) -> ModelTextDelta | ModelReasoningDelta:
    return ModelReasoningDelta(text=text) if reasoning else ModelTextDelta(text=text)


def _agent_turn_from_chat_response(
    response: Mapping[str, Any], text_answer: Callable[[str], dict[str, Any]] | None = None
) -> AgentTurn:
    _raise_for_incomplete_completion(response)
    message = _chat_message(response)
    raw_tool_calls = message.get("tool_calls")
    if isinstance(raw_tool_calls, list) and raw_tool_calls:
        calls = []
        for raw_call in raw_tool_calls:
            if not isinstance(raw_call, Mapping):
                raise InvalidModelOutputError(
                    "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                    "Tool calls must be JSON objects.",
                )
            call_id = raw_call.get("id")
            function = raw_call.get("function")
            if not isinstance(call_id, str) or not call_id or not isinstance(function, Mapping):
                raise InvalidModelOutputError(
                    "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                    "Tool call lacks an ID or function payload.",
                )
            name = function.get("name")
            arguments = function.get("arguments")
            if not isinstance(name, str) or not isinstance(arguments, str):
                raise InvalidModelOutputError(
                    "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                    "Tool call function must include string name and arguments.",
                )
            try:
                decoded_arguments = json.loads(arguments)
            except json.JSONDecodeError as error:
                raise InvalidModelOutputError(
                    "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                    "Tool call arguments must be a JSON object.",
                ) from error
            if not isinstance(decoded_arguments, dict):
                raise InvalidModelOutputError(
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
        content = _chat_content(response)
        try:
            raw_turn = json.loads(content)
        except json.JSONDecodeError as error:
            if text_answer is None:
                raise InvalidModelOutputError(
                    "OPENAI_COMPATIBLE_RESPONSE_INVALID",
                    "Model response without tool calls must be a JSON AgentTurn object.",
                ) from error
            raw_turn = {"type": "final", "output": text_answer(content.strip())}
        if text_answer is not None and isinstance(raw_turn, dict) and "type" not in raw_turn:
            raw_turn = {"type": "final", "output": raw_turn}
    try:
        return _AGENT_TURN_ADAPTER.validate_python(raw_turn)
    except ValidationError as error:
        field_errors = [
            f"{'.'.join(str(part) for part in issue['loc']) or '(root)'}: {issue['msg']}"
            for issue in error.errors()
        ]
        raise InvalidModelOutputError(
            "OPENAI_COMPATIBLE_RESPONSE_INVALID",
            "Provider response does not match the AgentTurn contract: "
            + "; ".join(field_errors[:5])
            + ".",
            {"field_errors": field_errors, "validation_error": str(error)},
        ) from error


def _raise_for_incomplete_completion(response: Mapping[str, Any]) -> None:
    choices = response.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    finish_reason = first.get("finish_reason") if isinstance(first, Mapping) else None
    if finish_reason == "length":
        raise AgentSdkError(
            "OPENAI_COMPATIBLE_RESPONSE_TRUNCATED",
            'The provider stopped at its output limit (finish_reason "length") before the '
            "turn was complete; raise max_tokens in the model binding parameters or ask for "
            "less output per turn.",
        )
    if finish_reason == "error":
        raw_error = first.get("error") if isinstance(first, Mapping) else None
        detail = (
            _provider_error_message(raw_error)
            if raw_error is not None
            else "the provider gave no detail"
        )
        raise AgentSdkError(
            "OPENAI_COMPATIBLE_PROVIDER_ERROR",
            f'The provider ended the turn with finish_reason "error": {detail}',
        )
    if finish_reason == "content_filter":
        raise AgentSdkError(
            "OPENAI_COMPATIBLE_RESPONSE_FILTERED",
            'The provider withheld the response (finish_reason "content_filter"); its content '
            "policy blocked this turn.",
        )


def _provider_error_message(error: Any) -> str:
    detail = provider_error_detail(json.dumps({"error": error}, default=str))
    return detail or "the provider sent an error object without a message"


def _chat_message(response: Mapping[str, Any]) -> Mapping[str, Any]:
    if "error" in response and not response.get("choices"):
        raise AgentSdkError(
            "OPENAI_COMPATIBLE_PROVIDER_ERROR",
            "Provider returned an error instead of a completion: "
            f"{_provider_error_message(response['error'])}",
        )
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
    visible = _REASONING_BLOCK.sub("", content) if isinstance(content, str) else content
    if not isinstance(visible, str) or not visible.strip():
        raise InvalidModelOutputError(
            "OPENAI_COMPATIBLE_RESPONSE_INVALID",
            "Chat response message must contain non-empty text content.",
        )
    return visible.strip()


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
        self._finish_reason: str | None = None
        self._splitter = ReasoningSplitter()
        self._reasoning_parts: dict[str, list[str]] = {}
        self._reasoning_details: list[Any] = []

    def flush(self) -> list[ModelTextDelta | ModelReasoningDelta]:
        return [_text_event(reasoning, text) for reasoning, text in self._splitter.flush()]

    @property
    def response_id(self) -> str | None:
        return self._response_id

    def consume(
        self, chunk: Mapping[str, Any]
    ) -> list[ModelTextDelta | ModelReasoningDelta | ModelToolCallDelta]:
        events: list[ModelTextDelta | ModelReasoningDelta | ModelToolCallDelta] = []
        if "error" in chunk and not chunk.get("choices"):
            raise AgentSdkError(
                "OPENAI_COMPATIBLE_PROVIDER_ERROR",
                "Provider reported an error in the middle of the stream: "
                f"{_provider_error_message(chunk['error'])}",
            )
        if isinstance(chunk.get("id"), str):
            self._response_id = chunk["id"]
        raw_usage = chunk.get("usage")
        if isinstance(raw_usage, Mapping):
            self._usage = dict(raw_usage)
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            return events
        if isinstance(choices[0].get("finish_reason"), str):
            self._finish_reason = choices[0]["finish_reason"]
        delta = choices[0].get("delta")
        if not isinstance(delta, Mapping):
            return events
        for field_name in _REASONING_FIELDS:
            reasoning = delta.get(field_name)
            if isinstance(reasoning, str) and reasoning:
                events.append(ModelReasoningDelta(text=reasoning))
                self._reasoning_parts.setdefault(field_name, []).append(reasoning)
                break
        details = delta.get("reasoning_details")
        if isinstance(details, list):
            self._reasoning_details.extend(details)
        content = delta.get("content")
        if isinstance(content, str) and content:
            self._content_parts.append(content)
            events.extend(_text_event(flag, text) for flag, text in self._splitter.feed(content))
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
            spoken = "".join(self._content_parts)
            if spoken.strip():
                message["content"] = spoken
            for field_name, parts in self._reasoning_parts.items():
                message[field_name] = "".join(parts)
            if self._reasoning_details:
                message["reasoning_details"] = self._reasoning_details
        else:
            message["content"] = "".join(self._content_parts)
        choice: dict[str, Any] = {"message": message}
        if self._finish_reason is not None:
            choice["finish_reason"] = self._finish_reason
        response: dict[str, Any] = {"choices": [choice]}
        if self._response_id is not None:
            response["id"] = self._response_id
        if self._usage is not None:
            response["usage"] = self._usage
        return response


async def _emit(on_delta: ModelStreamListener, event: Any) -> None:
    result = on_delta(event)
    if inspect.isawaitable(result):
        await result
