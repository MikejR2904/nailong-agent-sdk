"""Builds the model-facing context for a task under a chosen retention set.

Everything here is the SDK's own machinery: the episode store, tool-result journal, episode graph,
context projector and ``ModelContext`` that a ``BaseAgent`` run assembles. The only thing the
benchmark supplies is the recorded tool-call history and the retention set under test.
"""

from __future__ import annotations

import itertools
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.model import ModelContext
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    ModelBinding,
    ModelObservation,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    VersionedInstructions,
)
from nailong_agent_sdk.memory.context import assemble_initial_context
from nailong_agent_sdk.memory.context_projection import (
    ContextProjection,
    ContextProjectionPolicy,
    ContextProjector,
    InMemoryToolResultJournal,
)
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.episodes import InMemoryEpisodeGraph
from nailong_agent_sdk.state.project_state_engine import ProjectStateProjector
from nailong_agent_sdk.state.project_state_models import StageStateSchema, make_project_state

from ..instances import Instance
from .payloads import build_payload

AGENT_IDENTITY = "memory-reader"
INSTRUCTIONS = (
    "You answer questions about earlier tool calls using only the memory shown in your context. "
    "Quote values exactly as they appear in a tool result. If a value is not present in the "
    "context, answer null for it; never guess or infer a value that is not shown."
)
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class Scene:
    instance: Instance
    store: InMemoryEpisodeStore
    graph: InMemoryEpisodeGraph
    journal: InMemoryToolResultJournal
    projector: ContextProjector
    observations: list[ModelObservation]
    ids: list[str]


def _clock():
    counter = itertools.count()
    return lambda: _EPOCH + timedelta(seconds=next(counter))


def build_scene(instance: Instance, policy: ContextProjectionPolicy | None = None) -> Scene:
    policy = policy or ContextProjectionPolicy(episode_token_budget=_episode_budget(instance))
    store = InMemoryEpisodeStore()
    graph = InMemoryEpisodeGraph(now=_clock())
    journal = InMemoryToolResultJournal()
    projector = ContextProjector(policy)
    observations: list[ModelObservation] = []
    ids: list[str] = []
    for step in instance.recipe:
        if step["op"] not in ("explore", "action"):
            continue
        consumed = [ids[i] for i in step.get("deps", [])]
        built = build_payload(step, len(ids), consumed)
        if step["op"] == "explore":
            episode = graph.add_exploratory(built.summary, built.payload)
            record = store.open_exploratory(AGENT_IDENTITY, content=built.payload)
            store.close(record.id, description=built.summary)
        else:
            episode = graph.add_action(built.summary, consumed, built.payload)
            store.mark_accessed(consumed)
            record = store.open_action(AGENT_IDENTITY, consumed, content=built.payload)
            store.close(record.id)
        if episode.id != record.id:
            raise ValueError(
                f'Episode graph id "{episode.id}" and store id "{record.id}" diverged while '
                f'replaying "{instance.instance_id}".'
            )
        projected = projector.project_tool_result(built.call, built.result, journal)
        store.attach_source_handle(record.id, projected.handle.handle_id)
        observations.append(
            ModelObservation(
                kind="tool-result",
                iteration=len(ids) + 1,
                message=BaseAgent._tool_message(
                    built.call, built.result, projected.handle.handle_id
                ),
                tool_call_id=built.call.id,
                tool_name=built.call.name,
                episode_id=episode.id,
                result=projected,
            )
        )
        ids.append(record.id)
    return Scene(instance, store, graph, journal, projector, observations, ids)


def _episode_budget(instance: Instance) -> int:
    return max(0, min(instance.budget, 12_000))


def agent_definition(output_schema: dict[str, Any], binding: ModelBinding) -> AgentDefinition:
    return AgentDefinition(
        identity=AGENT_IDENTITY,
        instructions=VersionedInstructions(version="memory-qa-1", text=INSTRUCTIONS),
        input_schema={"type": "object", "properties": {"question": {"type": "string"}}},
        tools=[],
        model_binding=binding,
        output_schema=output_schema,
        termination_policy=TerminationPolicy(max_iterations=1, status_field="status"),
    )


def scoped_task(instance: Instance) -> ScopedAgentTask:
    return ScopedAgentTask(
        id=instance.instance_id,
        input={"question": instance.query},
        scope=TaskScope(label="memory-question", boundaries={"project_id": instance.template_id}),
        locked_interface={},
        instructions="Answer the question from the tool results still in your memory.",
        acceptance_criteria=[
            "Every requested key is present, with null where the value is unknown."
        ],
    )


def project(scene: Scene, retained_ids: Collection[str]) -> ContextProjection:
    scene.store.retain_only(retained_ids)
    definition_prompt = assemble_initial_context(
        agent_definition({"type": "object"}, ModelBinding(provider="p", model="m")),
        scoped_task(scene.instance),
    )
    return scene.projector.project(
        definition_prompt,
        scene.observations,
        scene.graph.list(),
        scene.store,
        frozenset(),
        relevance_query=scene.instance.query,
    )


def model_context(
    scene: Scene,
    retained_ids: Collection[str],
    output_schema: dict[str, Any],
    binding: ModelBinding,
    *,
    enforce_budget: bool = True,
) -> ModelContext:
    scene.store.retain_only(retained_ids)
    retained_tokens = scene.store.estimate_tokens()
    if enforce_budget and retained_tokens > scene.instance.budget:
        raise ValueError(
            f'Retention set for "{scene.instance.instance_id}" costs {retained_tokens} tokens, '
            f"over the episode budget of {scene.instance.budget}; the projector would compact it "
            "again, so the evaluated context would not be the one under test."
        )
    definition = agent_definition(output_schema, binding)
    task = scoped_task(scene.instance)
    prompt = assemble_initial_context(definition, task)
    projection = scene.projector.project(
        prompt,
        scene.observations,
        scene.graph.list(),
        scene.store,
        frozenset(),
        relevance_query=scene.instance.query,
    )
    state = make_project_state(
        project_id=scene.instance.template_id,
        revision=0,
        stage_schema=StageStateSchema(schema_id="memory-qa-v1", stage="answer"),
    )
    return ModelContext(
        task=task,
        prompt=prompt,
        iteration=len(scene.ids) + 1,
        project_state=ProjectStateProjector().project(state),
        observations=projection.observations,
        episodes=projection.episodes,
        projection=projection.metadata,
        model_binding=binding,
        output_schema=output_schema,
        compacted_episodes=projection.compacted_episodes,
        semantic_summaries=projection.summaries,
    )


def render_episode_views(
    task: Any, retained: set[int], policy: ContextProjectionPolicy
) -> dict[int, str]:
    """Model-visible text of every episode: full summary and preview if retained, else the stub."""

    scene = build_scene(
        task.instance,
        policy=ContextProjectionPolicy(
            context_token_budget=policy.context_token_budget,
            episode_token_budget=policy.context_token_budget,
            tool_result_preview_chars=policy.tool_result_preview_chars,
            compacted_stub_token_budget=policy.compacted_stub_token_budget,
            compacted_summary_chars=policy.compacted_summary_chars,
        ),
    )
    projection = project(scene, {scene.ids[i] for i in retained})
    position = {episode_id: index for index, episode_id in enumerate(scene.ids)}
    views: dict[int, str] = {i: "" for i in range(len(scene.ids))}
    previews = {o.episode_id: o for o in projection.observations if o.episode_id is not None}
    for summary in projection.episodes:
        observation = previews.get(summary.id)
        result = observation.result if observation is not None else None
        extra = ""
        if result is not None:
            extra = f" {result.preview!r} {result.error or ''}"
        views[position[summary.id]] = f"{summary.summary}{extra}"
    for stub in projection.compacted_episodes:
        views[position[stub.episode_id]] = stub.summary
    return views
