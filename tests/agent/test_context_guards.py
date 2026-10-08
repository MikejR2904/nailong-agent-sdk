from nailong_agent_sdk.foundations.contracts import (
    AgentRunStatus,
    EpisodeKind,
    ToolExecutionResult,
    VersionedInstructions,
)
from nailong_agent_sdk.memory.context_projection import ContextProjectionPolicy
from nailong_agent_sdk.state.project_state_models import (
    ProjectStateProjectionPolicy,
    StageStateSchema,
    StateAuthority,
    StateEvidence,
    StateTransition,
    StateTransitionKind,
)
from nailong_agent_sdk.state.project_state_store import InMemoryProjectStateStore
from tests.support.agents import FnExecutor, agent, arun, definition, final, task, tool, tool_call

UNCLASSIFIED = StageStateSchema(schema_id="unclassified-v1", stage="unclassified")


def question(index, authority=StateAuthority.HUMAN, kind=StateTransitionKind.QUESTION_OPENED):
    payload = (
        {"question_id": f"q{index}", "content": "why " * 120, "owner": "human"}
        if kind is StateTransitionKind.QUESTION_OPENED
        else {"question_id": f"q{index}", "resolution": "because"}
    )
    return StateTransition(
        kind=kind,
        actor=authority,
        action_id=f"{kind.value}-{index}",
        payload=payload,
        evidence=[StateEvidence(evidence_id=f"{kind.value}-{index}", kind="human-note")],
    )


def test_a_prompt_larger_than_the_context_budget_blocks_before_any_model_call():
    oversized = definition().model_copy(
        update={"instructions": VersionedInstructions(version="v1", text="word " * 3000)}
    )
    subject, model = agent(
        [final()],
        definition_=oversized,
        context_projection_policy=ContextProjectionPolicy(
            context_token_budget=256, episode_token_budget=256
        ),
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.BLOCKED
    assert result.reason.startswith("CONTEXT_BUDGET_EXCEEDED: immutable prompt plus bounded")
    assert model.calls == []


def test_a_project_state_larger_than_its_budget_blocks_until_its_questions_are_closed():
    store = InMemoryProjectStateStore()
    store.ensure("t1", UNCLASSIFIED)
    for index in range(6):
        store.apply("t1", question(index))
    policy = ProjectStateProjectionPolicy(token_budget=400)
    subject, model = agent(
        [final()], project_state_store=store, project_state_projection_policy=policy
    )
    blocked = arun(subject.run(task()))
    assert blocked.status is AgentRunStatus.BLOCKED
    assert blocked.reason.startswith("PROJECT_STATE_BUDGET_EXCEEDED: mandatory current-state")
    assert model.calls == []
    for index in range(6):
        store.apply("t1", question(index, kind=StateTransitionKind.QUESTION_RESOLVED))
    resumed, resumed_model = agent(
        [final()], project_state_store=store, project_state_projection_policy=policy
    )
    completed = arun(resumed.run(task()))
    assert completed.status is AgentRunStatus.COMPLETED
    assert len(resumed_model.calls) == 1


def test_unmanifested_action_episodes_over_the_episode_budget_end_in_a_context_deadlock():
    unmanifested = tool("eda", kind=EpisodeKind.ACTION, requires_manifest=True)
    executor = FnExecutor(
        lambda tool_definition, context: ToolExecutionResult(
            status="succeeded", output={"blob": "payload " * 800}
        )
    )
    subject, model = agent(
        [tool_call("c1", "eda"), final({"status": "wrong"}), final()],
        executor=executor,
        definition_=definition(tools=[unmanifested], max_iterations=6),
        context_projection_policy=ContextProjectionPolicy(
            context_token_budget=4000, episode_token_budget=256
        ),
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.BLOCKED
    assert result.reason.startswith("CONTEXT_DEADLOCK: no closed, dependency-free")
    assert len(model.calls) == 2
    assert [event.type for event in result.events][-3:] == [
        "context-projected",
        "terminated",
        "state-updated",
    ]
