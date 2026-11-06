from __future__ import annotations

import json
import sys

from nailong_cli.hook_protocol import interpret, normalise, tool_matches
from tests.conftest import CONFIG, call, final, run_cli, write_user_config


def test_claude_event_names_and_matchers_select_the_claude_protocol():
    hook = normalise({"event": "PreToolUse", "matcher": "Bash", "command": "x"})
    assert hook["event"] == "pre_tool" and hook["protocol"] == "claude"
    assert normalise({"event": "pre_tool", "command": "x"}).get("protocol") is None


def test_a_matcher_is_a_pattern_over_claude_names_and_ours():
    hook = {"matcher": "Edit|Write"}
    assert tool_matches(hook, "write_file") and tool_matches(hook, "edit_file")
    assert not tool_matches(hook, "run_command")
    assert tool_matches({"matcher": "run_command"}, "run_command")
    assert tool_matches({"matcher": "*"}, "anything")


def test_exit_two_blocks_other_failures_only_warn():
    assert interpret(2, "no rm here").blocked
    assert interpret(2, "no rm here").reason == "no rm here"
    assert not interpret(1, "oops").blocked and interpret(1, "oops").warning == "oops"
    assert not interpret(0, "").blocked


def test_json_decisions_on_standard_output_are_honoured():
    assert interpret(0, json.dumps({"decision": "block", "reason": "because"})).reason == "because"
    deny = {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "r"}}
    assert interpret(0, json.dumps(deny)).blocked
    assert interpret(0, json.dumps({"continue": False, "stopReason": "stop"})).blocked
    context = {"hookSpecificOutput": {"additionalContext": "note"}}
    assert interpret(0, json.dumps(context)).context == "note"


def hook_config(tmp_path, body: str, extra: str = "") -> str:
    script = tmp_path / "hook.py"
    script.write_text(body, encoding="utf-8")
    return (
        CONFIG
        + '[[hooks]]\nevent = "PreToolUse"\nmatcher = "Bash"\n'
        + f'command = \'"{sys.executable}" "{script.as_posix()}"\'\n'
        + extra
    )


BLOCKER = (
    "import json, sys\n"
    "data = json.load(sys.stdin)\n"
    "assert data['tool_name'] == 'Bash' and data['hook_event_name'] == 'PreToolUse'\n"
    "if 'forbidden' in data['tool_input']['command']:\n"
    "    print('forbidden word', file=sys.stderr)\n"
    "    sys.exit(2)\n"
)
BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]


def test_a_claude_style_hook_reads_json_on_stdin_and_blocks_with_exit_two(project, home, tmp_path):
    write_user_config(home, hook_config(tmp_path, BLOCKER))
    code, out, err, _ = run_cli(
        project,
        home,
        ["-p", "x", *BYPASS],
        call("run_command", command="echo forbidden"),
        final("done"),
    )
    assert "Hook refused run_command: forbidden word" in out + err


def test_a_claude_style_hook_that_only_fails_does_not_block(project, home, tmp_path):
    body = "import sys\nsys.exit(1)\n"
    write_user_config(home, hook_config(tmp_path, body))
    _, out, err, _ = run_cli(
        project, home, ["-p", "x", *BYPASS], call("run_command", command="echo fine"), final("done")
    )
    assert "Hook refused" not in out + err and "fine" in out


def test_updated_input_and_allow_in_the_json_decision_are_read():
    verdict = interpret(
        0,
        json.dumps(
            {
                "hookSpecificOutput": {
                    "permissionDecision": "allow",
                    "updatedInput": {"command": "x"},
                }
            }
        ),
    )
    assert verdict.allowed and verdict.updated == {"command": "x"} and not verdict.blocked


def test_claude_names_for_compaction_and_subagent_events_are_accepted():
    assert normalise({"event": "PreCompact", "command": "x"})["event"] == "pre_compact"
    assert normalise({"event": "SubagentStop", "command": "x"})["event"] == "subagent_stop"


REWRITER = (
    "import json, sys\n"
    "data = json.load(sys.stdin)\n"
    "print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'allow',\n"
    "    'updatedInput': {'command': 'echo REWRITTEN'}}}))\n"
)


def test_a_hook_can_rewrite_the_command_and_approve_it_without_a_prompt(project, home, tmp_path):
    write_user_config(home, hook_config(tmp_path, REWRITER))
    _, out, err, _ = run_cli(
        project,
        home,
        ["-p", "x", "--allow-unsandboxed"],
        call("run_command", command="echo ORIGINAL"),
        final("done"),
    )
    assert "REWRITTEN" in out and "ORIGINAL" not in out.replace("echo ORIGINAL", "")
    assert "needs approval" not in out + err


def test_a_pre_compact_hook_can_stop_the_compaction(project, home, tmp_path):
    script = tmp_path / "stop.py"
    script.write_text("import sys\nprint('not now', file=sys.stderr)\nsys.exit(2)\n", "utf-8")
    write_user_config(
        home,
        CONFIG
        + '[[hooks]]\nevent = "PreCompact"\n'
        + f'command = \'"{sys.executable}" "{script.as_posix()}"\'\n',
    )
    _, out, err, _ = run_cli(
        project,
        home,
        [],
        final("hi"),
        final("summary"),
        lines="Hello\n/compact\n/exit\n",
        interactive=True,
    )
    assert "A pre_compact hook stopped the compaction: not now" in out + err
