import asyncio

from nailong_agent_sdk.foundations.contracts import AgentRunStatus, ToolConcurrency
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.agents import (
    FnExecutor,
    agent,
    arun,
    batch,
    call,
    definition,
    final,
    ok,
    task,
    tool,
    tool_call,
)
from tests.support.base_agent_cases import ECHO, echo_executor


def test_huge_tool_output_is_projected_not_replayed():
    huge = "x" * 5_000_000
    executor = FnExecutor(lambda t, c: ok({"blob": huge}))
    subject, model = agent(
        [tool_call("c1", "echo"), final()], executor=executor, definition_=definition(tools=[ECHO])
    )
    result = arun(subject.run(task()))
    observation = model.calls[1].observations[0]
    assert result.status is AgentRunStatus.COMPLETED
    assert len(observation.model_dump_json()) < 20_000
    assert len(str(result.project_state)) < 50_000


def test_parallel_safe_batch_concurrency_is_bounded():
    peak = {"now": 0, "max": 0}
    parallel = tool("par", concurrency=ToolConcurrency.PARALLEL_SAFE)

    async def handler(tool_definition, context):
        peak["now"] += 1
        peak["max"] = max(peak["max"], peak["now"])
        await asyncio.sleep(0.05)
        peak["now"] -= 1
        return ok()

    calls = [call(f"c{i}", "par") for i in range(300)]
    subject, _ = agent(
        [batch(*calls), final()],
        executor=FnExecutor(handler),
        definition_=definition(tools=[parallel]),
    )
    result = arun(subject.run(task()))
    assert result.status is AgentRunStatus.COMPLETED
    assert peak["max"] <= 32, f"{peak['max']} parallel-safe calls ran at once"


def test_serial_tool_runs_alone_and_dependencies_order(tmp_path):
    order = []

    async def handler(tool_definition, context):
        order.append(("start", context.call.id))
        await asyncio.sleep(0.05)
        order.append(("end", context.call.id))
        return ok()

    serial = tool("ser")
    subject, _ = agent(
        [
            batch(call("a", "ser"), call("b", "ser"), call("c", "ser", depends_on_call_ids=["a"])),
            final(),
        ],
        executor=FnExecutor(handler),
        definition_=definition(tools=[serial]),
    )
    arun(subject.run(task()))
    starts = [item[1] for item in order if item[0] == "start"]
    assert starts == ["a", "b", "c"]
    for index in range(0, len(order) - 1, 2):
        assert order[index][0] == "start" and order[index + 1][0] == "end"


def test_telemetry_and_audit_record_the_run(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    audit = AuditTranscriptStore(tmp_path)
    try:
        subject, _ = agent(
            [tool_call("c1", "echo", {"q": 1}), final()],
            executor=echo_executor(),
            definition_=definition(tools=[ECHO]),
            telemetry=telemetry,
            audit_logs=audit,
        )
        result = arun(subject.run(task("audited")))
        assert result.status is AgentRunStatus.COMPLETED
        events = telemetry.list_events("audited", limit=500)
        types = [e.event_type for e in events]
        assert (
            "agent.run-started" in types
            and "agent.terminated" in types
            and "agent.profile-completed" in types
        )
        assert telemetry.verify_run_chain("audited") is True
        entries = audit.list_entries("audited")
        kinds = [e.event_type for e in entries]
        assert "task-received" in kinds and "model-turn" in kinds and "tool-result" in kinds
        assert audit.verify("audited") is True
        metrics = {
            m.metric_id: m.value for m in telemetry.list_metrics("audited") if m.value is not None
        }
        assert (
            metrics["agent.tool_call_attempt_count"] == 1
            and metrics["agent.model_turn_attempt_count"] == 2
        )
    finally:
        telemetry.close()


def test_terminal_metrics_are_per_run_and_not_truncated(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        executor = echo_executor()
        counts = []
        for _ in range(2):
            subject, _ = agent(
                [
                    tool_call("c1", "echo"),
                    tool_call("c2", "echo"),
                    tool_call("c3", "echo"),
                    final(),
                ],
                executor=executor,
                definition_=definition(tools=[ECHO], max_iterations=6),
                telemetry=telemetry,
            )
            arun(subject.run(task("shared-task-id")))
            observed = [
                m.value
                for m in telemetry.list_metrics("shared-task-id")
                if m.metric_id == "agent.tool_call_attempt_count"
            ]
            counts.append(observed[-1])
        assert counts == [3.0, 3.0], counts
    finally:
        telemetry.close()


def test_terminal_metrics_survive_long_runs(tmp_path):
    telemetry = TelemetryStore(tmp_path)
    try:
        executor = echo_executor()
        turns = 150
        scripted = [tool_call(f"x{i}", "echo") for i in range(turns)] + [final()]
        long_agent, _ = agent(
            scripted,
            executor=executor,
            definition_=definition(tools=[ECHO], max_iterations=turns + 5),
            telemetry=telemetry,
        )
        arun(long_agent.run(task("long-run")), timeout=600)
        values = {
            m.metric_id: m.value for m in telemetry.list_metrics("long-run") if m.value is not None
        }
        assert values["agent.tool_call_attempt_count"] == float(turns), values
    finally:
        telemetry.close()
