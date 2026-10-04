import sys

from nailong_agent_sdk.agent.base_agent import BaseAgent
from nailong_agent_sdk.foundations.contracts import AgentRunStatus, EpisodeKind
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from tests.support.agents import (
    DynamicModel,
    FnExecutor,
    agent,
    arun,
    call,
    definition,
    final,
    ok,
    results_of,
    task,
    tool,
    tool_call,
)
from tests.support.payloads import nested
from tests.support.tools import build

ECHO = tool("echo")


class RawModel:
    """Returns unvalidated turns so that BaseAgent itself must validate hostile shapes."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = 0

    async def next_turn(self, context):
        value = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        return value


def outcome_of(run_coro):
    try:
        result = arun(run_coro)
        return ("returned", result.status.value, result.reason, result)
    except BaseException as error:
        return (type(error).__name__, None, str(error)[:200], None)


def test_model_garbage_types_are_rejected_with_field_errors():
    for garbage in (
        None,
        "just text",
        42,
        {"no": "type"},
        {"type": "final"},
        {"type": "tool-call"},
        {"type": "mystery"},
        [],
        {"type": "blocked", "reason": ""},
    ):
        result = arun(BaseAgent(definition(), RawModel([garbage])).run(task()))
        assert result.status is AgentRunStatus.FAILED, garbage
        assert result.failure.code == "MODEL_TURN_INVALID", (garbage, result.failure)
        assert "declared agent-turn contract" in result.reason


def test_long_dependency_chain_in_a_tool_batch_does_not_escape_run():
    chain = [
        call(f"c{i}", "echo", depends_on_call_ids=[f"c{i + 1}"] if i + 1 < 1100 else [])
        for i in range(1100)
    ]
    subject = BaseAgent(
        definition(tools=[ECHO]),
        RawModel([{"type": "tool-batch", "calls": chain}]),
        tool_executor=FnExecutor(lambda t, c: ok()),
    )
    outcome = outcome_of(subject.run(task()))
    assert outcome[0] == "returned", outcome[:3]


def test_deeply_nested_final_output_with_audit_logs_does_not_escape_run(tmp_path):
    audit = AuditTranscriptStore(tmp_path)
    schema = {
        "type": "object",
        "properties": {"status": {"const": "complete"}},
        "required": ["status"],
    }
    output = {"status": "complete", "payload": nested(120)}
    subject = BaseAgent(
        definition(output_schema=schema),
        RawModel([{"type": "final", "output": output}]),
        audit_logs=audit,
    )
    outcome = outcome_of(subject.run(task("deep-final")))
    assert outcome[0] == "returned", outcome[:3]


def test_deeply_nested_tool_output_does_not_escape_run():
    executor = FnExecutor(lambda t, c: ok({"deep": nested(120)}))
    subject, _ = agent(
        [tool_call("c1", "echo"), final()], executor=executor, definition_=definition(tools=[ECHO])
    )
    outcome = outcome_of(subject.run(task()))
    assert outcome[0] == "returned", outcome[:3]


def test_lone_surrogate_in_tool_arguments_with_audit_does_not_escape_run(tmp_path):
    audit = AuditTranscriptStore(tmp_path)
    subject, _ = agent(
        [tool_call("c1", "echo", {"q": "broken \ud83d pair"}), final()],
        executor=FnExecutor(lambda t, c: ok({"fine": True})),
        definition_=definition(tools=[ECHO]),
        audit_logs=audit,
    )
    outcome = outcome_of(subject.run(task("surrogate")))
    assert outcome[0] == "returned", outcome[:3]


def test_unicode_line_separator_in_tool_output_does_not_break_the_next_audit_append(tmp_path):
    audit = AuditTranscriptStore(tmp_path)
    subject, _ = agent(
        [tool_call("c1", "echo"), tool_call("c2", "echo"), final()],
        executor=FnExecutor(lambda t, c: ok({"text": "line one\u2028line two"})),
        definition_=definition(tools=[ECHO]),
        audit_logs=audit,
    )
    outcome = outcome_of(subject.run(task("u2028")))
    assert outcome[0] == "returned", outcome[:3]


def test_tool_output_key_named_scratchpad_does_not_crash_a_run_with_audit_logs(tmp_path):
    audit = AuditTranscriptStore(tmp_path)
    subject, _ = agent(
        [tool_call("c1", "echo"), final()],
        executor=FnExecutor(lambda t, c: ok({"scratchpad": "a legitimate field in a tool's JSON"})),
        definition_=definition(tools=[ECHO]),
        audit_logs=audit,
    )
    outcome = outcome_of(subject.run(task("scratch")))
    assert outcome[0] == "returned", outcome[:3]


def test_final_output_property_named_scratchpad_does_not_crash_a_run_with_audit_logs(tmp_path):
    schema = {
        "type": "object",
        "properties": {"status": {"const": "complete"}, "scratchpad": {"type": "string"}},
        "required": ["status"],
    }
    second = BaseAgent(
        definition(output_schema=schema),
        RawModel([{"type": "final", "output": {"status": "complete", "scratchpad": "notes"}}]),
        audit_logs=AuditTranscriptStore(tmp_path / "second"),
    )
    outcome_two = outcome_of(second.run(task("scratch2")))
    assert outcome_two[0] == "returned", outcome_two[:3]


def test_unknown_consumed_episode_ids_are_rejected_before_the_action_runs():
    side_effects = []
    write = tool("write", kind=EpisodeKind.ACTION)

    def handler(tool_definition, context):
        side_effects.append(context.call.id)
        return ok({"written": True})

    seen = []

    def respond(context):
        seen.append(results_of(context))
        if context.iteration == 1:
            return tool_call("c1", "write", {"path": "x"}, consumed_episode_ids=["episode-99"])
        return final()

    subject = BaseAgent(
        definition(tools=[write]), DynamicModel(respond), tool_executor=FnExecutor(handler)
    )
    result = arun(subject.run(task()))
    assert side_effects == []
    assert result.status is AgentRunStatus.COMPLETED, result.reason
    assert '"episode-99" is not an episode of this run' in seen[1]["c1"].message


def test_compacted_consumed_episode_ids_do_not_crash_the_run():
    read = tool("read")
    write = tool("write", kind=EpisodeKind.ACTION)

    class Model:
        def __init__(self):
            self.step = 0

        async def next_turn(self, context):
            self.step += 1
            if self.step < 40:
                return {
                    "type": "tool-call",
                    "call": call(f"r{self.step}", "read", {"big": "x" * 400}),
                }
            if self.step == 40:
                stale = "episode-1"
                return {
                    "type": "tool-call",
                    "call": call("w1", "write", {}, consumed_episode_ids=[stale]),
                }
            return {"type": "final", "output": {"status": "complete"}}

    from nailong_agent_sdk.memory.context_projection import ContextProjectionPolicy

    policy = ContextProjectionPolicy(context_token_budget=1500, episode_token_budget=300)
    subject = BaseAgent(
        definition(tools=[read, write], max_iterations=50),
        Model(),
        tool_executor=FnExecutor(lambda t, c: ok({"data": "y" * 300})),
        context_projection_policy=policy,
    )
    outcome = outcome_of(subject.run(task()))
    assert outcome[0] == "returned", outcome[:3]


def test_registry_executor_denials_surface_as_blocked_not_exceptions(tmp_path):
    executor, ctx = build(tmp_path, role="nobody", grants=[])
    read_tool = tool(
        "read_file",
        schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    subject, _ = agent(
        [tool_call("c1", "read_file", {"path": "a.txt"}), final()],
        executor=executor,
        definition_=definition(tools=[read_tool]),
    )
    result = arun(subject.run(task()))
    assert (
        result.status is AgentRunStatus.BLOCKED
        and 'lacks capability "filesystem.read"' in result.reason
    )


def test_governed_tool_timeout_via_supervisor_still_terminates_the_run(tmp_path):
    from nailong_agent_sdk.agent.base_agent import AgentWatchdogPolicy
    from nailong_agent_sdk.tools.supervisor import CommandTemplate

    template = CommandTemplate(
        name="sleepy",
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=60,
    )
    executor, ctx = build(tmp_path, templates=[template], approve=("process.execute",))
    command = tool(
        "run_registered_command",
        kind=EpisodeKind.ACTION,
        schema={
            "type": "object",
            "properties": {"template_name": {"type": "string"}},
            "required": ["template_name"],
            "additionalProperties": False,
        },
    )
    subject, _ = agent(
        [tool_call("c1", "run_registered_command", {"template_name": "sleepy"}), final()],
        executor=executor,
        definition_=definition(tools=[command], max_iterations=3),
        watchdog_policy=AgentWatchdogPolicy(tool_call_timeout_seconds=1.0),
    )
    result = arun(subject.run(task()))
    assert result.failure is not None and result.failure.code == "WATCHDOG_TOOL_TIMEOUT", (
        result.status,
        result.reason,
    )
