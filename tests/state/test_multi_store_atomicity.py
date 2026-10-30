import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nailong_agent_sdk.agent.orchestrator import OrchestrationStatus, Orchestrator
from nailong_agent_sdk.agent.orchestrator.state_store import OrchestrationStateStore
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.orchestration import ControllerStateStore
from nailong_agent_sdk.state.orchestration_models import ControllerPhase
from nailong_agent_sdk.state.project_state_store import FileProjectStateStore
from nailong_agent_sdk.state.run_state_store import RunStateStore
from tests.support.agents import arun
from tests.support.controllers import executing_runtime, new_controller
from tests.support.orchestration import make_factory, make_policy, make_request
from tests.support.plans import ok, simple_plan

WRITE_POINTS = (
    (RunStateStore, "save"),
    (ControllerStateStore, "save"),
    (OrchestrationStateStore, "save"),
    (FileProjectStateStore, "apply"),
)

PHASES_OF = {
    OrchestrationStatus.PREPARED: {None, ControllerPhase.PLANNING},
    OrchestrationStatus.AWAITING_PLAN_APPROVAL: {ControllerPhase.AWAITING_PLAN_APPROVAL},
    OrchestrationStatus.APPROVED: {ControllerPhase.DISPATCH_READY},
    OrchestrationStatus.DISPATCHED: {
        ControllerPhase.EXECUTING,
        ControllerPhase.REPAIR_REQUIRED,
        ControllerPhase.ESCALATED,
    },
    OrchestrationStatus.EXECUTED: {ControllerPhase.EXECUTING, ControllerPhase.COMPLETED},
    OrchestrationStatus.FAILED: {
        ControllerPhase.EXECUTING,
        ControllerPhase.REPAIR_REQUIRED,
        ControllerPhase.ESCALATED,
    },
    OrchestrationStatus.BLOCKED: {ControllerPhase.EXECUTING},
    OrchestrationStatus.CANCELLED: {None, ControllerPhase.CANCELLED},
}


class Writes:
    def __init__(self, monkeypatch):
        self.watched: set[int] = set()
        self.calls: list[str] = []
        self.fail_at: int | None = None
        for owner, name in WRITE_POINTS:
            monkeypatch.setattr(owner, name, self._wrap(owner, name, getattr(owner, name)))

    def _wrap(self, owner, name, real):
        def wrapper(instance, *args, **kwargs):
            if id(instance) in self.watched:
                self.calls.append(f"{owner.__name__}.{name}")
                if self.fail_at == len(self.calls):
                    raise OSError(f"injected failure at write {len(self.calls)} ({self.calls[-1]})")
            return real(instance, *args, **kwargs)

        return wrapper

    def watch(self, case):
        self.watched = {id(store) for store in case.stores()}
        self.calls = []


@dataclass
class Case:
    root: Path
    runtime: ControllerRuntime
    controller_id: str | None = None
    orchestrator: Orchestrator | None = None
    orchestration_id: str | None = None
    services: AgentRuntimeServices | None = None
    closers: list[Any] = field(default_factory=list)

    def stores(self):
        stores = [
            self.runtime._controller_store,
            self.runtime._harness._store,
            self.runtime._project_state_store,
        ]
        if self.orchestrator is not None:
            stores.append(self.orchestrator._store)
        return stores

    def restarted(self):
        self.close()
        runtime = ControllerRuntime(self.root)
        orchestrator = services = None
        if self.orchestration_id is not None:
            orchestrator = Orchestrator.resume(self.root, self.orchestration_id)
            runtime = orchestrator.controller_runtime()
            services = AgentRuntimeServices.open(self.root)
        return Case(
            self.root,
            runtime,
            self.controller_id,
            orchestrator,
            self.orchestration_id,
            services,
        )

    def close(self):
        if self.orchestrator is not None:
            self.orchestrator._telemetry.close()
        if self.services is not None:
            self.services.telemetry.close()


def idempotent_factory(**options):
    base = make_factory(**options)

    def factory(context):
        return dataclasses.replace(base(context), idempotent=True)

    return factory


def orchestration_case(root, stage, blast=5):
    if stage == "executed":
        orchestrator = Orchestrator(root, make_policy())
        record = arun(orchestrator.prepare(make_request(blast=blast)))
        orchestrator.submit_for_approval(record.orchestration_id)
        orchestrator.approve(record.orchestration_id, True)
        services = AgentRuntimeServices.open(root)
        arun(
            orchestrator.dispatch_and_execute(
                record.orchestration_id, services, idempotent_factory()
            ),
            timeout=300,
        )
        return Case(
            root,
            orchestrator.controller_runtime(),
            None,
            orchestrator,
            record.orchestration_id,
            services,
        )
    orchestrator = Orchestrator(root, make_policy())
    record = arun(orchestrator.prepare(make_request(blast=blast)))
    oid = record.orchestration_id
    if stage in {"submitted", "approved"}:
        orchestrator.submit_for_approval(oid)
    if stage == "approved":
        orchestrator.approve(oid, True)
    services = AgentRuntimeServices.open(root) if stage == "approved" else None
    return Case(root, orchestrator.controller_runtime(), None, orchestrator, oid, services)


def controller_case(root, stage):
    if stage == "executing":
        runtime, controller_id = executing_runtime(root)
    else:
        runtime = ControllerRuntime(root)
        controller_id = new_controller(runtime).controller_id
        runtime.submit_plan(controller_id, simple_plan())
        runtime.approve_plan(controller_id, True)
    return Case(root, runtime, controller_id)


FAILING_WORKER = {
    "model_turns": [{"type": "tool-call", "call": {"id": "c1", "name": "nope", "arguments": {}}}]
}


def resume_or_dispatch(case, **options):
    record = case.orchestrator.get(case.orchestration_id)
    if record.status is OrchestrationStatus.APPROVED:
        return arun(
            case.orchestrator.dispatch_and_execute(
                case.orchestration_id, case.services, idempotent_factory(**options)
            ),
            timeout=300,
        )
    if record.status is OrchestrationStatus.DISPATCHED:
        return arun(
            case.orchestrator.recover_execution(
                case.orchestration_id, case.services, idempotent_factory(**options)
            ),
            timeout=300,
        )
    return record


def observe(case):
    runtime = case.runtime
    orchestration = controller_id = run_id = None
    if case.orchestration_id is not None:
        record = case.orchestrator.get(case.orchestration_id)
        orchestration, controller_id = record.status.value, record.controller_id
    controller_id = controller_id or case.controller_id
    controller = runtime.get_controller(controller_id) if controller_id else None
    run = work_items = None
    if controller is not None:
        run_id = controller.run_id
        work_items = sorted(
            (item.work_item_id, item.status.value)
            for item in runtime.project_state(controller_id).work_items
        )
    if run_id is not None:
        state = runtime._harness.get_run_state(run_id)
        run = {"statuses": sorted(state.graph["statuses"].items()), "cancelled": state.cancelled}
    return {
        "orchestration": orchestration,
        "controller": controller.phase.value if controller else None,
        "run": run,
        "work_items": work_items,
    }


def assert_consistent(case, label):
    runtime = case.runtime
    controller_id = case.controller_id
    if case.orchestration_id is not None:
        record = case.orchestrator.get(case.orchestration_id)
        controller_id = record.controller_id
        phase = runtime.get_controller(controller_id).phase if controller_id else None
        assert phase in PHASES_OF[record.status], (
            f"{label}: orchestration is {record.status.value} but its controller is "
            f"{phase.value if phase else None}"
        )
    controller = runtime.get_controller(controller_id) if controller_id else None
    if controller is None or controller.run_id is None:
        return
    run = runtime._harness.get_run_state(controller.run_id)
    if run.cancelled:
        assert controller.phase in {ControllerPhase.CANCELLED, ControllerPhase.COMPLETED}, (
            f"{label}: the run is cancelled but the controller is {controller.phase.value}"
        )
    items = {
        item.work_item_id: item.status.value
        for item in runtime.project_state(controller_id).work_items
    }
    for node_id, result in run.graph["results"].items():
        assert items.get(node_id) == result["status"], (
            f"{label}: node {node_id} is {result['status']} in the run but "
            f"{items.get(node_id)} in the project state"
        )


@dataclass(frozen=True)
class Scenario:
    name: str
    build: Any
    act: Any
    retry: Any
    reconcile: Any
    variants: tuple[str, ...] = ("reconcile", "retry")


def reconcile_orchestration(case):
    return case.orchestrator.reconcile(case.orchestration_id)


def reconcile_controller(case):
    return case.runtime.reconcile(case.controller_id)


SCENARIOS = [
    Scenario(
        "orchestration-submit",
        lambda root: orchestration_case(root, "prepared"),
        lambda case: case.orchestrator.submit_for_approval(case.orchestration_id),
        lambda case: case.orchestrator.submit_for_approval(case.orchestration_id),
        reconcile_orchestration,
    ),
    Scenario(
        "orchestration-approve",
        lambda root: orchestration_case(root, "submitted"),
        lambda case: case.orchestrator.approve(case.orchestration_id, True, "ok"),
        lambda case: case.orchestrator.approve(case.orchestration_id, True, "ok"),
        reconcile_orchestration,
    ),
    Scenario(
        "orchestration-reject",
        lambda root: orchestration_case(root, "submitted"),
        lambda case: case.orchestrator.approve(case.orchestration_id, False, "no"),
        lambda case: case.orchestrator.approve(case.orchestration_id, False, "no"),
        reconcile_orchestration,
    ),
    Scenario(
        "orchestration-cancel-before-dispatch",
        lambda root: orchestration_case(root, "submitted"),
        lambda case: case.orchestrator.cancel(case.orchestration_id, "stop"),
        lambda case: case.orchestrator.cancel(case.orchestration_id, "stop"),
        reconcile_orchestration,
    ),
    Scenario(
        "orchestration-cancel-after-execution",
        lambda root: orchestration_case(root, "executed"),
        lambda case: case.orchestrator.cancel(case.orchestration_id, "stop"),
        lambda case: case.orchestrator.cancel(case.orchestration_id, "stop"),
        reconcile_orchestration,
    ),
    Scenario(
        "orchestration-dispatch-and-execute",
        lambda root: orchestration_case(root, "approved", blast=0),
        resume_or_dispatch,
        resume_or_dispatch,
        reconcile_orchestration,
    ),
    Scenario(
        "orchestration-dispatch-with-a-failing-worker",
        lambda root: orchestration_case(root, "approved", blast=0),
        lambda case: resume_or_dispatch(case, **FAILING_WORKER),
        lambda case: resume_or_dispatch(case, **FAILING_WORKER),
        reconcile_orchestration,
    ),
    Scenario(
        "controller-dispatch",
        lambda root: controller_case(root, "dispatch-ready"),
        lambda case: case.runtime.dispatch(case.controller_id),
        lambda case: case.runtime.dispatch(case.controller_id),
        reconcile_controller,
    ),
    Scenario(
        "controller-node-result",
        lambda root: controller_case(root, "executing"),
        lambda case: case.runtime.record_node_result(case.controller_id, "node:T1", ok()),
        lambda case: case.runtime.record_node_result(case.controller_id, "node:T1", ok()),
        reconcile_controller,
        variants=("reconcile",),
    ),
    Scenario(
        "controller-cancel",
        lambda root: controller_case(root, "executing"),
        lambda case: case.runtime.cancel(case.controller_id, "stop"),
        lambda case: case.runtime.cancel(case.controller_id, "stop"),
        reconcile_controller,
    ),
]


def run_case(scenario, root, writes, *, fail_at=None, variant="retry", restart=False):
    case = scenario.build(root)
    try:
        writes.watch(case)
        writes.fail_at = fail_at
        try:
            if fail_at is None:
                scenario.act(case)
            else:
                with pytest.raises(OSError, match="injected failure"):
                    scenario.act(case)
        finally:
            writes.fail_at = None
        performed = list(writes.calls)
        if fail_at is not None:
            if restart:
                case = case.restarted()
                writes.watch(case)
            label = f"{scenario.name}: failure at write {fail_at} ({performed[fail_at - 1]})"
            if variant == "reconcile":
                scenario.reconcile(case)
                assert_consistent(case, f"{label}, after reconcile")
                again = scenario.reconcile(case)
                assert again.actions == [], f"{label}: a second reconcile still found {again}"
            try:
                scenario.retry(case)
            except ValueError:
                pass
            assert_consistent(case, f"{label}, after the retry")
        return observe(case), performed
    finally:
        case.close()


@pytest.mark.parametrize("restart", [False, True], ids=["same-process", "after-restart"])
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_a_failure_between_two_writes_is_healed_to_the_fault_free_outcome(
    scenario, restart, tmp_path, monkeypatch
):
    writes = Writes(monkeypatch)
    reference, performed = run_case(scenario, tmp_path / "reference", writes)
    assert len(performed) >= 2, performed
    for fail_at in range(1, len(performed) + 1):
        for variant in scenario.variants:
            root = tmp_path / f"w{fail_at}-{variant}"
            observed, _ = run_case(
                scenario, root, writes, fail_at=fail_at, variant=variant, restart=restart
            )
            assert observed == reference, (
                f"{scenario.name}: failure at write {fail_at} ({performed[fail_at - 1]}) "
                f"healed by {variant} ends in {observed}, the fault-free run in {reference}"
            )
