from nailong_agent_sdk.observability.telemetry_models import DecisionSource, TelemetryAuthority
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.state.controller_runtime import ControllerRuntime
from tests.support.controllers import new_controller
from tests.support.plans import simple_plan

PLAN_EVENT = "controller.plan-approved"


def presented(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    runtime = ControllerRuntime(tmp_path, telemetry=telemetry)
    controller = new_controller(runtime)
    runtime.submit_plan(controller.controller_id, simple_plan())
    return runtime, telemetry, controller.controller_id


def recorded_event(record):
    return next(event for event in record.events if event.type == "plan-approval-recorded")


def test_a_plan_decision_records_the_decider_in_the_controller_log_and_telemetry(tmp_path):
    runtime, telemetry, controller_id = presented(tmp_path)
    try:
        source = DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="designer-3")
        record = runtime.approve_plan(controller_id, True, "reviewed", source=source)
        assert recorded_event(record).details == {
            "approved": True,
            "reason": "reviewed",
            "decision_authority": "human",
            "decided_by": "designer-3",
        }
        (event,) = telemetry.list_events(controller_id, event_types=[PLAN_EVENT])
        assert event.authority is TelemetryAuthority.HUMAN
        assert event.payload["decided_by"] == "designer-3"
        assert event.payload["decision_authority"] == "human"
    finally:
        telemetry.close()


def test_a_plan_decision_without_a_source_keeps_the_events_as_they_were(tmp_path):
    runtime, telemetry, controller_id = presented(tmp_path)
    try:
        record = runtime.approve_plan(controller_id, False, "rework")
        assert recorded_event(record).details == {"approved": False, "reason": "rework"}
        (event,) = telemetry.list_events(controller_id, event_types=[PLAN_EVENT])
        assert event.authority is TelemetryAuthority.DETERMINISTIC
        assert event.payload == {"approved": False, "reason": "rework"}
    finally:
        telemetry.close()


def test_the_decider_is_part_of_the_persisted_controller_record(tmp_path):
    runtime, telemetry, controller_id = presented(tmp_path)
    try:
        source = DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="designer-3")
        runtime.approve_plan(controller_id, True, source=source)
        reopened = ControllerRuntime(tmp_path, telemetry=telemetry).get_controller(controller_id)
        assert recorded_event(reopened).details["decided_by"] == "designer-3"
    finally:
        telemetry.close()
