import inspect
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from nailong_agent_sdk.agent.task_runner import AgentTaskRunner
from nailong_agent_sdk.mcp.arguments import PYTHON_ONLY_CAPABILITIES
from tests.support.agents import definition, task
from tests.support.holding_provider import HoldingProvider
from tests.support.mcp_server import Server

PLAN = {"plan_id": "p", "tasks": []}


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    instance = Server(tmp_path_factory.mktemp("capabilities"))
    yield instance
    instance.stop()


def refusal(result):
    assert result["ok"] is False, result
    (error,) = result["errors"]
    assert error["type"] == "UnsupportedArgument", error
    return error["message"]


def test_a_python_only_capability_sent_over_mcp_is_refused_by_name_not_dropped(service):
    for name in ("verification_gates", "pre_tool_hooks", "post_tool_hooks", "cancellation"):
        message = refusal(service.call("validate_plan", {"plan": PLAN, name: ["anything"]}))
        assert f'Tool "validate_plan" does not accept "{name}".' in message
        assert f'"{name}" is a Python-only capability of AgentTaskRunner.run' in message
        assert "not exposed through MCP" in message
        assert "It accepts: plan." in message


def test_run_agent_task_names_the_python_only_option_in_its_own_arguments(service):
    sent = {"definition": {}, "task": {}, "model": {"anything": 1}}
    message = refusal(service.call("run_agent_task", sent))
    assert '"model" is a Python-only capability' in message
    assert "runtime_options.model_endpoint" in message
    assert "It accepts: definition, runtime_options, task." in message


def test_a_python_only_capability_inside_runtime_options_is_refused_naming_the_object(service):
    sent = {
        "definition": definition().model_dump(mode="json"),
        "task": task("boundary-1").model_dump(mode="json"),
        "runtime_options": {"verification_gates": ["x"], "scripted_turns": []},
    }
    message = refusal(service.call("run_agent_task", sent))
    assert message.startswith('The "runtime_options" of tool "run_agent_task" does not accept')
    assert '"verification_gates"' in message and "no serializable form" in message


def test_an_advance_approver_among_the_runtime_options_is_refused_by_name_with_its_reason(service):
    sent = {
        "definition": definition().model_dump(mode="json"),
        "task": task("boundary-3").model_dump(mode="json"),
        "runtime_options": {"approved_by": "reviewer-7"},
    }
    message = refusal(service.call("run_agent_task", sent))
    assert '"approved_by" is a Python-only capability of AgentTaskRunner.run' in message
    assert "an MCP caller cannot name an approver" in message


def test_an_argument_nobody_defined_is_refused_without_a_capability_claim(service):
    message = refusal(service.call("validate_plan", {"plan": PLAN, "surprise": 1}))
    assert message == 'Tool "validate_plan" does not accept "surprise". It accepts: plan.'
    assert (
        service.call("get_run_state", {"run_id": "run-404", "extra": True})["errors"][0]["type"]
        == "UnsupportedArgument"
    )


def test_an_unknown_option_that_is_not_a_known_capability_fails_in_the_options_model(service):
    sent = {
        "definition": definition().model_dump(mode="json"),
        "task": task("boundary-2").model_dump(mode="json"),
        "runtime_options": {"made_up_option": True},
    }
    result = service.call("run_agent_task", sent)
    assert result["ok"] is False
    assert result["errors"][0]["location"] == ["made_up_option"]
    assert result["errors"][0]["type"] == "extra_forbidden"


def test_calls_with_only_defined_arguments_are_untouched(service):
    assert service.call("validate_plan", {"plan": PLAN})["errors"][0]["location"] == ["tasks"]
    assert service.call("list_metric_definitions")["ok"] is True


def test_every_python_only_parameter_of_the_runner_has_a_documented_reason():
    parameters = set(inspect.signature(AgentTaskRunner.run).parameters)
    exposed_through_mcp = {"self", "definition", "task", "options"}
    python_only = parameters - exposed_through_mcp
    assert python_only == {
        "model",
        "verification_gates",
        "pre_tool_hooks",
        "post_tool_hooks",
        "cancellation",
        "approved_by",
        "approval_reason",
    }
    assert python_only <= set(PYTHON_ONLY_CAPABILITIES)


def test_cancelling_over_mcp_reports_what_happened_to_the_provider_request(tmp_path):
    provider = HoldingProvider()
    instance = Server(tmp_path)
    try:
        arguments = {
            "definition": definition(max_iterations=4).model_dump(mode="json"),
            "task": task("cancel-report-1").model_dump(mode="json"),
            "runtime_options": {
                "model_endpoint": {
                    "base_url": provider.base_url,
                    "api_key": "test-key-not-a-secret",
                    "allow_insecure_http": True,
                },
                "permissions": {"capabilities": []},
            },
        }
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(instance.call, "run_agent_task", arguments)
            assert provider.received.wait(30), "the provider never received the model call"
            started = time.monotonic()
            cancelled = instance.call("cancel_agent_task", {"task_id": "cancel-report-1"})
            assert cancelled == {"ok": True, "cancelled": True}
            run = running.result(30)
        assert time.monotonic() - started < 15
        assert run["ok"] and run["result"]["status"] == "cancelled", run
        assert provider.client_closed.wait(5), "the connection to the provider stayed open"
        assert not provider.responded.is_set()
        events = instance.call("get_telemetry_events", {"run_id": "cancel-report-1"})["events"]
        (event,) = [item for item in events if item["event_type"] == "agent.model-turn-cancelled"]
        details = event["payload"]["details"]
        assert details["logical"] == "cancelled"
        assert details["transport"] == "connection-closed"
        assert details["provider"] == "unsupported"
        assert details["late_response"] == "discarded"
        assert "may finish generating it and bill for it" in details["detail"]
        provider.release.set()
        time.sleep(0.3)
        later = instance.call("get_telemetry_events", {"run_id": "cancel-report-1"})["events"]
        assert [e["sequence"] for e in later] == [e["sequence"] for e in events]
    finally:
        provider.stop()
        instance.stop()
