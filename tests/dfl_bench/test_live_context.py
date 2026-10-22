import json

import pytest
from dfl_bench.instances import replay
from dfl_bench.live.context import build_scene, model_context
from dfl_bench.live.scenarios import FAMILIES, contains_value, evidence_free_ids, generate_task

from nailong_agent_sdk.agent.openai_compatible.chat import OpenAICompatibleAgentModel
from nailong_agent_sdk.agent.openai_compatible.transport import OpenAICompatibleEndpoint
from nailong_agent_sdk.foundations.contracts import ModelBinding
from nailong_agent_sdk.memory.context_projection import ContextProjectionPolicy

BINDING = ModelBinding(provider="fireworks", model="probe-model", parameters={"temperature": 0.0})
ADAPTER = OpenAICompatibleAgentModel(
    OpenAICompatibleEndpoint(
        base_url="https://api.fireworks.ai/inference/v1", api_key="unused-key"
    ),
    provider="fireworks",
    model="probe-model",
)


def relaxed_scene(task):
    return build_scene(task.instance, ContextProjectionPolicy(episode_token_budget=12_000))


def request_text(scene, retained, task):
    context = model_context(scene, retained, task.output_schema, BINDING, enforce_budget=False)
    payload = ADAPTER._chat_payload(context, BINDING)
    return "\n".join(message["content"] for message in payload["messages"]), context


def with_prerequisites(scene, indices):
    chosen = set(indices)
    pending = list(indices)
    while pending:
        for dependency in scene.store.get(scene.ids[pending.pop()]).depends_on:
            position = scene.ids.index(dependency)
            if position not in chosen:
                chosen.add(position)
                pending.append(position)
    return chosen


@pytest.mark.parametrize("family", FAMILIES)
def test_retained_evidence_reaches_the_model_request_and_compacted_evidence_does_not(family):
    task = generate_task(family, 0, 0, 11)
    scene = build_scene(task.instance)
    text_all, _ = request_text(relaxed_scene(task), {*scene.ids}, task)
    for value in task.expected.values():
        assert contains_value(text_all, value)
    kept = evidence_free_ids(task)
    text_none, context = request_text(relaxed_scene(task), kept, task)
    for value in task.expected.values():
        assert not contains_value(text_none, value)
    assert context.compacted_episodes
    assert all(stub.handle_id for stub in context.compacted_episodes)


def test_the_request_carries_the_question_the_schema_and_the_reader_instructions():
    task = generate_task("deploy", 1, 1, 4)
    scene = relaxed_scene(task)
    text, context = request_text(scene, set(scene.ids), task)
    assert task.instance.query in text
    assert "answer null" in text and "never guess" in text
    payload = ADAPTER._chat_payload(context, BINDING)
    assert payload["response_format"] == {"type": "json_object"} and "tools" not in payload
    assert payload["temperature"] == 0.0
    stable = json.loads(payload["messages"][1]["content"])
    assert stable["output_schema"]["required"] == list(task.answer_keys)


def test_episode_ids_match_between_the_feature_replay_and_the_live_scene():
    task = generate_task("triage", 2, 0, 9)
    assert build_scene(task.instance).ids == replay(task.instance).ids
    built, scene = replay(task.instance), build_scene(task.instance)
    for episode_id in scene.ids:
        assert built.store.get(episode_id).depends_on == scene.store.get(episode_id).depends_on
        assert built.store.get(episode_id).content == scene.store.get(episode_id).content


def test_over_budget_retention_is_refused_with_the_cost_and_the_budget():
    task = generate_task("ledger", 0, 1, 2)
    scene = build_scene(task.instance)
    with pytest.raises(ValueError, match="over the episode budget") as raised:
        model_context(scene, set(scene.ids), task.output_schema, BINDING)
    assert str(task.instance.budget) in str(raised.value)


def test_retention_without_prerequisites_is_rejected_by_the_store():
    task = generate_task("ledger", 0, 0, 2)
    scene = build_scene(task.instance)
    action = next(i for i in scene.ids if scene.store.get(i).depends_on)
    with pytest.raises(ValueError, match="without its prerequisites"):
        model_context(scene, {action}, task.output_schema, BINDING)


def test_a_within_budget_retention_set_is_projected_unchanged():
    task = generate_task("deploy", 0, 2, 6)
    scene = build_scene(task.instance)
    sources = {i for sources in task.fact_sources.values() for i in sources}
    cheapest = {
        scene.ids[i] for i in with_prerequisites(scene, [min(task.fact_sources["error_code"])])
    }
    context = model_context(scene, cheapest, task.output_schema, BINDING)
    retained = {episode.id for episode in context.episodes}
    assert retained == cheapest and sources
    assert len(context.compacted_episodes) == len(scene.ids) - len(cheapest)
