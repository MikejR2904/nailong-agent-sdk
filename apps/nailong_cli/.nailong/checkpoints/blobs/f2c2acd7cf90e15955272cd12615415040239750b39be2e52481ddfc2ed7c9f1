import json

from nailong_cli.cli import main
from nailong_cli.permissions import PermissionStore, permissions_path
from tests.conftest import call, factory, final, make_console


def run(project, home, argv, *turns, lines="", interactive=False, environ=None, **options):
    console, out, err = make_console(lines, interactive=interactive)
    build = factory(*turns)
    code = main(
        argv,
        environ=environ if environ is not None else {"TEST_KEY": "k"},
        console=console,
        model_factory=build,
        home=home,
        cwd=project,
        **options,
    )
    return code, out.getvalue(), err.getvalue(), build.setup


def test_print_mode_runs_once_prints_the_answer_and_exits_zero(project, home):
    code, out, err, _ = run(project, home, ["-p", "Say hi"], final("hello there"))
    assert code == 0 and "hello there" in out and "[completed] 1 step(s)" in out and err == ""


def test_json_output_is_one_parseable_object_with_the_session(project, home):
    code, out, _, _ = run(project, home, ["-p", "Say hi", "--output", "json"], final("hello"))
    data = json.loads(out)
    assert code == 0 and data["status"] == "completed" and data["answer"] == "hello"
    assert data["steps"] == 1 and data["session"].startswith("session-") and data["cost"] is None
    assert out.strip().count("\n") == 0


def test_a_run_that_fails_exits_one_and_json_says_why(project, home):
    code, out, _, _ = run(
        project, home, ["-p", "Loop", "--output", "json"], call("nope"), call("nope")
    )
    data = json.loads(out)
    assert code == 1 and data["status"] == "failed" and data["answer"] is None and data["reason"]


def test_configuration_problems_exit_64_with_a_specific_message(tmp_path, home):
    empty = tmp_path / "empty"
    empty.mkdir()
    code, _, err, _ = run(empty, home, ["-p", "x"], final())
    assert code == 64 and "Missing setting(s): provider, model" in err


def test_a_missing_key_names_the_variable(project, home):
    console, _, err = make_console()
    code = main(["-p", "x"], environ={}, console=console, home=home, cwd=project)
    assert code == 64 and 'The environment variable "TEST_KEY"' in err.getvalue()


def test_flags_override_the_model_without_touching_the_files(project, home):
    seen = {}

    def build(config, environ):
        seen["model"] = config.model
        return factory(final())(config, environ)

    console, _, _ = make_console()
    code = main(
        ["-p", "x", "--model", "other"],
        environ={"TEST_KEY": "k"},
        console=console,
        model_factory=build,
        home=home,
        cwd=project,
    )
    assert code == 0 and seen["model"] == "other"


def test_the_agent_cannot_write_the_cli_settings_even_with_a_matching_rule(project, home):
    store = PermissionStore(permissions_path(home))
    store.add(project, "write_file", ".nailong/config.toml")
    store.add(project, "write_file", "NAILONG.md")
    _, _, _, setup = run(
        project,
        home,
        ["-p", "Change my rules"],
        call("write_file", path=".nailong/config.toml", content="x"),
        final(),
    )
    config = (project / ".nailong" / "config.toml").read_text(encoding="utf-8")
    assert config.startswith("provider")
    assert "do not write to" in str(list(setup.model.calls[-1].observations))
    run(
        project,
        home,
        ["-p", "Edit notes"],
        call("write_file", path="NAILONG.md", content="y"),
        final(),
    )
    assert not (project / "NAILONG.md").exists()


def test_a_write_needs_approval_and_without_a_terminal_it_is_refused(project, home):
    code, out, _, _ = run(
        project,
        home,
        ["-p", "Make a file"],
        call("write_file", path="out.txt", content="hi"),
        final("tried"),
    )
    assert not (project / "out.txt").exists() and "Refused write_file" in out


def test_a_saved_rule_lets_the_write_through_unattended(project, home):
    PermissionStore(permissions_path(home)).add(project, "write_file", "out.txt")
    code, _, _, _ = run(
        project,
        home,
        ["-p", "Make a file"],
        call("write_file", path="out.txt", content="hi"),
        final("made it"),
    )
    assert code == 0 and (project / "out.txt").read_text(encoding="utf-8") == "hi"


def test_the_interactive_loop_runs_turns_commands_and_stops_on_exit(project, home):
    lines = "Say hi\n/tools\n/sessions\n/sandbox\n/nonsense\n/exit\n"
    code, out, _, _ = run(project, home, [], final("hi back"), lines=lines, interactive=True)
    assert code == 0
    assert "hi back" in out and "[completed]" in out
    assert "write_file" in out and "run_command" in out
    assert "NOT SANDBOXED" in out and "Unknown command /nonsense" in out


def test_end_of_input_leaves_the_loop_cleanly(project, home):
    code, out, _, _ = run(project, home, [], lines="", interactive=True)
    assert code == 0 and "/help for commands" in out


def test_continue_resumes_the_latest_session_and_the_model_sees_the_history(project, home):
    first = run(project, home, ["-p", "Remember the number 42"], final("noted"))
    session = json.loads(run(project, home, ["-p", "x", "--output", "json"], final("y"))[1])[
        "session"
    ]
    code, out, _, setup = run(
        project, home, ["-p", "What number?", "--continue", "--output", "json"], final("42")
    )
    data = json.loads(out)
    assert first[0] == 0 and code == 0 and data["session"] == session
    history = setup.model.calls[0].task.input["session_history"]
    assert [turn["prompt"] for turn in history["turns"]] == ["x"]


def test_fork_branches_a_saved_session_at_a_turn(project, home):
    created = json.loads(run(project, home, ["-p", "One", "--output", "json"], final("a"))[1])[
        "session"
    ]
    run(project, home, ["-p", "Two", "--resume", created], final("b"))
    code, out, _, setup = run(
        project,
        home,
        ["-p", "Three", "--fork", created, "--at", "1", "--output", "json"],
        final("c"),
    )
    data = json.loads(out)
    assert code == 0 and data["session"] != created
    assert [t["prompt"] for t in setup.model.calls[0].task.input["session_history"]["turns"]] == [
        "One"
    ]


def test_resuming_a_session_that_does_not_exist_is_a_usage_error(project, home):
    code, _, err, _ = run(project, home, ["-p", "x", "--resume", "ghost"], final())
    assert code == 64 and 'There is no session "ghost"' in err


def test_slash_commands_resume_fork_and_verify_inside_the_loop(project, home):
    lines = "First\n/fork 1\n/verify\n/sessions\n/new\n/cost\n/forge\n/exit\n"
    code, out, err, _ = run(project, home, [], final("one"), lines=lines, interactive=True)
    assert code == 0 and "forked session" in out and "chain intact" in out
    assert "started session" in out and "spent this run: 0" in out and "forge is off" in out


def test_the_forge_flag_adds_the_proposal_tools_and_a_reviewer(project, home):
    lines = "/tools\n/forge\n/exit\n"
    code, out, _, _ = run(project, home, ["--forge"], lines=lines, interactive=True)
    assert code == 0 and "propose_tool" in out and "'active': []" in out


def test_ctrl_c_during_a_turn_cancels_it_and_returns_to_the_prompt(project, home):
    import asyncio
    import signal
    import threading

    from nailong_agent_sdk.starter import ModelSetup

    from nailong_cli.cli import main as cli_main

    class Slow:
        async def next_turn(self, context):
            await asyncio.sleep(30)

    console, out, _ = make_console("Do it\n/exit\n", interactive=True)
    timer = threading.Timer(1.0, lambda: signal.raise_signal(signal.SIGINT))
    timer.start()
    try:
        code = cli_main(
            [],
            environ={"TEST_KEY": "k"},
            console=console,
            model_factory=lambda config, environ: ModelSetup(Slow(), "scripted", "s"),
            home=home,
            cwd=project,
        )
    finally:
        timer.cancel()
    assert code == 0 and "Cancelled." in out.getvalue()
