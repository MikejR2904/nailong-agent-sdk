import enum

import pytest

from nailong_agent_sdk.agent.orchestrator import AgentExecutionProfile, OrchestrationPolicy
from nailong_agent_sdk.foundations.contracts import (
    FallbackModelBinding,
    ModelBinding,
    ToolBatchTurn,
    ToolCall,
)
from nailong_agent_sdk.foundations.identifiers import repeated_values, require_unique
from nailong_agent_sdk.foundations.optimization.models import PckpItem, PckpProblem
from nailong_agent_sdk.mcp.client import McpClientManager
from nailong_agent_sdk.mcp.client_types import McpHttpServerConfig, McpStdioServerConfig
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.specifications.documents import (
    DocumentFormat,
    SpecificationDocument,
    SpecificationManifest,
)
from nailong_agent_sdk.specifications.evidence_graph import (
    EvidenceGraph,
    EvidenceNode,
    EvidenceNodeKind,
    EvidenceRelation,
    EvidenceRelationKind,
)
from nailong_agent_sdk.specifications.retrieval_models import RetrievalQuery
from nailong_agent_sdk.state.elastic import ElasticNodeRole, ElasticNodeSpec
from nailong_agent_sdk.state.graph import StateGraph
from nailong_agent_sdk.state.graph_models import GraphEdge, GraphNode, GraphNodeKind
from nailong_agent_sdk.state.planning import DependencyProof, DependencyRule, Plan
from nailong_agent_sdk.state.project_state_models import StageStateSchema
from nailong_agent_sdk.state.shared_state import DiscoveryRoutingRefs
from nailong_agent_sdk.tools.registry import HarnessToolRegistry
from tests.support.agents import definition, tool
from tests.support.elastic import req as elastic_request
from tests.support.orchestration import make_policy, make_request
from tests.support.plans import task as plan_task
from tests.support.specs import sref


class Colour(enum.StrEnum):
    RED = "red"
    BLUE = "blue"


def test_each_repeated_value_is_listed_once_in_the_order_it_first_repeats():
    assert repeated_values(["a", "b", "a", "c", "b", "a"]) == ["a", "b"]
    assert repeated_values(["a", "b", "c"]) == []
    assert repeated_values([(1, 2), (1, 2), (3, 4)]) == [(1, 2)]


def test_a_collection_without_repeats_passes_silently():
    require_unique(["a", "b"], "names")
    require_unique([], "names")


def test_the_error_names_the_label_and_quotes_every_repeat():
    with pytest.raises(ValueError) as raised:
        require_unique(["a", "b", "a", "b"], "plan task IDs")
    assert str(raised.value) == 'plan task IDs must be unique; repeated: "a", "b".'


def test_enum_members_are_reported_by_value():
    with pytest.raises(ValueError) as raised:
        require_unique([Colour.RED, Colour.BLUE, Colour.RED], "colours")
    assert str(raised.value) == 'colours must be unique; repeated: "red".'


def test_a_long_list_of_repeats_is_cut_after_ten_and_counted():
    values = [f"id-{index}" for index in range(15)] * 2
    with pytest.raises(ValueError) as raised:
        require_unique(values, "ids")
    message = str(raised.value)
    assert message.startswith('ids must be unique; repeated: "id-0", "id-1"')
    assert '"id-9" and 5 more.' in message and "id-10" not in message


def policy_data(**changes):
    return {**make_policy().model_dump(mode="json"), **changes}


def profile_data(**changes):
    return {**make_policy().profiles[0].model_dump(mode="json"), **changes}


def evidence_node(node_id, **changes):
    return EvidenceNode(
        node_id=node_id,
        kind=EvidenceNodeKind.SIGNAL,
        content="c",
        source=sref(),
        token_cost=1,
        **changes,
    )


def relation(source, target):
    return EvidenceRelation(
        from_node_id=source,
        to_node_id=target,
        kind=EvidenceRelationKind.REQUIRES,
        source=sref(),
    )


def manifest_document(document_id):
    return SpecificationDocument(
        id=document_id,
        title="t",
        format=DocumentFormat.MD,
        path=f"{document_id}.md",
        category="functional",
    )


def agent_node(node_id, dependencies=()):
    return GraphNode(node_id=node_id, kind=GraphNodeKind.AGENT, dependencies=list(dependencies))


def pckp_item(item_id, prerequisites=()):
    return PckpItem(item_id=item_id, token_cost=1, utility=1, prerequisites=list(prerequisites))


DEFAULT_TOOLS = HarnessToolRegistry.default_tools()

CASES = {
    "agent tool names": (
        lambda: definition(tools=[tool("a"), tool("a")]),
        'tool names must be unique; repeated: "a"',
    ),
    "tool call dependencies": (
        lambda: ToolCall(id="c1", name="x", depends_on_call_ids=["c0", "c0"]),
        'tool-call dependencies must be unique; repeated: "c0"',
    ),
    "tool call on itself": (
        lambda: ToolCall(id="c1", name="x", depends_on_call_ids=["c1"]),
        'tool call "c1" cannot depend on itself',
    ),
    "tool batch call ids": (
        lambda: ToolBatchTurn(calls=[ToolCall(id="c1", name="x"), ToolCall(id="c1", name="x")]),
        'tool batch call IDs must be unique; repeated: "c1"',
    ),
    "fallback model": (
        lambda: ModelBinding(
            provider="p", model="m", fallbacks=[FallbackModelBinding(provider="p", model="m")]
        ),
        'model fallback binding "p/m" must be distinct from the primary model',
    ),
    "pckp prerequisites": (
        lambda: pckp_item("x", ["y", "y"]),
        'PCKP item "x" prerequisites must be unique; repeated: "y"',
    ),
    "pckp item on itself": (
        lambda: pckp_item("x", ["x"]),
        'PCKP item "x" cannot require itself',
    ),
    "pckp item ids": (
        lambda: PckpProblem(token_budget=5, items=[pckp_item("x"), pckp_item("x")]),
        'PCKP item IDs must be unique; repeated: "x"',
    ),
    "plan task ids": (
        lambda: Plan(plan_id="p", tasks=[plan_task("T1"), plan_task("T1")]),
        'plan task IDs must be unique; repeated: "T1"',
    ),
    "plan task dependencies": (
        lambda: plan_task("T2", deps=["T1", "T1"]),
        'dependencies must be unique; repeated: "T1"',
    ),
    "dependency proof endpoints": (
        lambda: DependencyProof(
            parent_task_id="T1",
            child_task_id="T1",
            shared_signal_ids=["s"],
            applied_rule=DependencyRule.PRODUCER_TO_CONSUMER,
            source_spans=["span"],
        ),
        'dependency proof endpoints must be distinct; both are "T1"',
    ),
    "dependency proof signals": (
        lambda: DependencyProof(
            parent_task_id="T1",
            child_task_id="T2",
            shared_signal_ids=["s", "s"],
            applied_rule=DependencyRule.PRODUCER_TO_CONSUMER,
            source_spans=["span"],
        ),
        'dependency proof signal IDs must be unique; repeated: "s"',
    ),
    "graph node dependencies": (
        lambda: agent_node("a", ["b", "b"]),
        'node "a" dependencies must be unique; repeated: "b"',
    ),
    "graph node on itself": (
        lambda: agent_node("a", ["a"]),
        'node "a" cannot depend on itself',
    ),
    "graph edge endpoints": (
        lambda: GraphEdge(parent_node_id="a", child_node_id="a"),
        'graph edge endpoints must be distinct; both are "a"',
    ),
    "graph node ids": (
        lambda: StateGraph([agent_node("a"), agent_node("a")]),
        'graph node IDs must be unique; repeated: "a"',
    ),
    "elastic joins": (
        lambda: ElasticNodeSpec(
            role=ElasticNodeRole.JOIN, parent_node_id="p", root_node_id="p", joins=["x", "x"]
        ),
        'joins must be unique; repeated: "x"',
    ),
    "elastic request dependencies": (
        lambda: elastic_request("a", dependencies=["x", "x"]),
        'dependencies must be unique; repeated: "x"',
    ),
    "evidence aliases": (
        lambda: evidence_node("n1", aliases=["x", "x"]),
        'Evidence node "n1" aliases must be unique; repeated: "x"',
    ),
    "evidence relation endpoints": (
        lambda: relation("a", "a"),
        'Evidence relation endpoints must be distinct; both are "a"',
    ),
    "evidence node ids": (
        lambda: EvidenceGraph(snapshot_id="s", nodes=[evidence_node("a"), evidence_node("a")]),
        'Evidence node IDs must be unique; repeated: "a"',
    ),
    "evidence relation to an unknown node": (
        lambda: EvidenceGraph(
            snapshot_id="s", nodes=[evidence_node("a")], relations=[relation("a", "ghost")]
        ),
        "Evidence relations must reference known nodes; unknown: ['ghost']",
    ),
    "manifest document ids": (
        lambda: SpecificationManifest(documents=[manifest_document("d1"), manifest_document("d1")]),
        'specification document IDs must be unique; repeated: "d1"',
    ),
    "retrieval categories": (
        lambda: RetrievalQuery(
            snapshot_id="s",
            query_text="q",
            allowed_categories=["functional"] * 2,
        ),
        'retrieval allowed_categories must be unique; repeated: "functional"',
    ),
    "policy skill ids": (
        lambda: OrchestrationPolicy.model_validate(
            policy_data(skills=[*policy_data()["skills"], policy_data()["skills"][0]])
        ),
        'Orchestration skill IDs must be unique; repeated: "s1"',
    ),
    "profile allowed skills": (
        lambda: AgentExecutionProfile.model_validate(profile_data(allowed_skill_ids=["s1", "s1"])),
        'Agent profile allowed_skill_ids must be unique; repeated: "s1"',
    ),
    "profile required skills": (
        lambda: AgentExecutionProfile.model_validate(profile_data(required_skill_ids=["s2"])),
        "Agent profile required skills ['s2'] must be permitted skills.",
    ),
    "request selected skills": (
        lambda: make_request(skills=("s1", "s1")),
        'Selected skill IDs must be unique; repeated: "s1"',
    ),
    "mcp server names": (
        lambda: McpClientManager(
            [
                McpStdioServerConfig(name="a", command="x"),
                McpHttpServerConfig(name="a", url="http://x"),
            ]
        ),
        'MCP server names must be unique; repeated: "a"',
    ),
    "harness tool names": (
        lambda: HarnessToolRegistry([*DEFAULT_TOOLS, DEFAULT_TOOLS[0]]),
        f'Harness tool names must be unique; repeated: "{DEFAULT_TOOLS[0].name}"',
    ),
    "action episode dependencies": (
        lambda: InMemoryEpisodeStore().open_action("owner", ["e1", "e1"]),
        'action episode dependencies must be unique; repeated: "e1"',
    ),
    "stage schema fields": (
        lambda: StageStateSchema(schema_id="s", stage="st", required_field_ids=["a", "a"]),
        'required stage field IDs must be unique; repeated: "a"',
    ),
    "routing references": (
        lambda: DiscoveryRoutingRefs(requirement_ids=["r", "r"]),
        'requirement_ids entries must be unique; repeated: "r"',
    ),
}


@pytest.mark.parametrize(("build", "message"), CASES.values(), ids=list(CASES))
def test_a_rejected_collection_names_the_value_that_repeats(build, message):
    with pytest.raises(ValueError) as raised:
        build()
    assert message in str(raised.value)
