import pytest

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.foundations.contracts import ToolCall
from nailong_agent_sdk.memory.context_projection import ContextProjectionPolicy
from nailong_agent_sdk.memory.episode_models import EpisodeState
from tests.memory.test_semantic_projection import build, project
from tests.support.agents import definition, final
from tests.support.semantic_memory import compact_all, fill, semantic_store


def compacted_store():
    store = semantic_store()
    ids, _ = fill(store, 4, actions=(0,))
    compact_all(store)
    return store, ids


def test_a_quote_names_the_closure_and_cost_without_restoring_anything():
    store, ids = compacted_store()
    action = ids[4]
    before = [(r.id, r.state) for r in store.list()]
    quotes = {q.episode_id: q for q in store.rehydration_quote([action, ids[1], "episode-99"])}
    assert quotes[action].restorable and set(quotes[action].closure_ids) == {ids[0], action}
    assert quotes[ids[1]].closure_ids == [ids[1]] and quotes[ids[1]].tokens > 0
    assert quotes[action].tokens > quotes[ids[1]].tokens
    assert not quotes["episode-99"].restorable and quotes["episode-99"].reason == "unknown episode"
    assert [(r.id, r.state) for r in store.list()] == before
    spent = store.rehydrate([action], token_budget=100_000).restored_tokens
    assert spent == quotes[action].tokens
    assert store.rehydration_quote([action])[0].reason == "already live"


def test_cheapest_first_serves_more_requests_than_taking_them_in_order():
    outcomes = {}
    for order in ("in-order", "cheapest-first"):
        store, ids = compacted_store()
        action, cheap_a, cheap_b = ids[4], ids[1], ids[2]
        budget = store.rehydration_quote([action])[0].tokens
        result = store.rehydrate([action, cheap_a, cheap_b], token_budget=budget, order=order)
        outcomes[order] = result
        assert result.restored_tokens <= budget
        for episode_id in result.restored_episode_ids:
            assert store.get(episode_id).state is EpisodeState.CLOSED
        for episode_id in result.restored_episode_ids:
            for dependency in store.get(episode_id).depends_on:
                assert store.get(dependency).state is EpisodeState.CLOSED
    assert set(outcomes["in-order"].restored_episode_ids) == {ids[0], ids[4]}
    assert set(outcomes["cheapest-first"].restored_episode_ids) == {ids[1], ids[2]}
    assert len(outcomes["cheapest-first"].skipped) < len(outcomes["in-order"].skipped)


def test_requests_that_share_a_prerequisite_pay_for_it_once():
    store = semantic_store()
    ids, _ = fill(store, 3, actions=(0, 0))
    compact_all(store)
    first, second = ids[3], ids[4]
    each = [store.rehydration_quote([e])[0].tokens for e in (first, second)]
    shared = store.rehydrate([first, second], token_budget=100_000, order="cheapest-first")
    assert set(shared.restored_episode_ids) == {ids[0], first, second}
    assert shared.restored_tokens < sum(each)


def test_stubs_state_what_restoring_them_would_cost_only_when_retrieval_is_enabled():
    for budget in (0, 100_000):
        parts = build(count=10, episode_budget=400, rehydration_budget=budget, summary_budget=0)
        projection = project(parts)
        stubs = projection.compacted_episodes
        assert stubs
        if budget == 0:
            assert all(stub.restore_tokens is None for stub in stubs)
        else:
            quotes = {
                q.episode_id: q.tokens
                for q in parts[1].rehydration_quote([s.episode_id for s in stubs])
            }
            assert all(stub.restore_tokens == quotes[stub.episode_id] for stub in stubs)
            assert all(stub.restore_tokens > 0 for stub in stubs)


def agent_with(policy):
    from nailong_agent_sdk.agent.model import ScriptedModel

    return BaseAgent(definition(), ScriptedModel([final()]), context_projection_policy=policy)


def test_the_run_budget_caps_what_consumed_episodes_may_restore_across_all_calls():
    store, ids = compacted_store()
    first_cost = store.rehydration_quote([ids[1]])[0].tokens
    agent = agent_with(
        ContextProjectionPolicy(
            rehydration_token_budget=100_000,
            rehydration_run_budget=first_cost + 1,
            rehydration_order="cheapest-first",
        )
    )
    call_a = ToolCall(id="a", name="x", arguments={}, consumed_episode_ids=[ids[1]])
    call_b = ToolCall(id="b", name="x", arguments={}, consumed_episode_ids=[ids[2]])
    assert agent._rehydrate_consumed(call_a, store) == [ids[1]]
    assert agent._rehydrated_tokens == first_cost
    assert agent._rehydrate_consumed(call_b, store) == []
    assert store.get(ids[2]).state is EpisodeState.COMPACTED


def test_without_a_run_budget_each_call_keeps_the_per_call_budget():
    store, ids = compacted_store()
    agent = agent_with(ContextProjectionPolicy(rehydration_token_budget=100_000))
    for episode_id in (ids[1], ids[2]):
        call = ToolCall(id=episode_id, name="x", arguments={}, consumed_episode_ids=[episode_id])
        assert agent._rehydrate_consumed(call, store) == [episode_id]


def test_negative_budgets_are_rejected():
    store, _ = compacted_store()
    with pytest.raises(ValueError, match="may not be negative"):
        store.rehydrate(["episode-1"], token_budget=-1)
