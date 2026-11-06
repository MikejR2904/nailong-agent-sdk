from __future__ import annotations

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import ModelSetup

from tests.conftest import final, run_cli, write_user_config
from tests.test_sdk_features import BASE


def by_name(**scripts):
    setups = {
        name: ModelSetup(ScriptedModel(list(script)), "scripted", name)
        for name, script in scripts.items()
    }
    seen = []

    def build(config, environ):
        seen.append((config.model, dict(config.parameters)))
        return setups[config.model]

    build.setups = setups
    build.seen = seen
    return build


def user_settings(home, project, text):
    write_user_config(home, BASE + text)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")


def test_summaries_and_suggestions_use_the_small_model_when_one_is_set(project, home):
    user_settings(home, project, 'small_model = "small-model"\n')
    build = by_name(**{"m": [final("main reply")], "small-model": [final("a short summary")]})
    code, out, err, _ = run_cli(
        project, home, [], lines="Hello\n/compact\n/suggest\n/exit\n", interactive=True, build=build
    )
    assert code == 0, err
    assert len(build.setups["m"].model.calls) == 1
    assert len(build.setups["small-model"].model.calls) == 2


def test_without_a_small_model_the_main_model_does_those_jobs(project, home):
    build = by_name(**{"test-model": [final("reply")]})
    run_cli(project, home, [], lines="Hello\n/suggest\n/exit\n", interactive=True, build=build)
    assert len(build.setups["test-model"].model.calls) == 2


def test_a_variant_switches_the_model_parameters_and_off_goes_back(project, home):
    user_settings(
        home,
        project,
        '[variants.deep]\neffort = "high"\ntemperature = 0.1\n[variants.cool]\ntemperature = 0.0\n',
    )
    build = by_name(**{"m": [final("ok")]})
    _, out, err, _ = run_cli(
        project,
        home,
        [],
        lines="/variant\n/variant deep\n/variant off\n/variant nope\n/exit\n",
        interactive=True,
        build=build,
    )
    assert "variants: cool, deep" in out and "variant is now deep" in out
    assert "variant is now off" in out and 'There is no variant "nope"' in err
    deep = build.seen[1][1]
    assert deep["temperature"] == 0.1 and "output_config" in deep
    assert "temperature" not in build.seen[2][1]


def test_an_at_name_hands_the_request_to_that_subagent(project, home):
    agents = project / ".nailong" / "agents"
    agents.mkdir(parents=True)
    (agents / "scout.md").write_text("---\ndescription: Finds\n---\nLook.", encoding="utf-8")
    _, _, _, setup = run_cli(
        project, home, [], final(), lines="@scout find the loader\n/exit\n", interactive=True
    )
    task = setup.model.calls[0].task.instructions
    assert task.startswith("Hand this to the agent_scout tool") and "find the loader" in task


def test_an_at_word_that_is_a_real_file_stays_a_file_attachment(project, home):
    agents = project / ".nailong" / "agents"
    agents.mkdir(parents=True)
    (agents / "scout.md").write_text("---\ndescription: Finds\n---\nLook.", encoding="utf-8")
    (project / "scout").write_text("file text", encoding="utf-8")
    _, _, _, setup = run_cli(
        project, home, [], final(), lines="@scout what is this\n/exit\n", interactive=True
    )
    assert not setup.model.calls[0].task.instructions.startswith("Hand this")
