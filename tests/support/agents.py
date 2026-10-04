import asyncio

from pydantic import TypeAdapter

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    AgentTurn,
    EpisodeKind,
    EscalationTarget,
    MemoryScope,
    ModelBinding,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    ToolConcurrency,
    ToolDefinition,
    ToolExecutionResult,
    VersionedInstructions,
)

COMPLETE_SCHEMA = {
    "type": "object",
    "properties": {"status": {"const": "complete"}},
    "required": ["status"],
    "additionalProperties": True,
}

TURN_ADAPTER: TypeAdapter[AgentTurn] = TypeAdapter(AgentTurn)


def tool(
    name,
    kind=EpisodeKind.EXPLORATORY,
    concurrency=ToolConcurrency.SERIAL,
    schema=None,
    requires_manifest=False,
):
    return ToolDefinition(
        name=name,
        description=f"tool {name}",
        input_schema=schema or {"type": "object", "additionalProperties": True},
        episode_kind=kind,
        concurrency=concurrency,
        requires_manifest=requires_manifest,
    )


def definition(
    *,
    tools=(),
    max_iterations=4,
    escalation=EscalationTarget.NONE,
    output_schema=None,
    gate=None,
    memory_scope=MemoryScope.TASK_SCOPED,
    memory_rationale=None,
):
    return AgentDefinition(
        identity="audit-agent",
        instructions=VersionedInstructions(version="v1", text="Do the task."),
        input_schema={"type": "object"},
        tools=list(tools),
        model_binding=ModelBinding(provider="fake", model="fake-1"),
        output_schema=output_schema or COMPLETE_SCHEMA,
        memory_scope=memory_scope,
        memory_rationale=memory_rationale,
        termination_policy=TerminationPolicy(
            max_iterations=max_iterations, status_field="status", escalation=escalation
        ),
        verification_gate_id=gate,
    )


def task(task_id="t1", input=None, boundaries=None):
    return ScopedAgentTask(
        id=task_id,
        input=input or {},
        scope=TaskScope(label="scope", boundaries=boundaries or {}),
        locked_interface={},
        instructions="do the thing",
        acceptance_criteria=["done"],
    )


class FnExecutor:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def execute(self, tool_definition, context):
        self.calls.append(context)
        result = self.handler(tool_definition, context)
        if asyncio.iscoroutine(result):
            result = await result
        return result


class DynamicModel:
    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    async def next_turn(self, context):
        self.calls.append(context)
        return TURN_ADAPTER.validate_python(self.fn(context))


def results_of(context):
    return {o.tool_call_id: o for o in context.observations if o.kind == "tool-result"}


def ok(output=None):
    return ToolExecutionResult(status="succeeded", output=output)


def final(output=None):
    return {"type": "final", "output": output if output is not None else {"status": "complete"}}


def call(call_id, name, arguments=None, **extra):
    return {"id": call_id, "name": name, "arguments": arguments or {}, **extra}


def tool_call(call_id, name, arguments=None, **extra):
    return {"type": "tool-call", "call": call(call_id, name, arguments, **extra)}


def batch(*calls):
    return {"type": "tool-batch", "calls": list(calls)}


def agent(turns, *, executor=None, definition_=None, **kwargs):
    model = ScriptedModel(turns)
    return BaseAgent(
        definition_ or definition(),
        model,
        tool_executor=executor,
        **kwargs,
    ), model


def arun(coro, timeout=120):
    async def guarded():
        return await asyncio.wait_for(coro, timeout)

    return asyncio.run(guarded())
