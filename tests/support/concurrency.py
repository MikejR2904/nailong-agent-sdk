import asyncio

from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.state.project_state_store import FileProjectStateStore
from tests.support.agents import FnExecutor, definition, ok, task, tool

CALLS = 5


def turns_for(calls=CALLS):
    return [
        {"type": "tool-call", "call": {"id": f"c{j}", "name": "echo", "arguments": {"i": j}}}
        for j in range(calls)
    ] + [{"type": "final", "output": {"status": "complete"}}]


async def run_many(services, prefix, count, calls=CALLS):
    async def one(index):
        subject = services.create_agent(
            definition(tools=[tool("echo")], max_iterations=calls + 3),
            ScriptedModel(turns_for(calls)),
            tool_executor=FnExecutor(lambda t, c: ok({"v": c.call.id})),
        )
        return await subject.run(task(f"{prefix}-{index}"))

    return await asyncio.gather(*(one(i) for i in range(count)))


def verify(run_root, run_ids, calls=CALLS):
    services = AgentRuntimeServices.open(run_root)
    try:
        problems = []
        for run_id in run_ids:
            telemetry_break = services.telemetry.chain_break(run_id)
            audit_break = services.audit_logs.chain_break(run_id)
            if telemetry_break is not None:
                problems.append(("telemetry", run_id, telemetry_break.kind))
            if audit_break is not None:
                problems.append(("audit", run_id, audit_break.kind))
            events = list(services.telemetry.iter_events(run_id))
            if sum(e.event_type == "agent.tool-completed" for e in events) != calls:
                problems.append(("tool-events", run_id, len(events)))
        store = FileProjectStateStore(run_root)
        for run_id in run_ids:
            try:
                store.load(run_id)
            except Exception as error:
                problems.append(
                    ("project-state", run_id, f"{type(error).__name__}: {str(error)[:80]}")
                )
        handles = sorted((run_root / ".agent-tool-results").glob("*.json"))
        return problems, len(handles)
    finally:
        services.telemetry.close()
        services.audit_logs.close()
