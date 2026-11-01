from nailong_agent_sdk.agent.definition_digest import DIGEST_BASIS, definition_digests
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import VersionedInstructions
from nailong_agent_sdk.foundations.jcs import jcs_hash
from nailong_agent_sdk.observability.otlp import build_run_payloads
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call
from tests.support.otlp_runs import attributes_of, spans_of


def run_once(tmp_path, subject_definition):
    services = AgentRuntimeServices.open(tmp_path)
    from nailong_agent_sdk.agent.model import ScriptedModel

    subject = services.create_agent(
        subject_definition,
        ScriptedModel([tool_call("c1", "echo", {"q": 1}), final()]),
        require_tool_authorization=False,
        tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments})),
    )
    arun(subject.run(task("digest-1")))
    return services


def close(services):
    services.audit_logs.close()
    services.telemetry.close()


def test_the_digest_is_stable_and_moves_with_exactly_the_part_that_changed():
    base = definition(tools=[tool("echo")], max_iterations=6)
    assert definition_digests(base) == definition_digests(base.model_copy(deep=True))
    edited = base.model_copy(
        update={"instructions": VersionedInstructions(version="v2", text="Do another task.")}
    )
    before, after = definition_digests(base), definition_digests(edited)
    assert before["definition_sha256"] != after["definition_sha256"]
    assert before["instructions_sha256"] != after["instructions_sha256"]
    assert after["instructions_version"] == "v2"
    for unchanged in ("tools_sha256", "input_schema_sha256", "output_schema_sha256"):
        assert before[unchanged] == after[unchanged]
    fewer = definition_digests(definition(max_iterations=6))
    assert fewer["tools_sha256"] != before["tools_sha256"] and fewer["tool_names"] == []
    assert before["tool_names"] == ["echo"] and before["digest_basis"] == DIGEST_BASIS
    assert before["definition_sha256"] == jcs_hash(base.model_dump(mode="json"))


def test_a_run_records_its_configuration_in_the_ledgers_and_on_the_invoke_agent_span(tmp_path):
    subject_definition = definition(tools=[tool("echo")], max_iterations=6)
    services = run_once(tmp_path, subject_definition)
    try:
        expected = definition_digests(subject_definition)
        started = next(
            entry
            for entry in services.audit_logs.iter_entries("digest-1")
            if entry.event_type == "run-started"
        )
        assert started.payload["details"]["configuration"] == expected
        event = next(
            item
            for item in services.telemetry.iter_events("digest-1")
            if item.payload.get("details", {}).get("configuration")
        )
        assert (
            event.payload["details"]["configuration"]["definition_sha256"]
            == (expected["definition_sha256"])
        )
        built = build_run_payloads(services.telemetry, "digest-1")
        root = next(
            span for span in spans_of(built.traces) if span["name"].startswith("invoke_agent")
        )
        attributes = attributes_of(root)
        assert attributes["gen_ai.agent.id"] == {"stringValue": "audit-agent"}
        assert attributes["gen_ai.agent.version"] == {"stringValue": "v1"}
        assert attributes["nailong.agent.definition_sha256"] == {
            "stringValue": expected["definition_sha256"]
        }
    finally:
        close(services)
