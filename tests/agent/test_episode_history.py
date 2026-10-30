import pytest

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.foundations.contracts import AgentRunStatus, EpisodeKind
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.context_projection import ContextProjectionPolicy
from nailong_agent_sdk.memory.episodes import InMemoryEpisodeGraph
from tests.support.agents import (
    DynamicModel,
    FnExecutor,
    arun,
    definition,
    final,
    ok,
    task,
    tool,
    tool_call,
)

READ = tool("read")
APPLY = tool("apply", kind=EpisodeKind.ACTION)
POLICY = ContextProjectionPolicy(context_token_budget=4_000, episode_token_budget=400)


def test_the_graph_keeps_the_complete_history_unless_a_limit_is_set():
    graph = InMemoryEpisodeGraph()
    for index in range(6):
        graph.add_exploratory(f"read {index}")
    assert len(graph.list()) == graph.total == 6 and graph.omitted == 0


def test_a_limit_keeps_the_newest_summaries_and_every_id_stays_known():
    graph = InMemoryEpisodeGraph(retained_limit=3)
    first = graph.add_exploratory("read 0")
    for index in range(1, 6):
        graph.add_exploratory(f"read {index}")
    assert [e.id for e in graph.list()] == ["episode-4", "episode-5", "episode-6"]
    assert (graph.total, graph.omitted) == (6, 3)
    assert graph.kind_of(first.id) is EpisodeKind.EXPLORATORY
    action = graph.add_action("apply", [first.id])
    assert action.dependency_ids == [first.id] and graph.kind_of(action.id) is EpisodeKind.ACTION
    with pytest.raises(AgentSdkError, match="unknown exploratory episode"):
        graph.add_action("apply", ["episode-99"])
    with pytest.raises(AgentSdkError, match="may depend only on exploratory"):
        graph.add_action("apply", [action.id])
    with pytest.raises(ValueError, match="retained_limit"):
        InMemoryEpisodeGraph(retained_limit=0)


def reading_then_applying(count, target):
    def respond(context):
        if context.iteration <= count:
            return tool_call(f"c{context.iteration}", "read", {"path": f"f{context.iteration}.txt"})
        if context.iteration == count + 1:
            return tool_call("a1", "apply", {}, consumed_episode_ids=[target])
        return final()

    return DynamicModel(respond)


def executor():
    return FnExecutor(
        lambda tool_definition, context: ok({"text": "payload " * 60 + context.call.id})
    )


def agent_with(model, **options):
    return BaseAgent(
        definition(tools=[READ, APPLY], max_iterations=12),
        model,
        require_tool_authorization=False,
        tool_executor=executor(),
        context_projection_policy=POLICY,
        **options,
    )


def test_the_default_result_still_lists_every_episode():
    result = arun(agent_with(reading_then_applying(6, "episode-1")).run(task()))
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    assert len(result.episodes) == 6 + 1 and result.episodes_total is None
    assert result.episodes_omitted == 0


def test_a_history_limit_bounds_the_result_and_reports_what_it_left_out():
    subject = agent_with(reading_then_applying(6, "episode-1"), episode_history_limit=3)
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    assert [e.id for e in result.episodes] == ["episode-5", "episode-6", "episode-7"]
    assert result.episodes_total == 7 and result.episodes_omitted == 4


def test_an_old_episode_is_still_recognised_after_its_summary_left_the_history():
    seen = []

    def respond(context):
        seen.append(context)
        if context.iteration <= 6:
            return tool_call(f"c{context.iteration}", "read", {"path": f"f{context.iteration}.txt"})
        if context.iteration == 7:
            return tool_call("a1", "apply", {}, consumed_episode_ids=["episode-1", "episode-404"])
        return final()

    subject = agent_with(DynamicModel(respond), episode_history_limit=2)
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    handle = next(
        o.result.handle.handle_id for o in seen[-1].observations if o.tool_call_id == "a1"
    )
    error = subject.result_journal.read(handle)["result"]["error"]
    assert '"episode-404" is not an episode of this run' in error
    assert '"episode-1" is not an episode of this run' not in error


def test_a_history_limit_must_be_positive():
    with pytest.raises(ValueError, match="episode_history_limit must be at least 1"):
        agent_with(DynamicModel(lambda context: final()), episode_history_limit=0)
