import pytest

from nailong_agent_sdk.foundations.contracts import (
    EpisodeKind,
    ToolConcurrency,
    ToolDefinition,
    ToolExecutionResult,
)
from nailong_agent_sdk.state.elastic import (
    ELASTIC_REQUEST_TOOL_NAME,
    MAX_ELASTIC_REQUESTS_PER_RESULT,
    ElasticCapacity,
    ElasticRefusalCode,
)
from nailong_agent_sdk.state.shared_state import DiscoveryRoutingRefs
from nailong_agent_sdk.tools.core.definitions import core_tool_definitions
from nailong_agent_sdk.tools.elastic_requests import (
    ElasticRequestBuffer,
    ElasticRequestRejected,
    ElasticRequestToolExecutor,
)
from nailong_agent_sdk.tools.policy import CapabilityGrant, SideEffectClass
from nailong_agent_sdk.tools.registry import HarnessToolExecutor, HarnessToolRegistry
from tests.support.elastic import req
from tests.support.plans import n
from tests.support.tools import all_capabilities, build, invocation, run


def definition():
    return next(item for item in core_tool_definitions() if item.name == ELASTIC_REQUEST_TOOL_NAME)


def buffer(**overrides):
    values = {
        "node": n("root", routing_refs=DiscoveryRoutingRefs(signal_ids=["clk"])),
        "capacity": ElasticCapacity(node_depth=0, max_depth=1, nodes_used=0, max_nodes=3),
        "visible_dependencies": {"pre"},
    }
    values.update(overrides)
    return ElasticRequestBuffer(**values)


def arguments(request_id="a", **overrides):
    values = {
        "request_id": request_id,
        "scope": "the clock tree",
        "instructions": "Trace the clock tree.",
        "reason": "The section is ambiguous.",
    }
    values.update(overrides)
    return values


class Recording:
    def __init__(self):
        self.calls = []

    async def execute(self, tool, context):
        self.calls.append(tool.name)
        return ToolExecutionResult(status="succeeded", output={"ran": tool.name})


def call_tool(executor, name, args):
    tool = definition() if name == ELASTIC_REQUEST_TOOL_NAME else _other(name)
    return run(executor.execute(tool, invocation(name, args)))


def _other(name):
    return ToolDefinition(
        name=name,
        description="d",
        input_schema={"type": "object"},
        episode_kind=EpisodeKind.EXPLORATORY,
    )


def test_the_tool_is_a_serial_action_with_a_strict_schema():
    tool = definition()
    assert tool.episode_kind is EpisodeKind.ACTION
    assert tool.concurrency is ToolConcurrency.SERIAL
    schema = tool.input_schema
    assert schema["required"] == ["request_id", "scope", "instructions", "reason"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "request_id",
        "scope",
        "instructions",
        "reason",
        "handoff",
        "dependencies",
        "routing_refs",
    }


def test_the_tool_is_registered_with_a_capability_a_profile_can_grant():
    registered = HarnessToolRegistry().resolve(ELASTIC_REQUEST_TOOL_NAME)
    assert registered.capability == "graph.elastic.request"
    assert registered.side_effect is SideEffectClass.READ_ONLY
    assert "graph.elastic.request" in all_capabilities()


def test_the_schema_accepts_exactly_the_request_ids_the_model_accepts():
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(definition().input_schema["properties"]["request_id"])
    for good in ("a", "probe-1", "x" * 128, "a.b_c-d"):
        assert not list(validator.iter_errors(good)), good
    for bad in ("", "has space", "trailing.", "-lead", "a:b", "x" * 129):
        assert list(validator.iter_errors(bad)), bad


def test_a_valid_request_is_queued_and_reports_what_remains():
    queue = buffer()
    receipt = queue.add(req("a", dependencies=["pre"]))
    assert receipt == {
        "request_id": "a",
        "status": "queued",
        "queued_requests": 1,
        "child_depth": 1,
        "elastic_nodes_remaining_after_queue": 1,
        "capacity_decision_required": False,
        "runs": "after this task completes; a join node then resumes this task with the findings",
    }
    assert queue.requests == [req("a", dependencies=["pre"])]
    assert queue.add(req("b"))["elastic_nodes_remaining_after_queue"] == 0
    queue.requests.clear()
    assert len(queue.requests) == 2


def test_the_queue_refuses_what_the_graph_would_refuse_and_says_why():
    queue = buffer()
    queue.add(req("a"))
    with pytest.raises(ElasticRequestRejected) as duplicate:
        queue.add(req("a"))
    assert duplicate.value.code is ElasticRefusalCode.REQUEST_ID_DUPLICATE
    with pytest.raises(ElasticRequestRejected) as hidden:
        queue.add(req("b", dependencies=["secret"]))
    assert hidden.value.code is ElasticRefusalCode.DEPENDENCY_NOT_VISIBLE
    assert '"secret"' in str(hidden.value) and '"pre"' in str(hidden.value)
    with pytest.raises(ElasticRequestRejected) as wide:
        queue.add(req("c", routing_refs=DiscoveryRoutingRefs(signal_ids=["clk", "rst"])))
    assert wide.value.code is ElasticRefusalCode.ROUTING_REFS_NOT_INHERITED
    assert len(queue.requests) == 1


def test_the_queue_checks_capacity_before_the_run_ends():
    shallow = buffer(capacity=ElasticCapacity(node_depth=1, max_depth=1, nodes_used=0, max_nodes=3))
    with pytest.raises(ElasticRequestRejected) as depth:
        shallow.add(req("a"))
    assert depth.value.code is ElasticRefusalCode.DEPTH_CAP_REACHED
    assert "max_elastic_depth 1" in str(depth.value)
    tight = buffer(capacity=ElasticCapacity(node_depth=0, max_depth=1, nodes_used=1, max_nodes=3))
    tight.add(req("a"))
    with pytest.raises(ElasticRequestRejected) as nodes:
        tight.add(req("b"))
    assert nodes.value.code is ElasticRefusalCode.NODE_CAP_REACHED
    assert "2 requested plus the join node" in str(nodes.value)
    assert "1 already used" in str(nodes.value)
    assert len(tight.requests) == 1
    unknown = buffer(capacity=None)
    for index in range(5):
        unknown.add(req(f"r{index}"))
    assert len(unknown.requests) == 5


def test_an_escalating_queue_accepts_overflow_and_says_a_decision_will_be_needed():
    tight = buffer(
        capacity=ElasticCapacity(node_depth=0, max_depth=1, nodes_used=1, max_nodes=3),
        escalate_overflow=True,
    )
    first = tight.add(req("a"))
    second = tight.add(req("b"))
    assert first["capacity_decision_required"] is False
    assert second["capacity_decision_required"] is True
    assert second["elastic_nodes_remaining_after_queue"] == 0
    assert "controller grants more elastic capacity or declines" in second["runs"]
    shallow = buffer(
        capacity=ElasticCapacity(node_depth=1, max_depth=1, nodes_used=0, max_nodes=3),
        escalate_overflow=True,
    )
    assert shallow.add(req("a"))["capacity_decision_required"] is True
    assert len(tight.requests) == 2 and len(shallow.requests) == 1


def test_an_escalating_queue_still_refuses_requests_that_are_invalid():
    queue = buffer(
        capacity=ElasticCapacity(node_depth=1, max_depth=1, nodes_used=0, max_nodes=3),
        escalate_overflow=True,
    )
    with pytest.raises(ElasticRequestRejected) as hidden:
        queue.add(req("a", dependencies=["secret"]))
    assert hidden.value.code is ElasticRefusalCode.DEPENDENCY_NOT_VISIBLE
    queue.add(req("a"))
    with pytest.raises(ElasticRequestRejected) as duplicate:
        queue.add(req("a"))
    assert duplicate.value.code is ElasticRefusalCode.REQUEST_ID_DUPLICATE


def test_the_queue_has_a_per_task_limit():
    queue = buffer(
        capacity=ElasticCapacity(node_depth=0, max_depth=1, nodes_used=0, max_nodes=100), limit=2
    )
    queue.add(req("a"))
    queue.add(req("b"))
    with pytest.raises(ElasticRequestRejected) as full:
        queue.add(req("c"))
    assert full.value.code is ElasticRefusalCode.REQUEST_LIMIT_REACHED
    assert "2" in str(full.value)
    assert (
        ElasticRequestBuffer(node=n("root"), capacity=None, visible_dependencies=set())._limit
        == MAX_ELASTIC_REQUESTS_PER_RESULT
    )


def test_other_tools_pass_through_to_the_wrapped_executor():
    inner = Recording()
    executor = ElasticRequestToolExecutor(inner, buffer())
    result = call_tool(executor, "read_file", {"path": "a"})
    assert result.status == "succeeded" and result.output == {"ran": "read_file"}
    assert inner.calls == ["read_file"]


def test_without_a_wrapped_executor_other_tools_fail_naming_the_tool():
    executor = ElasticRequestToolExecutor(None, buffer())
    result = call_tool(executor, "read_file", {"path": "a"})
    assert result.status == "failed"
    assert '"read_file"' in result.error and "no tool executor" in result.error.lower()


def test_a_valid_call_queues_the_request_and_returns_the_receipt():
    inner = Recording()
    queue = buffer()
    executor = ElasticRequestToolExecutor(inner, queue)
    result = call_tool(executor, ELASTIC_REQUEST_TOOL_NAME, arguments("probe"))
    assert result.status == "succeeded"
    assert result.output["request_id"] == "probe" and result.output["status"] == "queued"
    assert [item.request_id for item in queue.requests] == ["probe"]
    assert inner.calls == []


def test_a_malformed_call_fails_with_the_field_that_is_wrong():
    queue = buffer()
    executor = ElasticRequestToolExecutor(None, queue)
    result = call_tool(executor, ELASTIC_REQUEST_TOOL_NAME, arguments("bad id"))
    assert result.status == "failed"
    assert f"{ELASTIC_REQUEST_TOOL_NAME} arguments are invalid" in result.error
    assert "request_id" in result.error
    assert result.failure.code == "ELASTIC_REQUEST_INVALID"
    assert queue.requests == []


def test_a_refused_call_fails_with_the_code_and_the_numbers():
    queue = buffer(capacity=ElasticCapacity(node_depth=1, max_depth=1, nodes_used=0, max_nodes=3))
    executor = ElasticRequestToolExecutor(None, queue)
    result = call_tool(executor, ELASTIC_REQUEST_TOOL_NAME, arguments("a"))
    assert result.status == "failed"
    assert "max_elastic_depth 1" in result.error
    assert result.failure.code == "ELASTIC_DEPTH_CAP_REACHED"
    assert result.failure.details["request_id"] == "a"


def test_the_harness_executor_explains_that_the_tool_needs_a_graph_node(tmp_path):
    grants = [CapabilityGrant(role="worker", capabilities=["graph.elastic.request"])]
    executor, _ = build(tmp_path, grants=grants)
    assert isinstance(executor, HarnessToolExecutor)
    result = run(executor.execute(definition(), invocation(ELASTIC_REQUEST_TOOL_NAME, arguments())))
    assert result.status == "failed"
    assert "GraphAgentExecutor" in result.error and ELASTIC_REQUEST_TOOL_NAME in result.error
