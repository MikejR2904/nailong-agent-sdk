import pytest

from nailong_agent_sdk.state.elastic import (
    ElasticCapacity,
    ElasticProblem,
    ElasticRefusalCode,
    ElasticReservation,
)
from nailong_agent_sdk.tools.elastic_requests import ElasticRequestBuffer, ElasticRequestRejected
from tests.support.elastic import req
from tests.support.plans import n

CAP_PROBLEM = ElasticProblem(ElasticRefusalCode.NODE_CAP_REACHED, "no room beside a sibling")
CEILING_PROBLEM = ElasticProblem(
    ElasticRefusalCode.NODE_CEILING_REACHED, "above the ceiling", grantable=False
)


def reservation(*problems, free=2):
    pending = list(problems)
    log = {"reserved": [], "released": 0}

    def reserve(total):
        log["reserved"].append(total)
        return pending.pop(0) if pending else None

    def release():
        log["released"] += 1

    handle = ElasticReservation(reserve=reserve, release=release, remaining=lambda: free)
    return handle, log


def buffer(handle=None, **overrides):
    values = {
        "node": n("root"),
        "capacity": ElasticCapacity(node_depth=0, max_depth=1, nodes_used=0, max_nodes=3),
        "visible_dependencies": set(),
        "reservation": handle,
    }
    values.update(overrides)
    return ElasticRequestBuffer(**values)


def test_the_live_reservation_decides_instead_of_the_wave_start_snapshot():
    handle, log = reservation()
    full = ElasticCapacity(node_depth=0, max_depth=1, nodes_used=3, max_nodes=3)
    queue = buffer(handle, capacity=full)
    queue.add(req("a"))
    queue.add(req("b"))
    assert log["reserved"] == [1, 2]
    assert [item.request_id for item in queue.requests] == ["a", "b"]


def test_a_refusal_from_the_reservation_is_raised_and_nothing_is_queued():
    handle, log = reservation(None, CAP_PROBLEM)
    queue = buffer(handle)
    queue.add(req("a"))
    with pytest.raises(ElasticRequestRejected, match="no room beside a sibling") as rejected:
        queue.add(req("b"))
    assert rejected.value.code is ElasticRefusalCode.NODE_CAP_REACHED
    assert [item.request_id for item in queue.requests] == ["a"]
    assert log["released"] == 0


def test_an_escalating_queue_keeps_an_overflow_a_grant_could_fit_and_gives_its_hold_back():
    handle, log = reservation(CAP_PROBLEM)
    queue = buffer(handle, escalate_overflow=True)
    receipt = queue.add(req("a"))
    assert receipt["capacity_decision_required"] is True
    assert "controller grants more elastic capacity or declines" in receipt["runs"]
    assert [item.request_id for item in queue.requests] == ["a"]
    assert log["released"] == 1


def test_an_escalating_queue_still_refuses_what_no_grant_could_ever_fit():
    handle, log = reservation(CEILING_PROBLEM)
    queue = buffer(handle, escalate_overflow=True)
    with pytest.raises(ElasticRequestRejected, match="above the ceiling") as rejected:
        queue.add(req("a"))
    assert rejected.value.code is ElasticRefusalCode.NODE_CEILING_REACHED
    assert queue.requests == []
    assert log["released"] == 0


def test_without_a_reservation_the_snapshot_counts_the_join_node():
    queue = buffer()
    assert queue.add(req("a"))["elastic_nodes_remaining_after_queue"] == 1
    assert queue.add(req("b"))["elastic_nodes_remaining_after_queue"] == 0
    with pytest.raises(ElasticRequestRejected) as rejected:
        queue.add(req("c"))
    assert rejected.value.code is ElasticRefusalCode.NODE_CAP_REACHED
    assert "3 requested plus the join node" in str(rejected.value)


def test_without_a_reservation_the_snapshot_still_enforces_the_ceilings():
    capacity = ElasticCapacity(
        node_depth=0, max_depth=1, nodes_used=0, max_nodes=2, ceiling_depth=1, ceiling_nodes=3
    )
    plain = buffer(capacity=capacity)
    plain.add(req("a"))
    with pytest.raises(ElasticRequestRejected) as capped:
        plain.add(req("b"))
    assert capped.value.code is ElasticRefusalCode.NODE_CAP_REACHED
    escalating = buffer(capacity=capacity, escalate_overflow=True)
    escalating.add(req("a"))
    assert escalating.add(req("b"))["capacity_decision_required"] is True
    with pytest.raises(ElasticRequestRejected) as beyond:
        escalating.add(req("c"))
    assert beyond.value.code is ElasticRefusalCode.NODE_CEILING_REACHED
    assert [item.request_id for item in escalating.requests] == ["a", "b"]


def test_the_receipt_reports_what_the_live_reservation_says_is_still_free():
    handle, log = reservation(free=5)
    queue = buffer(
        handle, capacity=ElasticCapacity(node_depth=0, max_depth=1, nodes_used=3, max_nodes=3)
    )
    assert queue.add(req("a"))["elastic_nodes_remaining_after_queue"] == 5
    assert queue.add(req("b"))["elastic_nodes_remaining_after_queue"] == 5
