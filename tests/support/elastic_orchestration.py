from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentBinding
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    EscalationTarget,
    MemoryScope,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    VersionedInstructions,
)
from nailong_agent_sdk.state.elastic import ELASTIC_REQUEST_TOOL_NAME
from nailong_agent_sdk.tools.core.definitions import core_tool_definitions
from tests.support.agents import COMPLETE_SCHEMA, final
from tests.support.elastic_agents import observed
from tests.support.orchestration import make_policy

TOOLS = ("brief", ELASTIC_REQUEST_TOOL_NAME)
CAPABILITIES = ("utility.brief", "graph.elastic.request")
CORE = {item.name: item for item in core_tool_definitions()}
REQUEST = {
    "request_id": "probe",
    "scope": "clock tree",
    "instructions": "Trace the clock tree and list every divider.",
    "reason": "The section is ambiguous.",
}


def elastic_policy(**overrides):
    values = {"tools": TOOLS, "capabilities": CAPABILITIES}
    values.update(overrides)
    return make_policy(**values)


def elastic_factory(
    seen,
    prompts,
    *,
    root_turns,
    child_turns=None,
    join_turns=None,
    elastic_tools=("brief",),
    escalate=False,
    idempotent=None,
    child_model=None,
):
    def factory(context):
        seen.append(context)
        role = "root" if context.elastic is None else context.elastic.role.value
        node_id = context.assignment.node_id
        if role == "root":
            turns = root_turns.get(node_id, [final()])
            names = TOOLS
        elif role == "child":
            turns = child_turns or [final({"status": "complete", "finding": "none"})]
            names = elastic_tools
        else:
            turns = join_turns or [final()]
            names = elastic_tools
        definition = AgentDefinition(
            identity=context.assignment.agent_identity,
            instructions=VersionedInstructions(version="v1", text="work"),
            input_schema={"type": "object"},
            tools=[CORE[name] for name in names],
            model_binding=context.model.binding,
            output_schema=COMPLETE_SCHEMA,
            memory_scope=MemoryScope.TASK_SCOPED,
            termination_policy=TerminationPolicy(
                max_iterations=4, status_field="status", escalation=EscalationTarget.NONE
            ),
        )

        def task_adapter(node, node_context):
            return ScopedAgentTask(
                id=f"task-{node.node_id}",
                input={},
                scope=TaskScope(label=node.node_id),
                locked_interface={},
                instructions=context.execution_task.instructions,
                acceptance_criteria=["done"],
            )

        class Recording(ScriptedModel):
            async def next_turn(self, model_context):
                texts = prompts.setdefault(node_id, [])
                texts.append(model_context.prompt.model_dump_json())
                results = observed(model_context)
                if results:
                    texts.append(results)
                return await super().next_turn(model_context)

        def model_factory(node, node_context):
            if role == "child" and child_model is not None:
                return child_model(context)
            return Recording(turns)

        return GraphAgentBinding(
            node_id=node_id,
            definition=definition,
            task_adapter=task_adapter,
            model_factory=model_factory,
            escalate_elastic_overflow=escalate,
            idempotent=idempotent(context) if idempotent is not None else False,
        )

    return factory


def graph_of(orchestrator, executed):
    return orchestrator.controller_runtime()._harness.get_run_state(executed.graph_run_id).graph
