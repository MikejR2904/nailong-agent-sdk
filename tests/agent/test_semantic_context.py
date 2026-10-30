import json
import threading
import time
from dataclasses import replace

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.base_agent.types import AgentWatchdogPolicy
from nailong_agent_sdk.agent.openai_compatible import (
    OpenAICompatibleAgentModel,
    OpenAICompatibleEndpoint,
)
from nailong_agent_sdk.foundations.contracts import (
    AgentRunStatus,
    ContextProjectionMetadata,
    EpisodeKind,
)
from nailong_agent_sdk.memory.context_projection import ContextProjectionPolicy
from nailong_agent_sdk.memory.episode_models import SemanticCompactionPolicy, SemanticSummaryView
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.semantic_compaction import (
    ExtractiveEpisodeSummarizer,
    SemanticCompactor,
)
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
from tests.support.model_context import FAKE_KEY, model_context

READ = tool("read")
APPLY = tool("apply", kind=EpisodeKind.ACTION)
POLICY = ContextProjectionPolicy(context_token_budget=4_000, episode_token_budget=400)


def reader(tool_definition, context):
    path = context.call.arguments.get("path", "")
    return ok({"path": path, "text": f"contents of {path} " + "ipsum " * 80})


def semantic_factory(summarizer=None, created=None, **policy):
    options = {"consolidate_when_summaries": 0, **policy}

    def make():
        store = InMemoryEpisodeStore(
            semantic=SemanticCompactor(
                summarizer or ExtractiveEpisodeSummarizer(),
                policy=SemanticCompactionPolicy(**options),
            )
        )
        if created is not None:
            created.append(store)
        return store

    return make


def reading_model(count, then=None):
    def respond(context):
        if context.iteration <= count:
            return tool_call(f"c{context.iteration}", "read", {"path": f"f{context.iteration}.txt"})
        if then is not None and context.iteration == count + 1:
            return then
        return final()

    return DynamicModel(respond)


def agent_for(model, *, tools=(READ,), iterations=8, factory=None, policy=POLICY, **kwargs):
    return BaseAgent(
        definition(tools=list(tools), max_iterations=iterations),
        model,
        require_tool_authorization=False,
        tool_executor=kwargs.pop("executor", FnExecutor(reader)),
        context_projection_policy=policy,
        episode_store_factory=factory,
        **kwargs,
    )


def events_of(result, event_type):
    return [event for event in result.events if event.type == event_type]


class Token:
    def __init__(self):
        self._event = threading.Event()

    def cancel(self):
        self._event.set()

    def is_cancelled(self):
        return self._event.is_set()


class BlockingSummarizer:
    summarizer_id = "blocking"

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def summarize(self, request):
        self.entered.set()
        assert self.release.wait(30), "the test never released the summarizer"
        return ExtractiveEpisodeSummarizer().summarize(request)


def join_abandoned_projection():
    for thread in threading.enumerate():
        if thread.name == "detached-project":
            thread.join(15)


def test_digests_of_compacted_episodes_reach_the_model_with_their_provenance():
    created = []
    model = reading_model(6)
    subject = agent_for(model, factory=semantic_factory(created=created))
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    store = created[0]
    final_context = model.calls[-1]
    assert final_context.semantic_summaries
    assert [view.summary_id for view in final_context.semantic_summaries] == (
        final_context.projection.semantic_summary_ids
    )
    covered = {
        episode_id
        for view in final_context.semantic_summaries
        for episode_id in view.source_episode_ids
    }
    stubs = {stub.episode_id for stub in final_context.compacted_episodes}
    assert covered and not covered & stubs
    for view in final_context.semantic_summaries:
        assert view.source_handles
        for handle in view.source_handles:
            payload = subject.result_journal.read(handle)
            assert payload["call"]["name"] == "read"
    committed = [
        summary
        for event in events_of(result, "semantic-compaction")
        for summary in event.details["committed_summary_ids"]
    ]
    assert committed and set(committed) <= {s.summary_id for s in store.summaries()}
    projected = events_of(result, "context-projected")[-1]
    assert projected.details["semantic_summary_count"] == len(final_context.semantic_summaries)


def test_the_archive_holds_exactly_what_the_journal_recorded_for_each_released_episode():
    created = []
    subject = agent_for(reading_model(6), factory=semantic_factory(created=created))
    arun(subject.run(task()))
    store = created[0]
    released = [
        record
        for record in store.list()
        if record.archive_id is not None and record.content is None
    ]
    assert released
    for record in released:
        assert record.source_handle is not None
        archived = store.archive.get(record.archive_id)
        assert archived.content == subject.result_journal.read(record.source_handle)
        assert archived.id == record.id
    assert store.pending_episode_ids() == []


def test_a_run_without_semantic_compaction_sees_no_digests_and_no_new_events():
    model = reading_model(6)
    subject = agent_for(model)
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    assert all(call.semantic_summaries == () for call in model.calls)
    assert events_of(result, "semantic-compaction") == []
    assert events_of(result, "episodes-rehydrated") == []
    for event in events_of(result, "context-projected"):
        assert "semantic_summary_count" not in event.details
        assert "omitted_summary_count" not in event.details
    assert any(call.compacted_episodes for call in model.calls)


def test_cancelling_while_the_summarizer_runs_ends_the_run_and_commits_nothing():
    summarizer = BlockingSummarizer()
    created = []
    token = Token()
    threading.Thread(
        target=lambda: (summarizer.entered.wait(30), token.cancel()), daemon=True
    ).start()
    subject = agent_for(reading_model(6), factory=semantic_factory(summarizer, created=created))
    started = time.monotonic()
    result = arun(subject.run(task(), token), timeout=60)
    elapsed = time.monotonic() - started
    assert result.status is AgentRunStatus.CANCELLED
    assert "while the episode memory was being summarized" in result.reason
    assert elapsed < 10, "cancellation waited for the summarizer"
    summarizer.release.set()
    join_abandoned_projection()
    store = created[0]
    assert store.summaries() == []
    assert all(
        record.content is not None for record in store.list() if record.state.value == "compacted"
    )


def test_a_run_deadline_that_expires_during_summarization_fails_with_the_deadline_code():
    summarizer = BlockingSummarizer()
    created = []
    subject = agent_for(
        reading_model(6),
        factory=semantic_factory(summarizer, created=created),
        watchdog_policy=AgentWatchdogPolicy(run_deadline_seconds=2.0),
    )
    started = time.monotonic()
    result = arun(subject.run(task()), timeout=60)
    elapsed = time.monotonic() - started
    assert result.status is AgentRunStatus.FAILED
    assert result.failure.code == "WATCHDOG_RUN_DEADLINE"
    assert "while the episode memory was being summarized" in result.failure.message
    assert elapsed < 10
    summarizer.release.set()
    join_abandoned_projection()
    assert created[0].summaries() == []


def consuming_model(count):
    chosen = {}

    def respond(context):
        if context.iteration <= count:
            return tool_call(f"c{context.iteration}", "read", {"path": f"f{context.iteration}.txt"})
        if context.iteration == count + 1:
            digests = context.semantic_summaries
            chosen["episode"] = (
                digests[0].source_episode_ids[0]
                if digests
                else context.compacted_episodes[0].episode_id
            )
            return tool_call("a1", "apply", {"path": "x"}, consumed_episode_ids=[chosen["episode"]])
        return final()

    return DynamicModel(respond), chosen


def applying_executor(applied):
    def execute(tool_definition, context):
        if tool_definition.name == "apply":
            applied.append(list(context.call.consumed_episode_ids))
            return ok({"applied": True})
        return reader(tool_definition, context)

    return FnExecutor(execute)


def test_an_action_over_a_compacted_episode_restores_it_when_a_budget_is_configured():
    applied = []
    model, chosen = consuming_model(6)
    policy = POLICY.model_copy(update={"rehydration_token_budget": 5_000})
    subject = agent_for(
        model,
        tools=(READ, APPLY),
        iterations=10,
        factory=semantic_factory(),
        policy=policy,
        executor=applying_executor(applied),
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    restored = events_of(result, "episodes-rehydrated")
    assert len(restored) == 1 and chosen["episode"] in restored[0].details["episode_ids"]
    assert restored[0].details["tool"] == "apply"
    assert applied == [[chosen["episode"]]]


def test_without_a_rehydration_budget_the_action_is_refused_as_before():
    applied = []
    model, chosen = consuming_model(6)
    subject = agent_for(
        model,
        tools=(READ, APPLY),
        iterations=10,
        factory=semantic_factory(),
        executor=applying_executor(applied),
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    assert applied == [] and events_of(result, "episodes-rehydrated") == []
    outcome = [o for o in model.calls[-1].observations if o.tool_call_id == "a1"]
    assert outcome and f'"{chosen["episode"]}" was compacted out' in outcome[0].message


class CaptureTransport:
    def __init__(self):
        self.payloads = []

    def post_json(self, url, *, headers, payload, timeout_seconds):
        self.payloads.append(payload)
        content = json.dumps(final())
        return {
            "id": "chatcmpl-1",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }


def view_of(summary_id="summary-1"):
    return SemanticSummaryView(
        summary_id=summary_id,
        level=1,
        summary_text="episode-1: read f1.txt",
        key_facts=[],
        decisions=[],
        unresolved_issues=[],
        source_episode_ids=["episode-1"],
        source_handles=["result-1"],
    )


def ask(context):
    transport = CaptureTransport()
    model = OpenAICompatibleAgentModel(
        OpenAICompatibleEndpoint(
            base_url="https://api.example.invalid/v1", api_key=FAKE_KEY, timeout_seconds=5.0
        ),
        provider="fake",
        model="fake-1",
        transport=transport,
    )
    arun(model.next_turn(context))
    payload = transport.payloads[0]
    system = payload["messages"][0]["content"]
    volatile = json.loads(payload["messages"][2]["content"])
    return system, volatile


def test_the_chat_request_carries_digests_only_when_there_are_some():
    base = model_context()
    base_system, volatile = ask(base)
    assert "semantic_summaries" not in volatile and "omitted_summary_count" not in volatile
    with_digests = replace(
        base,
        semantic_summaries=(view_of(),),
        projection=ContextProjectionMetadata(
            estimated_tokens=10,
            context_token_budget=4_000,
            episode_token_budget=400,
            semantic_summary_ids=["summary-1"],
            omitted_summary_count=3,
        ),
    )
    system, volatile = ask(with_digests)
    assert volatile["semantic_summaries"][0]["summary_id"] == "summary-1"
    assert volatile["semantic_summaries"][0]["source_handles"] == ["result-1"]
    assert volatile["omitted_summary_count"] == 3
    assert "A digest is derived, not evidence" in system
    assert system == base_system


def join_background_workers():
    for thread in threading.enumerate():
        if thread.name == "semantic-summarizer":
            thread.join(15)


def test_with_background_summarization_a_run_never_waits_for_the_summarizer():
    summarizer = BlockingSummarizer()
    created = []
    subject = agent_for(
        reading_model(6),
        factory=semantic_factory(summarizer, created=created, background_summarization=True),
    )
    started = time.monotonic()
    result = arun(subject.run(task()), timeout=60)
    elapsed = time.monotonic() - started
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    assert elapsed < 10 and summarizer.entered.is_set()
    store = created[0]
    assert store.summaries() == [] and store.background_counts() == {
        "queued": 0,
        "running": 0,
        "abandoned": 0,
    }
    assert all(call.semantic_summaries == () for call in subject.model.calls)
    summarizer.release.set()
    join_background_workers()
    assert store.summaries() == []


def test_with_background_summarization_digests_reach_later_turns_once_jobs_finish():
    created = []
    slow = FnExecutor(
        lambda tool_definition, context: (time.sleep(0.15), reader(tool_definition, context))[1]
    )
    model = reading_model(8)
    subject = BaseAgent(
        definition(tools=[READ], max_iterations=10),
        model,
        require_tool_authorization=False,
        tool_executor=slow,
        context_projection_policy=POLICY,
        episode_store_factory=semantic_factory(created=created, background_summarization=True),
    )
    result = arun(subject.run(task()), timeout=120)
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    assert any(call.semantic_summaries for call in model.calls)
    assert events_of(result, "semantic-compaction")
    join_background_workers()
