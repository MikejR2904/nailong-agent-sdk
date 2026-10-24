# Copyright (c) 2026 David Michael Indraputra

"""Token pricing, per-call and per-run cost accounting, and prefix-cache forecasting.

Prices come from the host; the SDK ships none. A call is priced from provider-reported usage, split
into fresh input, cached input and output. The forecaster predicts a run's cost from the size of the
stable prefix and the per-turn growth, with and without a prefix cache, so a change that makes the
prompt prefix more reusable can be valued before any provider call is made.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from os.path import commonprefix
from typing import Any

from pydantic import Field, model_validator

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import _CHARACTERS_PER_TOKEN
from .model import ProviderUsage

MILLION = Decimal(1_000_000)


class TokenPrice(StrictModel):
    """Cost of one million tokens in the host's currency; cached input is never dearer."""

    input_per_million: Decimal = Field(ge=0)
    cached_input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def cached_input_is_not_dearer(self) -> TokenPrice:
        if self.cached_input_per_million > self.input_per_million:
            raise ValueError(
                f"cached_input_per_million ({self.cached_input_per_million}) exceeds "
                f"input_per_million ({self.input_per_million}); a cache read cannot cost more "
                "than a fresh read."
            )
        return self


class PriceTable:
    """Host-supplied prices by (provider, model)."""

    def __init__(self, prices: Mapping[tuple[str, str], TokenPrice]) -> None:
        self._prices = dict(prices)

    def price_for(self, provider: str, model: str) -> TokenPrice:
        price = self._prices.get((provider, model))
        if price is None:
            raise AgentSdkError(
                "PRICE_UNKNOWN",
                f'No price is registered for provider "{provider}" model "{model}"; '
                f"registered: {sorted(self._prices)}.",
                {"provider": provider, "model": model},
            )
        return price


@dataclass(frozen=True)
class CallCost:
    input_tokens: int
    cached_input_tokens: int
    fresh_input_tokens: int
    output_tokens: int
    fresh_cost: Decimal
    cached_cost: Decimal
    output_cost: Decimal
    uncached_total: Decimal
    cached_reported: bool

    @property
    def total(self) -> Decimal:
        return self.fresh_cost + self.cached_cost + self.output_cost

    @property
    def savings(self) -> Decimal:
        return self.uncached_total - self.total

    @property
    def cache_hit_ratio(self) -> Decimal:
        return (
            Decimal(self.cached_input_tokens) / Decimal(self.input_tokens)
            if self.input_tokens
            else Decimal(0)
        )


def price_call(
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    price: TokenPrice,
    cached_reported: bool = True,
) -> CallCost:
    if cached_input_tokens > input_tokens:
        raise AgentSdkError(
            "COST_USAGE_INVALID",
            f"Cached input tokens ({cached_input_tokens}) exceed total input tokens "
            f"({input_tokens}).",
            {"input_tokens": input_tokens, "cached_input_tokens": cached_input_tokens},
        )
    fresh = input_tokens - cached_input_tokens
    return CallCost(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        fresh_input_tokens=fresh,
        output_tokens=output_tokens,
        fresh_cost=price.input_per_million * fresh / MILLION,
        cached_cost=price.cached_input_per_million * cached_input_tokens / MILLION,
        output_cost=price.output_per_million * output_tokens / MILLION,
        uncached_total=(
            price.input_per_million * input_tokens / MILLION
            + price.output_per_million * output_tokens / MILLION
        ),
        cached_reported=cached_reported,
    )


def price_usage(usage: ProviderUsage, price: TokenPrice) -> CallCost:
    """Price one provider response. Output includes reasoning tokens as the provider counts them."""

    missing = [
        name
        for name, value in (
            ("input_tokens", usage.input_tokens),
            ("output_tokens", usage.output_tokens),
        )
        if value is None
    ]
    if missing:
        raise AgentSdkError(
            "COST_USAGE_INCOMPLETE",
            f"Provider usage lacks {missing} (request {usage.request_id!r}); the call cannot be "
            "priced without them.",
            {"missing": missing, "request_id": usage.request_id},
        )
    assert usage.input_tokens is not None and usage.output_tokens is not None
    return price_call(
        usage.input_tokens,
        usage.cached_input_tokens or 0,
        usage.output_tokens,
        price,
        cached_reported=usage.cached_input_tokens is not None,
    )


@dataclass
class RunCostLedger:
    calls: list[CallCost] = field(default_factory=list)

    def record(self, cost: CallCost) -> None:
        self.calls.append(cost)

    @property
    def total(self) -> Decimal:
        return sum((c.total for c in self.calls), Decimal(0))

    @property
    def savings(self) -> Decimal:
        return sum((c.savings for c in self.calls), Decimal(0))

    @property
    def cache_hit_ratio(self) -> Decimal:
        tokens = sum(c.input_tokens for c in self.calls)
        return (
            Decimal(sum(c.cached_input_tokens for c in self.calls)) / Decimal(tokens)
            if tokens
            else Decimal(0)
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "calls": len(self.calls),
            "input_tokens": sum(c.input_tokens for c in self.calls),
            "cached_input_tokens": sum(c.cached_input_tokens for c in self.calls),
            "output_tokens": sum(c.output_tokens for c in self.calls),
            "total": str(self.total),
            "savings": str(self.savings),
            "cache_hit_ratio": str(self.cache_hit_ratio.quantize(Decimal("0.0001"))),
            "calls_without_cached_counter": sum(1 for c in self.calls if not c.cached_reported),
        }


@dataclass(frozen=True)
class CacheRules:
    """When a provider's automatic prefix cache can serve a shared prefix."""

    min_prefix_tokens: int = 1024
    granularity: int = 128

    def cacheable(self, shared_prefix_tokens: int) -> int:
        if shared_prefix_tokens < self.min_prefix_tokens:
            return 0
        return shared_prefix_tokens // self.granularity * self.granularity


OPENAI_AUTOMATIC = CacheRules(min_prefix_tokens=1024, granularity=128)


def shared_prefix_tokens(previous: str, current: str) -> int:
    """Estimated tokens in the longest common prefix of two serialized requests."""

    return len(commonprefix([previous, current])) // _CHARACTERS_PER_TOKEN


@dataclass(frozen=True)
class RunForecast:
    per_turn: tuple[CallCost, ...]

    @property
    def total(self) -> Decimal:
        return sum((c.total for c in self.per_turn), Decimal(0))

    @property
    def total_uncached(self) -> Decimal:
        return sum((c.uncached_total for c in self.per_turn), Decimal(0))

    @property
    def savings(self) -> Decimal:
        return self.total_uncached - self.total


def forecast_run(
    price: TokenPrice,
    *,
    stable_tokens: int,
    growth_tokens_per_turn: int,
    volatile_tail_tokens: int,
    output_tokens_per_turn: int,
    turns: int,
    rules: CacheRules = OPENAI_AUTOMATIC,
    history_before_tail: bool = True,
) -> RunForecast:
    """Expected cost of a run of ``turns`` model calls.

    Turn ``t`` sends ``stable + growth*(t-1) + tail`` input tokens. The prefix it shares with turn
    ``t-1`` is the stable block plus the history both sent when the history is append-only and sits
    before the tail that changes each turn (``history_before_tail``); otherwise only the stable
    block is shared. The first turn is a cold start.
    """

    if turns < 1:
        raise ValueError(f"turns must be at least 1, got {turns}.")
    calls = []
    for turn in range(1, turns + 1):
        history = growth_tokens_per_turn * (turn - 1)
        total_input = stable_tokens + history + volatile_tail_tokens
        shared = 0
        if turn > 1:
            shared = stable_tokens + (
                growth_tokens_per_turn * (turn - 2) if history_before_tail else 0
            )
        cached = min(rules.cacheable(shared), total_input)
        calls.append(price_call(total_input, cached, output_tokens_per_turn, price))
    return RunForecast(tuple(calls))
