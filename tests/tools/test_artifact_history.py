import json
import shutil
import subprocess
import sys
import threading

import pytest

from nailong_agent_sdk.tools.artifacts import ArtifactStore
from tests.support.processes import child_environment
from tests.support.tools import build, call, run

WORKER = r"""
import sys
from pathlib import Path
from nailong_agent_sdk.tools.artifacts import ArtifactStore
root, rel, tag, count = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
store = ArtifactStore(Path(root))
for index in range(count):
    store.write_text(rel, f"{tag}{index}\n", manifest={"writer": tag})
"""


def test_versions_of_a_path_come_back_in_write_order_with_their_own_ordinals(tmp_path):
    store = ArtifactStore(tmp_path / "run")
    store.write_text("a.txt", "a1\n", manifest={"writer": "x"})
    store.write_text("b.txt", "b1\n", manifest={"writer": "y"})
    store.write_text("a.txt", "a2\n", manifest={"writer": "z"})
    store.write_text("b.txt", "b2\n")
    store.write_text("a.txt", "a2\n", manifest={"writer": "w"})
    versions = store.history("a.txt")
    assert [item.sequence for item in versions] == [1, 2, 3]
    assert [item.write_sequence for item in versions] == [1, 3, 5]
    assert [item.manifest.get("writer") for item in versions] == ["x", "z", "w"]
    assert (
        versions[1].sha256 == versions[2].sha256
        and versions[1].occurrence_id != versions[2].occurrence_id
    )
    assert [item.sequence for item in store.history("b.txt")] == [1, 2]
    assert store.history("never-written.txt") == []
    assert store.paths() == ["a.txt", "b.txt"]
    assert store.get_occurrence(versions[0].occurrence_id).write_sequence == 1


def test_another_spelling_of_the_same_file_is_the_same_history(tmp_path):
    store = ArtifactStore(tmp_path / "run")
    store.write_text("notes/b.md", "one")
    store.write_text("./notes/../notes/b.md", "two")
    assert [item.sequence for item in store.history("notes/b.md")] == [1, 2]
    assert len(store.history("./notes/b.md")) == 2


def test_the_content_of_any_version_is_read_back_and_checked(tmp_path):
    store = ArtifactStore(tmp_path / "run")
    store.write_text("a.txt", "first\n")
    store.write_text("a.txt", "second\n")
    first, second = store.history("a.txt")
    assert store.read_bytes(first.sha256) == b"first\n"
    assert store.read_bytes(second.sha256) == b"second\n"
    blob = tmp_path / "run" / ".agent-artifacts" / "content" / first.sha256
    blob.write_bytes(b"tampered\n")
    with pytest.raises(ValueError, match="now hashes to"):
        store.read_bytes(first.sha256)
    with pytest.raises(ValueError, match="is not a SHA-256 digest"):
        store.read_bytes("../escape")
    blob.unlink()
    with pytest.raises(ValueError, match="is not stored under"):
        store.read_bytes(first.sha256)


def test_a_read_only_store_reads_the_history_and_creates_and_writes_nothing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    absent = ArtifactStore(empty, read_only=True)
    assert absent.history("x.txt") == [] and absent.paths() == []
    assert list(empty.iterdir()) == []
    store = ArtifactStore(tmp_path / "run")
    store.write_text("x.txt", "v1")
    before = sorted(
        path.relative_to(tmp_path / "run").as_posix() for path in (tmp_path / "run").rglob("*")
    )
    viewer = ArtifactStore(tmp_path / "run", read_only=True)
    assert [item.sequence for item in viewer.history("x.txt")] == [1]
    with pytest.raises(RuntimeError, match='"write_text" is not available'):
        viewer.write_text("x.txt", "v2")
    with pytest.raises(RuntimeError, match='"register_existing" is not available'):
        viewer.register_existing("x.txt", kind="draft")
    after = sorted(
        path.relative_to(tmp_path / "run").as_posix() for path in (tmp_path / "run").rglob("*")
    )
    assert after == before


def test_a_store_written_before_the_log_existed_is_read_in_time_order_and_adopted_on_the_next_write(
    tmp_path,
):
    root = tmp_path / "run"
    store = ArtifactStore(root)
    for index in range(3):
        store.write_text("old.txt", f"v{index}\n", manifest={"n": index})
    store.write_text("other.txt", "o\n")
    shutil.rmtree(root / ".agent-artifacts" / "history")
    legacy = ArtifactStore(root, read_only=True)
    assert [item.manifest["n"] for item in legacy.history("old.txt")] == [0, 1, 2]
    assert [item.sequence for item in legacy.history("old.txt")] == [1, 2, 3]
    assert legacy.paths() == ["old.txt", "other.txt"]
    writer = ArtifactStore(root)
    writer.write_text("old.txt", "v3\n", manifest={"n": 3})
    adopted = ArtifactStore(root, read_only=True).history("old.txt")
    assert [item.manifest["n"] for item in adopted] == [0, 1, 2, 3]
    assert [item.sequence for item in adopted] == [1, 2, 3, 4]
    assert len({item.write_sequence for item in adopted}) == 4


def test_a_write_cut_short_in_the_log_is_ignored_when_reading_and_repaired_when_writing(tmp_path):
    root = tmp_path / "run"
    store = ArtifactStore(root)
    store.write_text("a.txt", "one\n")
    log = root / ".agent-artifacts" / "history" / "writes.jsonl"
    with log.open("ab") as stream:
        stream.write(b'{"write_sequence":2,"path_key":"a.txt","occ')
    assert [item.sequence for item in ArtifactStore(root, read_only=True).history("a.txt")] == [1]
    ArtifactStore(root).write_text("a.txt", "two\n")
    assert [item.write_sequence for item in ArtifactStore(root).history("a.txt")] == [1, 2]
    assert log.read_bytes().endswith(b"\n") and log.read_text("utf-8").count("\n") == 2


def test_a_log_line_that_is_not_a_write_record_is_named(tmp_path):
    root = tmp_path / "run"
    store = ArtifactStore(root)
    store.write_text("a.txt", "one\n")
    log = root / ".agent-artifacts" / "history" / "writes.jsonl"
    with log.open("ab") as stream:
        stream.write(b'"path_key":"a.txt" this is not json\n')
    with pytest.raises(ValueError, match='Line 2 of the artifact write log "writes.jsonl"'):
        ArtifactStore(root, read_only=True).history("a.txt")


def test_threads_with_their_own_store_instances_get_dense_unique_sequences(tmp_path):
    root = tmp_path / "run"
    ArtifactStore(root)
    errors = []

    def work(tag):
        store = ArtifactStore(root)
        try:
            for index in range(25):
                store.write_text("shared.txt", f"{tag}{index}\n", manifest={"writer": tag})
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")

    threads = [threading.Thread(target=work, args=(tag,)) for tag in "ABCD"]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=300)
    assert errors == []
    versions = ArtifactStore(root, read_only=True).history("shared.txt")
    assert [item.sequence for item in versions] == list(range(1, 101))
    assert [item.write_sequence for item in versions] == list(range(1, 101))
    per_writer = {tag: [i.manifest["writer"] for i in versions].count(tag) for tag in "ABCD"}
    assert per_writer == {"A": 25, "B": 25, "C": 25, "D": 25}


def test_processes_writing_one_path_get_dense_unique_sequences(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(WORKER, "utf-8")
    root = tmp_path / "run"
    ArtifactStore(root)
    processes = [
        subprocess.Popen(
            [sys.executable, str(script), str(root), "shared/out.txt", tag, "20"],
            env=child_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for tag in "ABC"
    ]
    for process in processes:
        _, err = process.communicate(timeout=300)
        assert process.returncode == 0, err[-300:]
    versions = ArtifactStore(root, read_only=True).history("shared/out.txt")
    assert [item.write_sequence for item in versions] == list(range(1, 61))
    assert sorted(item.manifest["writer"] for item in versions) == sorted("ABC" * 20)


def test_a_tool_write_records_which_agent_wrote_it_and_from_which_call(tmp_path):
    executor, context = build(tmp_path, declared=("out/draft.md",))
    run(call(executor, "write_draft", path="out/draft.md", content="v1\n"))
    run(call(executor, "edit_draft", path="out/draft.md", old_text="v1", new_text="v2"))
    first, second = context.artifacts.history("out/draft.md")
    assert first.manifest == {
        "run_id": "run-1",
        "node_id": "node-1",
        "task_id": "T1",
        "agent_identity": "a",
        "agent_run_id": "T1",
        "role": "worker",
        "tool": "write_draft",
        "tool_call_id": "c1",
        "iteration": 1,
    }
    assert second.manifest["tool"] == "edit_draft" and second.sequence == 2
    assert context.artifacts.read_bytes(second.sha256) == b"v2\n"


def test_the_log_is_compact_json_one_line_per_write(tmp_path):
    root = tmp_path / "run"
    store = ArtifactStore(root)
    store.write_text("café/文件.txt", "x")
    store.write_text("b.txt", "y")
    lines = (root / ".agent-artifacts" / "history" / "writes.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["relative_path"] == "café/文件.txt"
    assert [json.loads(line)["write_sequence"] for line in lines] == [1, 2]
