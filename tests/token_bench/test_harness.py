import json
from dataclasses import replace

import httpx
import pytest
from token_bench.__main__ import LONG, arms, pick_groups
from token_bench.layouts import LEGACY_SYSTEM, LegacyChatModel, legacy_payload, with_salt
from token_bench.report import FIREWORKS_GLM_5P3_FLASH, bootstrap_ratio, render
from token_bench.runner import Arm, Ledger, Settings, TokenCapReached, run_experiment
from token_bench.scene import ROOMY, Regime, turns
from token_bench.simulator import SimulatedProvider

from nailong_agent_sdk.agent.openai_compatible import (
    OpenAICompatibleAgentModel,
    OpenAICompatibleEndpoint,
)
from nailong_agent_sdk.foundations.contracts import ModelBinding

BINDING = ModelBinding(provider="p", model="m", parameters={"max_tokens": 32})
ENDPOINT = OpenAICompatibleEndpoint(base_url="https://x.invalid/v1", api_key="k")


@pytest.fixture(scope="module")
def groups():
    return pick_groups(4100, 3)


def contexts(group, regime=ROOMY, **kwargs):
    return list(turns(group[0], BINDING, regime, instruction_tokens=800, **kwargs))


def quiet(_message):
    return None


def test_the_legacy_layout_leads_with_the_fields_that_change_every_turn(groups):
    context = contexts(groups[0])[3]
    payload = legacy_payload("m", context, BINDING)
    volatile = json.loads(payload["messages"][2]["content"])
    assert list(volatile)[:2] == ["project_state", "iteration"]
    assert payload["messages"][0]["content"] == LEGACY_SYSTEM
    assert "Call " in payload["messages"][1]["content"] and payload["tools"]


def test_the_current_layout_differs_from_the_legacy_one_only_in_the_intended_ways(groups):
    context = contexts(groups[0])[3]
    current = OpenAICompatibleAgentModel(ENDPOINT, provider="p", model="m")
    new = current._chat_payload(context, BINDING)
    old = legacy_payload("m", context, BINDING)
    new_volatile = json.loads(new["messages"][2]["content"])
    old_volatile = json.loads(old["messages"][2]["content"])
    assert new_volatile == old_volatile
    assert list(new_volatile)[-2:] == ["project_state", "iteration"]
    assert new["tools"] == old["tools"]
    assert len(new["messages"][1]["content"]) < len(old["messages"][1]["content"])
    assert new["messages"][0]["content"] != old["messages"][0]["content"]


def test_a_salt_changes_only_the_system_message(groups):
    context = contexts(groups[0])[1]
    payload = legacy_payload("m", context, BINDING)
    first, second = with_salt(payload, "a"), with_salt(payload, "b")
    assert first["messages"][1:] == payload["messages"][1:] == second["messages"][1:]
    assert first["messages"][0]["content"].startswith("[run a] ")
    assert first["messages"][0] != second["messages"][0]


def test_the_scene_replays_the_same_history_for_every_layout_and_grows_each_turn(groups):
    one = contexts(groups[0])
    two = contexts(groups[0])
    assert [c.iteration for c in one] == list(range(2, len(one) + 2))
    assert [len(c.observations) for c in one] == list(range(1, len(one) + 1))
    assert [o.model_dump(mode="json") for o in one[-1].observations] == [
        o.model_dump(mode="json") for o in two[-1].observations
    ]


def test_a_session_joins_histories_and_a_tight_budget_makes_the_sdk_compact(groups):
    session = contexts(groups[0], Regime("t", 6_500, 1_800), session=groups[0][1:2])
    single = contexts(groups[0])
    assert len(session) > len(single)
    assert any(c.compacted_episodes for c in session)
    assert not any(c.compacted_episodes for c in single)


def test_the_simulated_provider_applies_its_prefix_rule_per_user():
    with SimulatedProvider(min_prefix=100, granularity=64) as provider:
        url = provider.base_url + "/chat/completions"
        body = {"model": "m", "user": "u1", "messages": [{"role": "user", "content": "x" * 3000}]}
        first = httpx.post(url, json=body).json()["usage"]
        longer = {**body, "messages": [{"role": "user", "content": "x" * 3000 + "tail"}]}
        second = httpx.post(url, json=longer).json()["usage"]
        other = httpx.post(url, json={**longer, "user": "u2"}).json()["usage"]
    assert first["prompt_tokens_details"]["cached_tokens"] == 0
    cached = second["prompt_tokens_details"]["cached_tokens"]
    assert 0 < cached <= second["prompt_tokens"] and cached % 64 == 0
    assert other["prompt_tokens_details"]["cached_tokens"] == 0


def small_settings(provider, **overrides):
    return Settings(
        base_url=provider.base_url,
        api_key="k",
        model="m",
        instruction_tokens=800,
        **{"workers": 2, **overrides},
    )


def run(provider, arm_list, groups, repeats, run_id, ledger, **overrides):
    run_experiment(
        arm_list, groups, repeats, run_id, small_settings(provider, **overrides), ledger, quiet
    )


def test_a_resumed_run_makes_no_repeat_calls(groups, tmp_path):
    arm_list = [Arm("legacy", "legacy", ROOMY), Arm("current", "current", ROOMY)]
    ledger = Ledger(tmp_path / "ledger.jsonl")
    with SimulatedProvider() as provider:
        run(provider, arm_list, groups[:1], 1, "r1", ledger)
        made = provider.calls
        assert made == len(ledger.rows()) > 0
        resumed = Ledger(tmp_path / "ledger.jsonl")
        run(provider, arm_list, groups[:1], 1, "r1", resumed)
        assert provider.calls == made
    assert {r["label"] for r in resumed.rows()} == {"legacy", "current"}
    assert all(r["cached_tokens"] is not None for r in resumed.rows())
    assert all(r["cached_tokens"] == 0 for r in resumed.rows() if r["turn"] == 1)


def test_the_prompt_token_cap_stops_the_run_before_the_next_call(groups, tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    with SimulatedProvider() as provider:
        with pytest.raises(TokenCapReached, match="prompt-token cap of 5000"):
            run(
                provider,
                [Arm("current", "current", ROOMY)],
                groups[:1],
                1,
                "cap",
                ledger,
                prompt_token_cap=5_000,
                workers=1,
            )
        assert provider.calls == len(ledger.rows()) <= 3
    assert ledger.prompt_tokens >= 5_000


def test_the_report_prices_cached_tokens_separately_and_compares_with_the_baseline(
    groups, tmp_path
):
    arm_list = [Arm("legacy", "legacy", ROOMY), Arm("current", "current", ROOMY)]
    ledger = Ledger(tmp_path / "ledger.jsonl")
    with SimulatedProvider() as provider:
        run(provider, arm_list, groups, 2, "rep", ledger)
    text = render(ledger.rows(), "t")
    assert "Fireworks GLM 5.3 Flash list price" in text and "1.000 (baseline)" in text
    legacy = [r for r in ledger.rows() if r["label"] == "legacy"]
    current = [r for r in ledger.rows() if r["label"] == "current"]

    def cost(rows):
        cached = sum(r["cached_tokens"] for r in rows)
        fresh = sum(r["prompt_tokens"] for r in rows) - cached
        completion = sum(r["completion_tokens"] for r in rows)
        return FIREWORKS_GLM_5P3_FLASH.cost(fresh, cached, completion)

    def hit_ratio(rows):
        return sum(r["cached_tokens"] for r in rows) / sum(r["prompt_tokens"] for r in rows)

    assert cost(current) < cost(legacy)
    assert hit_ratio(current) > hit_ratio(legacy)


def test_bootstrap_ratio_brackets_the_point_estimate_and_prices_are_per_million():
    point, low, high = bootstrap_ratio([8, 9, 10, 11], [10, 10, 10, 10])
    assert point == pytest.approx(0.95) and low <= point <= high
    assert replace(FIREWORKS_GLM_5P3_FLASH, input=1.0).cost(1_000_000, 0, 0) == 1.0


def test_the_live_arm_set_covers_both_layouts_in_both_regimes():
    names = {a.name for a in arms()}
    assert {"roomy:legacy", "roomy:current", "long:legacy", "long:current"} <= names
    assert {a.regime.name for a in arms()} == {"roomy", LONG.name}
    assert LegacyChatModel.__mro__[1] is OpenAICompatibleAgentModel
