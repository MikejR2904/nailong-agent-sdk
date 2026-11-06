from __future__ import annotations

import sys
from datetime import date
from types import SimpleNamespace

import pytest

from nailong_cli.config import ConfigError, load_config
from nailong_cli.environment import environment_note
from nailong_cli.factory import context_policy
from nailong_cli.repeat_guard import RepeatGuard
from nailong_cli.todo import TodoList, todo_tool
from tests.conftest import call, final, make_console, run_cli, write_user_config

BASE = 'provider = "anthropic"\nmodel = "m"\napi_key_env = "K"\n'


def test_the_coding_agent_gets_far_more_context_than_the_sdk_default(project, home):
    config = load_config(project, home=home)
    policy = context_policy(config)
    assert policy.context_token_budget == 48_000 and policy.episode_token_budget == 36_000
    assert policy.tool_result_preview_chars == 12_000
    assert policy.compacted_summary_chars == 400
    assert config.max_steps == 30


def test_the_context_settings_are_validated_with_their_range(project, home):
    write_user_config(home, BASE + "context_tokens = 10\n")
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="context_tokens must be a whole number from 2000"):
        load_config(project, home=home)
    write_user_config(home, BASE + "tool_result_chars = 5\n")
    with pytest.raises(ConfigError, match="tool_result_chars must be a whole number from 200"):
        load_config(project, home=home)


def test_a_small_local_model_can_shrink_the_context(project, home):
    write_user_config(home, BASE + "context_tokens = 6000\ntool_result_chars = 1500\n")
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    policy = context_policy(load_config(project, home=home))
    assert policy.context_token_budget == 6_000 and policy.compacted_stub_token_budget == 750


def test_the_environment_note_names_the_shell_so_the_agent_does_not_guess(monkeypatch, tmp_path):
    monkeypatch.setattr("nailong_cli.environment.shutil.which", lambda name: None)
    monkeypatch.setattr(sys, "platform", "win32")
    note = environment_note(tmp_path, sandboxed=False, today=date(2026, 11, 4))
    assert "cmd.exe (not bash)" in note and "Today is 2026-11-04" in note
    assert "not sandboxed" in note and str(tmp_path) in note
    monkeypatch.setattr(sys, "platform", "linux")
    assert "POSIX shell" in environment_note(tmp_path, sandboxed=True)


def test_the_instructions_sent_to_the_model_carry_the_environment(project, home):
    _, _, _, setup = run_cli(project, home, ["-p", "hi"], final())
    system = next(
        section["value"]["text"]
        for section in setup.model.calls[0].prompt.model_dump()["sections"]
        if section["kind"] == "instructions"
    )
    assert "Environment:" in system and "Working directory:" in system


def test_the_task_list_result_repeats_the_whole_list_for_the_model():
    import asyncio

    console, _, _ = make_console("")
    items = [
        {"content": "Find sources", "status": "completed"},
        {"content": "Write answer", "status": "in_progress"},
    ]
    result = asyncio.run(todo_tool(TodoList(), console).run({"items": items}))
    assert result.status == "succeeded"
    assert "1 of 2 done" in result.output and "[in_progress] Write answer" in result.output


def event(tool, status="succeeded", **arguments):
    result = SimpleNamespace(status=status, output=None, error=None)
    return SimpleNamespace(tool=tool, arguments=arguments, result=result)


def test_an_identical_read_is_refused_with_the_step_that_already_did_it():
    guard = RepeatGuard()
    guard.after_tool(event("read_file", path="a.py"))
    reason = guard.before_tool(event("read_file", path="a.py"))
    assert "step 1" in reason and "nothing has changed" in reason
    assert guard.before_tool(event("read_file", path="b.py")) is None
    assert guard.before_tool(event("read_file", path="a.py", offset=50)) is None


def test_a_change_makes_the_same_read_legitimate_again():
    guard = RepeatGuard()
    guard.after_tool(event("read_file", path="a.py"))
    guard.after_tool(event("edit_file", path="a.py"))
    assert guard.before_tool(event("read_file", path="a.py")) is None
    guard.after_tool(event("read_file", path="a.py"))
    guard.after_tool(event("run_command", command="python a.py"))
    assert guard.before_tool(event("read_file", path="a.py")) is None


def test_writing_the_identical_file_twice_is_refused_but_not_after_a_command():
    guard = RepeatGuard()
    guard.after_tool(event("write_file", path="s.py", content="x"))
    assert "wrote exactly this file" in guard.before_tool(
        event("write_file", path="s.py", content="x")
    )
    assert guard.before_tool(event("write_file", path="s.py", content="y")) is None
    guard.after_tool(event("run_command", command="python s.py"))
    assert guard.before_tool(event("write_file", path="s.py", content="x")) is None


def test_failed_calls_and_commands_are_never_treated_as_repeats():
    guard = RepeatGuard()
    guard.after_tool(event("read_file", status="failed", path="a.py"))
    assert guard.before_tool(event("read_file", path="a.py")) is None
    guard.after_tool(event("run_command", command="pytest"))
    assert guard.before_tool(event("run_command", command="pytest")) is None


def test_a_new_turn_forgets_what_was_done_before():
    guard = RepeatGuard()
    guard.after_tool(event("read_file", path="a.py"))
    guard.new_turn()
    assert guard.before_tool(event("read_file", path="a.py")) is None


def test_the_model_is_told_when_it_repeats_a_read_and_the_user_sees_a_note(project, home):
    (project / "a.txt").write_text("hello", encoding="utf-8")
    read = call("read_file", path="a.txt")
    code, out, _, setup = run_cli(
        project,
        home,
        [],
        read,
        read,
        final("done"),
        lines="Read it twice\n/exit\n",
        interactive=True,
    )
    assert code == 0 and "skipped a repeated read_file (same as step 1)" in out
    seen = " ".join(str(item) for item in setup.model.calls[-1].observations)
    assert "You already made this exact call at step 1" in seen


def test_when_the_step_limit_is_hit_the_user_is_asked_whether_to_keep_going(project, home):
    steps = [call("glob", pattern="*"), call("glob", pattern="**"), call("glob", pattern="*.py")]
    code, out, _, _ = run_cli(
        project,
        home,
        ["--max-turns", "2"],
        *steps,
        lines="Look around\nn\n/exit\n",
        interactive=True,
    )
    assert code == 0 and "It used all 2 steps. Keep going for 2 more? [Y/n]" in out


def test_continuing_after_the_limit_sends_a_continue_message(project, home):
    steps = [call("glob", pattern="*"), call("glob", pattern="**"), call("glob", pattern="*.py")]
    code, out, _, setup = run_cli(
        project,
        home,
        ["--max-turns", "2"],
        *steps,
        lines="Look around\ny\nn\n/exit\n",
        interactive=True,
    )
    assert code == 0 and out.count("Keep going for 2 more?") == 2
    assert any(c.task.instructions == "Continue from where you stopped." for c in setup.model.calls)


def test_a_file_the_agent_just_read_reaches_its_next_step_whole(project, home):
    body = "\n".join(f"line {number} of the file" for number in range(1, 301))
    (project / "big.txt").write_text(body, encoding="utf-8")
    _, _, _, setup = run_cli(
        project, home, ["-p", "read it"], call("read_file", path="big.txt"), final("ok")
    )
    context = setup.model.calls[-1]
    seen = " ".join(str(item) for item in [*context.observations, *context.episodes])
    assert "line 1 of the file" in seen and "line 120 of the file" in seen
