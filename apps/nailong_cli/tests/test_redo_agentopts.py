from __future__ import annotations

import pytest
from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import ModelSetup

from nailong_cli.agent_files import load_agent_files
from tests.conftest import call, final, run_cli

BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]
PATCH = "*** Begin Patch\n*** Update File: app.py\n-x = 1\n+x = 2\n*** End Patch"


def test_redo_puts_back_what_a_rewind_undid(project, home):
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    code, out, err, _ = run_cli(
        project,
        home,
        BYPASS,
        call("apply_patch", patch=PATCH),
        final("done"),
        lines="Patch it\n/rewind 1\ny\n/redo\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    assert "put back 1 file(s)" in out
    assert (project / "app.py").read_text(encoding="utf-8") == "x = 2\n"


def test_redo_without_a_rewind_says_so(project, home):
    _, _, err, _ = run_cli(project, home, [], lines="/redo\n/exit\n", interactive=True)
    assert "nothing to redo" in err


def test_a_new_turn_ends_the_chance_to_redo(project, home):
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    _, _, err, _ = run_cli(
        project,
        home,
        BYPASS,
        call("apply_patch", patch=PATCH),
        final("done"),
        lines="Patch it\n/rewind 1\ny\nAnother thing\n/redo\n/exit\n",
        interactive=True,
    )
    assert "nothing to redo" in err


def write_agent(project, name, header):
    folder = project / ".nailong" / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(f"---\n{header}---\nDo the work.", encoding="utf-8")


def test_an_agent_file_can_set_temperature_steps_and_deny_rules(project, home):
    write_agent(
        project,
        "careful",
        "description: Careful one\ntemperature: 0.2\nsteps: 7\n"
        "deny: run_command:rm *, write_file\n",
    )
    spec = load_agent_files(project, home)["careful"]
    assert spec["temperature"] == 0.2 and spec["steps"] == 7
    assert spec["deny"] == ["run_command:rm *", "write_file"]


@pytest.mark.parametrize(
    ("header", "fragment"),
    [("temperature: warm\n", "temperature must be a number"), ("steps: many\n", "steps must be")],
)
def test_bad_numbers_in_an_agent_file_name_the_file(project, home, header, fragment):
    write_agent(project, "odd", "description: Odd\n" + header)
    with pytest.raises(ValueError, match=fragment) as caught:
        load_agent_files(project, home)
    assert "odd.md" in str(caught.value)


def models(**scripts):
    setups = {
        name: ModelSetup(ScriptedModel(list(script)), "scripted", name)
        for name, script in scripts.items()
    }
    seen = []

    def build(config, environ):
        seen.append((config.model, dict(config.parameters)))
        return setups[config.model]

    build.setups, build.seen = setups, seen
    return build


def test_a_subagent_temperature_reaches_the_model_it_uses(project, home):
    write_agent(project, "scout", "description: Finds\nmodel: cheap\ntemperature: 0.3\n")
    build = models(
        **{"test-model": [call("agent_scout", task="look"), final("ok")], "cheap": [final("seen")]}
    )
    run_cli(project, home, [], lines="Go\n/exit\n", interactive=True, build=build)
    assert ("cheap", {"temperature": 0.3}) in build.seen


def test_a_deny_rule_in_an_agent_file_stops_that_agents_tool_call(project, home):
    import sys

    def maker(name):
        return f"\"{sys.executable}\" -c \"open('{name}', 'w').close()\""

    write_agent(
        project,
        "worker",
        "description: Works\ntools: Bash\nmodel: w\ndeny: run_command:*made.txt*\n",
    )
    write_agent(project, "free", "description: Free\ntools: Bash\nmodel: f\n")
    build = models(
        **{
            "test-model": [
                call("agent_worker", task="make it"),
                call("agent_free", task="make it"),
                final("done"),
            ],
            "w": [call("run_command", command=maker("made.txt")), final("tried")],
            "f": [call("run_command", command=maker("free.txt")), final("made")],
        }
    )
    run_cli(project, home, BYPASS, lines="Go\n/exit\n", interactive=True, build=build)
    assert (project / "free.txt").exists()
    assert not (project / "made.txt").exists()
