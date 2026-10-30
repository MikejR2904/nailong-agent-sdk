import json

import pytest
from pydantic import ValidationError

from nailong_agent_sdk.observability.telemetry_models import DecisionSource, TelemetryAuthority
from nailong_agent_sdk.tools.approvals import ApprovalRegistry, ApprovalStatus


def pending(registry, capability="draft.write"):
    return registry.request("run-1", "node-1", capability, "writes the draft")


def test_a_request_starts_with_no_decider():
    request = pending(ApprovalRegistry())
    assert request.status is ApprovalStatus.PENDING
    assert request.decided_by is None and request.decision_authority is None


def test_a_decision_records_the_decider_and_its_authority_class():
    registry = ApprovalRegistry()
    request = pending(registry)
    source = DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="reviewer-7")
    decided = registry.submit(request.approval_id, True, "reviewed", source=source)
    assert decided.status is ApprovalStatus.APPROVED and decided.decision_reason == "reviewed"
    assert decided.decided_by == "reviewer-7"
    assert decided.decision_authority is TelemetryAuthority.HUMAN
    assert registry.get(request.approval_id) == decided


def test_a_decision_without_a_source_leaves_the_decider_unrecorded():
    registry = ApprovalRegistry()
    request = pending(registry)
    decided = registry.submit(request.approval_id, False, "no")
    assert decided.status is ApprovalStatus.REJECTED
    assert decided.decided_by is None and decided.decision_authority is None


def test_a_source_without_an_identifier_still_records_the_authority_class():
    registry = ApprovalRegistry()
    request = pending(registry)
    source = DecisionSource(authority=TelemetryAuthority.SYSTEM)
    decided = registry.submit(request.approval_id, False, "expired", source=source)
    assert decided.decided_by is None
    assert decided.decision_authority is TelemetryAuthority.SYSTEM


def test_the_decider_survives_a_reload_from_disk(tmp_path):
    path = tmp_path / "approvals.json"
    first = ApprovalRegistry(path)
    request = pending(first)
    first.submit(
        request.approval_id,
        True,
        source=DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="approver-1"),
    )
    reloaded = ApprovalRegistry(path).get(request.approval_id)
    assert reloaded.decided_by == "approver-1"
    assert reloaded.decision_authority is TelemetryAuthority.HUMAN


def test_approvals_saved_before_deciders_were_recorded_still_load(tmp_path):
    path = tmp_path / "approvals.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "approvals-v1",
                "next_id": 2,
                "requests": [
                    {
                        "approval_id": "approval-1",
                        "run_id": "run-1",
                        "node_id": "node-1",
                        "capability": "draft.write",
                        "reason": "writes the draft",
                        "status": "approved",
                        "decision_reason": "ok",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = ApprovalRegistry(path).get("approval-1")
    assert loaded.status is ApprovalStatus.APPROVED
    assert loaded.decided_by is None and loaded.decision_authority is None


def test_a_second_decision_does_not_overwrite_the_first_decider():
    registry = ApprovalRegistry()
    request = pending(registry)
    first = DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="first")
    second = DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="second")
    registry.submit(request.approval_id, True, source=first)
    with pytest.raises(ValueError, match='already has the decision "approved"'):
        registry.submit(request.approval_id, False, source=second)
    assert registry.get(request.approval_id).decided_by == "first"


def test_decision_sources_are_validated_where_they_are_built():
    with pytest.raises(ValidationError, match="identifier"):
        DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="")
    with pytest.raises(ValidationError, match="identifier"):
        DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="x" * 129)
    with pytest.raises(ValidationError, match="authority"):
        DecisionSource(authority="oracle", identifier="someone")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="a", note="b")


def test_a_source_names_its_recorded_fields():
    source = DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="reviewer-7")
    assert source.fields() == {"decision_authority": "human", "decided_by": "reviewer-7"}
    assert DecisionSource(authority=TelemetryAuthority.SYSTEM).fields() == {
        "decision_authority": "system"
    }
