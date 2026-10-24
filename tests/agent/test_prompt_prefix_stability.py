import json
from dataclasses import replace

from nailong_agent_sdk.agent.cost_model import shared_prefix_tokens
from nailong_agent_sdk.agent.model import RequestDiagnosticsSource
from nailong_agent_sdk.agent.openai_compatible import (
    OpenAICompatibleAgentModel,
    OpenAICompatibleEndpoint,
)
from nailong_agent_sdk.foundations.contracts import ModelBinding, ModelObservation
from nailong_agent_sdk.foundations.hashing import estimate_tokens
from nailong_agent_sdk.memory.context import assemble_initial_context
from tests.support.agents import definition, task, tool
from tests.support.model_context import FAKE_KEY, model_context

BINDING = ModelBinding(provider="fake", model="fake-1")
OLD_ORDER = (
    "project_state",
    "iteration",
    "recent_observations",
    "episode_summaries",
    "compacted_episodes",
    "omitted_compacted_count",
)


def adapter():
    return OpenAICompatibleAgentModel(
        OpenAICompatibleEndpoint(
            base_url="https://api.example.invalid/v1", api_key=FAKE_KEY, timeout_seconds=5.0
        ),
        provider="fake",
        model="fake-1",
    )


def observation(index):
    return ModelObservation(
        kind="agent-error", iteration=index, message=f"observation {index} " + "detail " * 120
    )


def at_turn(base, turn):
    return replace(base, iteration=turn, observations=[observation(i) for i in range(1, turn + 1)])


def volatile_of(payload):
    return json.loads(payload["messages"][2]["content"])


def test_changing_fields_come_last_and_growing_history_comes_before_them():
    payload = adapter()._chat_payload(at_turn(model_context(), 3), BINDING)
    keys = list(volatile_of(payload))
    assert keys[-2:] == ["project_state", "iteration"]
    assert keys.index("compacted_episodes") < keys.index("recent_observations")
    assert keys.index("recent_observations") < keys.index("episode_summaries")
    assert keys.index("episode_summaries") < keys.index("project_state")


def test_the_system_message_is_identical_whatever_the_context_holds():
    model = adapter()
    first = model._chat_payload(model_context(), BINDING)["messages"][0]
    later = model._chat_payload(at_turn(model_context(), 4), BINDING)["messages"][0]
    assert first == later


def test_consecutive_requests_share_nearly_all_of_the_previous_request():
    model = adapter()
    base = model_context()
    task_id = base.task.id
    ratios = []
    for turn in range(1, 7):
        model._chat_payload(at_turn(base, turn), BINDING)
        diagnostics = model.request_diagnostics(task_id)
        assert diagnostics is not None
        if turn == 1:
            assert diagnostics.estimated_shared_prefix_tokens == 0
            assert diagnostics.previous_request_tokens is None
        else:
            assert diagnostics.previous_request_tokens is not None
            ratios.append(
                diagnostics.estimated_shared_prefix_tokens / diagnostics.previous_request_tokens
            )
    assert min(ratios) > 0.8 and ratios[-1] > 0.9, ratios
    assert ratios == sorted(ratios), ratios
    assert isinstance(model, RequestDiagnosticsSource)


def test_the_previous_layout_reused_far_less_of_the_prompt():
    model = adapter()
    base = model_context()
    earlier = model._chat_payload(at_turn(base, 5), BINDING)
    later = model._chat_payload(at_turn(base, 6), BINDING)

    def legacy(payload):
        volatile = volatile_of(payload)
        reordered = {key: volatile[key] for key in OLD_ORDER if key in volatile}
        messages = [*payload["messages"][:2], {"role": "user", "content": json.dumps(reordered)}]
        return json.dumps({"messages": messages}, separators=(",", ":"))

    def current(payload):
        return json.dumps({"messages": payload["messages"]}, separators=(",", ":"))

    new_shared = shared_prefix_tokens(current(earlier), current(later))
    old_shared = shared_prefix_tokens(legacy(earlier), legacy(later))
    assert new_shared > 2 * old_shared
    assert new_shared / estimate_tokens(current(earlier), str) > 0.9


def test_tools_sent_as_native_function_tools_are_not_repeated_in_the_prompt():
    agent_definition = definition(tools=[tool("lookup"), tool("fetch")]).model_copy(
        update={"model_binding": BINDING}
    )
    context = replace(
        model_context(),
        prompt=assemble_initial_context(agent_definition, task()),
    )
    payload = adapter()._chat_payload(context, BINDING)
    stable = json.loads(payload["messages"][1]["content"])
    (section,) = [s for s in stable["prompt"]["sections"] if s["kind"] == "tools"]
    assert section["value"] == {"function_tools": ["lookup", "fetch"], "other": []}
    assert [t["function"]["name"] for t in payload["tools"]] == ["lookup", "fetch"]
    assert "tool lookup" not in payload["messages"][1]["content"]
    assert "tool lookup" in json.dumps(payload["tools"])


def test_a_tool_free_prompt_is_unchanged_and_diagnostics_are_bounded_per_adapter():
    model = adapter()
    base = model_context()
    payload = model._chat_payload(base, BINDING)
    stable = json.loads(payload["messages"][1]["content"])
    assert stable["prompt"] == base.prompt.model_dump(mode="json")
    for index in range(135):
        other = replace(base, task=base.task.model_copy(update={"id": f"task-{index}"}))
        model._chat_payload(other, BINDING)
    assert model.request_diagnostics("task-0") is None
    assert model.request_diagnostics("task-134") is not None
    assert model.request_diagnostics("never-seen") is None
