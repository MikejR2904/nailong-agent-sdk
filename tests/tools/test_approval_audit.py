from dataclasses import replace

from nailong_agent_sdk.observability.telemetry_models import (
    DecisionSource,
    TelemetryAuthority,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.tools.approval_audit import TelemetryApprovalSink
from nailong_agent_sdk.tools.approvals import (
    ApprovalEventKind,
    ApprovalRegistry,
    ApprovalStatus,
)
from nailong_agent_sdk.tools.policy import CapabilityGrant
from nailong_agent_sdk.tools.registry import HarnessToolExecutor
from tests.support.tools import all_capabilities, build, call, run

HUMAN = DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="alice")


class ListSink:
    def __init__(self, fail_on=()):
        self.events = []
        self.fail_on = set(fail_on)

    def record(self, event):
        if event.kind in self.fail_on:
            raise OSError("audit volume full")
        self.events.append(event)

    def kinds(self):
        return [event.kind for event in self.events]


def world(tmp_path, *, sink=None, **context_changes):
    sink = sink or ListSink()
    registry = ApprovalRegistry(sink=sink)
    executor, context = build(
        tmp_path, declared=("out.txt",), approve=(), approvals=registry, **context_changes
    )
    return sink, registry, executor, context


def single_use(executor, context):
    narrowed = replace(context, single_use_capabilities=frozenset({"draft.write"}))
    return HarnessToolExecutor(executor._registry, narrowed), narrowed


def test_every_registry_decision_is_recorded_with_its_kind_and_decider():
    sink = ListSink()
    registry = ApprovalRegistry(sink=sink)
    first = registry.request("r", "n", "draft.write", "why")
    registry.submit(first.approval_id, True, "fine", source=HUMAN)
    registry.revoke(first.approval_id, "changed my mind", source=HUMAN)
    second = registry.request("r", "n", "draft.write", "again")
    registry.submit(second.approval_id, False, "no", source=HUMAN)
    third = registry.request("r", "n", "process.execute", "run it")
    registry.expire(third.approval_id, "Expired: not decided within 5s.")
    assert sink.kinds() == [
        ApprovalEventKind.REQUESTED,
        ApprovalEventKind.GRANTED,
        ApprovalEventKind.REVOKED,
        ApprovalEventKind.REQUESTED,
        ApprovalEventKind.REJECTED,
        ApprovalEventKind.REQUESTED,
        ApprovalEventKind.EXPIRED,
    ]
    granted = sink.events[1]
    assert granted.decided_by == "alice" and granted.decision_authority is TelemetryAuthority.HUMAN
    assert sink.events[2].reason == "changed my mind"
    assert registry.get(third.approval_id).status is ApprovalStatus.REJECTED


def test_a_single_use_approval_leaves_a_consumed_event_naming_the_call(tmp_path):
    sink, registry, executor, context = world(tmp_path)
    executor, context = single_use(executor, context)
    blocked = run(call(executor, "write_draft", path="out.txt", content="1"))
    registry.submit(blocked.output["approval_id"], True, "ok", source=HUMAN)
    assert run(call(executor, "write_draft", path="out.txt", content="1")).status == "succeeded"
    consumed = [e for e in sink.events if e.kind is ApprovalEventKind.CONSUMED]
    assert len(consumed) == 1
    event = consumed[0]
    assert event.approval_id == blocked.output["approval_id"] and event.single_use
    assert event.tool == "write_draft" and event.tool_call_id == "c1"
    again = run(call(executor, "write_draft", path="out.txt", content="2"))
    assert again.status == "blocked"
    assert sink.kinds().count(ApprovalEventKind.REQUESTED) == 2


def test_a_revoked_approval_denies_the_next_call_and_records_the_denial(tmp_path):
    sink, registry, executor, context = world(tmp_path)
    request = registry.request("run-1", "node-1", "draft.write", "x")
    registry.submit(request.approval_id, True, "ok", source=HUMAN)
    assert run(call(executor, "write_draft", path="out.txt", content="1")).status == "succeeded"
    registry.revoke(request.approval_id, "withdrawn", source=HUMAN)
    denied = run(call(executor, "write_draft", path="out.txt", content="2"))
    assert denied.status == "blocked" and denied.failure.code == "TOOL_DENIED_BY_POLICY"
    events = [e for e in sink.events if e.kind is ApprovalEventKind.DENIED]
    assert len(events) == 1
    assert events[0].approval_id == request.approval_id
    assert events[0].reason == "withdrawn" and events[0].tool == "write_draft"


def test_a_missing_capability_and_a_foreign_approval_are_recorded_as_denials(tmp_path):
    sink, registry, executor, context = world(
        tmp_path, grants=[CapabilityGrant(role="worker", capabilities=[])]
    )
    run(call(executor, "write_draft", path="out.txt", content="x"))
    assert [e.reason for e in sink.events if e.kind is ApprovalEventKind.DENIED] == [
        'Role "worker" lacks capability "draft.write".'
    ]
    other = registry.request("other-run", "other-node", "draft.write", "x")
    registry.submit(other.approval_id, True, "ok", source=HUMAN)
    pinned = replace(
        context,
        policy=type(context.policy)(
            [CapabilityGrant(role="worker", capabilities=all_capabilities(), allowed_paths=["."])]
        ),
        approval_ids_by_capability={"draft.write": other.approval_id},
    )
    foreign = HarnessToolExecutor(executor._registry, pinned)
    run(call(foreign, "write_draft", path="out.txt", content="x"))
    reasons = [e.reason for e in sink.events if e.kind is ApprovalEventKind.DENIED]
    assert any("belongs to run" in reason for reason in reasons)


def test_a_consumption_that_cannot_be_recorded_is_refused_instead_of_run_unrecorded(tmp_path):
    sink = ListSink(fail_on={ApprovalEventKind.CONSUMED})
    sink, registry, executor, context = world(tmp_path, sink=sink)
    executor, context = single_use(executor, context)
    blocked = run(call(executor, "write_draft", path="out.txt", content="1"))
    registry.submit(blocked.output["approval_id"], True, "ok", source=HUMAN)
    result = run(call(executor, "write_draft", path="out.txt", content="1"))
    assert result.status == "blocked"
    assert "could not be recorded" in result.error
    assert not context.artifacts.paths()


def test_the_telemetry_sink_writes_one_event_per_decision_with_the_deciders_authority(tmp_path):
    telemetry = TelemetryStore(tmp_path / "telemetry")
    try:
        registry = ApprovalRegistry(sink=TelemetryApprovalSink(telemetry))
        request = registry.request("r1", "n1", "draft.write", "why")
        registry.submit(request.approval_id, True, "ok", source=HUMAN)
        registry.revoke(request.approval_id, "withdrawn", source=HUMAN)
        events = {e.event_type: e for e in telemetry.iter_events("r1")}
        assert set(events) == {"approval.requested", "approval.granted", "approval.revoked"}
        revoked = events["approval.revoked"]
        assert revoked.status == "revoked" and revoked.authority is TelemetryAuthority.HUMAN
        assert revoked.payload["approval_id"] == request.approval_id
        assert revoked.payload["decided_by"] == "alice" and revoked.payload["reason"] == "withdrawn"
        assert revoked.context.node_id == "n1"
    finally:
        telemetry.close()
