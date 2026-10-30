import random

import pytest

from nailong_agent_sdk.state.elastic import (
    ELASTIC_REQUEST_TOOL_NAME,
    MAX_ELASTIC_DEPTH_LIMIT,
    MAX_ELASTIC_NODES_LIMIT,
    MAX_ELASTIC_REQUESTS_PER_RESULT,
    ElasticCapacity,
    ElasticNodeRole,
    ElasticNodeSpec,
    ElasticProblem,
    ElasticRefusalCode,
    ElasticSpawnRequest,
    GraphCapacityGrant,
    GraphSpawnRecord,
    GraphSpawnStatus,
    capacity_problem,
    check_spawn_request,
    elastic_child_id,
    elastic_join_id,
    validate_elastic_caps,
)
from nailong_agent_sdk.state.graph_models import GraphNodeResult, GraphNodeStatus
from nailong_agent_sdk.state.shared_state import DiscoveryRoutingRefs
from tests.support.elastic import req


def test_the_tool_name_and_hard_limits_are_the_documented_constants():
    assert ELASTIC_REQUEST_TOOL_NAME == "request_elastic_node"
    assert (MAX_ELASTIC_DEPTH_LIMIT, MAX_ELASTIC_NODES_LIMIT) == (8, 256)
    assert MAX_ELASTIC_REQUESTS_PER_RESULT == 32


@pytest.mark.parametrize(
    "bad", ["", "has space", "trailing.", "-leading", "a/b", "a:b", "x" * 129, "ünï"]
)
def test_a_request_id_must_be_a_valid_identifier_without_colons(bad):
    with pytest.raises(ValueError, match="request_id"):
        req(request_id=bad)


@pytest.mark.parametrize("good", ["a", "probe-1", "Probe_2.v3", "x" * 128, "0abc"])
def test_valid_request_ids_are_accepted(good):
    assert req(request_id=good).request_id == good


@pytest.mark.parametrize("field", ["scope", "instructions", "reason"])
def test_scope_instructions_and_reason_must_be_present_and_not_blank(field):
    with pytest.raises(ValueError, match=field):
        req(**{field: ""})
    with pytest.raises(ValueError, match=field):
        req(**{field: "   \n"})


@pytest.mark.parametrize(
    ("field", "limit"), [("scope", 2_000), ("instructions", 8_000), ("reason", 2_000)]
)
def test_request_text_is_bounded(field, limit):
    assert len(getattr(req(**{field: "x" * limit}), field)) == limit
    with pytest.raises(ValueError, match=field):
        req(**{field: "x" * (limit + 1)})


def test_dependencies_must_be_unique_nonblank_and_bounded():
    assert req(dependencies=["a", "b"]).dependencies == ["a", "b"]
    with pytest.raises(ValueError, match="dependencies must be unique"):
        req(dependencies=["a", "a"])
    with pytest.raises(ValueError, match="dependencies entries must be non-empty"):
        req(dependencies=["a", " "])
    with pytest.raises(ValueError, match="dependencies"):
        req(dependencies=[f"d{i}" for i in range(65)])


def test_a_request_rejects_unknown_fields_so_typos_are_not_silently_dropped():
    with pytest.raises(ValueError, match="scoop"):
        ElasticSpawnRequest(request_id="a", scoop="typo", scope="s", instructions="i", reason="r")


def test_node_specs_must_match_their_role():
    request = req()
    child = ElasticNodeSpec(
        role=ElasticNodeRole.CHILD, parent_node_id="p", root_node_id="p", request=request
    )
    assert child.joins == []
    join = ElasticNodeSpec(
        role=ElasticNodeRole.JOIN, parent_node_id="p", root_node_id="p", joins=["c1"]
    )
    held = ElasticNodeSpec(role=ElasticNodeRole.JOIN, parent_node_id="p", root_node_id="p")
    assert join.request is None and held.joins == []
    with pytest.raises(ValueError, match="child node spec requires the request"):
        ElasticNodeSpec(role=ElasticNodeRole.CHILD, parent_node_id="p", root_node_id="p")
    with pytest.raises(ValueError, match="child node spec cannot list joined nodes"):
        ElasticNodeSpec(
            role=ElasticNodeRole.CHILD,
            parent_node_id="p",
            root_node_id="p",
            request=request,
            joins=["x"],
        )
    with pytest.raises(ValueError, match="join node spec cannot carry a request"):
        ElasticNodeSpec(
            role=ElasticNodeRole.JOIN, parent_node_id="p", root_node_id="p", request=request
        )
    with pytest.raises(ValueError, match="joins must be unique"):
        ElasticNodeSpec(
            role=ElasticNodeRole.JOIN, parent_node_id="p", root_node_id="p", joins=["c", "c"]
        )


def test_spawn_records_must_state_the_fields_their_status_requires():
    base = {"sequence": 1, "event_sequence": 0, "parent_node_id": "p", "request": req(), "depth": 1}
    accepted = GraphSpawnRecord(
        **base, status=GraphSpawnStatus.ACCEPTED, child_node_id="c", join_node_id="j"
    )
    assert accepted.code is None and accepted.grant_sequence is None
    with pytest.raises(ValueError, match="accepted spawn record requires child_node_id"):
        GraphSpawnRecord(**base, status=GraphSpawnStatus.ACCEPTED, join_node_id="j")
    with pytest.raises(ValueError, match="accepted spawn record cannot carry a refusal code"):
        GraphSpawnRecord(
            **base,
            status=GraphSpawnStatus.ACCEPTED,
            child_node_id="c",
            join_node_id="j",
            code=ElasticRefusalCode.NODE_CAP_REACHED,
            reason="x",
        )
    refused = GraphSpawnRecord(
        **base,
        status=GraphSpawnStatus.REFUSED,
        code=ElasticRefusalCode.REQUEST_ID_DUPLICATE,
        reason="dup",
    )
    assert refused.child_node_id is None and refused.join_node_id is None
    with pytest.raises(ValueError, match="refused spawn record requires a code and a reason"):
        GraphSpawnRecord(**base, status=GraphSpawnStatus.REFUSED)
    with pytest.raises(ValueError, match="refused spawn record cannot name a child or join"):
        GraphSpawnRecord(
            **base,
            status=GraphSpawnStatus.REFUSED,
            code=ElasticRefusalCode.REQUEST_ID_DUPLICATE,
            reason="x",
            join_node_id="j",
        )
    deferred = GraphSpawnRecord(
        **base,
        status=GraphSpawnStatus.DEFERRED,
        code=ElasticRefusalCode.NODE_CAP_REACHED,
        reason="cap",
        join_node_id="j",
    )
    assert deferred.child_node_id is None
    with pytest.raises(ValueError, match="deferred spawn record requires the held join_node_id"):
        GraphSpawnRecord(
            **base,
            status=GraphSpawnStatus.DEFERRED,
            code=ElasticRefusalCode.NODE_CAP_REACHED,
            reason="cap",
        )
    with pytest.raises(ValueError, match="deferred spawn record requires a code and a reason"):
        GraphSpawnRecord(**base, status=GraphSpawnStatus.DEFERRED, join_node_id="j")
    with pytest.raises(ValueError, match="discarded spawn record cannot name a child node"):
        GraphSpawnRecord(
            **base,
            status=GraphSpawnStatus.DISCARDED,
            code=ElasticRefusalCode.BATCH_DECLINED,
            reason="declined",
            join_node_id="j",
            child_node_id="c",
        )


def test_capacity_grants_record_what_changed_and_why():
    grant = GraphCapacityGrant(
        sequence=1,
        previous_max_depth=1,
        max_depth=2,
        previous_max_nodes=2,
        max_nodes=2,
        reason="designer approved",
    )
    assert grant.max_depth == 2
    with pytest.raises(ValueError, match="reason"):
        GraphCapacityGrant(
            sequence=1,
            previous_max_depth=1,
            max_depth=2,
            previous_max_nodes=2,
            max_nodes=2,
            reason=" ",
        )
    with pytest.raises(ValueError, match="must raise at least one cap"):
        GraphCapacityGrant(
            sequence=1,
            previous_max_depth=1,
            max_depth=1,
            previous_max_nodes=2,
            max_nodes=2,
            reason="nothing",
        )
    with pytest.raises(ValueError, match="cannot lower"):
        GraphCapacityGrant(
            sequence=1,
            previous_max_depth=2,
            max_depth=1,
            previous_max_nodes=2,
            max_nodes=3,
            reason="lower",
        )


def test_node_ids_are_derived_from_the_parent_and_cannot_collide():
    assert elastic_child_id("node:T1", "probe") == "elastic:node:T1:probe"
    assert elastic_join_id("node:T1") == "join:node:T1"
    rng = random.Random(7)
    parent_alphabet = "abc:._-T1"
    request_alphabet = "abc._-T1"
    seen = {}
    for _ in range(4_000):
        parent = "".join(rng.choice(parent_alphabet) for _ in range(rng.randint(1, 8)))
        request = "".join(rng.choice(request_alphabet) for _ in range(rng.randint(1, 6)))
        derived = elastic_child_id(parent, request)
        assert seen.setdefault(derived, (parent, request)) == (parent, request)
        assert not derived.startswith("join:")
    assert not elastic_join_id("x").startswith("elastic:")


def check(request, **overrides):
    values = {
        "parent_node_id": "root",
        "parent_routing_refs": DiscoveryRoutingRefs(),
        "visible_dependencies": frozenset(),
        "taken_request_ids": frozenset(),
    }
    values.update(overrides)
    return check_spawn_request(request, **values)


def test_a_clean_request_has_no_problem():
    assert check(req()) is None
    assert check(req(dependencies=["pre"]), visible_dependencies={"pre", "other"}) is None


def test_a_repeated_request_id_is_the_duplicate_problem():
    problem = check(req("a"), taken_request_ids={"a"})
    assert isinstance(problem, ElasticProblem)
    assert problem.code is ElasticRefusalCode.REQUEST_ID_DUPLICATE
    assert '"a"' in problem.message and '"root"' in problem.message


def test_invisible_dependencies_are_named_with_the_parent_and_what_it_can_see():
    problem = check(req("x", dependencies=["ghost", "b"]), visible_dependencies={"pre"})
    assert problem.code is ElasticRefusalCode.DEPENDENCY_NOT_VISIBLE
    assert '"b"' in problem.message and '"ghost"' in problem.message
    assert '"pre"' in problem.message and '"root"' in problem.message and '"x"' in problem.message
    nothing = check(req("x", dependencies=["ghost"]))
    assert "can see no dependencies" in nothing.message


def test_widened_routing_references_are_named_per_field():
    parent = DiscoveryRoutingRefs(signal_ids=["clk"])
    wide = DiscoveryRoutingRefs(signal_ids=["clk", "rst"], task_ids=["T9"])
    problem = check(req("x", routing_refs=wide), parent_routing_refs=parent)
    assert problem.code is ElasticRefusalCode.ROUTING_REFS_NOT_INHERITED
    assert "signal_ids" in problem.message and "rst" in problem.message
    assert "task_ids" in problem.message and "T9" in problem.message
    assert check(req("x", routing_refs=parent), parent_routing_refs=parent) is None
    assert check(req("x", routing_refs=DiscoveryRoutingRefs()), parent_routing_refs=parent) is None


def test_the_first_problem_found_is_reported_in_a_fixed_order():
    problem = check(
        req("a", dependencies=["ghost"], routing_refs=DiscoveryRoutingRefs(signal_ids=["z"])),
        taken_request_ids={"a"},
    )
    assert problem.code is ElasticRefusalCode.REQUEST_ID_DUPLICATE


def test_capacity_problems_name_the_cap_the_numbers_and_the_node():
    depth = capacity_problem(
        node_id="root", node_depth=1, max_depth=1, nodes_used=0, max_nodes=5, requested=1
    )
    assert depth.code is ElasticRefusalCode.DEPTH_CAP_REACHED
    assert "max_elastic_depth 1" in depth.message and '"root"' in depth.message
    assert "depth 1" in depth.message and "depth 2" in depth.message
    nodes = capacity_problem(
        node_id="root", node_depth=0, max_depth=1, nodes_used=1, max_nodes=2, requested=2
    )
    assert nodes.code is ElasticRefusalCode.NODE_CAP_REACHED
    assert "max_elastic_nodes 2" in nodes.message and "1 already used" in nodes.message
    assert "2 requested plus the join node" in nodes.message
    assert (
        capacity_problem(
            node_id="root", node_depth=0, max_depth=1, nodes_used=1, max_nodes=4, requested=2
        )
        is None
    )
    assert (
        capacity_problem(
            node_id="root", node_depth=0, max_depth=1, nodes_used=1, max_nodes=3, requested=1
        )
        is None
    )
    both = capacity_problem(
        node_id="root", node_depth=1, max_depth=1, nodes_used=2, max_nodes=2, requested=1
    )
    assert both.code is ElasticRefusalCode.DEPTH_CAP_REACHED


def test_capacity_exposes_what_a_node_may_still_request():
    capacity = ElasticCapacity(node_depth=1, max_depth=2, nodes_used=3, max_nodes=4)
    assert capacity.remaining_nodes == 1 and capacity.depth_available is True
    assert capacity.remaining_requests == 0
    roomy = ElasticCapacity(node_depth=0, max_depth=1, nodes_used=1, max_nodes=6)
    assert (roomy.remaining_nodes, roomy.remaining_requests) == (5, 4)
    exhausted = ElasticCapacity(node_depth=0, max_depth=1, nodes_used=5, max_nodes=4)
    assert exhausted.remaining_nodes == 0
    with pytest.raises(ValueError, match="node_depth"):
        ElasticCapacity(node_depth=-1, max_depth=1, nodes_used=0, max_nodes=1)


def test_a_result_holds_at_most_the_documented_number_of_requests():
    requests = [req(f"r{i}") for i in range(MAX_ELASTIC_REQUESTS_PER_RESULT)]
    assert (
        len(
            GraphNodeResult(
                status=GraphNodeStatus.COMPLETED, spawn_requests=requests
            ).spawn_requests
        )
        == 32
    )
    with pytest.raises(ValueError, match="spawn_requests"):
        GraphNodeResult(
            status=GraphNodeStatus.COMPLETED, spawn_requests=[*requests, req("one-too-many")]
        )


def test_a_ceiling_problem_is_not_grantable_and_a_cap_problem_is():
    cap = capacity_problem(
        node_id="root", node_depth=0, max_depth=1, nodes_used=0, max_nodes=2, requested=2
    )
    assert cap.code is ElasticRefusalCode.NODE_CAP_REACHED and cap.grantable is True
    ceiling = capacity_problem(
        node_id="root",
        node_depth=0,
        max_depth=1,
        nodes_used=0,
        max_nodes=2,
        requested=2,
        ceiling_nodes=2,
    )
    assert ceiling.code is ElasticRefusalCode.NODE_CEILING_REACHED and ceiling.grantable is False
    assert (
        "ceiling max_elastic_nodes 2" in ceiling.message and "no capacity grant" in ceiling.message
    )
    deep = capacity_problem(
        node_id="root",
        node_depth=0,
        max_depth=1,
        nodes_used=0,
        max_nodes=2,
        requested=1,
        ceiling_depth=0,
    )
    assert deep.code is ElasticRefusalCode.DEPTH_CEILING_REACHED and deep.grantable is False
    assert "ceiling max_elastic_depth 0" in deep.message


def test_reserved_capacity_counts_against_the_cap_but_not_the_ceiling():
    held = capacity_problem(
        node_id="p2",
        node_depth=0,
        max_depth=1,
        nodes_used=1,
        max_nodes=4,
        requested=1,
        reserved=2,
    )
    assert held.code is ElasticRefusalCode.NODE_CAP_REACHED and held.grantable is True
    assert "3 already used, 2 of them held by tasks running at the same time" in held.message
    unreserved = capacity_problem(
        node_id="p2",
        node_depth=0,
        max_depth=1,
        nodes_used=1,
        max_nodes=4,
        requested=1,
        ceiling_nodes=4,
    )
    assert unreserved is None
    still_inside = capacity_problem(
        node_id="p2",
        node_depth=0,
        max_depth=1,
        nodes_used=1,
        max_nodes=4,
        requested=1,
        reserved=2,
        ceiling_nodes=4,
    )
    assert still_inside.code is ElasticRefusalCode.NODE_CAP_REACHED


def test_validated_caps_must_sit_inside_the_ceilings_and_the_hard_limits():
    validate_elastic_caps(1, 3, 2, 5)
    validate_elastic_caps(0, 0, 0, 0)
    with pytest.raises(ValueError, match="max_elastic_depth 3 is above the ceiling 2 of this run"):
        validate_elastic_caps(3, 3, 2, 5)
    with pytest.raises(ValueError, match="max_elastic_nodes 6 is above the ceiling 5 of this run"):
        validate_elastic_caps(1, 6, 2, 5)
    with pytest.raises(ValueError, match="elastic_depth_ceiling must be between 0 and"):
        validate_elastic_caps(1, 3, MAX_ELASTIC_DEPTH_LIMIT + 1, 5)
    with pytest.raises(ValueError, match="elastic_nodes_ceiling must be between 0 and"):
        validate_elastic_caps(1, 3, 2, -1)
