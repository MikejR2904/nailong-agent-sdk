from __future__ import annotations

import sys

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import ModelSetup

from nailong_cli.repl import command_descriptions
from tests.conftest import final, run_cli


def command(project, name, text):
    folder = project / ".nailong" / "commands"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(text, encoding="utf-8")


def models(**scripts):
    setups = {
        name: ModelSetup(ScriptedModel(list(script)), "scripted", name)
        for name, script in scripts.items()
    }

    def build(config, environ):
        return setups[config.model]

    build.setups = setups
    return build


def first_task(setup, index=0):
    return setup.model.calls[index].task.instructions


def test_positional_arguments_fill_in_and_arguments_still_means_everything(project, home):
    command(project, "pair", "Compare $1 with $2. All: $ARGUMENTS. Missing: [$3]")
    _, _, _, setup = run_cli(
        project, home, [], final(), lines='/pair alpha "beta gamma"\n/exit\n', interactive=True
    )
    assert (
        first_task(setup) == 'Compare alpha with beta gamma. All: alpha "beta gamma". Missing: []'
    )


def test_the_header_is_not_sent_to_the_model_and_its_description_appears_in_the_menu(project, home):
    command(project, "ship", "---\ndescription: Run the checks and ship\n---\nShip it.")
    _, _, _, setup = run_cli(project, home, [], final(), lines="/ship\n/exit\n", interactive=True)
    assert first_task(setup) == "Ship it."

    class Holder:
        user_commands = {}

    from nailong_cli.user_commands import load_commands

    holder = Holder()
    holder.user_commands = load_commands(project, home)
    holder.parts = type("P", (), {"skills": []})()
    assert command_descriptions(holder)["/ship"] == "Run the checks and ship"


def test_a_backquoted_read_only_command_runs_and_its_output_goes_into_the_prompt(project, home):
    command(project, "branch", "Current state:\n!`echo hello-from-shell`\nSummarise it.")
    _, _, _, setup = run_cli(project, home, [], final(), lines="/branch\n/exit\n", interactive=True)
    task = first_task(setup)
    assert "hello-from-shell" in task and "!`" not in task


def test_any_other_command_needs_your_yes_and_a_no_stops_the_command(project, home):
    command(project, "make", f'Result: !`"{sys.executable}" -c "print(41 + 1)"`')
    _, out, err, setup = run_cli(
        project, home, [], final(), lines="/make\nn\n/exit\n", interactive=True
    )
    assert "Run it? [y/N]" in out and "did not approve running" in err
    assert not setup.model.calls
    _, _, _, setup = run_cli(
        project, home, [], final(), lines="/make\ny\n/exit\n", interactive=True
    )
    assert "Result: 42" in first_task(setup)


def test_without_a_terminal_a_non_read_only_injection_is_refused_with_the_reason(project, home):
    command(project, "make", f'Result: !`"{sys.executable}" -c "print(1)"`')
    _, _, err, _ = run_cli(project, home, [], final(), lines="/make\n/exit\n")
    assert "approving it needs a terminal" in err


def test_a_command_can_hand_its_work_to_a_named_subagent(project, home):
    folder = project / ".nailong" / "agents"
    folder.mkdir(parents=True)
    (folder / "scout.md").write_text("---\ndescription: Finds things\n---\nLook.", encoding="utf-8")
    command(project, "find", "---\nagent: scout\n---\nFind the config loader.")
    _, _, _, setup = run_cli(project, home, [], final(), lines="/find\n/exit\n", interactive=True)
    assert first_task(setup).startswith("Hand this to the agent_scout tool")
    assert "Find the config loader." in first_task(setup)


def test_a_missing_agent_or_a_subtask_without_one_is_explained(project, home):
    command(project, "ghost", "---\nagent: nobody\n---\nDo it.")
    command(project, "lone", "---\nsubtask: true\n---\nDo it.")
    _, _, err, _ = run_cli(project, home, [], lines="/ghost\n/lone\n/exit\n", interactive=True)
    assert 'names the agent "nobody"' in err and "sets subtask: true but names no agent" in err


def test_a_command_can_use_another_model_for_its_turn_only(project, home):
    command(project, "cheap", "---\nmodel: small-model\n---\nQuick job.")
    build = models(**{"test-model": [final("main answer")], "small-model": [final("small answer")]})
    code, out, err, _ = run_cli(
        project, home, [], lines="/cheap\nNext\n/exit\n", interactive=True, build=build
    )
    assert code == 0, err
    assert len(build.setups["small-model"].model.calls) == 1
    assert len(build.setups["test-model"].model.calls) == 1
    assert "small answer" in out and "main answer" in out
