import pytest

from nailong_agent_sdk.foundations.contracts import (
    AgentPrompt,
    ModelObservation,
    PromptSection,
    ToolCall,
    ToolExecutionResult,
)
from nailong_agent_sdk.foundations.hashing import estimate_tokens
from nailong_agent_sdk.memory.context_projection import (
    ContextProjectionPolicy,
    ContextProjector,
    InMemoryToolResultJournal,
)
from nailong_agent_sdk.memory.episode_models import EpisodeState, SemanticCompactionPolicy
from nailong_agent_sdk.memory.episode_scoring import summary_relevance
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.episodes import InMemoryEpisodeGraph
from nailong_agent_sdk.memory.semantic_compaction import (
    ExtractiveEpisodeSummarizer,
    SemanticCompactor,
)

PROMPT = AgentPrompt(sections=[PromptSection(kind="identity", value="p")])


def build(
    *,
    semantic=True,
    count=12,
    episode_budget=600,
    summary_budget=1_200,
    rehydration_budget=0,
    marked=(),
    failed=(),
    policy=None,
):
    compactor = (
        SemanticCompactor(
            ExtractiveEpisodeSummarizer(),
            policy=policy or SemanticCompactionPolicy(consolidate_when_summaries=0),
        )
        if semantic
        else None
    )
    store = InMemoryEpisodeStore(semantic=compactor)
    graph = InMemoryEpisodeGraph()
    journal = InMemoryToolResultJournal()
    projector = ContextProjector(
        ContextProjectionPolicy(
            context_token_budget=4_000,
            episode_token_budget=episode_budget,
            tool_result_preview_chars=200,
            semantic_summary_token_budget=summary_budget,
            rehydration_token_budget=rehydration_budget,
        )
    )
    observations = []
    for index in range(count):
        word = "needle" if index in marked else "lorem"
        call = ToolCall(id=f"c{index}", name="read_file", arguments={"path": f"f{index}.txt"})
        if index in failed:
            result = ToolExecutionResult(
                status="failed", error=f"missing f{index}.txt " + "denied " * 60
            )
        else:
            result = ToolExecutionResult(
                status="succeeded", output={"text": f"{word} contents {index} " + "ipsum " * 90}
            )
        description = f"read_file f{index}.txt {result.status} {word}"
        episode = graph.add_exploratory(description, {})
        record = store.open_exploratory(
            "agent",
            content={
                "call": call.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            },
        )
        store.close(record.id, description=description)
        projected = projector.project_tool_result(call, result, journal)
        store.attach_source_handle(record.id, projected.handle.handle_id)
        observations.append(
            ModelObservation(
                kind="tool-result",
                iteration=index + 1,
                message=f"m{index}",
                tool_call_id=call.id,
                tool_name="read_file",
                episode_id=episode.id,
                result=projected,
            )
        )
    return projector, store, graph, observations


def project(parts, query="read_file"):
    projector, store, graph, observations = parts
    protected = frozenset({observations[-1].episode_id})
    return projector.project(PROMPT, observations, graph.list(), store, protected, query)


def covered_by(projection):
    return {
        episode_id for summary in projection.summaries for episode_id in summary.source_episode_ids
    }


def test_verified_summaries_stand_in_for_the_stubs_of_the_episodes_they_cover():
    projection = project(build())
    compacted = set(projection.compaction.compacted_episode_ids)
    assert projection.summaries and compacted
    assert covered_by(projection) == compacted
    assert projection.compacted_episodes == () and projection.metadata.omitted_compacted_count == 0
    assert projection.metadata.semantic_summary_ids == [
        summary.summary_id for summary in projection.summaries
    ]
    assert projection.metadata.estimated_tokens <= projection.metadata.context_token_budget


def test_every_projected_summary_carries_its_provenance_and_is_verified_source_only():
    parts = build()
    projection = project(parts)
    store = parts[1]
    for view in projection.summaries:
        summary = next(s for s in store.summaries() if s.summary_id == view.summary_id)
        assert summary.verification_status.value == "verified" and summary.intact()
        assert view.source_episode_ids == summary.source_episode_ids
        assert view.source_handles == summary.source_handles and view.source_handles
        assert all(
            store.get(episode_id).state is EpisodeState.COMPACTED
            for episode_id in view.source_episode_ids
        )


def test_the_summary_budget_bounds_the_selection_and_counts_what_it_leaves_out():
    parts = build()
    full = project(parts)
    one = estimate_tokens(full.summaries[0].model_dump(mode="json"))
    tight = project(build(summary_budget=one * 2))
    assert 0 < len(tight.summaries) < len(full.summaries)
    spent = sum(estimate_tokens(view.model_dump(mode="json")) for view in tight.summaries)
    assert spent <= one * 2
    assert tight.metadata.omitted_summary_count == len(full.summaries) - len(tight.summaries)
    stubs = {stub.episode_id for stub in tight.compacted_episodes}
    assert not covered_by(tight) & stubs


def test_a_zero_summary_budget_falls_back_to_exactly_the_pckp_only_projection():
    plain = project(build(semantic=False))
    zero = project(build(summary_budget=0))
    assert zero.summaries == () and zero.metadata.semantic_summary_ids == []
    assert zero.metadata.omitted_summary_count == len(project(build()).summaries)
    assert [stub.episode_id for stub in zero.compacted_episodes] == [
        stub.episode_id for stub in plain.compacted_episodes
    ]
    assert [o.episode_id for o in zero.observations] == [o.episode_id for o in plain.observations]


def test_a_store_without_semantic_compaction_projects_exactly_as_before():
    projection = project(build(semantic=False))
    assert projection.summaries == ()
    assert projection.metadata.semantic_summary_ids == []
    assert projection.metadata.omitted_summary_count == 0
    assert projection.compacted_episodes


def test_the_most_relevant_summaries_win_when_the_budget_cannot_hold_all():
    parts = build(marked=(0, 1, 2, 3, 4, 5), summary_budget=400)
    projection = project(parts, query="needle")
    store = parts[1]
    picked = {view.summary_id for view in projection.summaries}
    scored = {
        summary.summary_id: summary_relevance(store.relevance_scorer, "needle", summary)
        for summary in store.active_summaries()
    }
    left_out = set(scored) - picked
    assert picked and left_out
    assert min(scored[i] for i in picked) >= max(scored[i] for i in left_out)
    assert max(scored.values()) > min(scored.values())
    assert projection.metadata.omitted_summary_count == len(left_out)


def test_rehydrated_episodes_are_live_again_and_their_summary_is_not_projected_twice():
    parts = build(count=10, episode_budget=400)
    first = project(parts)
    store = parts[1]
    compacted = first.compaction.compacted_episode_ids
    assert first.summaries and compacted
    result = store.rehydrate(compacted, token_budget=100_000)
    assert sorted(result.restored_episode_ids) == sorted(compacted)
    assert all(store.get(i).state is EpisodeState.CLOSED for i in compacted)
    roomy = ContextProjector(
        ContextProjectionPolicy(context_token_budget=20_000, episode_token_budget=20_000)
    )
    projector, _, graph, observations = parts
    again = roomy.project(
        PROMPT,
        observations,
        graph.list(),
        store,
        frozenset({observations[-1].episode_id}),
        "read_file",
    )
    assert again.compaction.compacted_episode_ids == []
    assert again.summaries == () and again.metadata.omitted_summary_count == 0
    shown = {o.episode_id for o in again.observations}
    assert set(compacted) <= shown | {e.id for e in again.episodes}


@pytest.mark.parametrize("budget", [0, 150])
def test_summaries_never_push_the_context_over_its_budget(budget):
    projection = project(build(summary_budget=budget))
    assert projection.metadata.estimated_tokens <= projection.metadata.context_token_budget


def test_among_equally_relevant_summaries_those_with_unresolved_issues_come_first():
    failed = (1, 2, 3, 4)
    roomy = project(build(failed=failed), query="zzz")
    cost = max(estimate_tokens(view.model_dump(mode="json")) for view in roomy.summaries)
    parts = build(failed=failed, summary_budget=cost * 2)
    projection = project(parts, query="zzz")
    store = parts[1]
    active = store.active_summaries()
    with_issues = {s.summary_id for s in active if s.unresolved_issues}
    without_issues = {s.summary_id for s in active} - with_issues
    assert len(with_issues) >= 2 and without_issues
    picked = [view.summary_id for view in projection.summaries]
    assert picked and len(picked) < len(active)
    assert set(picked) <= with_issues


def test_the_projector_restores_episodes_only_within_its_rehydration_budget():
    for budget, expected in ((0, False), (100_000, True)):
        parts = build(count=10, episode_budget=400, rehydration_budget=budget)
        first = project(parts)
        compacted = first.compaction.compacted_episode_ids
        assert compacted
        result = parts[0].rehydrate(parts[1], compacted[:2])
        assert result.token_budget == budget
        assert bool(result.restored_episode_ids) is expected
        if not expected:
            assert set(result.skipped) == set(compacted[:2])
        else:
            assert all(
                parts[1].get(i).state is EpisodeState.CLOSED for i in result.restored_episode_ids
            )
