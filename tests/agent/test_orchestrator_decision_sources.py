import pytest

from nailong_agent_sdk.agent.orchestrator import Orchestrator
from nailong_agent_sdk.observability.telemetry_models import DecisionSource, TelemetryAuthority
from tests.support.agents import arun
from tests.support.orchestration import make_policy, make_request


def submitted(tmp_path):
    orchestrator = Orchestrator(tmp_path, make_policy())
    record = arun(orchestrator.prepare(make_request(blast=5)))
    orchestrator.submit_for_approval(record.orchestration_id)
    return orchestrator, record.orchestration_id


@pytest.mark.parametrize("approved", [True, False])
def test_a_plan_decision_names_its_source_in_the_controller_log_and_both_telemetry_events(
    tmp_path, approved
):
    orchestrator, orchestration_id = submitted(tmp_path)
    try:
        source = DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="designer-9")
        decided = orchestrator.approve(orchestration_id, approved, "reviewed", source=source)
        controller = orchestrator.controller_runtime().get_controller(decided.controller_id)
        recorded = next(e for e in controller.events if e.type == "plan-approval-recorded")
        assert recorded.details == {
            "approved": approved,
            "reason": "reviewed",
            "decision_authority": "human",
            "decided_by": "designer-9",
        }
        telemetry = orchestrator._telemetry
        (plan_event,) = telemetry.list_events(
            decided.controller_id, event_types=["controller.plan-approved"]
        )
        assert plan_event.authority is TelemetryAuthority.HUMAN
        assert plan_event.payload["decided_by"] == "designer-9"
        (decision_event,) = telemetry.list_events(
            orchestration_id, event_types=["orchestration.plan-decision"]
        )
        assert decision_event.authority is TelemetryAuthority.HUMAN
        assert decision_event.payload["decided_by"] == "designer-9"
        assert decision_event.status == ("approved" if approved else "rejected")
    finally:
        orchestrator._telemetry.close()


def test_a_plan_decision_without_a_source_leaves_every_level_as_it_was(tmp_path):
    orchestrator, orchestration_id = submitted(tmp_path)
    try:
        decided = orchestrator.approve(orchestration_id, True, "reviewed")
        controller = orchestrator.controller_runtime().get_controller(decided.controller_id)
        recorded = next(e for e in controller.events if e.type == "plan-approval-recorded")
        assert recorded.details == {"approved": True, "reason": "reviewed"}
        telemetry = orchestrator._telemetry
        (plan_event,) = telemetry.list_events(
            decided.controller_id, event_types=["controller.plan-approved"]
        )
        assert plan_event.authority is TelemetryAuthority.DETERMINISTIC
        (decision_event,) = telemetry.list_events(
            orchestration_id, event_types=["orchestration.plan-decision"]
        )
        assert decision_event.authority is TelemetryAuthority.DETERMINISTIC
        assert "decided_by" not in decision_event.payload
    finally:
        orchestrator._telemetry.close()
