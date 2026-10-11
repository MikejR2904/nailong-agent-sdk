import threading
from contextlib import contextmanager

from nailong_agent_sdk.agent.orchestrator import Orchestrator
from nailong_agent_sdk.foundations import record_locks
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.orchestration import ControllerStateStore
from nailong_agent_sdk.state.orchestration_models import ControllerPhase
from nailong_agent_sdk.state.project_state_store import FileProjectStateStore
from nailong_agent_sdk.state.run_state_store import RunStateStore
from tests.support.agents import arun
from tests.support.controllers import new_controller
from tests.support.orchestration import make_policy, make_request
from tests.support.plans import ok, simple_plan
from tests.support.processes import RENDEZVOUS, meeting, run_workers

PROJECT_WRITER = (
    RENDEZVOUS
    + """
import sys
from pathlib import Path

from nailong_agent_sdk.state.project_state_store import FileProjectStateStore
from tests.support.project_state import SCHEMA, T, transition

worker, root = int(sys.argv[1]), Path(sys.argv[2])
ready, peers, count = sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
store = FileProjectStateStore(root)
store.ensure("shared", SCHEMA)
rendezvous(ready, worker, peers)
for index in range(count):
    key = f"w{worker}-{index}"
    payload = {"question_id": key, "content": "?", "owner": "human"}
    store.apply("shared", transition(T.QUESTION_OPENED, payload, action=key))
print("done")
"""
)

RUN_WRITER = (
    RENDEZVOUS
    + """
import sys
from pathlib import Path

from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.shared_state import SharedStateWrite

worker, root = int(sys.argv[1]), Path(sys.argv[2])
ready, peers = sys.argv[3], int(sys.argv[4])
run_id, count = sys.argv[5], int(sys.argv[6])
coordinator = HarnessCoordinator(root)
coordinator.get_run_state(run_id)
rendezvous(ready, worker, peers)
for index in range(count):
    key = f"w{worker}-{index}"
    write = SharedStateWrite(
        producer_node_id="node:T1", key=key, value={"i": index}, provenance_hash="h"
    )
    coordinator.write_shared_value(run_id, write)
print("done")
"""
)

CONTROLLER_WRITER = (
    RENDEZVOUS
    + """
import sys
from pathlib import Path

from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from tests.support.plans import simple_plan

worker, root = int(sys.argv[1]), Path(sys.argv[2])
ready, peers = sys.argv[3], int(sys.argv[4])
controller_id, cycles = sys.argv[5], int(sys.argv[6])
runtime = ControllerRuntime(root)
runtime.get_controller(controller_id)
rendezvous(ready, worker, peers)
succeeded = 0
for _ in range(cycles):
    for command in (
        lambda: runtime.submit_plan(controller_id, simple_plan()),
        lambda: runtime.approve_plan(controller_id, False),
    ):
        try:
            command()
            succeeded += 1
        except ValueError:
            pass
print(succeeded)
"""
)

SUBMITTER = (
    RENDEZVOUS
    + """
import sys
from pathlib import Path

from nailong_agent_sdk.agent.orchestrator import Orchestrator

worker, root = int(sys.argv[1]), Path(sys.argv[2])
ready, peers = sys.argv[3], int(sys.argv[4])
orchestration_id = sys.argv[5]
orchestrator = Orchestrator.resume(root, orchestration_id)
rendezvous(ready, worker, peers)
try:
    orchestrator.submit_for_approval(orchestration_id)
    print("submitted")
except Exception as error:
    print(f"{type(error).__name__}: {error}")
"""
)


def outputs_of(results):
    for code, _out, err in results:
        assert code == 0, err
    return [out.strip() for _code, out, _err in results]


def waiting_signal(monkeypatch, thread_name):
    reached = threading.Event()
    real = record_locks.exclusive_file_lock

    @contextmanager
    def probe(path, **options):
        if threading.current_thread().name == thread_name:
            reached.set()
        with real(path, **options):
            yield

    monkeypatch.setattr(record_locks, "exclusive_file_lock", probe)
    return reached


def test_processes_applying_transitions_to_one_project_lose_none(tmp_path):
    workers, count = 4, 20
    results = run_workers(
        PROJECT_WRITER, workers, str(tmp_path), *meeting(tmp_path, workers), str(count)
    )
    assert outputs_of(results) == ["done"] * workers
    state = FileProjectStateStore(tmp_path).load("shared")
    assert state.revision == workers * count
    assert len(state.open_questions) == workers * count


def test_processes_writing_one_run_lose_no_shared_value(tmp_path):
    coordinator = HarnessCoordinator(tmp_path)
    run_id = coordinator.start_run(simple_plan()).run_id
    coordinator.record_node_result(run_id, "node:T1", ok())
    workers, count = 4, 8
    results = run_workers(
        RUN_WRITER, workers, str(tmp_path), *meeting(tmp_path, workers), run_id, str(count)
    )
    assert outputs_of(results) == ["done"] * workers
    values = HarnessCoordinator(tmp_path).get_run_state(run_id).graph["shared_state"]["values"]
    assert set(values) == {f"w{w}-{i}" for w in range(workers) for i in range(count)}


def test_processes_driving_one_controller_keep_every_event(tmp_path):
    runtime = ControllerRuntime(tmp_path)
    controller_id = new_controller(runtime).controller_id
    started = len(runtime.get_controller(controller_id).events)
    workers, cycles = 3, 8
    results = run_workers(
        CONTROLLER_WRITER,
        workers,
        str(tmp_path),
        *meeting(tmp_path, workers),
        controller_id,
        str(cycles),
    )
    succeeded = sum(int(out) for out in outputs_of(results))
    stored = ControllerStateStore(tmp_path).load(controller_id)
    assert succeeded > 0
    assert len(stored.events) == started + succeeded


def test_two_processes_submitting_one_orchestration_create_one_controller(tmp_path):
    orchestrator = Orchestrator(tmp_path, make_policy())
    orchestration_id = arun(orchestrator.prepare(make_request())).orchestration_id
    results = run_workers(SUBMITTER, 2, str(tmp_path), *meeting(tmp_path, 2), orchestration_id)
    assert sorted(outputs_of(results)) == [
        "ValueError: Only a prepared orchestration can be submitted for approval.",
        "submitted",
    ]
    assert len(list((tmp_path / ".agent-controllers").glob("controller-*.json"))) == 1


def test_a_run_save_checks_for_a_concurrent_change_only_after_it_holds_the_lock(
    tmp_path, monkeypatch
):
    run_id = HarnessCoordinator(tmp_path).start_run(simple_plan()).run_id
    stale_store = RunStateStore(tmp_path)
    stale = stale_store.load(run_id)
    reached = waiting_signal(monkeypatch, "late-writer")
    outcome = {}

    def late_writer():
        try:
            stale_store.save(stale)
        except AgentSdkError as error:
            outcome["code"] = error.code

    late = threading.Thread(target=late_writer, name="late-writer")
    with RunStateStore(tmp_path).locked(run_id):
        late.start()
        assert reached.wait(timeout=30)
        HarnessCoordinator(tmp_path).cancel_run(run_id)
    late.join(timeout=60)
    assert outcome == {"code": "RUN_STATE_CONFLICT"}
    assert HarnessCoordinator(tmp_path).get_run_state(run_id).cancelled


def test_a_controller_save_checks_for_a_concurrent_change_only_after_it_holds_the_lock(
    tmp_path, monkeypatch
):
    controller_id = new_controller(ControllerRuntime(tmp_path)).controller_id
    stale_store = ControllerStateStore(tmp_path)
    stale = stale_store.load(controller_id)
    reached = waiting_signal(monkeypatch, "late-writer")
    outcome = {}

    def late_writer():
        try:
            stale_store.save(stale)
        except AgentSdkError as error:
            outcome["code"] = error.code

    late = threading.Thread(target=late_writer, name="late-writer")
    with ControllerStateStore(tmp_path).locked(controller_id):
        late.start()
        assert reached.wait(timeout=30)
        ControllerRuntime(tmp_path).submit_plan(controller_id, simple_plan())
    late.join(timeout=60)
    assert outcome == {"code": "CONTROLLER_STATE_CONFLICT"}
    phase = ControllerStateStore(tmp_path).load(controller_id).phase
    assert phase is ControllerPhase.AWAITING_PLAN_APPROVAL


def test_an_orchestration_save_checks_for_a_concurrent_change_only_after_it_holds_the_lock(
    tmp_path, monkeypatch
):
    orchestrator = Orchestrator(tmp_path, make_policy())
    record = arun(orchestrator.prepare(make_request()))
    stale_store = orchestrator._store
    stale = stale_store.load(record.orchestration_id)
    reached = waiting_signal(monkeypatch, "late-writer")
    outcome = {}

    def late_writer():
        try:
            stale_store.save(stale)
        except AgentSdkError as error:
            outcome["code"] = error.code

    late = threading.Thread(target=late_writer, name="late-writer")
    with orchestrator._store.locked(record.orchestration_id):
        late.start()
        assert reached.wait(timeout=30)
        Orchestrator.resume(tmp_path, record.orchestration_id).submit_for_approval(
            record.orchestration_id
        )
    late.join(timeout=60)
    assert outcome == {"code": "ORCHESTRATION_STATE_CONFLICT"}
    assert orchestrator.get(record.orchestration_id).controller_id is not None


def test_a_controller_runtime_sees_a_change_another_runtime_made(tmp_path):
    first, second = ControllerRuntime(tmp_path), ControllerRuntime(tmp_path)
    controller_id = new_controller(first).controller_id
    assert second.get_controller(controller_id).phase is ControllerPhase.PLANNING
    first.submit_plan(controller_id, simple_plan())
    assert second.get_controller(controller_id).phase is ControllerPhase.AWAITING_PLAN_APPROVAL
    second.approve_plan(controller_id, True)
    assert first.get_controller(controller_id).phase is ControllerPhase.DISPATCH_READY
