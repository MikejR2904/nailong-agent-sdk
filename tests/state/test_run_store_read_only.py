import pytest

from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.run_state_store import RunStateStore
from tests.support.diagnosis_runs import fingerprints
from tests.support.plans import AGENT, arun, run_ok, simple_plan


def test_a_read_only_store_over_an_empty_root_creates_nothing(tmp_path):
    store = RunStateStore(tmp_path, read_only=True)
    assert store.exists("run-1") is False
    assert list(tmp_path.iterdir()) == []


def test_a_read_only_store_loads_an_existing_run_and_changes_no_file(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    started = coordinator.start_run(simple_plan())
    arun(coordinator.execute_run(started.run_id, {AGENT: run_ok}))
    before = fingerprints(tmp_path)
    store = RunStateStore(tmp_path, read_only=True)
    record = store.load(started.run_id)
    assert record.run_id == started.run_id and record.graph["statuses"]
    assert fingerprints(tmp_path) == before


def test_a_read_only_store_refuses_to_reserve_or_save_naming_the_operation(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    started = coordinator.start_run(simple_plan())
    store = RunStateStore(tmp_path, read_only=True)
    with pytest.raises(RuntimeError, match='"reserve_run_id" is not available'):
        store.reserve_run_id()
    with pytest.raises(RuntimeError, match='"save" is not available'):
        store.save(store.load(started.run_id))
