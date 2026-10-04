import hashlib
import json
import random

import pytest

from nailong_agent_sdk.foundations.contracts import (
    AgentPrompt,
    EpisodeKind,
    ModelObservation,
    PromptSection,
    ToolCall,
    ToolExecutionResult,
)
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.context import assemble_initial_context
from nailong_agent_sdk.memory.context_projection import (
    ContextProjectionPolicy,
    ContextProjector,
    FileToolResultJournal,
    InMemoryToolResultJournal,
)
from nailong_agent_sdk.memory.context_selection import DesignStage, TaskAwareContextSelector
from nailong_agent_sdk.memory.episode_models import (
    CompactionStatus,
    CompactionStrategy,
    EpisodeState,
    PaskCompactionPolicy,
)
from nailong_agent_sdk.memory.episode_scoring import _episode_tokens
from nailong_agent_sdk.memory.episode_store import FileEpisodeStore, InMemoryEpisodeStore
from nailong_agent_sdk.memory.episodes import InMemoryEpisodeGraph
from nailong_agent_sdk.specifications.documents import (
    DocumentFormat,
    DocumentNode,
    DocumentNodeKind,
    DocumentTree,
    SourceRef,
    SpecificationCategory,
)
from tests.support.agents import definition as agent_definition
from tests.support.agents import tool


def test_episode_graph_rules():
    graph = InMemoryEpisodeGraph()
    e1 = graph.add_exploratory("read spec")
    a1 = graph.add_action("write draft", [e1.id])
    assert (e1.id, a1.id) == ("episode-1", "episode-2") and a1.dependency_ids == [e1.id]
    with pytest.raises(AgentSdkError, match="unknown exploratory"):
        graph.add_action("x", ["episode-99"])
    with pytest.raises(AgentSdkError, match="only on exploratory"):
        graph.add_action("x", [a1.id])
    with pytest.raises(AgentSdkError):
        graph.add_exploratory("   ")
    assert [e.id for e in graph.list()] == [e1.id, a1.id]


def test_store_lifecycle_rules():
    store = InMemoryEpisodeStore()
    e = store.open_exploratory("a", content={"x": 1})
    with pytest.raises(ValueError, match="description"):
        store.close(e.id)
    with pytest.raises(ValueError, match="closed exploratory"):
        store.open_action("a", [e.id])
    store.close(e.id, description="found x")
    with pytest.raises(ValueError, match="not open"):
        store.close(e.id, description="again")
    act = store.open_action("a", [e.id], content={"y": 2})
    assert store.get(e.id).depended_on_by == [act.id]
    with pytest.raises(ValueError, match="unique"):
        store.open_action("a", [e.id, e.id])
    with pytest.raises(ValueError, match="unknown"):
        store.open_action("a", ["episode-77"])
    with pytest.raises(ValueError, match="Only action"):
        store.attach_eda_manifest(e.id, {"m": 1})
    store.mark_accessed([e.id, e.id])
    assert store.get(e.id).access_count == 1


def make_random_store(
    rng: random.Random, n_exploratory: int, n_action: int, *, size=400, manifest_prob=0.0
):
    store = InMemoryEpisodeStore()
    explore = []
    for i in range(n_exploratory):
        r = store.open_exploratory("a", content={"result": "e" * rng.randint(size // 4, size)})
        store.close(r.id, description=f"explore {i}")
        explore.append(r.id)
    actions = []
    for j in range(n_action):
        deps = rng.sample(explore, min(len(explore), rng.choice([0, 1, 1, 2])))
        manifest = rng.random() < manifest_prob
        r = store.open_action(
            "a",
            deps,
            content={"result": "a" * rng.randint(size // 4, size)},
            requires_manifest=manifest,
        )
        store.close(r.id)
        actions.append(r.id)
    return store, explore, actions


STRATEGIES = [
    CompactionStrategy.EXACT_PCKP,
    CompactionStrategy.PASK,
    CompactionStrategy.GREEDY_BASELINE,
]


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_compaction_invariants_hold_on_random_episode_graphs(strategy):
    rng = random.Random(2026)
    policy = PaskCompactionPolicy(strategy=strategy)
    for trial in range(60):
        store, explore, actions = make_random_store(
            rng, rng.randint(1, 9), rng.randint(0, 9), manifest_prob=0.2
        )
        all_ids = explore + actions
        protected = set(rng.sample(all_ids, min(len(all_ids), rng.choice([0, 0, 1, 2]))))
        active = rng.choice([None, rng.choice(all_ids)])
        budget = rng.randint(0, max(1, store.estimate_tokens()))
        before = {r.id: r for r in store.list()}
        result = store.compact(
            budget,
            protected_episode_ids=protected,
            active_episode_id=active,
            relevance_query="explore 3",
            policy=policy,
        )
        after = {r.id: r for r in store.list()}
        newly = {
            i
            for i in after
            if after[i].state is EpisodeState.COMPACTED
            and before[i].state is not EpisodeState.COMPACTED
        }
        assert set(result.compacted_episode_ids) == newly, (strategy, trial)
        for episode_id in protected | ({active} if active else set()):
            assert after[episode_id].state is not EpisodeState.COMPACTED, (
                strategy,
                trial,
                "protected episode compacted",
            )
        for record in after.values():
            if record.requires_manifest and record.eda_manifest is None:
                assert record.state is not EpisodeState.COMPACTED
        for record in after.values():
            if record.state is not EpisodeState.COMPACTED:
                for dep in record.depends_on:
                    assert after[dep].state is not EpisodeState.COMPACTED, (
                        strategy,
                        trial,
                        "live action lost its prerequisite",
                    )
        for episode_id in newly:
            assert after[episode_id].content is None and after[episode_id].tombstone
        if result.status in (CompactionStatus.COMPACTED, CompactionStatus.WITHIN_BUDGET):
            assert (
                store.estimate_tokens() <= budget or strategy is CompactionStrategy.PASK and False
            ), (strategy, trial, store.estimate_tokens(), budget, result.status)
        else:
            assert result.status in (
                CompactionStatus.PROTECTED_OVER_BUDGET,
                CompactionStatus.CONTEXT_DEADLOCK,
            )


def _static_utilities(result):
    return {
        u["episode_id"]: round(u["utility"] * 1_000_000) for u in result.dossier["static_utilities"]
    }


def test_exact_compaction_is_optimal_for_its_own_objective():
    rng = random.Random(77)
    checked = 0
    for trial in range(40):
        store, explore, actions = make_random_store(
            rng, rng.randint(1, 5), rng.randint(0, 5), size=300
        )
        records = {r.id: r for r in store.list()}
        costs = {i: _episode_tokens(r) for i, r in records.items()}
        budget = rng.randint(1, sum(costs.values()))
        result = store.compact(budget, relevance_query="explore 2")
        if result.status is not CompactionStatus.COMPACTED:
            continue
        utilities = _static_utilities(result)
        retained = set(result.dossier["retained_episode_ids"])
        ids = sorted(records)
        best = -1
        for mask in range(1 << len(ids)):
            sel = {ids[k] for k in range(len(ids)) if mask >> k & 1}
            if any(d not in sel for i in sel for d in records[i].depends_on):
                continue
            if sum(costs[i] for i in sel) > budget:
                continue
            best = max(best, sum(utilities[i] for i in sel))
        assert sum(utilities[i] for i in retained) == best, (trial, budget)
        checked += 1
    assert checked >= 10


def test_compaction_is_deterministic():
    def run():
        rng = random.Random(5)
        store, *_ = make_random_store(rng, 8, 6)
        result = store.compact(1200, relevance_query="explore 1")
        return result.model_dump(mode="json")

    assert run() == run()


def test_open_and_manifest_incomplete_episodes_force_deadlock_not_loss():
    store = InMemoryEpisodeStore()
    e = store.open_exploratory("a", content={"r": "x" * 2000})
    store.close(e.id, description="d")
    a = store.open_action("a", [e.id], content={"r": "y" * 2000}, requires_manifest=True)
    store.close(a.id)
    result = store.compact(10)
    assert result.status in (
        CompactionStatus.CONTEXT_DEADLOCK,
        CompactionStatus.PROTECTED_OVER_BUDGET,
    )
    assert (
        store.get(a.id).state is EpisodeState.CLOSED
        and store.get(e.id).state is EpisodeState.CLOSED
    )
    store.attach_eda_manifest(a.id, {"outputs": []})
    assert store.compact(10).status is not CompactionStatus.CONTEXT_DEADLOCK


def test_checkpoint_roundtrip_and_tamper(tmp_path):
    store = FileEpisodeStore(tmp_path)
    rng = random.Random(1)
    populated, *_ = make_random_store(rng, 4, 3)
    for record in populated.list():
        pass
    e = store.open_exploratory("a", content={"r": 1})
    store.close(e.id, description="d")
    checkpoint = store.persist_checkpoint()
    assert store.verify_checkpoint(checkpoint) and store.load_checkpoint() == checkpoint
    store.mark_accessed([e.id])
    assert not store.verify_checkpoint(checkpoint)
    store.persist()
    assert (
        json.loads((tmp_path / ".agent-memory" / "episodes.json").read_text(encoding="utf-8"))[0][
            "id"
        ]
        == e.id
    )


def _result(i=0, output=None):
    return ToolCall(id=f"c{i}", name="t", arguments={"i": i}), ToolExecutionResult(
        status="succeeded", output=output if output is not None else {"v": i}
    )


@pytest.mark.parametrize("journal_factory", ["memory", "file"])
def test_journal_handles_verify_content_hash(tmp_path, journal_factory):
    journal = (
        InMemoryToolResultJournal()
        if journal_factory == "memory"
        else FileToolResultJournal(tmp_path)
    )
    call, result = _result(1, {"big": "z" * 5000})
    handle = journal.record(call, result)
    payload = journal.read(handle.handle_id)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    assert (
        hashlib.sha256(encoded).hexdigest() == handle.content_hash
        and len(encoded) == handle.byte_count
    )
    with pytest.raises(ValueError, match="Unknown"):
        journal.read("result-999")
    if journal_factory == "file":
        reopened = FileToolResultJournal(tmp_path)
        assert reopened.read(handle.handle_id) == payload


def test_file_journal_rejects_path_traversal_in_handle_ids(tmp_path):
    secret = tmp_path / "outside.json"
    secret.write_text(
        json.dumps({"call": {}, "result": {"status": "succeeded", "output": "SECRET"}}),
        encoding="utf-8",
    )
    journal_root = tmp_path / "run"
    journal_root.mkdir()
    journal = FileToolResultJournal(journal_root)
    for handle in ("../outside", "..\\outside", "../../outside", str(secret.with_suffix(""))):
        try:
            payload = journal.read(handle)
        except ValueError:
            continue
        pytest.fail(
            f"journal.read({handle!r}) escaped the journal directory and returned {payload!r}"
        )


def prompt_of(text="p"):
    return AgentPrompt(sections=[PromptSection(kind="identity", value=text)])


def make_projection_fixture(count=12, budget=1200):
    store = InMemoryEpisodeStore()
    graph = InMemoryEpisodeGraph()
    journal = InMemoryToolResultJournal()
    projector = ContextProjector(
        ContextProjectionPolicy(
            context_token_budget=4000, episode_token_budget=budget, tool_result_preview_chars=200
        )
    )
    observations = []
    for i in range(count):
        call, result = _result(i, {"payload": "w" * 500})
        summary = graph.add_exploratory(f"Tool t({i}) succeeded", {})
        record = store.open_exploratory(
            "a", content={"call": call.model_dump(), "result": result.model_dump()}
        )
        store.close(record.id, description=f"s{i}")
        projected = projector.project_tool_result(call, result, journal)
        observations.append(
            ModelObservation(
                kind="tool-result",
                iteration=i + 1,
                message=f"m{i}",
                tool_call_id=call.id,
                tool_name="t",
                episode_id=summary.id,
                result=projected,
            )
        )
    return projector, store, graph, observations


def test_projection_bounds_context_and_leaves_findable_stubs():
    projector, store, graph, observations = make_projection_fixture()
    protected = frozenset({observations[-1].episode_id})
    projection = projector.project(prompt_of(), observations, graph.list(), store, protected, "t")
    assert projection.metadata.estimated_tokens <= projection.metadata.context_token_budget
    compacted_ids = set(projection.compaction.compacted_episode_ids)
    assert compacted_ids and observations[-1].episode_id not in compacted_ids
    stub_ids = {s.episode_id for s in projection.compacted_episodes}
    assert stub_ids <= compacted_ids and projection.metadata.omitted_compacted_count == len(
        compacted_ids
    ) - len(stub_ids)
    for stub in projection.compacted_episodes:
        owner = next(o for o in observations if o.episode_id == stub.episode_id)
        assert stub.handle_id == owner.result.handle.handle_id and stub.tool_name == "t"
    shown = {o.episode_id for o in projection.observations}
    assert not (shown & compacted_ids) and observations[-1].episode_id in shown


def test_projection_preview_truncation_flags_and_error_text():
    projector = ContextProjector(ContextProjectionPolicy(tool_result_preview_chars=64))
    journal = InMemoryToolResultJournal()
    call = ToolCall(id="c", name="t")
    big = projector.project_tool_result(
        call, ToolExecutionResult(status="succeeded", output={"k": "v" * 500}), journal
    )
    assert (
        big.handle.truncated
        and big.preview["kind"] == "truncated-json-preview"
        and len(big.preview["preview"]) == 64
    )
    small = projector.project_tool_result(
        call, ToolExecutionResult(status="failed", error="e" * 200), journal
    )
    assert small.error.endswith("[truncated]") and not small.handle.truncated
    assert journal.read(big.handle.handle_id)["result"]["output"]["k"] == "v" * 500


def test_projection_policy_validation():
    with pytest.raises(ValueError):
        ContextProjectionPolicy(context_token_budget=1000, episode_token_budget=2000)
    with pytest.raises(ValueError):
        ContextProjectionPolicy(context_token_budget=100)


def test_initial_context_is_ordered_and_by_value():
    from nailong_agent_sdk.foundations.contracts import ScopedAgentTask, TaskScope

    definition = agent_definition(tools=[tool("t", kind=EpisodeKind.ACTION)])
    locked = {"signals": ["a"]}
    task = ScopedAgentTask(
        id="t",
        input={},
        scope=TaskScope(label="s"),
        locked_interface=locked,
        instructions="i",
        acceptance_criteria=["c"],
    )
    context = assemble_initial_context(definition, task)
    assert [s.kind for s in context.sections] == [
        "identity",
        "instructions",
        "task",
        "skills",
        "tools",
    ]
    locked["signals"].append("mutated")
    assert context.sections[2].value["locked_interface"] == {"signals": ["a"]}


def _node(node_id, text, location="line:1"):
    return DocumentNode(
        node_id=node_id,
        kind=DocumentNodeKind.TEXT,
        content=text,
        source=SourceRef(
            document_id="d1",
            relative_path="d1.md",
            source_hash="h",
            format=DocumentFormat.MD,
            location=location,
        ),
    )


def _tree(category=SpecificationCategory.FUNCTIONAL, nodes=None):
    return DocumentTree(
        document_id="d1",
        title="t",
        category=category,
        format=DocumentFormat.MD,
        relative_path="d1.md",
        source_hash="h",
        nodes=nodes or [],
    )


def test_selector_prunes_by_stage_and_matches_keywords_and_pointers():
    trees = [
        _tree(
            nodes=[
                _node("n1", "The adder shall add two values"),
                _node("n2", "Unrelated clock text"),
            ]
        )
    ]
    selector = TaskAwareContextSelector()
    chosen = selector.select(trees, DesignStage.RTL_DEVELOPMENT, "implement adder logic")
    assert [n.node_id for n in chosen.nodes] == ["n1"]
    assert selector.select(trees, DesignStage.PHYSICAL_DESIGN, "implement adder logic").nodes == []
    by_pointer = selector.select(trees, DesignStage.RTL_DEVELOPMENT, "zzz", ["line:1"])
    assert len(by_pointer.nodes) == 2


def test_selector_scope_pointers_are_case_insensitive_against_content():
    trees = [
        _tree(
            nodes=[
                _node("n1", "REQ-FUNC-001 The adder shall add two values", location="line:7"),
                _node("n2", "other"),
            ]
        )
    ]
    chosen = TaskAwareContextSelector().select(
        trees, DesignStage.RTL_DEVELOPMENT, "qqq", ["REQ-FUNC-001"]
    )
    assert [n.node_id for n in chosen.nodes] == ["n1"], (
        "an upper-case requirement id used as a scope pointer did not match its node"
    )
