import pytest

from nailong_agent_sdk.developer_tools.attribute_lineage import (
    FlowBasis,
    FlowRelationKind,
    InputKind,
    OriginKind,
    attribute_lineage,
    collect_inputs,
)
from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
from nailong_agent_sdk.foundations.hashing import canonical_json
from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from nailong_agent_sdk.tools.artifacts import ArtifactStore


def writer(run_id, call_id, iteration=1):
    return {
        "agent_identity": f"agent-{run_id}",
        "agent_run_id": run_id,
        "tool": "write_draft",
        "tool_call_id": call_id,
        "iteration": iteration,
    }


def record(journal, run_id, call_id, name, arguments, output):
    return journal.record_for_run(
        run_id,
        ToolCall(id=call_id, name=name, arguments=arguments),
        ToolExecutionResult(status="succeeded", output=output),
    )


def process_output(text):
    return {"template_name": "generate", "output": text, "exit_kind": "succeeded", "return_code": 0}


def bad_content(path, content):
    return b"BAD" in content


def bad_result(tool, arguments, result):
    return "BAD" in canonical_json(result)


@pytest.fixture
def world(tmp_path):
    return tmp_path, ArtifactStore(tmp_path), FileToolResultJournal(tmp_path)


def shell_then_write(world, written):
    root, store, journal = world
    record(
        journal,
        "r1",
        "c1",
        "run_registered_command",
        {"template_name": "generate"},
        process_output("report BAD value"),
    )
    record(
        journal, "r1", "c2", "write_draft", {"path": "out.txt", "content": written}, {"ok": True}
    )
    version = store.write_text("out.txt", written, manifest=writer("r1", "c2", 2))
    record(
        journal,
        "r2",
        "c3",
        "read_artifact",
        {"artifact_id": version.artifact_id},
        {"artifact_id": version.artifact_id, "content": written},
    )
    return root


def test_the_inputs_of_a_run_are_typed_and_every_retrieval_is_an_observed_fact(world):
    root, store, journal = world
    record(
        journal,
        "r1",
        "c1",
        "run_registered_command",
        {"template_name": "generate"},
        process_output("x"),
    )
    record(
        journal,
        "r1",
        "c2",
        "web_fetch",
        {"url": "https://example.org/a"},
        {"url": "https://example.org/a", "text": "page"},
    )
    record(journal, "r1", "c3", "web_search", {"query": "q"}, {"results": []})
    stored = record(journal, "r1", "c4", "grep", {"pattern": "p"}, {"matches": []})
    record(
        journal,
        "r2",
        "c5",
        "get_tool_result",
        {"handle_id": stored.handle_id},
        {"handle_id": stored.handle_id, "content": "{}"},
    )
    kinds = {item.tool_call_id: item.kind for item in collect_inputs(root, "r1")}
    assert kinds == {
        "c1": InputKind.PROCESS_OUTPUT,
        "c2": InputKind.WEB_FETCH,
        "c3": InputKind.WEB_SEARCH,
    }
    (retrieval,) = collect_inputs(root, "r2")
    assert retrieval.kind is InputKind.STORED_RESULT
    assert retrieval.derived_from_handle == stored.handle_id
    assert retrieval.derived_from_tool == "grep" and retrieval.derived_from_call_id == "c4"
    assert all(item.basis is FlowBasis.OBSERVED for item in collect_inputs(root, "r1"))


def test_a_defect_that_entered_through_a_shell_command_ends_the_trace_at_that_command(world):
    root = shell_then_write(world, "the report says BAD value today")
    result = attribute_lineage(root, root, "r2", bad_content, is_defective_result=bad_result)
    assert [hop.run_id for hop in result.hops] == ["r2", "r1"]
    assert result.origin_kind is OriginKind.EXTERNAL_INPUT
    assert result.origin_input.kind is InputKind.PROCESS_OUTPUT
    assert (
        result.origin_input.tool_call_id == "c1" and result.origin_input.basis is FlowBasis.OBSERVED
    )
    assert "outside the SDK's records" in result.origin_reason
    assert result.origin_decision is not None and result.origin_decision.relation == "unlinked"


def test_the_relation_between_that_command_and_the_write_is_only_that_it_came_first(world):
    root = shell_then_write(world, "the report says BAD value today")
    result = attribute_lineage(root, root, "r2", bad_content, is_defective_result=bad_result)
    (relation,) = result.relations
    assert relation.kind is FlowRelationKind.PRECEDES
    assert relation.basis is FlowBasis.OBSERVED and relation.causal is False
    assert (relation.input_call_id, relation.write_call_id) == ("c1", "c2")
    assert not [r for r in result.relations if r.basis is FlowBasis.INFERRED]


def test_a_verbatim_copy_is_an_inferred_relation_only_when_asked_for_and_only_if_exact(world):
    root = shell_then_write(world, "the report says report BAD value today")
    plain = attribute_lineage(root, root, "r2", bad_content, is_defective_result=bad_result)
    assert not [r for r in plain.relations if r.basis is FlowBasis.INFERRED]
    inferred = attribute_lineage(
        root,
        root,
        "r2",
        bad_content,
        is_defective_result=bad_result,
        infer_verbatim=True,
        min_verbatim_chars=10,
    )
    containment = [r for r in inferred.relations if r.kind is FlowRelationKind.VERBATIM_CONTAINMENT]
    assert len(containment) == 1 and containment[0].basis is FlowBasis.INFERRED
    assert containment[0].causal is False and "exact" in containment[0].detail


def test_a_near_copy_is_never_taken_for_a_dependency(tmp_path):
    root = tmp_path
    store, journal = ArtifactStore(root), FileToolResultJournal(root)
    record(
        journal,
        "r1",
        "c1",
        "run_registered_command",
        {"template_name": "g"},
        process_output("report BAD value"),
    )
    record(
        journal,
        "r1",
        "c2",
        "write_draft",
        {"path": "o.txt", "content": "report BAD valve"},
        {"ok": True},
    )
    version = store.write_text("o.txt", "report BAD valve", manifest=writer("r1", "c2"))
    record(
        journal,
        "r2",
        "c3",
        "read_artifact",
        {"artifact_id": version.artifact_id},
        {"content": "report BAD valve"},
    )
    result = attribute_lineage(
        root,
        root,
        "r2",
        bad_content,
        is_defective_result=bad_result,
        infer_verbatim=True,
        min_verbatim_chars=10,
    )
    assert [r.kind for r in result.relations] == [FlowRelationKind.PRECEDES]


def test_a_defective_page_or_search_is_traced_to_the_call_that_fetched_it(world):
    root, store, journal = world
    record(journal, "r1", "c1", "web_fetch", {"url": "https://example.org/a"}, {"text": "BAD fact"})
    result = attribute_lineage(root, root, "r1", bad_content, is_defective_result=bad_result)
    assert (
        result.origin_kind is OriginKind.EXTERNAL_INPUT
        and result.origin_input.kind is InputKind.WEB_FETCH
    )
    assert result.origin_input.source == "https://example.org/a"


def test_a_stored_result_retrieved_later_is_followed_back_to_the_call_that_produced_it(world):
    root, store, journal = world
    version = store.write_text("data.txt", "BAD total\n", manifest=writer("r0", "w0", 5))
    original = record(
        journal,
        "r1",
        "c1",
        "read_artifact",
        {"artifact_id": version.artifact_id},
        {"artifact_id": version.artifact_id, "content": "BAD total\n"},
    )
    record(
        journal,
        "r2",
        "c2",
        "get_tool_result",
        {"handle_id": original.handle_id},
        {"handle_id": original.handle_id, "content": "{}"},
    )
    result = attribute_lineage(root, root, "r2", bad_content, is_defective_result=bad_result)
    assert [hop.run_id for hop in result.hops] == ["r2", "r1"]
    assert result.origin_kind is OriginKind.ARTIFACT_WRITE
    assert result.origin.agent_run_id == "r0" and result.origin.iteration == 5


def test_a_run_with_no_recorded_defective_input_says_what_it_could_not_see(world):
    root, store, journal = world
    record(
        journal,
        "r1",
        "c1",
        "run_registered_command",
        {"template_name": "g"},
        process_output("fine"),
    )
    result = attribute_lineage(root, root, "r1", bad_content, is_defective_result=bad_result)
    assert result.ok and result.origin_kind is OriginKind.NONE
    assert "only the inputs the journal recorded" in result.summary
