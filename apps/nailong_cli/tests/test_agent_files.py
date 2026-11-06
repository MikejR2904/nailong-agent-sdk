from __future__ import annotations

import pytest
from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import ModelSetup

from nailong_cli.agent_files import load_agent_files, tool_level
from tests.conftest import call, final, run_cli


def write_agent(root, name, header, body="Look around and report."):
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(f"---\n{header}---\n{body}", encoding="utf-8")


def project_agents(project):
    return project / ".nailong" / "agents"


def models(**turns):
    setups = {
        name: ModelSetup(ScriptedModel(list(script)), "scripted", name)
        for name, script in turns.items()
    }

    def build(config, environ):
        return setups[config.model]

    build.setups = setups
    return build


def test_agent_files_give_description_instructions_tools_and_model(project, home):
    write_agent(
        project_agents(project),
        "code-scout",
        "name: code-scout\ndescription: Finds code\ntools: Read, Grep\nmodel: cheap-model\n",
    )
    found = load_agent_files(project, home)
    spec = found["code_scout"]
    assert spec["description"] == "Finds code" and spec["tools"] == "read-only"
    assert spec["model"] == "cheap-model" and spec["instructions"] == "Look around and report."
    assert "agents" in spec["source"]


@pytest.mark.parametrize(
    ("value", "level"),
    [
        (None, "read-only"),
        ("Read, Grep, Glob", "read-only"),
        ("Read, Write", "edit"),
        ("Bash", "all"),
        ("Bash(git status:*), Read", "all"),
        ("edit", "edit"),
        ("all", "all"),
        (["Read", "Edit"], "edit"),
    ],
)
def test_the_tools_line_maps_to_what_the_subagent_may_do(value, level):
    assert tool_level(value, "x") == level


def test_the_project_folder_beats_the_user_one_and_bad_files_name_the_problem(project, home):
    write_agent(home / ".claude" / "agents", "scout", "description: From claude\n", "claude")
    write_agent(project_agents(project), "scout", "description: From project\n", "project")
    assert load_agent_files(project, home)["scout"]["description"] == "From project"
    write_agent(project_agents(project), "broken", "name: broken\n", "text")
    with pytest.raises(ValueError, match='broken.md needs a "description:"'):
        load_agent_files(project, home)


def test_a_subagent_can_run_on_its_own_model(project, home):
    write_agent(project_agents(project), "scout", "description: Finds things\nmodel: cheap-model\n")
    build = models(
        **{
            "test-model": [call("agent_scout", task="look"), final("main done")],
            "cheap-model": [final("scout answer")],
        }
    )
    code, out, err, _ = run_cli(
        project, home, [], lines="Go\n/exit\n", interactive=True, build=build
    )
    assert code == 0, err
    assert build.setups["cheap-model"].model.calls, "the subagent should have used cheap-model"
    assert len(build.setups["test-model"].model.calls) >= 2


def test_a_read_only_subagent_runs_in_the_background_and_its_answer_is_collected(project, home):
    write_agent(project_agents(project), "scout", "description: Finds things\nmodel: cheap-model\n")
    build = models(
        **{
            "test-model": [
                call("agent_start", agent="scout", task="look"),
                call("agent_result", run="agent-1", wait_seconds=30),
                final("main done"),
            ],
            "cheap-model": [final("found it")],
        }
    )
    code, out, err, _ = run_cli(
        project, home, [], lines="Go\n/agents\n/exit\n", interactive=True, build=build
    )
    assert code == 0, err
    assert "found it" in out
    assert "agent-1: done" in out


def test_only_read_only_subagents_may_run_in_the_background(project, home):
    write_agent(project_agents(project), "reader", "description: Reads\n")
    write_agent(project_agents(project), "builder", "description: Builds\ntools: Bash\n")
    build = models(
        **{
            "test-model": [
                call("agent_start", agent="builder", task="build"),
                final("main done"),
            ]
        }
    )
    _, out, _, _ = run_cli(project, home, [], lines="Go\n/exit\n", interactive=True, build=build)
    assert '"builder" cannot run in the background' in out and "reader" in out


def user_agents(home):
    return home / ".config" / "nailong" / "agents"


def test_a_user_agent_with_an_allow_list_runs_in_the_background_inside_that_list(project, home):
    (project / "notes").mkdir()
    write_agent(
        user_agents(home),
        "scribe",
        "description: Writes notes\ntools: Write\nallow: write_file:notes/*\nmodel: cheap-model\n",
    )
    build = models(
        **{
            "test-model": [
                call("agent_start", agent="scribe", task="write"),
                call("agent_result", run="agent-1", wait_seconds=30),
                final("main done"),
            ],
            "cheap-model": [
                call("write_file", path="notes/a.txt", content="ok"),
                call("write_file", path="other.txt", content="no"),
                final("scribe done"),
            ],
        }
    )
    code, out, err, _ = run_cli(
        project, home, [], lines="Go\n/exit\n", interactive=True, build=build
    )
    assert code == 0, err
    assert (project / "notes" / "a.txt").read_text(encoding="utf-8") == "ok"
    assert not (project / "other.txt").exists()


def test_a_project_agent_cannot_grant_itself_an_allow_list(project, home):
    write_agent(
        project_agents(project),
        "sneaky",
        "description: Writes\ntools: Write\nallow: write_file:*\n",
    )
    assert "allow" not in load_agent_files(project, home)["sneaky"]
    write_agent(project_agents(project), "reader", "description: Reads\n")
    build = models(**{"test-model": [call("agent_start", agent="sneaky", task="x"), final("ok")]})
    _, out, _, _ = run_cli(project, home, [], lines="Go\n/exit\n", interactive=True, build=build)
    assert '"sneaky" cannot run in the background' in out
