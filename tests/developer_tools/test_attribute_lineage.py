import pytest

from nailong_agent_sdk.developer_tools.attribute_lineage import attribute_lineage
from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
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


def read_artifact(journal, run_id, call_id, artifact_id, content):
    journal.record_for_run(
        run_id,
        ToolCall(id=call_id, name="read_artifact", arguments={"artifact_id": artifact_id}),
        ToolExecutionResult(
            status="succeeded", output={"artifact_id": artifact_id, "content": content}
        ),
    )


def read_file(journal, run_id, call_id, path, lines):
    journal.record_for_run(
        run_id,
        ToolCall(id=call_id, name="read_file", arguments={"path": path}),
        ToolExecutionResult(
            status="succeeded",
            output={
                "path": path,
                "offset": 0,
                "lines": [{"line": i + 1, "text": text} for i, text in enumerate(lines)],
                "truncated": False,
            },
        ),
    )


def bad(path, content):
    return b"BAD" in content


@pytest.fixture
def world(tmp_path):
    store = ArtifactStore(tmp_path)
    journal = FileToolResultJournal(tmp_path)
    return tmp_path, store, journal


def test_a_defect_is_traced_through_two_runs_to_the_write_that_introduced_it(world):
    root, store, journal = world
    first = store.write_text("data.txt", "BAD total\n", manifest=writer("r1", "w1", 3))
    read_artifact(journal, "r2", "c1", first.artifact_id, "BAD total\n")
    second = store.write_text("out.txt", "report BAD\n", manifest=writer("r2", "w2", 2))
    read_artifact(journal, "r3", "c2", second.artifact_id, "report BAD\n")
    result = attribute_lineage(root, root, "r3", bad)
    assert not result.ok
    assert [hop.run_id for hop in result.hops] == ["r3", "r2"]
    assert result.origin.agent_run_id == "r1" and result.origin.iteration == 3
    assert result.origin.tool_call_id == "w1"
    assert "read no version that fails the check" in result.origin_reason
    assert "iteration 3" in result.summary


def test_the_trace_stops_at_the_run_whose_own_decision_made_the_output_bad(world):
    root, store, journal = world
    clean = store.write_text("data.txt", "fine\n", manifest=writer("r1", "w1"))
    read_artifact(journal, "r2", "c1", clean.artifact_id, "fine\n")
    produced = store.write_text("out.txt", "BAD guess\n", manifest=writer("r2", "w2", 4))
    read_artifact(journal, "r3", "c2", produced.artifact_id, "BAD guess\n")
    result = attribute_lineage(root, root, "r3", bad)
    assert [hop.run_id for hop in result.hops] == ["r3"]
    assert result.origin.agent_run_id == "r2" and result.origin.iteration == 4
    assert 'Run "r2" read no version that fails the check' in result.origin_reason


def test_a_run_that_read_only_clean_versions_is_ok(world):
    root, store, journal = world
    clean = store.write_text("data.txt", "fine\n", manifest=writer("r1", "w1"))
    read_artifact(journal, "r2", "c1", clean.artifact_id, "fine\n")
    result = attribute_lineage(root, root, "r2", bad)
    assert result.ok and result.origin is None and not result.hops


def test_identical_content_written_twice_is_reported_as_ambiguous_not_guessed(world):
    root, store, journal = world
    store.write_text("a.txt", "BAD\n", manifest=writer("r1", "w1"))
    again = store.write_text("b.txt", "BAD\n", manifest=writer("r9", "w9"))
    read_artifact(journal, "r2", "c1", again.artifact_id, "BAD\n")
    result = attribute_lineage(root, root, "r2", bad)
    assert result.hops[0].ambiguous and result.origin is None
    assert "cannot tell which one was served" in result.origin_reason


def test_a_read_file_is_matched_to_the_version_whose_lines_it_returned(world):
    root, store, journal = world
    store.write_text("notes.txt", "ok\n", manifest=writer("r1", "w1"))
    store.write_text("notes.txt", "BAD\n", manifest=writer("r5", "w5", 7))
    read_file(journal, "r2", "c1", "notes.txt", ["BAD"])
    result = attribute_lineage(root, root, "r2", bad)
    assert result.origin.agent_run_id == "r5" and result.origin.iteration == 7


def test_a_cycle_depth_limit_and_bad_arguments_are_named(world, tmp_path):
    root, store, journal = world
    first = store.write_text("a.txt", "BAD\n", manifest=writer("r2", "w1"))
    read_artifact(journal, "r1", "c1", first.artifact_id, "BAD\n")
    second = store.write_text("b.txt", "BAD2 BAD\n", manifest=writer("r1", "w2"))
    read_artifact(journal, "r2", "c2", second.artifact_id, "BAD2 BAD\n")
    result = attribute_lineage(root, root, "r1", bad)
    assert "returned to run" in result.origin_reason
    with pytest.raises(ValueError, match="max_depth must be at least 1, got 0"):
        attribute_lineage(root, root, "r1", bad, max_depth=0)
    with pytest.raises(ValueError, match="holds no .agent-tool-results directory"):
        attribute_lineage(tmp_path / "x", root, "r1", bad)
