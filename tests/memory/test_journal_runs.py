import pytest

from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
from nailong_agent_sdk.memory.context_projection import (
    FileToolResultJournal,
    InMemoryToolResultJournal,
    journal_content_hash,
    journal_for_run,
)


def call(call_id="c1"):
    return ToolCall(id=call_id, name="read_file", arguments={"path": "a.md"})


def result(text="x"):
    return ToolExecutionResult(status="succeeded", output={"text": text})


def test_a_journal_for_a_run_indexes_the_handles_it_records(tmp_path):
    journal = FileToolResultJournal(tmp_path)
    first = journal.for_run("run-1").record(call("c1"), result("one"))
    unscoped = journal.record(call("c2"), result("two"))
    second = journal.for_run("run-1").record(call("c3"), result("three"))
    other = journal.for_run("run-2").record(call("c4"), result("four"))
    assert journal.handles_of("run-1") == [first.handle_id, second.handle_id]
    assert journal.handles_of("run-2") == [other.handle_id]
    assert journal.handles_of("nobody") == []
    assert unscoped.handle_id not in journal.handles_of("run-1")
    assert journal.read(first.handle_id)["result"]["output"] == {"text": "one"}


def test_scoping_a_run_changes_neither_the_stored_payload_nor_its_hash(tmp_path):
    journal = FileToolResultJournal(tmp_path)
    plain = journal.record(call(), result("same"))
    scoped = journal.for_run("run-1").record(call(), result("same"))
    root = tmp_path / ".agent-tool-results"
    assert (root / f"{plain.handle_id}.json").read_text("utf-8") == (
        root / f"{scoped.handle_id}.json"
    ).read_text("utf-8")
    assert plain.content_hash == scoped.content_hash == journal_content_hash(root, scoped.handle_id)


def test_a_run_index_survives_a_restart_and_is_shared_between_instances(tmp_path):
    writer = FileToolResultJournal(tmp_path)
    handle = writer.for_run("run/with:odd chars").record(call(), result())
    reader = FileToolResultJournal(tmp_path)
    assert reader.handles_of("run/with:odd chars") == [handle.handle_id]
    other = FileToolResultJournal(tmp_path).for_run("run/with:odd chars").record(call(), result())
    assert reader.handles_of("run/with:odd chars") == [handle.handle_id, other.handle_id]


def test_the_in_memory_journal_needs_no_run_scope():
    journal = InMemoryToolResultJournal()
    assert journal.for_run("run-1") is journal
    assert journal_for_run(journal, "run-1") is journal


def test_a_journal_without_scoping_is_used_as_is():
    class Plain:
        def record(self, call, result): ...
        def read(self, handle_id): ...

    plain = Plain()
    assert journal_for_run(plain, "run-1") is plain


def test_the_journal_footprint_lists_each_handle_with_its_hash_and_size(tmp_path):
    journal = FileToolResultJournal(tmp_path)
    recorded = [journal.for_run("run-1").record(call(f"c{i}"), result("y" * i)) for i in range(3)]
    footprint = journal.footprint("run-1")
    assert [item.handle_id for item in footprint.handles] == [h.handle_id for h in recorded]
    assert [item.content_hash for item in footprint.handles] == [h.content_hash for h in recorded]
    root = tmp_path / ".agent-tool-results"
    assert footprint.bytes == sum((root / f"{h.handle_id}.json").stat().st_size for h in recorded)
    assert [item.byte_count for item in footprint.handles] == [h.byte_count for h in recorded]
    empty = journal.footprint("nobody")
    assert empty.handles == [] and empty.bytes == 0


def test_pruning_a_run_deletes_its_handles_and_index_and_never_reuses_a_handle_number(tmp_path):
    journal = FileToolResultJournal(tmp_path)
    keep = journal.for_run("keep").record(call("k"), result("keep"))
    old = [journal.for_run("old").record(call(f"c{i}"), result(str(i))) for i in range(3)]
    dry = journal.prune_run("old", dry_run=True)
    assert [h.handle_id for h in dry.handles] == [h.handle_id for h in old]
    root = tmp_path / ".agent-tool-results"
    assert all((root / f"{h.handle_id}.json").is_file() for h in old)
    removed = journal.prune_run("old")
    assert removed == dry
    assert not any((root / f"{h.handle_id}.json").exists() for h in old)
    assert journal.handles_of("old") == [] and journal.handles_of("keep") == [keep.handle_id]
    with pytest.raises(ValueError, match="has no journal file"):
        journal_content_hash(root, old[-1].handle_id)
    assert journal.read(keep.handle_id)["result"]["output"] == {"text": "keep"}
    highest_removed = max(int(h.handle_id.removeprefix("result-")) for h in old)
    for journal_instance in (journal, FileToolResultJournal(tmp_path)):
        issued = journal_instance.record(call("z"), result("fresh"))
        assert int(issued.handle_id.removeprefix("result-")) > highest_removed


def test_pruning_a_run_with_nothing_recorded_is_a_no_op(tmp_path):
    journal = FileToolResultJournal(tmp_path)
    nothing = journal.prune_run("nobody")
    assert nothing.handles == [] and nothing.bytes == 0
    assert journal.prune_run("nobody", dry_run=True) == nothing
