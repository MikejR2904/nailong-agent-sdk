import pytest
from pydantic import ValidationError

from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_runner import (
    AgentTaskRunner,
    PermissionOptions,
    TaskOptionError,
    TaskRunOptions,
)
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.observability.telemetry_models import DecisionSource, TelemetryAuthority
from nailong_agent_sdk.tools.core import core_tool_definitions
from tests.support.agents import arun, definition, task
from tests.support.chat_provider import ScriptedChatTransport, final_chat, tool_chat

KEY = "sk-live-0123456789abcdef"
ENDPOINT = {"base_url": "http://127.0.0.1:9/v1", "api_key": KEY, "allow_insecure_http": True}
BUILTIN = {item.name: item for item in core_tool_definitions()}
WRITE = definition(tools=[BUILTIN["write_draft"]], max_iterations=6)
CALL = tool_chat("c1", "write_draft", {"path": "out.txt", "content": "drafted"})
HUMAN = DecisionSource(authority=TelemetryAuthority.HUMAN, identifier="reviewer-7")


def options(**permissions):
    return TaskRunOptions.model_validate(
        {
            "model_endpoint": ENDPOINT,
            "permissions": {"capabilities": ["draft.write"], **permissions},
            "declared_output_paths": ["out.txt"],
        }
    )


def run(tmp_path, run_options, **named):
    services = AgentRuntimeServices.open(tmp_path / "service")
    runner = AgentTaskRunner(services, transport=ScriptedChatTransport([CALL, final_chat()]))
    return arun(runner.run(WRITE, task(), run_options, **named)), services


def events_of(services, event_type):
    return [e for e in services.telemetry.iter_events("t1") if e.event_type == event_type]


def test_a_capability_approved_in_advance_leaves_a_durable_record_naming_the_run_options(tmp_path):
    result, services = run(tmp_path, options(approved_capabilities=["draft.write"]))
    assert result.status is AgentRunStatus.COMPLETED
    (event,) = events_of(services, "approval.decided")
    assert event.status == "approved" and event.authority is TelemetryAuthority.DETERMINISTIC
    assert event.payload["capability"] == "draft.write"
    assert event.payload["decided_by"] == "run-options"
    assert event.payload["decision_authority"] == "deterministic"
    assert event.payload["decision_reason"] == "Approved by the caller in the run options."


def test_the_record_of_an_advance_approval_comes_after_the_run_is_created(tmp_path):
    _, services = run(tmp_path, options(approved_capabilities=["draft.write"]))
    types = [e.event_type for e in services.telemetry.iter_events("t1")]
    assert types.index("run.created") < types.index("approval.decided")


def test_a_run_with_nothing_approved_in_advance_records_no_advance_approval(tmp_path):
    result, services = run(tmp_path, options())
    assert result.status is AgentRunStatus.BLOCKED
    assert events_of(services, "approval.decided") == []


def test_the_caller_can_say_who_approved_in_advance_and_by_which_rule(tmp_path):
    result, services = run(
        tmp_path,
        options(approved_capabilities=["draft.write"]),
        approved_by=HUMAN,
        approval_reason="rule:R3",
    )
    assert result.status is AgentRunStatus.COMPLETED
    (event,) = events_of(services, "approval.decided")
    assert event.authority is TelemetryAuthority.HUMAN
    assert event.payload["decided_by"] == "reviewer-7"
    assert event.payload["decision_authority"] == "human"
    assert event.payload["decision_reason"] == "rule:R3"


def test_naming_only_the_approver_keeps_the_default_reason(tmp_path):
    _, services = run(tmp_path, options(approved_capabilities=["draft.write"]), approved_by=HUMAN)
    (event,) = events_of(services, "approval.decided")
    assert event.payload["decided_by"] == "reviewer-7"
    assert event.payload["decision_reason"] == "Approved by the caller in the run options."


def test_every_capability_approved_in_advance_gets_its_own_record(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "service")
    scripted = ScriptedChatTransport([CALL, final_chat()])
    runner = AgentTaskRunner(services, transport=scripted)
    both = options(capabilities=["draft.write", "filesystem.read"])
    both = TaskRunOptions.model_validate(
        {
            **both.model_dump(mode="json", exclude={"model_endpoint"}),
            "model_endpoint": ENDPOINT,
            "permissions": {
                "capabilities": ["draft.write", "filesystem.read"],
                "approved_capabilities": ["draft.write", "filesystem.read"],
            },
        }
    )
    arun(runner.run(WRITE, task(), both, approved_by=HUMAN))
    capabilities = {e.payload["capability"] for e in events_of(services, "approval.decided")}
    assert capabilities == {"draft.write", "filesystem.read"}


@pytest.mark.parametrize(
    ("named", "label"),
    (
        ({"approved_by": HUMAN}, "approved_by"),
        ({"approval_reason": "rule:R3"}, "approval_reason"),
        ({"approved_by": HUMAN, "approval_reason": "rule:R3"}, "approved_by and approval_reason"),
    ),
)
def test_naming_an_approver_when_nothing_is_approved_in_advance_is_refused_before_the_run(
    tmp_path, named, label
):
    services = AgentRuntimeServices.open(tmp_path / "service")
    runner = AgentTaskRunner(services, transport=ScriptedChatTransport([]))
    with pytest.raises(TaskOptionError) as caught:
        arun(runner.run(WRITE, task(), options(), **named))
    assert str(caught.value).startswith(f"{label} describe who approved")
    assert "permissions.approved_capabilities" in str(caught.value)
    assert services.telemetry.iter_events("t1") is not None
    assert events_of(services, "run.created") == []


def test_a_run_without_permissions_refuses_an_approver_too(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "service")
    runner = AgentTaskRunner(services, transport=ScriptedChatTransport([]))
    with pytest.raises(TaskOptionError, match="approved_by describe who approved"):
        arun(runner.run(WRITE, task(), TaskRunOptions(), approved_by=HUMAN))


def test_who_approved_in_advance_cannot_be_written_into_the_options_a_remote_caller_sends():
    assert "approved_by" not in PermissionOptions.model_fields
    with pytest.raises(ValidationError, match="approved_by"):
        PermissionOptions.model_validate(
            {
                "capabilities": ["draft.write"],
                "approved_capabilities": ["draft.write"],
                "approved_by": {"authority": "human", "identifier": "reviewer-7"},
            }
        )
