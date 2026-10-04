from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from nailong_agent_sdk.state.orchestration_models import (
    ComplexityRoutingRules,
    GapMetadata,
    SkillToolProfile,
)
from nailong_agent_sdk.state.shared_state import SharedSubstrateSnapshot
from tests.support.plans import simple_plan


def new_controller(runtime):
    snapshot = SharedSubstrateSnapshot(snapshot_id="snap", version="1", content_hash="h")
    profile = SkillToolProfile(stage="design", source_snapshot_id="snap")
    rules = ComplexityRoutingRules(multi_agent_min_categories=3, multi_agent_min_blast_radius=5)
    return runtime.create_controller(snapshot, profile, rules, GapMetadata(), max_repair_attempts=1)


def executing_runtime(tmp_path, telemetry=None):
    runtime = ControllerRuntime(tmp_path, telemetry=telemetry)
    controller = new_controller(runtime)
    runtime.submit_plan(controller.controller_id, simple_plan())
    runtime.approve_plan(controller.controller_id, True)
    runtime.dispatch(controller.controller_id)
    return runtime, controller.controller_id
