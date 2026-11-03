# Copyright (c) 2026 David Michael Indraputra

"""Fold Messages API server-sent events into the one non-streaming response shape."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ...foundations.errors import AgentSdkError
from ..model import ModelReasoningDelta, ModelStreamEvent, ModelTextDelta, ModelToolCallDelta

_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


class StreamAccumulator:
    def __init__(self) -> None:
        self.message_id: str | None = None
        self._blocks: dict[int, dict[str, Any]] = {}
        self._tool_indexes: dict[int, int] = {}
        self._partial_inputs: dict[int, list[str]] = {}
        self._stop_reason: str | None = None
        self._stop_details: Any = None
        self._usage: dict[str, Any] = {}
        self._finished = False

    def consume(self, chunk: Mapping[str, Any]) -> list[ModelStreamEvent]:
        kind = chunk.get("type")
        if kind == "message_start":
            self._start(chunk)
        elif kind == "content_block_start":
            return self._block_start(chunk)
        elif kind == "content_block_delta":
            return self._block_delta(chunk)
        elif kind == "message_delta":
            self._message_delta(chunk)
        elif kind == "message_stop":
            self._finished = True
        elif kind == "error":
            error = chunk.get("error")
            detail = error.get("message") if isinstance(error, Mapping) else None
            raise AgentSdkError(
                "ANTHROPIC_MESSAGES_PROVIDER_ERROR",
                f"Provider reported an error mid-stream: {detail or 'no message given'}",
            )
        return []

    def finalize(self) -> dict[str, Any]:
        if not self._finished:
            raise AgentSdkError(
                "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
                "The stream ended before the provider sent message_stop, so the turn is "
                "incomplete.",
            )
        content = [self._finished_block(index) for index in sorted(self._blocks)]
        response: dict[str, Any] = {
            "id": self.message_id,
            "type": "message",
            "content": content,
            "stop_reason": self._stop_reason,
            "usage": dict(self._usage),
        }
        if self._stop_details is not None:
            response["stop_details"] = self._stop_details
        return response

    def _start(self, chunk: Mapping[str, Any]) -> None:
        message = chunk.get("message")
        if isinstance(message, Mapping):
            message_id = message.get("id")
            self.message_id = message_id if isinstance(message_id, str) else None
            self._merge_usage(message.get("usage"))

    def _block_start(self, chunk: Mapping[str, Any]) -> list[ModelStreamEvent]:
        index, block = chunk.get("index"), chunk.get("content_block")
        if not isinstance(index, int) or not isinstance(block, Mapping):
            raise AgentSdkError(
                "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
                "A content_block_start event lacks an integer index or a block.",
            )
        self._blocks[index] = dict(block)
        if block.get("type") == "tool_use":
            self._tool_indexes[index] = len(self._tool_indexes)
            self._partial_inputs[index] = []
            return [
                ModelToolCallDelta(
                    index=self._tool_indexes[index],
                    call_id=block.get("id") if isinstance(block.get("id"), str) else None,
                    name=block.get("name") if isinstance(block.get("name"), str) else None,
                    arguments_delta="",
                )
            ]
        return []

    def _block_delta(self, chunk: Mapping[str, Any]) -> list[ModelStreamEvent]:
        index, delta = chunk.get("index"), chunk.get("delta")
        block = self._blocks.get(index) if isinstance(index, int) else None
        if not isinstance(index, int) or block is None or not isinstance(delta, Mapping):
            raise AgentSdkError(
                "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
                "A content_block_delta event refers to a block that was never started.",
            )
        kind = delta.get("type")
        if kind == "text_delta" and isinstance(delta.get("text"), str):
            block["text"] = block.get("text", "") + delta["text"]
            return [ModelTextDelta(text=delta["text"])]
        if kind == "input_json_delta" and isinstance(delta.get("partial_json"), str):
            self._partial_inputs.setdefault(index, []).append(delta["partial_json"])
            return [
                ModelToolCallDelta(
                    index=self._tool_indexes.get(index, 0),
                    call_id=None,
                    name=None,
                    arguments_delta=delta["partial_json"],
                )
            ]
        if kind == "thinking_delta" and isinstance(delta.get("thinking"), str):
            block["thinking"] = block.get("thinking", "") + delta["thinking"]
            return [ModelReasoningDelta(text=delta["thinking"])]
        elif kind == "signature_delta" and isinstance(delta.get("signature"), str):
            block["signature"] = block.get("signature", "") + delta["signature"]
        return []

    def _message_delta(self, chunk: Mapping[str, Any]) -> None:
        delta = chunk.get("delta")
        if isinstance(delta, Mapping):
            if isinstance(delta.get("stop_reason"), str):
                self._stop_reason = delta["stop_reason"]
            self._stop_details = delta.get("stop_details", self._stop_details)
        self._merge_usage(chunk.get("usage"))

    def _merge_usage(self, usage: Any) -> None:
        if isinstance(usage, Mapping):
            for key in _USAGE_KEYS:
                if usage.get(key) is not None:
                    self._usage[key] = usage[key]

    def _finished_block(self, index: int) -> dict[str, Any]:
        block = dict(self._blocks[index])
        if block.get("type") == "tool_use":
            joined = "".join(self._partial_inputs.get(index, []))
            try:
                block["input"] = json.loads(joined) if joined.strip() else {}
            except json.JSONDecodeError as error:
                raise AgentSdkError(
                    "ANTHROPIC_MESSAGES_RESPONSE_INVALID",
                    f'The streamed input of tool_use "{block.get("name")}" is not valid JSON.',
                ) from error
        return block
