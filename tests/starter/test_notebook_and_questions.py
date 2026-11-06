import asyncio
import json

from nailong_agent_sdk.starter import core_tools, notebook_tools

NOTEBOOK = {
    "cells": [
        {"cell_type": "code", "metadata": {}, "outputs": [], "source": ["print(1)\n"]},
        {"cell_type": "markdown", "metadata": {}, "source": ["# Title\n"]},
    ],
    "metadata": {},
    "nbformat": 4,
    "nbformat_minor": 5,
}


def make_project(tmp_path):
    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "a.ipynb").write_text(json.dumps(NOTEBOOK), encoding="utf-8")
    (tmp_path / ".nailong").mkdir()
    (tmp_path / ".nailong" / "n.ipynb").write_text(json.dumps(NOTEBOOK), encoding="utf-8")
    (tmp_path / "plain.txt").write_text("x", encoding="utf-8")
    return notebook_tools(tmp_path, protected=(".nailong",))


def edit(tool, **arguments):
    return asyncio.run(tool.run(arguments))


def cells(tmp_path, name="analysis/a.ipynb"):
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))["cells"]


def test_a_cell_in_any_notebook_of_the_project_can_be_replaced(tmp_path):
    tool = make_project(tmp_path)
    result = edit(tool, path="analysis/a.ipynb", new_source="print(2)", cell_index=0)
    assert result.status == "succeeded"
    assert "".join(cells(tmp_path)[0]["source"]) == "print(2)"
    assert "".join(cells(tmp_path)[1]["source"]) == "# Title\n"


def test_append_adds_to_the_cell_and_a_far_index_extends_the_notebook(tmp_path):
    tool = make_project(tmp_path)
    edit(tool, path="analysis/a.ipynb", new_source="print(3)\n", cell_index=0, mode="append")
    assert "".join(cells(tmp_path)[0]["source"]).endswith("print(3)\n")
    edit(tool, path="analysis/a.ipynb", new_source="later", cell_index=3)
    assert len(cells(tmp_path)) == 4


def test_a_notebook_outside_the_project_or_not_a_notebook_is_refused_with_the_reason(tmp_path):
    tool = make_project(tmp_path)
    cases = {
        "../elsewhere.ipynb": "is outside the project",
        "plain.txt": "is not a notebook",
        "analysis/missing.ipynb": "does not exist",
        ".nailong/n.ipynb": "is protected",
    }
    for path, fragment in cases.items():
        result = edit(tool, path=path, new_source="x")
        assert result.status == "failed" and fragment in result.error, path
    assert cells(tmp_path, ".nailong/n.ipynb")[0]["source"] == ["print(1)\n"]


def test_the_tool_asks_for_approval_by_default_and_names_itself(tmp_path):
    tool = notebook_tools(tmp_path)
    assert tool.name == "notebook_edit" and tool.needs_approval is True
    assert notebook_tools(tmp_path, needs_approval=False).needs_approval is False


def test_a_question_tool_reaches_the_responder_the_caller_gave(tmp_path):
    asked = []

    async def responder(question):
        asked.append(question)
        return "use the blue one"

    tools = core_tools(tmp_path, only=("ask_human_question",), ask_human=responder)
    result = asyncio.run(tools.run("ask_human_question", {"question": "Which colour?"}))
    assert result.status == "succeeded" and asked == ["Which colour?"]
    assert result.output == {"answer": "use the blue one", "answered": True}


def test_a_question_nobody_answers_is_reported_as_unanswered(tmp_path):
    async def responder(question):
        return None

    tools = core_tools(tmp_path, only=("ask_human_question",), ask_human=responder)
    result = asyncio.run(tools.run("ask_human_question", {"question": "Which?"}))
    assert result.output == {"answer": None, "answered": False}


def test_without_a_responder_the_question_tool_fails_naming_the_missing_piece(tmp_path):
    tools = core_tools(tmp_path, only=("ask_human_question",))
    result = asyncio.run(tools.run("ask_human_question", {"question": "Which?"}))
    assert result.status == "failed"
    assert "No interactive human-question responder is configured" in result.error
