import os
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from nailong_agent_sdk.foundations import atomic_io
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    AgentFailure,
    AgentTurn,
    ModelBinding,
    RuntimeOptions,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    ToolDefinition,
    VersionedInstructions,
    validate_candidate_output,
    validate_task_input,
    validate_tool_arguments,
)
from nailong_agent_sdk.foundations.errors import AgentSdkError

TURN = TypeAdapter(AgentTurn)


def test_replace_atomic_replaces_and_retries_permission_errors(tmp_path, monkeypatch):
    target = tmp_path / "t.txt"
    target.write_text("old")
    temporary = tmp_path / ".t.tmp"
    temporary.write_text("new")
    real = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("busy")
        return real(src, dst)

    monkeypatch.setattr(atomic_io.os, "replace", flaky)
    atomic_io.replace_atomic(temporary, target)
    assert target.read_text() == "new" and calls["n"] == 3


def test_replace_atomic_gives_up_and_validates_attempts(tmp_path, monkeypatch):
    temporary = tmp_path / "a"
    temporary.write_text("x")
    monkeypatch.setattr(
        atomic_io.os, "replace", lambda *_: (_ for _ in ()).throw(PermissionError("busy"))
    )
    with pytest.raises(PermissionError):
        atomic_io.replace_atomic(temporary, tmp_path / "b", attempts=2)
    with pytest.raises(ValueError):
        atomic_io.replace_atomic(temporary, tmp_path / "b", attempts=0)


def _definition(**overrides):
    base = dict(
        identity="agent",
        instructions=VersionedInstructions(version="v1", text="do"),
        input_schema={"type": "object"},
        model_binding=ModelBinding(provider="p", model="m"),
        output_schema={
            "type": "object",
            "required": ["status"],
            "properties": {"status": {"type": "string"}},
        },
        termination_policy=TerminationPolicy(max_iterations=3, status_field="status"),
    )
    base.update(overrides)
    return AgentDefinition(**base)


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        VersionedInstructions(version="v", text="t", extra="x")


def test_definition_invariants():
    tool = ToolDefinition(
        name="t", description="d", input_schema={"type": "object"}, episode_kind="exploratory"
    )
    with pytest.raises(ValidationError, match="tool names must be unique"):
        _definition(tools=[tool, tool])
    with pytest.raises(ValidationError, match="memory_rationale"):
        _definition(memory_scope="cross-session")
    with pytest.raises(ValidationError, match="single line"):
        _definition(identity="a\nb")
    with pytest.raises(ValidationError, match="Draft 2020-12"):
        _definition(output_schema={"type": 123})
    with pytest.raises(ValidationError, match="distinct"):
        ModelBinding(provider="p", model="m", fallbacks=[{"provider": "p", "model": "m"}])


def test_schema_validation_errors_name_the_path():
    definition = _definition()
    task = ScopedAgentTask(
        id="t",
        input={},
        scope=TaskScope(label="s"),
        locked_interface={},
        instructions="i",
        acceptance_criteria=["c"],
    )
    validate_task_input(definition, task)
    with pytest.raises(AgentSdkError) as caught:
        validate_candidate_output(definition, {"status": 5})
    assert caught.value.code == "SCHEMA_VALIDATION_FAILED" and "$.status" in caught.value.message
    tool = ToolDefinition(
        name="t",
        description="d",
        input_schema={"type": "object", "required": ["x"]},
        episode_kind="action",
    )
    with pytest.raises(AgentSdkError, match='tool "t" arguments'):
        validate_tool_arguments(tool, {})


def test_non_json_schema_values_are_honored():
    schema = {"type": "number", "const": Decimal("1.5")}
    definition = _definition(output_schema=schema)
    validate_candidate_output(definition, Decimal("1.5"))
    with pytest.raises(AgentSdkError):
        validate_candidate_output(definition, Decimal("2.5"))


def test_turn_discriminator_and_batch_rules():
    assert TURN.validate_python({"type": "final", "output": 1}).type == "final"
    with pytest.raises(ValidationError):
        TURN.validate_python({"type": "nonsense"})
    with pytest.raises(ValidationError, match="acyclic"):
        TURN.validate_python(
            {
                "type": "tool-batch",
                "calls": [
                    {"id": "a", "name": "t", "depends_on_call_ids": ["b"]},
                    {"id": "b", "name": "t", "depends_on_call_ids": ["a"]},
                ],
            }
        )
    with pytest.raises(ValidationError, match="unknown batch call IDs"):
        TURN.validate_python(
            {
                "type": "tool-batch",
                "calls": [{"id": "a", "name": "t", "depends_on_call_ids": ["zz"]}],
            }
        )
    with pytest.raises(ValidationError, match="unique"):
        TURN.validate_python(
            {"type": "tool-batch", "calls": [{"id": "a", "name": "t"}, {"id": "a", "name": "t"}]}
        )
    with pytest.raises(ValidationError, match="tool-batch"):
        TURN.validate_python(
            {"type": "tool-call", "call": {"id": "a", "name": "t", "depends_on_call_ids": ["b"]}}
        )


@pytest.mark.parametrize("length", [500, 990, 1100, 3000])
def test_tool_batch_chain_of_any_reasonable_length_is_validated_without_recursion_error(length):
    calls = [
        {"id": f"c{i}", "name": "t", "depends_on_call_ids": [f"c{i - 1}"] if i else []}
        for i in range(length)
    ]
    try:
        turn = TURN.validate_python({"type": "tool-batch", "calls": calls})
    except RecursionError as error:
        pytest.fail(f"RecursionError for a {length}-call dependency chain: {error}")
    assert len(turn.calls) == length


def test_runtime_options_bounds():
    RuntimeOptions(run_deadline_seconds=1, context_token_budget=256)
    for bad in (
        {"run_deadline_seconds": 0},
        {"context_token_budget": 10},
        {"tool_result_preview_chars": 1},
        {"mode": "live"},
    ):
        with pytest.raises(ValidationError):
            RuntimeOptions(**bad)


def test_agent_failure_details_are_sanitized():
    failure = AgentFailure(code="X", message="m", details={"password": "p", "ok": 1})
    assert failure.details["password"] == "[REDACTED]"
