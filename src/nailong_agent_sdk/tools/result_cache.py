# Copyright (c) 2026 David Michael Indraputra

"""Opt-in cache of successful results of read-only tools.

Nothing is cached unless the host names the tool and a time-to-live. A cache entry is created only
from a successful run of a tool classified read-only, and any run of a tool that can change state
(mutating, process or destructive) clears the whole cache, so a read is never served from before a
write the same run made. The caller evaluates capability policy before asking the cache, so a hit
never bypasses it. The cache cannot see changes made outside the run; the time-to-live bounds how
stale such a result can be.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel, ToolExecutionResult
from ..foundations.hashing import estimate_tokens, sha256_hex, strict_canonical_json


class CacheableTool(StrictModel):
    """How long a tool's result may be replayed, and whether to point at an earlier result."""

    ttl_seconds: float = Field(gt=0, allow_inf_nan=False)
    reference_window: int = Field(default=0, ge=0)


class ToolCachePolicy(StrictModel):
    """Tools the host has chosen to cache; empty means no caching."""

    cacheable: dict[str, CacheableTool] = Field(default_factory=dict)


@dataclass(frozen=True)
class CachedResult:
    result: ToolExecutionResult
    stored_at: float
    call_id: str
    iteration: int
    tokens: int


@dataclass
class CacheStats:
    hits: int = 0
    references: int = 0
    misses: int = 0
    stores: int = 0
    expired: int = 0
    invalidations: int = 0
    evictions: int = 0
    replayed_tokens: int = 0
    invalidation_reasons: list[str] = field(default_factory=list)


class ToolResultCache:
    def __init__(
        self,
        policy: ToolCachePolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = 256,
    ) -> None:
        if max_entries < 1:
            raise ValueError(f"max_entries must be at least 1, got {max_entries}.")
        self._policy = policy
        self._clock = clock
        self._max_entries = max_entries
        self._entries: OrderedDict[str, CachedResult] = OrderedDict()
        self._stats = CacheStats()

    def cacheable(self, tool_name: str) -> bool:
        return tool_name in self._policy.cacheable

    def stats(self) -> CacheStats:
        return self._stats

    @staticmethod
    def _key(tool_name: str, arguments: dict[str, Any]) -> str:
        return sha256_hex(strict_canonical_json({"tool": tool_name, "arguments": arguments}))

    def lookup(
        self, tool_name: str, arguments: dict[str, Any], *, call_id: str, iteration: int
    ) -> ToolExecutionResult | None:
        """The earlier result of the same call, or a reference to it, or None on a miss."""

        entry_policy = self._policy.cacheable.get(tool_name)
        if entry_policy is None:
            raise ValueError(f'Tool "{tool_name}" is not in the cache policy.')
        key = self._key(tool_name, arguments)
        entry = self._entries.get(key)
        if entry is None:
            self._stats.misses += 1
            return None
        if self._clock() - entry.stored_at > entry_policy.ttl_seconds:
            del self._entries[key]
            self._stats.expired += 1
            self._stats.misses += 1
            return None
        self._entries.move_to_end(key)
        if (
            entry_policy.reference_window
            and iteration - entry.iteration <= entry_policy.reference_window
        ):
            self._stats.references += 1
            self._stats.replayed_tokens += entry.tokens
            return ToolExecutionResult(
                status="succeeded",
                output={
                    "unchanged_since": {
                        "tool_call_id": entry.call_id,
                        "iteration": entry.iteration,
                    },
                    "result_tokens": entry.tokens,
                },
            )
        self._stats.hits += 1
        self._stats.replayed_tokens += entry.tokens
        return entry.result.model_copy(deep=True)

    def store(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolExecutionResult,
        *,
        call_id: str,
        iteration: int,
    ) -> bool:
        if tool_name not in self._policy.cacheable or result.status != "succeeded":
            return False
        key = self._key(tool_name, arguments)
        self._entries[key] = CachedResult(
            result=result.model_copy(deep=True),
            stored_at=self._clock(),
            call_id=call_id,
            iteration=iteration,
            tokens=estimate_tokens(result.output),
        )
        self._entries.move_to_end(key)
        self._stats.stores += 1
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
            self._stats.evictions += 1
        return True

    def invalidate(self, reason: str) -> int:
        """Drop every entry; returns how many were dropped."""

        dropped = len(self._entries)
        self._entries.clear()
        self._stats.invalidations += 1
        self._stats.invalidation_reasons.append(reason)
        return dropped
