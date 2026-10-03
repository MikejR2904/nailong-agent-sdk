"""Crash recovery, cross-process locking, and incremental run-history saves."""

from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from nailong_agent_sdk.foundations.file_lock import FileLock
from nailong_agent_sdk.state.project_state_models import (
    StageStateSchema,
    StateAuthority,
    StateEvidence,
    StateTransition,
    StateTransitionKind,
)
from nailong_agent_sdk.state.project_state_store import FileProjectStateStore

SCHEMA = StageStateSchema(schema_id="rtl-v1", stage="rtl")


def _transition(index: int) -> StateTransition:
    return StateTransition(
        kind=StateTransitionKind.WORK_ITEM_UPDATED,
        actor=StateAuthority.CONTROLLER,
        action_id=f"a{index}",
        payload={"work_item_id": f"w{index % 5}", "status": "in-progress", "owner": "ctl"},
        evidence=[StateEvidence(evidence_id=f"e{index}", kind="test")],
    )


# --- project state: crash between the event write and the state write ------------


def test_crash_after_event_write_rolls_forward(tmp_path, monkeypatch):
    store = FileProjectStateStore(tmp_path)
    store.ensure("p", SCHEMA)
    store.apply("p", _transition(1))

    original = FileProjectStateStore._write_json

    def die_on_state_write(path: Path, value):
        if path.parent.name == ".agent-project-state":
            raise KeyboardInterrupt("process killed")
        original(path, value)

    monkeypatch.setattr(FileProjectStateStore, "_write_json", staticmethod(die_on_state_write))
    with pytest.raises(KeyboardInterrupt):
        store.apply("p", _transition(2), summary_max_chars=512)
    monkeypatch.setattr(FileProjectStateStore, "_write_json", staticmethod(original))

    recovered = FileProjectStateStore(tmp_path).load("p")  # a fresh process
    assert recovered.revision == 2
    assert recovered.state_hash == FileProjectStateStore(tmp_path).events("p")[-1].state_hash
    assert FileProjectStateStore(tmp_path).apply("p", _transition(3)).revision == 3


def _crash_after_event_write(store, monkeypatch, transition):
    original = FileProjectStateStore._write_json

    def die_on_state_write(path: Path, value):
        if path.parent.name == ".agent-project-state":
            raise KeyboardInterrupt("process killed")
        original(path, value)

    monkeypatch.setattr(FileProjectStateStore, "_write_json", staticmethod(die_on_state_write))
    with pytest.raises(KeyboardInterrupt):
        store.apply("p", transition)
    monkeypatch.setattr(FileProjectStateStore, "_write_json", staticmethod(original))


def test_altered_event_is_not_replayed(tmp_path, monkeypatch):
    import json

    store = FileProjectStateStore(tmp_path)
    store.ensure("p", SCHEMA)
    store.apply("p", _transition(1))
    _crash_after_event_write(store, monkeypatch, _transition(2))
    event_dir = next(p for p in (tmp_path / ".agent-project-state").iterdir() if p.is_dir())
    last = sorted(event_dir.glob("*.json"))[-1]
    event = json.loads(last.read_text())
    event["payload"]["owner"] = "someone-else"  # payload is outside event_hash
    last.write_text(json.dumps(event))
    with pytest.raises(ValueError, match="did not reproduce"):
        FileProjectStateStore(tmp_path).load("p")


def test_legacy_event_without_replay_data_explains_the_fix(tmp_path):
    import json

    store = FileProjectStateStore(tmp_path)
    store.ensure("p", SCHEMA)
    before = next((tmp_path / ".agent-project-state").glob("*.json")).read_text()
    store.apply("p", _transition(1))
    event_file = next(
        next(p for p in (tmp_path / ".agent-project-state").iterdir() if p.is_dir()).glob("*.json")
    )
    legacy = json.loads(event_file.read_text())
    legacy.pop("payload")
    legacy.pop("summary_max_chars")
    event_file.write_text(json.dumps(legacy))
    next((tmp_path / ".agent-project-state").glob("*.json")).write_text(before)
    with pytest.raises(ValueError, match="Remove the last event file"):
        FileProjectStateStore(tmp_path).load("p")


# --- cross-process locking ----------------------------------------------------------


def _apply_many(root: str, start: int, count: int) -> None:
    store = FileProjectStateStore(Path(root))
    for index in range(start, start + count):
        store.apply("shared", _transition(index))


def test_concurrent_processes_never_lose_or_fork_revisions(tmp_path):
    FileProjectStateStore(tmp_path).ensure("shared", SCHEMA)
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(target=_apply_many, args=(str(tmp_path), 100 * n, 15)) for n in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(120)
        assert worker.exitcode == 0
    store = FileProjectStateStore(tmp_path)
    assert store.load("shared").revision == 60
    assert [event.revision for event in store.events("shared")] == list(range(1, 61))


def _increment(path: str, times: int) -> None:
    target = Path(path)
    lock = FileLock(target)
    for _ in range(times):
        with lock.hold():
            value = int(target.read_text())
            time.sleep(0.0005)
            target.write_text(str(value + 1))


def test_file_lock_serializes_processes_and_is_reentrant(tmp_path):
    counter = tmp_path / "counter"
    counter.write_text("0")
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=_increment, args=(str(counter), 40)) for _ in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(120)
    assert int(counter.read_text()) == 120

    lock = FileLock(counter)
    with lock.hold(), lock.hold():  # re-entrant within one thread
        pass


def test_file_lock_times_out_with_a_clear_error(tmp_path):
    from nailong_agent_sdk.foundations.errors import AgentSdkError

    target = tmp_path / "busy"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    holder = context.Process(target=_hold_lock, args=(str(target), ready))
    holder.start()
    assert ready.wait(30)
    with pytest.raises(AgentSdkError, match="STORE_LOCK_TIMEOUT|Could not acquire"):
        with FileLock(target, timeout_seconds=0.3).hold():
            pass
    holder.terminate()
    holder.join()


def _hold_lock(path: str, ready) -> None:
    with FileLock(Path(path)).hold():
        ready.set()
        time.sleep(30)


# --- run history: saves are incremental --------------------------------------------


def _plan():
    from nailong_agent_sdk.state.planning import ModelTier, Plan, PlanTask

    return Plan(
        plan_id="p1",
        tasks=[
            PlanTask(
                task_id="t1",
                scope="s",
                locked_interface={},
                instructions="i",
                acceptance_criteria="a",
                model_tier=ModelTier.STANDARD,
            )
        ],
    )


def test_run_saves_do_not_reread_history_and_survive_torn_appends(tmp_path, monkeypatch):
    from nailong_agent_sdk.state import run_state_store
    from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator

    coordinator = HarnessCoordinator(tmp_path)
    run = coordinator.start_run(_plan())
    for _ in range(3):
        coordinator.cancel_run(run.run_id)  # each save appends graph events

    def no_reads(*args, **kwargs):
        raise AssertionError("save re-read the history sidecar")

    monkeypatch.setattr(run_state_store, "_read_history_prefix", no_reads)
    coordinator.cancel_run(run.run_id)
    monkeypatch.undo()

    history = tmp_path / ".agent-runs" / f"{run.run_id}.history.jsonl"
    with history.open("ab") as handle:  # an append whose snapshot never published
        handle.write(b'{"kind":"event","value":{"torn":true}}\n{"kind":"ev')
    restarted = HarnessCoordinator(tmp_path)
    record = restarted.get_run_state(run.run_id)
    assert not any(event.get("torn") for event in record.graph["events"])
    restarted.cancel_run(run.run_id)  # truncates the uncommitted tail, then saves
    assert b"torn" not in history.read_bytes()
    assert HarnessCoordinator(tmp_path).get_run_state(run.run_id).run_hash
    assert os.path.getsize(history) > 0
