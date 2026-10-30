import pytest

from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.harness_coordinator import HarnessCoordinator
from nailong_agent_sdk.state.shared_state import LateralDependencyRequest, SharedStateWrite
from tests.support.controllers import executing_runtime, new_controller
from tests.support.elastic import discovery
from tests.support.plans import AGENT, arun, ok, run_ok

MUTATIONS = {
    "discovery": (
        "Discoveries require",
        lambda runtime, cid: runtime.publish_discovery(cid, discovery()),
    ),
    "shared value": (
        "Shared graph values require",
        lambda runtime, cid: runtime.write_shared_value(
            cid,
            SharedStateWrite(
                producer_node_id="node:T1", key="k", value={"a": 1}, provenance_hash="h"
            ),
        ),
    ),
    "node result": (
        "Node results require",
        lambda runtime, cid: runtime.record_node_result(cid, "node:T1", ok()),
    ),
    "lateral dependency": (
        "Lateral dependencies require",
        lambda runtime, cid: runtime.request_lateral_dependency(
            cid,
            LateralDependencyRequest(
                consumer_node_id="node:T2",
                consumer_action_id="act",
                discovery_episode_id="d1",
                reason="needs the finding",
            ),
        ),
    ),
}


def completed(tmp_path):
    runtime, cid = executing_runtime(tmp_path)
    arun(runtime.execute_graph(cid, {AGENT: run_ok}))
    runtime.complete(cid)
    return runtime, cid


def cancelled(tmp_path):
    runtime, cid = executing_runtime(tmp_path)
    runtime.cancel(cid, "stop")
    return runtime, cid


@pytest.mark.parametrize("mutation", MUTATIONS)
@pytest.mark.parametrize(("build", "phase"), [(completed, "completed"), (cancelled, "cancelled")])
def test_a_finished_controllers_run_is_not_changed_by_a_late_call(tmp_path, build, phase, mutation):
    requirement, call = MUTATIONS[mutation]
    runtime, cid = build(tmp_path)
    run_id = runtime.get_controller(cid).run_id
    before = HarnessCoordinator(tmp_path).get_run_state(run_id)
    state_before = runtime.project_state(cid)
    with pytest.raises(ValueError) as raised:
        call(runtime, cid)
    assert str(raised.value) == (
        f'{requirement} a controller that is not finished; "{cid}" is in phase "{phase}".'
    )
    assert HarnessCoordinator(tmp_path).get_run_state(run_id).run_hash == before.run_hash
    assert runtime.project_state(cid) == state_before


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_a_controller_without_a_run_keeps_its_earlier_message(tmp_path, mutation):
    requirement, call = MUTATIONS[mutation]
    runtime = ControllerRuntime(tmp_path)
    cid = new_controller(runtime).controller_id
    with pytest.raises(ValueError) as raised:
        call(runtime, cid)
    assert str(raised.value) == f"{requirement} a dispatched graph run."
