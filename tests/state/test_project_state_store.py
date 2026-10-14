import subprocess
import sys
import textwrap

import pytest

from nailong_agent_sdk.state.project_state_engine import ProjectStateProjector
from nailong_agent_sdk.state.project_state_models import ProjectStateProjectionPolicy
from nailong_agent_sdk.state.project_state_store import (
    FileProjectStateStore,
    InMemoryProjectStateStore,
)
from tests.support.processes import child_environment
from tests.support.project_state import SCHEMA, T, transition


def test_file_project_state_store_roundtrip_hash_chain_and_tamper(tmp_path):
    store = FileProjectStateStore(tmp_path)
    store.ensure("p", SCHEMA)
    for i in range(5):
        store.apply(
            "p",
            transition(
                T.QUESTION_OPENED,
                {"question_id": f"q{i}", "content": "?", "owner": "human"},
                action=f"q{i}",
            ),
        )
    fresh = FileProjectStateStore(tmp_path)
    state = fresh.load("p")
    assert state.revision == 5 and len(fresh.events("p")) == 5
    import hashlib

    key = hashlib.sha256(b"p").hexdigest()
    event_file = tmp_path / ".agent-project-state" / key / "00000003.json"
    original = event_file.read_text("utf-8")
    event_file.write_text(original.replace('"revision":3', '"revision":4'), "utf-8")
    with pytest.raises(Exception):
        FileProjectStateStore(tmp_path).load("p")
    event_file.write_text(original, "utf-8")
    event_file.unlink()
    with pytest.raises(ValueError, match="is at revision 5 but 4 events are persisted"):
        FileProjectStateStore(tmp_path).load("p")


def test_crash_between_event_and_state_write_does_not_brick_the_project(tmp_path, monkeypatch):
    store = FileProjectStateStore(tmp_path)
    store.ensure("p", SCHEMA)
    store.apply(
        "p",
        transition(
            T.QUESTION_OPENED, {"question_id": "q0", "content": "?", "owner": "human"}, action="q0"
        ),
    )
    real = FileProjectStateStore._write_json

    def failing(path, value):
        if path.parent.name == ".agent-project-state" and path.suffix == ".json":
            raise OSError("simulated crash before state write")
        real(path, value)

    monkeypatch.setattr(FileProjectStateStore, "_write_json", staticmethod(failing))
    with pytest.raises(OSError):
        store.apply(
            "p",
            transition(
                T.QUESTION_OPENED,
                {"question_id": "q1", "content": "?", "owner": "human"},
                action="q1",
            ),
        )
    monkeypatch.undo()
    try:
        fresh = FileProjectStateStore(tmp_path)
        fresh.load("p")
        fresh.apply(
            "p",
            transition(
                T.QUESTION_OPENED,
                {"question_id": "q1", "content": "?", "owner": "human"},
                action="q1",
            ),
        )
        outcome = f"recovered at revision {fresh.load('p').revision}"
    except Exception as error:
        outcome = f"{type(error).__name__}: {error}"
    assert outcome.startswith("recovered"), outcome


def test_concurrent_reader_never_sees_a_spurious_integrity_failure(tmp_path):
    writer = textwrap.dedent(
        f"""
        import sys, time
        from pathlib import Path
        from tests.support.project_state import SCHEMA, T, transition
        from nailong_agent_sdk.state.project_state_store import FileProjectStateStore
        store = FileProjectStateStore(Path(r"{tmp_path}"))
        store.ensure("p", SCHEMA)
        print("ready", flush=True)
        for i in range(120):
            payload = {{"question_id": f"q{{i}}", "content": "?", "owner": "human"}}
            store.apply("p", transition(T.QUESTION_OPENED, payload, action=f"q{{i}}"))
        print("done", flush=True)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", writer],
        env=child_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout.readline().strip() == "ready"
    reader = FileProjectStateStore(tmp_path)
    errors, reads = [], 0
    while proc.poll() is None:
        try:
            reader.load("p")
            reads += 1
        except Exception as error:
            errors.append(f"{type(error).__name__}: {str(error)[:100]}")
    stdout, stderr = proc.communicate(timeout=120)
    assert proc.returncode == 0 and "done" in stdout, stderr[-500:]
    assert errors == [], errors[:2]
    assert reads > 0


def test_projection_budget_behaviour():
    store = InMemoryProjectStateStore()
    store.ensure("p", SCHEMA)
    for i in range(60):
        store.apply(
            "p",
            transition(
                T.TOOL_OUTCOME,
                {
                    "tool_call_id": f"c{i}",
                    "tool_name": "write_draft",
                    "status": "succeeded",
                    "artifact": {
                        "relative_path": f"dir/file-{i}.txt",
                        "artifact_id": f"sha256:{i:064d}",
                        "status": "in-progress",
                    },
                },
                action=f"c{i}",
            ),
        )
    state = store.load("p")
    view = ProjectStateProjector(ProjectStateProjectionPolicy(token_budget=400)).project(state)
    assert view.over_budget is False and view.omitted_artifact_count > 0
    assert view.artifacts[-1].relative_path == "dir/file-59.txt"
    tiny = ProjectStateProjector(ProjectStateProjectionPolicy(token_budget=128)).project(state)
    assert tiny.estimated_tokens <= 128 or tiny.over_budget
