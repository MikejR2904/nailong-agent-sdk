import pytest

from nailong_agent_sdk.state.elastic import (
    MAX_ELASTIC_HANDOFF_CHARS,
    MAX_ELASTIC_HANDOFF_TOTAL_CHARS,
    ElasticRefusalCode,
    ElasticSpawnRequest,
    GraphSpawnStatus,
    check_spawn_request,
)
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.shared_state import DiscoveryRoutingRefs
from nailong_agent_sdk.tools.core.definitions import core_tool_definitions
from nailong_agent_sdk.tools.elastic_requests import ElasticRequestBuffer, ElasticRequestRejected
from tests.support.elastic import done_with, req
from tests.support.plans import n


def test_the_handoff_limits_are_the_documented_constants():
    assert (MAX_ELASTIC_HANDOFF_CHARS, MAX_ELASTIC_HANDOFF_TOTAL_CHARS) == (4_000, 8_000)


def test_a_request_may_carry_a_handoff_note_up_to_its_limit():
    assert req("a").handoff is None
    assert req("a", handoff="x" * MAX_ELASTIC_HANDOFF_CHARS).handoff == "x" * 4_000
    with pytest.raises(ValueError, match="handoff"):
        req("a", handoff="x" * (MAX_ELASTIC_HANDOFF_CHARS + 1))
    with pytest.raises(ValueError, match="handoff"):
        req("a", handoff="")
    with pytest.raises(ValueError, match="handoff must not be blank"):
        req("a", handoff="   \n")


def test_a_request_stored_without_a_handoff_still_loads_and_keeps_none():
    stored = {
        "request_id": "a",
        "scope": "the clock tree",
        "instructions": "Trace it.",
        "reason": "Ambiguous.",
    }
    assert ElasticSpawnRequest.model_validate(stored).handoff is None


def test_the_handoff_survives_the_graph_snapshot():
    graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=3)
    graph.mark_started("root")
    graph.mark_terminal("root", done_with(req("a", handoff="I checked A and B.")))
    restored = StateGraph.from_snapshot(graph.snapshot())
    assert restored.spawn_records[0].request.handoff == "I checked A and B."
    assert restored.nodes["elastic:root:a"].elastic.request.handoff == "I checked A and B."


def test_a_handoff_that_would_pass_the_total_is_refused_with_the_numbers():
    problem = check_spawn_request(
        req("b", handoff="y" * 3_000),
        parent_node_id="root",
        parent_routing_refs=DiscoveryRoutingRefs(),
        visible_dependencies=set(),
        taken_request_ids=set(),
        handoff_chars_used=6_000,
    )
    assert problem is not None and problem.code is ElasticRefusalCode.HANDOFF_LIMIT_REACHED
    assert problem.grantable is False
    for text in ('"b"', '"root"', "3000", "6000", "9000", "8000"):
        assert text in problem.message, (text, problem.message)
    fits = check_spawn_request(
        req("b", handoff="y" * 2_000),
        parent_node_id="root",
        parent_routing_refs=DiscoveryRoutingRefs(),
        visible_dependencies=set(),
        taken_request_ids=set(),
        handoff_chars_used=6_000,
    )
    assert fits is None


def test_a_result_whose_handoffs_pass_the_total_has_the_overflowing_request_refused():
    graph = StateGraph([n("root")], max_elastic_depth=1, max_elastic_nodes=8)
    graph.mark_started("root")
    result = done_with(
        req("a", handoff="a" * 4_000),
        req("a", handoff="dup" * 1_000),
        req("b", handoff="b" * 4_000),
        req("c", handoff="c" * 10),
        req("d"),
    )
    graph.mark_terminal("root", result)
    records = {record.request.request_id: record for record in graph.spawn_records}
    statuses = [(record.request.request_id, record.status) for record in graph.spawn_records]
    assert statuses == [
        ("a", GraphSpawnStatus.ACCEPTED),
        ("a", GraphSpawnStatus.REFUSED),
        ("b", GraphSpawnStatus.ACCEPTED),
        ("c", GraphSpawnStatus.REFUSED),
        ("d", GraphSpawnStatus.ACCEPTED),
    ]
    assert graph.spawn_records[1].code is ElasticRefusalCode.REQUEST_ID_DUPLICATE
    assert records["c"].code is ElasticRefusalCode.HANDOFF_LIMIT_REACHED
    assert "elastic-refused:ELASTIC_HANDOFF_LIMIT_REACHED:c" in graph.result("root").diagnostics


def test_the_queue_of_an_agent_refuses_a_handoff_over_the_total_and_keeps_what_fits():
    queue = ElasticRequestBuffer(
        node=n("root"), capacity=None, visible_dependencies=set(), limit=10
    )
    queue.add(req("a", handoff="a" * 4_000))
    queue.add(req("b", handoff="b" * 4_000))
    with pytest.raises(ElasticRequestRejected) as raised:
        queue.add(req("c", handoff="c" * 1))
    assert raised.value.code is ElasticRefusalCode.HANDOFF_LIMIT_REACHED
    assert "8000" in str(raised.value) and "8001" in str(raised.value)
    queue.add(req("d"))
    assert [item.request_id for item in queue.requests] == ["a", "b", "d"]


def test_the_request_tool_offers_the_handoff_field_to_the_model():
    tool = next(item for item in core_tool_definitions() if item.name == "request_elastic_node")
    handoff = tool.input_schema["properties"]["handoff"]
    assert handoff["type"] == "string" and handoff["maxLength"] == MAX_ELASTIC_HANDOFF_CHARS
    assert handoff["minLength"] == 1
    assert "handoff" in tool.description
    assert "handoff" not in tool.input_schema.get("required", [])
