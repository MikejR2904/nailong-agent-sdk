from __future__ import annotations

from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import ModelSetup

from tests.conftest import call, final, run_cli, write_user_config


class Queue:
    def __init__(self, *turns):
        self.turns = [ScriptedModel([turn]) for turn in turns]
        self.calls = []

    async def next_turn(self, context):
        self.calls.append(context)
        return await self.turns[len(self.calls) - 1].next_turn(context)


def queued(*turns):
    setup = ModelSetup(Queue(*turns), "scripted", "scripted-1")

    def build(config, environ):
        return setup

    build.setup = setup
    return build


BASE = 'provider = "anthropic"\nmodel = "test-model"\napi_key_env = "TEST_KEY"\n'


def test_compact_summarises_earlier_turns_and_the_summary_reaches_the_next_turn(project, home):
    code, out, err, setup = run_cli(
        project,
        home,
        [],
        build=queued(
            final("first answer"),
            final("The user asked one thing and it is done."),
            final("second answer"),
        ),
        lines="Do the first thing\n/compact\nAnd now the next\n/exit\n",
        interactive=True,
    )
    assert code == 0 and "Summarised turns 1 to 1" in out, err
    last = setup.model.calls[-1]
    assert "The user asked one thing and it is done." in str(last.task.input)


def test_export_writes_the_session_as_markdown_and_search_finds_it(project, home):
    code, out, err, _ = run_cli(
        project,
        home,
        [],
        final("a distinctive answer about kumquats"),
        lines="Tell me something\n/export\n/search kumquats\n/exit\n",
        interactive=True,
    )
    exports = list((project / ".nailong" / "exports").glob("*.md"))
    assert code == 0 and len(exports) == 1, err
    assert "kumquats" in exports[0].read_text(encoding="utf-8")
    assert out.count("kumquats") >= 1 and "No matches." not in out


def test_search_without_a_hit_says_so_and_without_text_gives_usage(project, home):
    _, out, _, _ = run_cli(
        project, home, [], lines="/search nothing-here\n/search\n/exit\n", interactive=True
    )
    assert "No matches." in out and "Usage: /search <text>" in out


def test_add_dir_lists_and_adds_a_directory_and_refuses_a_reused_name(project, home, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    lines = f"/add-dir {other} docs\n/add-dir\n/add-dir {other} docs\n/exit\n"
    code, out, err, _ = run_cli(project, home, [], lines=lines, interactive=True)
    assert code == 0 and "added docs" in out and "docs:" in out
    assert 'The directory name "docs" is already used' in err


def test_the_add_dir_flag_makes_the_directory_readable_by_the_agent(project, home, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.txt").write_text("remember the milk", encoding="utf-8")
    code, out, _, _ = run_cli(
        project,
        home,
        ["--add-dir", str(other), "-p", "read it"],
        call("read_file", root="other", path="notes.txt"),
        final("read"),
    )
    assert code == 0 and "read" in out


def test_jobs_and_kill_report_when_there_is_nothing_running(project, home):
    _, out, _, _ = run_cli(
        project, home, [], lines="/jobs\n/kill\n/plugins\n/exit\n", interactive=True
    )
    assert "No background commands or shells." in out
    assert "Usage: /kill <job id or shell:name>" in out
    assert "No plugins." in out


def test_cost_reports_a_price_once_one_is_configured(project, home):
    write_user_config(
        home,
        BASE
        + "[prices.scripted-1]\ninput_per_million = 3.0\n"
        + "cached_input_per_million = 0.3\noutput_per_million = 15.0\n",
    )
    _, out, _, _ = run_cli(
        project, home, [], final(), lines="Hello\n/cost\n/exit\n", interactive=True
    )
    assert "spent this run" in out


def test_cost_names_the_missing_price_for_a_model_without_one(project, home):
    _, out, _, _ = run_cli(
        project, home, [], final(), lines="Hello\n/cost\n/exit\n", interactive=True
    )
    assert "had no price" in out and "[prices.MODEL]" in out


def test_suggest_asks_the_model_read_only_and_prints_its_answer(project, home):
    _, out, _, _ = run_cli(
        project,
        home,
        [],
        build=queued(final("ok"), final("1. run the tests\n2. fix lint\n3. commit")),
        lines="Hi\n/suggest\n/exit\n",
        interactive=True,
    )
    assert "1. run the tests" in out
