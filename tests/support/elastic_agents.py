import json

from nailong_agent_sdk.agent.graph_agent_executor import GraphAgentBinding
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import ScopedAgentTask, TaskScope
from nailong_agent_sdk.state.elastic import ELASTIC_REQUEST_TOOL_NAME
from nailong_agent_sdk.tools.core.definitions import core_tool_definitions
from tests.support.agents import definition, final


def request_arguments(request_id="probe", **overrides):
    values = {
        "request_id": request_id,
        "scope": "clock tree",
        "instructions": "Trace the clock tree and list every divider.",
        "reason": "The section is ambiguous.",
    }
    values.update(overrides)
    return values


def elastic_tool():
    return next(item for item in core_tool_definitions() if item.name == ELASTIC_REQUEST_TOOL_NAME)


def binding(
    node_id, turns=None, *, tool=True, prompts=None, model=None, idempotent=False, escalate=False
):
    class Recording(ScriptedModel):
        async def next_turn(self, model_context):
            if prompts is not None:
                prompts.append(model_context.prompt.model_dump_json())
            return await super().next_turn(model_context)

    def task_adapter(node, context):
        return ScopedAgentTask(
            id=f"task-{node.node_id}",
            input={},
            scope=TaskScope(label=node.node_id),
            locked_interface={},
            instructions="original instructions",
            acceptance_criteria=["done"],
        )

    return GraphAgentBinding(
        node_id=node_id,
        definition=definition(tools=[elastic_tool()] if tool else []),
        task_adapter=task_adapter,
        model_factory=lambda node, context: model or Recording(turns or [final()]),
        idempotent=idempotent,
        escalate_elastic_overflow=escalate,
    )


def observed(context):
    parts = []
    for observation in context.observations:
        if observation.kind != "tool-result":
            continue
        parts.append(observation.message)
        if observation.result is not None:
            parts.append(json.dumps(observation.result.preview, sort_keys=True, default=str))
            parts.append(observation.result.error or "")
    return " ".join(parts)


def services_for(tmp_path):
    return AgentRuntimeServices.open(tmp_path)


def close(services):
    services.telemetry.close()
