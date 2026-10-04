import json
import subprocess
import sys
import threading
import time

import pytest

from nailong_agent_sdk.tools.artifacts import ArtifactStore
from tests.support.processes import child_environment

WORKER = r"""
import json, sys
from pathlib import Path
from nailong_agent_sdk.tools.artifacts import ArtifactStore
root, rel, tag = sys.argv[1], sys.argv[2], sys.argv[3]
count, size = int(sys.argv[4]), int(sys.argv[5])
store = ArtifactStore(Path(root))
errors = []
content = tag * size
for index in range(count):
    try:
        record = store.write_text(rel, content)
        if store.read_text(record.artifact_id) != content:
            errors.append(["readback-mismatch", index])
    except Exception as error:
        errors.append([type(error).__name__, str(error)[:160]])
print(json.dumps(errors))
"""


def _spawn(root, rel, tag, count, size, script):
    return subprocess.Popen(
        [sys.executable, str(script), str(root), rel, tag, str(count), str(size)],
        env=child_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_roundtrip_dedupe_and_unique_occurrences(tmp_path):
    store = ArtifactStore(tmp_path / "run")
    first = store.write_text("a/b.txt", "héllo\n")
    second = store.write_text("c.txt", "héllo\n")
    assert first.artifact_id == second.artifact_id == f"sha256:{first.sha256}"
    assert first.occurrence_id != second.occurrence_id
    assert store.read_text(first.artifact_id) == "héllo\n"
    occurrence = store.get_occurrence(first.occurrence_id)
    assert (
        occurrence.relative_path == "a/b.txt"
        and store.get_occurrence(second.occurrence_id).relative_path == "c.txt"
    )
    with pytest.raises(ValueError, match='Artifact "sha256:nope" is unknown'):
        store.read_text("sha256:nope")
    for bad in ("../x", "/abs", "", "  "):
        with pytest.raises(ValueError):
            store.write_text(bad, "x")


def test_content_tamper_is_detected_with_artifact_id(tmp_path):
    store = ArtifactStore(tmp_path / "run")
    record = store.write_text("a.txt", "original")
    blob = tmp_path / "run" / ".agent-artifacts" / "content" / record.sha256
    blob.write_text("tampered", "utf-8")
    with pytest.raises(ValueError, match="content hash no longer matches"):
        store.read_text(record.artifact_id)


def test_occurrence_lookup_cannot_leave_the_occurrence_directory(tmp_path):
    store = ArtifactStore(tmp_path / "run")
    forged = {
        "occurrence_id": "occ-x",
        "artifact_id": "sha256:abc",
        "relative_path": "p",
        "sha256": "abc",
        "size_bytes": 0,
        "kind": "draft",
        "manifest": {},
        "written_at_utc": "2026-01-01T00:00:00+00:00",
    }
    (tmp_path / "run" / "model_written.json").write_text(json.dumps(forged), "utf-8")
    found = store.get_occurrence("../../model_written")
    assert found is None


def test_manifest_lookup_cannot_leave_the_manifest_directory(tmp_path):
    store = ArtifactStore(tmp_path / "run")
    record = {
        "artifact_id": "x",
        "relative_path": "p.txt",
        "sha256": "abc",
        "size_bytes": 0,
        "kind": "draft",
        "manifest": {},
    }
    (tmp_path / "outside_record.json").write_text(json.dumps(record), "utf-8")
    found = store.get("../../outside_record")
    assert found is None


def test_multiprocess_writers_to_the_same_path_stay_consistent(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(WORKER, "utf-8")
    root = tmp_path / "run"
    ArtifactStore(root)
    procs = [_spawn(root, "shared/out.txt", tag, 25, 400_000, script) for tag in "ABC"]
    reports = []
    for proc in procs:
        out, err = proc.communicate(timeout=300)
        reports.append((proc.returncode, out.strip(), err.strip()[-300:]))
    errors = []
    for code, out, err in reports:
        assert code == 0, err
        errors.extend(json.loads(out))
    final = (root / "shared" / "out.txt").read_text("utf-8")
    whole = final in {tag * 400_000 for tag in "ABC"}
    assert errors == [], errors[:3]
    assert whole


def test_threaded_writers_with_separate_store_instances(tmp_path):
    root = tmp_path / "run"
    ArtifactStore(root)
    errors = []

    def work(tag):
        store = ArtifactStore(root)
        content = tag * 300_000
        for _ in range(30):
            try:
                record = store.write_text("shared.txt", content)
                if store.read_text(record.artifact_id) != content:
                    errors.append(("mismatch", tag))
            except Exception as error:
                errors.append((type(error).__name__, str(error)[:120]))

    threads = [threading.Thread(target=work, args=(tag,)) for tag in "ABCD"]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=300)
    assert errors == [], errors[:3]


def test_killed_writer_never_leaves_a_partial_target(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(WORKER, "utf-8")
    root = tmp_path / "run"
    ArtifactStore(root).write_text("t.txt", "OLD" * 100)
    states = set()
    for round_index in range(6):
        proc = _spawn(root, "t.txt", "N", 400, 3_000_000, script)
        time.sleep(0.4 + 0.15 * round_index)
        proc.kill()
        proc.communicate(timeout=60)
        content = (root / "t.txt").read_text("utf-8")
        states.add(
            "old"
            if content == "OLD" * 100
            else "new"
            if content == "N" * 3_000_000
            else f"PARTIAL:{len(content)}"
        )
    assert not any(state.startswith("PARTIAL") for state in states), states


def test_large_artifact_roundtrip_speed(tmp_path):
    store = ArtifactStore(tmp_path / "run")
    content = "line of text for the artifact store\n" * 300_000
    record = store.write_text("big.txt", content)
    assert store.read_text(record.artifact_id) == content
    diff = store.diff(record.artifact_id, record.artifact_id)
    assert diff["unified_diff"] == ""
