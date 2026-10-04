import time
import tracemalloc

from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from tests.support.agents import (
    FnExecutor,
    agent,
    arun,
    definition,
    final,
    ok,
    task,
    tool,
    tool_call,
)


def long_run(count, *, with_services=False, tmp_path=None):
    turns = [tool_call(f"c{i}", "echo", {"i": i}) for i in range(count)] + [final()]
    executor = FnExecutor(lambda t, c: ok({"echo": c.call.arguments, "pad": "x" * 200}))
    services = None
    if with_services:
        from nailong_agent_sdk.agent.model import ScriptedModel
        from nailong_agent_sdk.agent.runtime import AgentRuntimeServices

        services = AgentRuntimeServices.open(tmp_path)
        subject = services.create_agent(
            definition(tools=[tool("echo")], max_iterations=count + 5),
            ScriptedModel(turns),
            tool_executor=executor,
        )
    else:
        subject, _ = agent(
            turns,
            executor=executor,
            definition_=definition(tools=[tool("echo")], max_iterations=count + 5),
        )
    started = time.monotonic()
    result = arun(subject.run(task("soak")), timeout=1500)
    elapsed = time.monotonic() - started
    if services is not None:
        services.telemetry.close()
        services.audit_logs.close()
    return result, elapsed


def test_in_memory_run_cost_grows_with_the_number_of_tool_calls():
    timings = {}
    for count in (50, 100, 200, 400):
        result, elapsed = long_run(count)
        assert result.status is AgentRunStatus.COMPLETED
        timings[count] = round(elapsed, 2)
    assert timings[400] < 4 * timings[200] * 1.5, timings


def test_durable_run_cost_with_runtime_services(tmp_path):
    timings = {}
    for count in (50, 100, 200):
        result, elapsed = long_run(count, with_services=True, tmp_path=tmp_path / f"n{count}")
        assert result.status is AgentRunStatus.COMPLETED
        timings[count] = round(elapsed, 2)


def test_memory_growth_over_one_hundred_sequential_agents():
    tracemalloc.start()
    baseline = None
    samples = []
    for index in range(100):
        subject, _ = agent(
            [tool_call("c1", "echo", {}), final()],
            executor=FnExecutor(lambda t, c: ok({"v": 1})),
            definition_=definition(tools=[tool("echo")]),
        )
        arun(subject.run(task(f"mem-{index}")))
        if index in (9, 49, 99):
            current, _ = tracemalloc.get_traced_memory()
            samples.append(current)
        if index == 9:
            baseline = samples[0]
    tracemalloc.stop()
    assert samples[-1] < baseline * 3 + 5_000_000, samples
