import asyncio
import os
import sys
from pathlib import Path

import pytest

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import Agent, ModelSetup, dev_tools
from nailong_agent_sdk.starter.core_tools import CoreTools
from nailong_agent_sdk.starter.tools import FunctionTool
from nailong_agent_sdk.tools.supervisor import ProcessExecutionRecord, ProcessExitKind

PY = f'"{sys.executable}"'


def pack(root, **options):
    tools = dev_tools(root, **options)
    return {t.name: t for t in tools if isinstance(t, FunctionTool)}


def run(tool_, **arguments):
    return asyncio.run(tool_.run(arguments))


def final(answer="done"):
    return {"type": "final", "output": {"status": "complete", "answer": answer}}


def call(name, **arguments):
    return {"type": "tool-call", "call": {"id": f"c-{name}", "name": name, "arguments": arguments}}


def test_the_pack_has_read_tools_write_tools_and_a_command_tool(tmp_path):
    tools = dev_tools(tmp_path)
    assert isinstance(tools[0], CoreTools)
    assert {d.name for d in tools[0].definitions} == {"read_file", "glob", "grep"}
    assert sorted(pack(tmp_path)) == ["edit_file", "run_command", "write_file"]
    assert sorted(pack(tmp_path, allow_writes=False)) == ["run_command"]
    assert sorted(pack(tmp_path, allow_commands=False)) == ["edit_file", "write_file"]
    with pytest.raises(ValueError, match="does not exist"):
        dev_tools(tmp_path / "missing")
    with pytest.raises(ValueError, match="either docker_image or sandbox"):
        dev_tools(tmp_path, docker_image="python:3", sandbox=object())


def test_write_file_creates_and_overwrites_inside_the_project(tmp_path):
    write = pack(tmp_path)["write_file"]
    first = run(write, path="src/app.py", content="print(1)\n")
    assert first.output == {"path": "src/app.py", "bytes": 9, "created": True}
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "print(1)\n"
    assert run(write, path="src/app.py", content="print(2)\n").output["created"] is False


@pytest.mark.parametrize(
    ("path", "fragment"),
    [
        ("../outside.txt", "escapes the project directory"),
        ("sub/../../outside.txt", "escapes the project directory"),
        ("/etc/passwd" if os.name != "nt" else "C:/Windows/x.txt", "relative"),
        (".env", "protected credential path"),
        (".git/config", "do not write to"),
        (".agent-sessions/x.jsonl", "do not write to"),
        ("", "non-empty"),
    ],
)
def test_write_file_refuses_paths_outside_the_project_or_protected(tmp_path, path, fragment):
    result = run(pack(tmp_path)["write_file"], path=path, content="x")
    assert result.status == "failed" and fragment in result.error
    assert not (tmp_path.parent / "outside.txt").exists()


def test_write_file_respects_the_size_limit(tmp_path):
    write = pack(tmp_path, max_file_bytes=10)["write_file"]
    result = run(write, path="big.txt", content="x" * 11)
    assert result.status == "failed" and "limit for one write is 10" in result.error


def test_edit_file_replaces_one_occurrence_and_explains_ambiguity(tmp_path):
    (tmp_path / "a.txt").write_text("one two two\n", encoding="utf-8")
    edit = pack(tmp_path)["edit_file"]
    ambiguous = run(edit, path="a.txt", old_text="two", new_text="2")
    assert ambiguous.status == "failed" and "occurs 2 times" in ambiguous.error
    assert run(edit, path="a.txt", old_text="one", new_text="1").output["replacements"] == 1
    assert (
        run(edit, path="a.txt", old_text="two", new_text="2", replace_all=True).output[
            "replacements"
        ]
        == 2
    )
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "1 2 2\n"
    assert "does not occur" in run(edit, path="a.txt", old_text="zzz", new_text="y").error
    assert "not an existing file" in run(edit, path="nope.txt", old_text="a", new_text="b").error
    assert "must not be empty" in run(edit, path="a.txt", old_text="", new_text="b").error


def test_run_command_returns_exit_code_and_output_and_a_failure_is_not_an_error(tmp_path):
    command = pack(tmp_path)["run_command"]
    ok = run(command, command=f'{PY} -c "print(6*7)"')
    assert ok.status == "succeeded" and ok.output["exit_code"] == 0
    assert "42" in ok.output["output"] and ok.output["outcome"] == "succeeded"
    failed = run(command, command=f'{PY} -c "import sys; sys.exit(3)"')
    assert failed.status == "succeeded" and failed.output["exit_code"] == 3
    assert failed.output["outcome"] == "exit-nonzero"
    assert run(command, command="  ").status == "failed"


def test_run_command_runs_in_the_project_directory(tmp_path):
    (tmp_path / "marker.txt").write_text("here", encoding="utf-8")
    result = run(
        pack(tmp_path)["run_command"], command=f"{PY} -c \"print(open('marker.txt').read())\""
    )
    assert "here" in result.output["output"]


def test_run_command_times_out_and_the_limit_caps_the_request(tmp_path):
    command = pack(tmp_path, command_timeout_seconds=1.0)["run_command"]
    result = run(command, command=f'{PY} -c "import time; time.sleep(30)"', timeout_seconds=600)
    assert result.output["outcome"] == "timed-out" and result.output["seconds"] < 10


def test_secrets_in_the_host_environment_do_not_reach_a_command(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "do-not-leak")
    probe = f"{PY} -c \"import os; print(os.environ.get('SECRET_TOKEN'))\""
    hidden = run(pack(tmp_path)["run_command"], command=probe)
    assert "do-not-leak" not in hidden.output["output"] and "None" in hidden.output["output"]
    shared = run(pack(tmp_path, environment=["SECRET_TOKEN"])["run_command"], command=probe)
    assert "do-not-leak" in shared.output["output"]


def test_output_is_capped_and_says_how_much_was_dropped(tmp_path):
    command = pack(tmp_path, max_output_bytes=100)["run_command"]
    result = run(command, command=f"{PY} -c \"print('x' * 5000)\"")
    assert result.output["output_truncated_bytes"] > 4000 and len(result.output["output"]) <= 200


def test_a_sandbox_backend_receives_the_template_and_the_project_directory(tmp_path):
    seen = {}

    class Recording:
        async def run(self, template, *, cwd, environment=None):
            seen.update(template=template, cwd=cwd, environment=environment)
            return ProcessExecutionRecord(
                template_name=template.name,
                command=template.command,
                started_at_utc="2026-01-01T00:00:00+00:00",
                ended_at_utc="2026-01-01T00:00:01+00:00",
                duration_ns=1_000_000_000,
                return_code=0,
                exit_kind=ProcessExitKind.SUCCEEDED,
                output="from the sandbox",
                output_bytes_total=16,
                output_bytes_retained=16,
                output_truncated_bytes=0,
            )

    command = pack(tmp_path, sandbox=Recording())["run_command"]
    result = run(command, command="make test")
    assert result.output["output"] == "from the sandbox"
    assert (
        seen["template"].command == ["sh", "-c", "make test"] and seen["cwd"] == tmp_path.resolve()
    )


def test_writes_and_commands_need_approval_by_default_and_can_be_relaxed(tmp_path):
    script = [call("write_file", path="out.txt", content="hi"), final("tried")]
    blocked_setup = ModelSetup(ScriptedModel(script), "scripted", "scripted-1")
    with Agent(
        model=blocked_setup,
        instructions="x",
        tools=dev_tools(tmp_path),
        workspace=tmp_path / "ws",
    ) as agent:
        asyncio.run(agent.run("Write it."))
    assert not (tmp_path / "out.txt").exists()

    asked = []

    def approve(request):
        asked.append(request.tool)
        return True

    setup = ModelSetup(ScriptedModel(list(script)), "scripted", "scripted-1")
    with Agent(
        model=setup,
        instructions="x",
        tools=dev_tools(tmp_path),
        approve=approve,
        workspace=tmp_path / "ws2",
    ) as agent:
        assert agent.run_sync("Write it.").ok
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hi" and asked == ["write_file"]

    relaxed = ModelSetup(
        ScriptedModel([call("write_file", path="free.txt", content="y"), final()]),
        "scripted",
        "scripted-1",
    )
    with Agent(
        model=relaxed,
        instructions="x",
        tools=dev_tools(tmp_path, approve_writes=False),
        workspace=tmp_path / "ws3",
    ) as agent:
        assert agent.run_sync("Write.").ok
    assert (tmp_path / "free.txt").exists()


def test_an_agent_can_edit_then_test_its_own_work(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    test_command = f'{PY} -c "import calc; print(calc.add(2, 3))"'
    setup = ModelSetup(
        ScriptedModel(
            [
                call("run_command", command=test_command),
                call("edit_file", path="calc.py", old_text="a - b", new_text="a + b"),
                call("run_command", command=test_command),
                final("fixed"),
            ]
        ),
        "scripted",
        "scripted-1",
    )
    with Agent(
        model=setup,
        instructions="Fix the bug.",
        tools=dev_tools(tmp_path),
        approve=lambda request: True,
        workspace=tmp_path / "ws",
    ) as agent:
        answer = agent.run_sync("Fix calc.add.")
    assert answer.ok and answer.steps == 4
    assert "a + b" in Path(tmp_path / "calc.py").read_text(encoding="utf-8")
    seen = str(list(setup.model.calls[-1].observations))
    assert "-1" in seen and "5" in seen


def test_extra_protected_names_cannot_be_written_but_other_paths_still_can(tmp_path):
    tools = pack(tmp_path, protected=[".nailong", "NAILONG.md"])
    write = tools["write_file"]
    for path in (".nailong/config.toml", "sub/.nailong/x", "NAILONG.md"):
        result = run(write, path=path, content="x")
        assert result.status == "failed" and "do not write to" in result.error, path
    assert run(write, path="notes.md", content="ok").status == "succeeded"


def test_added_directories_are_readable_and_writable_by_name_and_nothing_else_is(tmp_path):
    project, library = tmp_path / "app", tmp_path / "lib"
    project.mkdir()
    library.mkdir()
    (library / "api.txt").write_text("v2 api", encoding="utf-8")
    (project / "main.txt").write_text("main", encoding="utf-8")
    tools = dev_tools(project, extra_roots={"lib": library})
    reader = tools[0]
    schema = next(d for d in reader.definitions if d.name == "read_file").input_schema
    assert schema["properties"]["root"]["enum"] == ["lib", "project"]
    from_lib = asyncio.run(reader.run("read_file", {"path": "api.txt", "root": "lib"}))
    from_project = asyncio.run(reader.run("read_file", {"path": "main.txt"}))
    assert "v2 api" in str(from_lib.output) and "main" in str(from_project.output)
    unknown = asyncio.run(reader.run("read_file", {"path": "x", "root": "elsewhere"}))
    assert unknown.status == "failed" and "no root named" in unknown.error
    write = {t.name: t for t in tools if isinstance(t, FunctionTool)}["write_file"]
    assert run(write, path="notes.txt", content="in lib", root="lib").status == "succeeded"
    assert (library / "notes.txt").read_text(encoding="utf-8") == "in lib"
    assert not (project / "notes.txt").exists()
    escape = run(write, path="../app/leak.txt", content="x", root="lib")
    assert escape.status == "failed" and "escapes" in escape.error
    assert run(write, path="a.txt", content="x", root="nope").status == "failed"


def test_a_root_name_cannot_repeat_or_point_at_nothing(tmp_path):
    project = tmp_path / "app"
    project.mkdir()
    with pytest.raises(ValueError, match='root name "project" is already used'):
        dev_tools(project, extra_roots={"project": project})
    with pytest.raises(ValueError, match="does not exist"):
        dev_tools(project, extra_roots={"gone": tmp_path / "gone"})
