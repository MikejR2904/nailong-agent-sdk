import json
import sys

from nailong_cli.config import ConfigError, load_config
from tests.conftest import (
    call,
    final,
    instructions_text,
    run_cli,
    tool_names,
    write_user_config,
)

PY = f'"{sys.executable}"'
BASE = 'provider = "anthropic"\nmodel = "m"\napi_key_env = "K"\n'


def test_plan_mode_removes_the_tools_that_change_things_and_says_so(project, home):
    code, _, _, setup = run_cli(
        project, home, ["-p", "Plan it", "--permission-mode", "plan"], final("1. do x")
    )
    names = tool_names(setup.model.calls[0])
    assert code == 0 and "read_file" in names and "todo_write" in names
    assert "write_file" not in names and "edit_file" not in names and "run_command" not in names
    assert "plan mode" in instructions_text(setup.model.calls[0])


def test_the_default_mode_has_every_tool(project, home):
    _, _, _, setup = run_cli(project, home, ["-p", "x"], final())
    assert {"write_file", "edit_file", "run_command"} <= set(tool_names(setup.model.calls[0]))


def test_accept_edits_lets_file_changes_through_but_commands_still_ask(project, home):
    run_cli(
        project,
        home,
        ["-p", "Edit", "--permission-mode", "accept-edits"],
        call("write_file", path="a.txt", content="x"),
        call("run_command", command="echo hi"),
        final(),
    )
    assert (project / "a.txt").read_text(encoding="utf-8") == "x"


def test_bypass_needs_a_sandbox_or_an_explicit_flag(project, home):
    refused = run_cli(
        project,
        home,
        ["-p", "Run", "--permission-mode", "bypass"],
        call("run_command", command=f"{PY} -c \"print('ran')\""),
        final(),
    )
    assert "no sandbox is active" in refused[1]
    allowed = run_cli(
        project,
        home,
        ["-p", "Run", "--permission-mode", "bypass", "--allow-unsandboxed"],
        call("run_command", command=f"{PY} -c \"print('ran')\""),
        final(),
    )
    assert "succeeded" in allowed[1] and "ran" in allowed[1]


def test_allow_and_disallow_rules_apply_to_this_run_only(project, home):
    run_cli(
        project,
        home,
        ["-p", "Write", "--allow", "write_file:ok.txt"],
        call("write_file", path="ok.txt", content="1"),
        call("write_file", path="no.txt", content="2"),
        final(),
    )
    assert (project / "ok.txt").exists() and not (project / "no.txt").exists()
    run_cli(
        project,
        home,
        ["-p", "Write", "--permission-mode", "accept-edits", "--disallow", "write_file:secret*"],
        call("write_file", path="secret.txt", content="3"),
        final(),
    )
    assert not (project / "secret.txt").exists()


def test_a_malformed_rule_is_a_usage_error(project, home):
    code, _, err, _ = run_cli(project, home, ["-p", "x", "--allow", ":x"], final())
    assert code == 64 and "needs a tool name" in err


def test_at_mentions_attach_files_and_directories_but_not_secrets_or_strangers(project, home):
    (project / "notes.txt").write_text("remember the milk", encoding="utf-8")
    (project / "src").mkdir()
    (project / "src" / "a.py").write_text("x = 1", encoding="utf-8")
    (project / ".env").write_text("TOKEN=abc", encoding="utf-8")
    prompt = "Read @notes.txt and @src and @.env and @missing.txt and mail me@example.com"
    _, out, _, setup = run_cli(project, home, ["-p", prompt], final())
    task = setup.model.calls[0].task.instructions
    assert "Contents of notes.txt:\nremember the milk" in task
    assert "Contents of directory src:\na.py" in task
    assert "TOKEN=abc" not in task and "missing.txt:" not in task.split("\n\n", 1)[1]
    assert "attached: notes.txt, src" in out


def test_memory_files_are_part_of_the_agents_instructions(project, home):
    (project / "AGENTS.md").write_text("Use tabs.", encoding="utf-8")
    (project / "NAILONG.md").write_text("Run make test.", encoding="utf-8")
    (home / ".config" / "nailong").mkdir(parents=True)
    (home / ".config" / "nailong" / "NAILONG.md").write_text("Be brief.", encoding="utf-8")
    _, _, _, setup = run_cli(project, home, ["-p", "x"], final())
    text = instructions_text(setup.model.calls[0])
    assert "Use tabs." in text and "Run make test." in text and "Be brief." in text


def test_system_prompt_append_and_max_turns_and_json_schema(project, home):
    schema = project / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"score": {"type": "integer"}},
                "required": ["score"],
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    code, out, _, setup = run_cli(
        project,
        home,
        [
            "-p",
            "Score it",
            "--system-prompt-append",
            "Answer tersely.",
            "--json-schema",
            str(schema),
            "--output",
            "json",
        ],
        {"type": "final", "output": {"score": 7}},
    )
    data = json.loads(out)
    assert code == 0 and json.loads(data["answer"]) == {"score": 7}
    assert "Answer tersely." in instructions_text(setup.model.calls[0])
    failing, _, _, _ = run_cli(
        project, home, ["-p", "Loop", "--max-turns", "1"], call("nope"), call("nope")
    )
    assert failing == 1


def test_piped_input_is_added_to_the_prompt_in_print_mode(project, home):
    _, _, _, setup = run_cli(
        project, home, ["-p", "Summarize this"], final(), lines="log line 1\nlog line 2\n"
    )
    assert "log line 2" in setup.model.calls[0].task.instructions


def test_stream_json_prints_one_event_per_line_ending_with_the_result(project, home):
    code, out, _, _ = run_cli(
        project,
        home,
        ["-p", "Go", "--output", "stream-json", "--allow", "write_file:a.txt"],
        call("write_file", path="a.txt", content="x"),
        final("finished"),
    )
    events = [json.loads(line) for line in out.strip().splitlines()]
    kinds = [event["type"] for event in events]
    assert code == 0 and kinds[0] == "session" and kinds[-1] == "result"
    assert "tool_use" in kinds and "tool_result" in kinds
    assert events[-1]["answer"] == "finished" and events[-1]["status"] == "completed"


def test_settings_for_hooks_mcp_and_subagents_are_validated(project, home):
    import pytest

    write_user_config(home, BASE + '[[hooks]]\nevent = "bad"\ncommand = "x"\n')
    with pytest.raises(ConfigError, match="hooks entry 1"):
        load_config(project, home=home)
    write_user_config(home, BASE + "[mcp.x]\nfoo = 1\n")
    with pytest.raises(ConfigError, match='mcp server "x" needs a command'):
        load_config(project, home=home)
    write_user_config(home, BASE + 'permission_mode = "yolo"\n')
    with pytest.raises(ConfigError, match="permission_mode must be one of"):
        load_config(project, home=home)


def test_hooks_and_mcp_in_a_project_file_are_ignored_and_the_user_is_told(project, home):
    (project / ".nailong" / "config.toml").write_text(
        'provider = "anthropic"\nmodel = "m"\napi_key_env = "TEST_KEY"\n'
        '[[hooks]]\nevent = "pre_tool"\ncommand = "echo x"\n[mcp.evil]\ncommand = "evil"\n',
        encoding="utf-8",
    )
    config = load_config(project, home=home)
    assert config.hooks == () and config.mcp_servers == {}
    assert config.ignored_project_keys == ("hooks", "mcp")
    _, out, _, _ = run_cli(project, home, [], lines="/doctor\n/exit\n", interactive=True)
    assert "ignored ['hooks', 'mcp']" in out
