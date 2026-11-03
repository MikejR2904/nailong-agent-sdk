import asyncio

import pytest
from pydantic import BaseModel

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import Agent, ModelSetup, core_tools, tool


def scripted(*turns):
    return ModelSetup(ScriptedModel(list(turns)), "scripted", "scripted-1")


def final(answer="done"):
    return {"type": "final", "output": {"status": "complete", "answer": answer}}


def call(name, **arguments):
    return {"type": "tool-call", "call": {"id": f"c-{name}", "name": name, "arguments": arguments}}


def seen_by_model(setup):
    context = setup.model.calls[-1]
    return str(list(context.observations)) + str(list(context.episodes))


def agent_for(tmp_path, setup, *tools, **options):
    return Agent(
        model=setup,
        instructions="Answer briefly.",
        tools=tools,
        workspace=tmp_path / "workspace",
        **options,
    )


@tool
def add(a: int, b: int = 1) -> int:
    """Add two whole numbers."""
    return a + b


@tool(read_only=True)
async def shout(text: str) -> str:
    """Upper-case the text."""
    return text.upper()


def test_an_agent_with_no_tools_answers_in_one_step(tmp_path):
    answer = agent_for(tmp_path, scripted(final("hello"))).run_sync("Say hello.")
    assert answer.ok and answer.text == "hello" and answer.steps == 1
    assert str(answer) == "hello"


def test_a_function_becomes_a_tool_with_a_schema_read_from_its_signature():
    assert add.definition.name == "add"
    assert add.definition.description == "Add two whole numbers."
    schema = add.definition.input_schema
    assert schema["required"] == ["a"]
    assert schema["additionalProperties"] is False
    assert "title" not in schema and "title" not in schema["properties"]["a"]
    assert shout.definition.concurrency.value == "parallel-safe"
    assert add.definition.concurrency.value == "serial"


def test_the_model_calls_sync_and_async_tools_and_sees_their_results(tmp_path):
    setup = scripted(call("add", a=2, b=3), call("shout", text="ok"), final("5 and OK"))
    answer = agent_for(tmp_path, setup, add, shout).run_sync("Use both tools.")
    assert answer.ok and answer.steps == 3
    assert "5" in seen_by_model(setup) and "OK" in seen_by_model(setup)


def test_a_tool_that_raises_fails_that_call_and_names_the_exception(tmp_path):
    @tool
    def broken(value: int) -> int:
        """Always fails."""
        raise KeyError("missing-key")

    setup = scripted(call("broken", value=1), final("recovered"))
    answer = agent_for(tmp_path, setup, broken).run_sync("Try it.")
    assert answer.text == "recovered"
    assert "raised KeyError" in seen_by_model(setup)


def test_a_tool_that_needs_approval_is_blocked_without_a_callback(tmp_path):
    @tool(needs_approval=True)
    def delete(path: str) -> str:
        """Delete a path."""
        raise AssertionError("must not run")

    answer = agent_for(tmp_path, scripted(call("delete", path="x"), final()), delete).run_sync(
        "Go."
    )
    assert not answer.ok
    assert "needs approval and this agent has no approve= callback" in str(
        answer.result.model_dump()
    )


def test_an_approve_callback_decides_each_call(tmp_path):
    seen = []

    @tool(needs_approval=True)
    def send(text: str) -> str:
        """Send text."""
        return "sent " + text

    async def approve(request):
        seen.append((request.tool, request.arguments))
        return True

    setup = scripted(call("send", text="hi"), final("sent"))
    answer = agent_for(tmp_path, setup, send, approve=approve).run_sync("Send hi.")
    assert answer.ok and seen == [("send", {"text": "hi"})]

    declined = agent_for(
        tmp_path, scripted(call("send", text="hi"), final()), send, approve=lambda request: False
    ).run_sync("Send hi.")
    assert not declined.ok
    assert "declined by the approve= callback" in str(declined.result.model_dump())


def test_core_tools_read_a_directory_and_write_only_declared_drafts(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    read_only = core_tools(tmp_path)
    assert {d.name for d in read_only.definitions} == {"read_file", "glob", "grep"}
    setup = scripted(call("read_file", path="notes.txt"), final("read it"))
    answer = agent_for(tmp_path, setup, read_only).run_sync("Read notes.txt.")
    assert answer.ok
    assert "alpha" in seen_by_model(setup)

    writable = core_tools(tmp_path, writable=("out.txt",))
    assert "write_draft" in {d.name for d in writable.definitions}
    write = scripted(call("write_draft", path="out.txt", content="x"), final("wrote"))
    assert agent_for(tmp_path, write, writable).run_sync("Write.").ok
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "x"


def test_a_pydantic_class_shapes_and_validates_the_output(tmp_path):
    class Verdict(BaseModel):
        status: str
        score: int

    turn = {"type": "final", "output": {"status": "complete", "score": 7}}
    answer = agent_for(tmp_path, scripted(turn), output=Verdict).run_sync("Score it.")
    assert answer.ok and isinstance(answer.output, Verdict) and answer.output.score == 7


def test_a_run_that_does_not_finish_reports_why_in_one_line(tmp_path):
    setup = scripted(call("add", a=1), call("add", a=1))
    answer = agent_for(tmp_path, setup, add, max_steps=2).run_sync("Loop.")
    assert not answer.ok
    assert str(answer).startswith(f"[{answer.status}]")


def test_run_sync_inside_a_running_loop_says_to_await_instead(tmp_path):
    agent = agent_for(tmp_path, scripted(final()))

    async def inside():
        agent.run_sync("hi")

    with pytest.raises(RuntimeError, match=r"use `await agent.run\(\.\.\.\)`"):
        asyncio.run(inside())


def test_declaring_a_tool_wrongly_fails_at_once_and_says_which(tmp_path):
    with pytest.raises(ValueError, match='Tool function "bare" needs a docstring'):

        @tool
        def bare(x: int) -> int:
            return x

    with pytest.raises(ValueError, match='variadic parameter "rest"'):

        @tool
        def spread(*rest: int) -> int:
            """Sum."""
            return sum(rest)

    with pytest.raises(ValueError, match='Two tools are named "add"'):
        agent_for(tmp_path, scripted(final()), add, add)


def test_closing_the_agent_releases_the_workspace_so_it_can_be_deleted(tmp_path):
    import shutil

    with agent_for(tmp_path, scripted(final("bye"))) as agent:
        assert agent.run_sync("Say bye.").ok
    shutil.rmtree(tmp_path / "workspace")
    assert not (tmp_path / "workspace").exists()
