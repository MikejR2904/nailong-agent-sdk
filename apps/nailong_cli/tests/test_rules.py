from __future__ import annotations

import pytest

from nailong_cli.config import ConfigError, load_config
from nailong_cli.rules import PermissionRules, Rule, rule_from_table
from tests.conftest import call, final, run_cli, write_user_config
from tests.test_sdk_features import BASE, user_only_project
from tests.test_terminal_ui import run_fancy

BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]


def decide(rules, tool, subject):
    rule = PermissionRules(rules).decide(tool, subject)
    return None if rule is None else rule.action


def test_the_last_matching_rule_decides():
    rules = [
        Rule("ask", ("run_command",), "git *"),
        Rule("allow", ("run_command",), "git status"),
        Rule("deny", ("run_command",), "git push*"),
    ]
    assert decide(rules, "run_command", "git commit -m x") == "ask"
    assert decide(rules, "run_command", "git status") == "allow"
    assert decide(rules, "run_command", "git push origin") == "deny"
    assert decide(rules, "run_command", "ls") is None
    assert decide(rules, "read_file", "git status") is None


def test_a_command_rule_looks_at_each_part_of_a_chained_command():
    ask = [Rule("ask", ("run_command",), "rm *")]
    assert decide(ask, "run_command", "echo hi && rm -rf build") == "ask"
    allow = [Rule("allow", ("run_command",), "git *")]
    assert decide(allow, "run_command", "git status && git diff") == "allow"
    assert decide(allow, "run_command", "git status && curl x") is None


def test_several_tool_names_and_wildcards_can_share_one_rule():
    rule = rule_from_table(
        {"action": "deny", "tool": "write_file, edit_file", "pattern": "*.lock"}, "t", 1
    )
    assert decide([rule], "edit_file", "poetry.lock") == "deny"
    mcp = rule_from_table({"action": "deny", "tool": "mcp__db__*"}, "t", 1)
    assert decide([mcp], "mcp__db__drop", "") == "deny"


@pytest.mark.parametrize(
    ("entry", "fragment"),
    [
        ({"action": "maybe", "tool": "x"}, "action must be one of"),
        ({"action": "ask"}, "needs a tool name"),
        ({"action": "ask", "tool": "x", "pattern": 5}, "pattern must be text"),
    ],
)
def test_a_bad_rule_says_which_entry_and_why(entry, fragment):
    with pytest.raises(ValueError, match=fragment):
        rule_from_table(entry, "t", 3)


def test_rules_in_the_user_file_all_count_and_bad_ones_stop_startup(project, home):
    write_user_config(
        home,
        BASE + '[[rules]]\naction = "ask"\ntool = "run_command"\npattern = "git push*"\n',
    )
    config = load_config(project, home=home)
    assert config.rules[0]["pattern"] == "git push*" and config.rules[0]["source"] == "user"
    with pytest.raises(ConfigError, match="action must be one of"):
        user_only_project(project, home, '[[rules]]\naction = "nope"\ntool = "x"\n')


def test_a_project_file_may_add_ask_and_deny_rules_but_never_allow_rules(project, home):
    write_user_config(home, BASE)
    (project / ".nailong" / "config.toml").write_text(
        '[[rules]]\naction = "deny"\ntool = "write_file"\npattern = "*.lock"\n'
        '[[rules]]\naction = "allow"\ntool = "run_command"\npattern = "*"\n',
        encoding="utf-8",
    )
    config = load_config(project, home=home)
    assert [item["action"] for item in config.rules] == ["deny"]
    assert "rules (allow entries)" in config.ignored_project_keys


ASK_PRIVATE = BASE + '[[rules]]\naction = "ask"\ntool = "read_file"\npattern = "private/*"\n'


def private_project(project, home):
    (project / "private").mkdir()
    (project / "private" / "plan.txt").write_text("the plan\n", encoding="utf-8")
    write_user_config(home, ASK_PRIVATE)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")


def test_reading_a_file_under_an_ask_rule_asks_first_and_a_no_stops_it(project, home):
    private_project(project, home)
    asked = []

    def chooser(title, options):
        asked.append(title)
        return 3

    code, out, _ = run_fancy(
        project,
        home,
        call("read_file", path="private/plan.txt"),
        final("done"),
        lines="Read it\n/exit\n",
        chooser=chooser,
        argv=BYPASS,
    )
    assert code == 0 and asked == ["Approve read_file?"]
    assert "read 1 lines" not in out and "was not approved" in out


def test_saying_yes_lets_the_read_through(project, home):
    private_project(project, home)
    code, out, _ = run_fancy(
        project,
        home,
        call("read_file", path="private/plan.txt"),
        final("done"),
        lines="Read it\n/exit\n",
        chooser=lambda title, options: 0,
        argv=BYPASS,
    )
    assert code == 0 and "read 1 lines" in out and "was not approved" not in out


def test_a_file_no_rule_mentions_reads_without_asking(project, home):
    private_project(project, home)
    (project / "open.txt").write_text("hi\n", encoding="utf-8")
    code, out, _ = run_fancy(
        project,
        home,
        call("read_file", path="open.txt"),
        final("done"),
        lines="Read it\n/exit\n",
        chooser=lambda title, options: pytest.fail("should not ask"),
    )
    assert code == 0 and "read 1 lines" in out


def test_a_deny_rule_stops_even_a_read_and_bypass_cannot_override_an_ask_rule(project, home):
    (project / "vault").mkdir()
    (project / "vault" / "key.txt").write_text("k", encoding="utf-8")
    write_user_config(
        home,
        BASE
        + '[[rules]]\naction = "deny"\ntool = "read_file"\npattern = "vault/*"\n'
        + '[[rules]]\naction = "ask"\ntool = "run_command"\npattern = "git push*"\n',
    )
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    _, out, err, _ = run_cli(
        project,
        home,
        [*BYPASS, "-p", "Go"],
        call("read_file", path="vault/key.txt"),
        call("run_command", command="git push origin main"),
        final("done"),
    )
    assert "refused by a deny rule" in out + err
    assert "Refused run_command" in out + err and "needs approval" in out + err


def test_the_allow_flag_still_works_and_becomes_a_rule(project, home):
    _, out, err, _ = run_cli(
        project,
        home,
        ["-p", "Go", "--allow", "run_command:echo *", "--allow-unsandboxed"],
        call("run_command", command="echo hello"),
        final("done"),
    )
    assert "hello" in out + err
