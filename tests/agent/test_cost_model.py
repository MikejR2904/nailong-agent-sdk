from decimal import Decimal

import pytest

from nailong_agent_sdk.agent.cost_model import (
    OPENAI_AUTOMATIC,
    CacheRules,
    PriceTable,
    RunCostLedger,
    TokenPrice,
    forecast_run,
    price_call,
    price_usage,
    shared_prefix_tokens,
)
from nailong_agent_sdk.agent.model import ProviderUsage
from nailong_agent_sdk.foundations.errors import AgentSdkError

PRICE = TokenPrice(
    input_per_million=Decimal("2.50"),
    cached_input_per_million=Decimal("1.25"),
    output_per_million=Decimal("10.00"),
)


def test_a_call_is_priced_from_fresh_cached_and_output_tokens():
    cost = price_usage(
        ProviderUsage(input_tokens=10_000, cached_input_tokens=6_000, output_tokens=500), PRICE
    )
    assert cost.fresh_input_tokens == 4_000 and cost.cached_input_tokens == 6_000
    assert cost.fresh_cost == Decimal("0.0100")
    assert cost.cached_cost == Decimal("0.0075")
    assert cost.output_cost == Decimal("0.0050")
    assert cost.total == Decimal("0.0225")
    assert cost.uncached_total == Decimal("0.0300")
    assert cost.savings == Decimal("0.0075")
    assert cost.cache_hit_ratio == Decimal("0.6")
    assert cost.cached_reported


def test_an_unreported_cached_counter_is_priced_as_uncached_and_flagged():
    cost = price_usage(ProviderUsage(input_tokens=1_000, output_tokens=10), PRICE)
    assert cost.cached_input_tokens == 0 and not cost.cached_reported
    assert cost.savings == 0


def test_incomplete_or_impossible_usage_fails_by_name():
    with pytest.raises(AgentSdkError, match=r"lacks \['output_tokens'\]") as incomplete:
        price_usage(ProviderUsage(input_tokens=5, request_id="req-9"), PRICE)
    assert incomplete.value.code == "COST_USAGE_INCOMPLETE"
    assert "req-9" in incomplete.value.message
    with pytest.raises(AgentSdkError, match=r"Cached input tokens \(9\) exceed total input") as bad:
        price_call(5, 9, 1, PRICE)
    assert bad.value.code == "COST_USAGE_INVALID"


def test_a_cache_read_may_not_cost_more_than_a_fresh_read():
    with pytest.raises(ValueError, match="exceeds input_per_million"):
        TokenPrice(
            input_per_million=Decimal(1),
            cached_input_per_million=Decimal(2),
            output_per_million=Decimal(1),
        )


def test_unknown_models_are_reported_with_the_registered_ones():
    table = PriceTable({("fake", "fake-1"): PRICE})
    assert table.price_for("fake", "fake-1") is PRICE
    with pytest.raises(AgentSdkError, match='provider "fake" model "other"') as error:
        table.price_for("fake", "other")
    assert error.value.code == "PRICE_UNKNOWN" and "('fake', 'fake-1')" in error.value.message


def test_the_ledger_totals_calls_savings_and_hit_ratio():
    ledger = RunCostLedger()
    ledger.record(price_call(2_000, 0, 100, PRICE))
    ledger.record(price_call(2_000, 1_500, 100, PRICE))
    report = ledger.to_json()
    assert report["calls"] == 2 and report["cached_input_tokens"] == 1_500
    assert Decimal(report["total"]) == ledger.total
    assert Decimal(report["savings"]) == Decimal(1_500) * Decimal("1.25") / Decimal(1_000_000)
    assert report["cache_hit_ratio"] == "0.3750"


def test_cache_rules_apply_a_minimum_and_round_down_to_the_granularity():
    rules = CacheRules(min_prefix_tokens=1024, granularity=128)
    assert rules.cacheable(1023) == 0
    assert rules.cacheable(1024) == 1024
    assert rules.cacheable(1500) == 1408
    assert OPENAI_AUTOMATIC.cacheable(5000) == 4992


def test_shared_prefix_is_measured_between_serialized_requests():
    assert shared_prefix_tokens("abcdefgh" * 100, "abcdefgh" * 50 + "zzzz") == 100
    assert shared_prefix_tokens("", "abc") == 0


def test_a_prefix_stable_prompt_costs_less_than_one_whose_history_is_resent_fresh():
    common = dict(
        price=PRICE,
        stable_tokens=3_000,
        growth_tokens_per_turn=1_200,
        volatile_tail_tokens=300,
        output_tokens_per_turn=200,
        turns=8,
    )
    stable = forecast_run(**common, history_before_tail=True)
    unstable = forecast_run(**common, history_before_tail=False)
    assert stable.total_uncached == unstable.total_uncached
    assert stable.total < unstable.total < stable.total_uncached
    assert stable.savings > unstable.savings > 0
    assert stable.per_turn[0].cached_input_tokens == 0
    assert stable.per_turn[3].cached_input_tokens == OPENAI_AUTOMATIC.cacheable(3_000 + 1_200 * 2)
    assert unstable.per_turn[3].cached_input_tokens == OPENAI_AUTOMATIC.cacheable(3_000)


def test_forecast_rejects_runs_without_turns():
    with pytest.raises(ValueError, match="turns must be at least 1"):
        forecast_run(
            PRICE,
            stable_tokens=1,
            growth_tokens_per_turn=1,
            volatile_tail_tokens=1,
            output_tokens_per_turn=1,
            turns=0,
        )
