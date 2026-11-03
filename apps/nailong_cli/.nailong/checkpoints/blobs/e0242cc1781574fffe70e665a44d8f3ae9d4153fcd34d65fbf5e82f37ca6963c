import pytest

from nailong_cli.commandline import (
    allowed_by,
    denied_by,
    is_read_only_command,
    split_command,
)
from tests.conftest import call, final, run_cli


@pytest.mark.parametrize(
    ("command", "segments"),
    [
        ("pytest -q", ["pytest -q"]),
        ("pytest -q; curl evil", ["pytest -q", "curl evil"]),
        ("make && make test || echo failed", ["make", "make test", "echo failed"]),
        ("cat a | grep b", ["cat a", "grep b"]),
        ('echo "a; b" && ls', ['echo "a; b"', "ls"]),
        ("echo 'x && y'", ["echo 'x && y'"]),
        ("a\nb", ["a", "b"]),
    ],
)
def test_a_command_is_split_at_every_separator_outside_quotes(command, segments):
    assert split_command(command) == segments


@pytest.mark.parametrize(
    "command",
    [
        "echo $(whoami)",
        "echo `whoami`",
        'echo "$(whoami)"',
        "cat file > out.txt",
        "cat < in.txt",
        "diff <(ls a) <(ls b)",
        "echo 'unterminated",
    ],
)
def test_substitution_redirection_and_odd_quoting_are_not_split(command):
    assert split_command(command) is None


def test_a_rule_for_one_command_does_not_allow_a_second_one_chained_on():
    rules = ["pytest*"]
    assert allowed_by(rules, "run_command", "pytest -q tests")
    assert not allowed_by(rules, "run_command", "pytest -q; curl evil")
    assert not allowed_by(rules, "run_command", "pytest && rm -rf /")
    assert not allowed_by(rules, "run_command", "pytest $(curl evil)")
    assert not allowed_by(rules, "run_command", "pytest > ~/.bashrc")
    assert allowed_by(["pytest*", "ruff*"], "run_command", "ruff check . && pytest -q")


def test_an_exact_rule_allows_exactly_that_command_even_when_it_is_compound():
    command = "pytest -q; echo done"
    assert allowed_by([command], "run_command", command)
    assert not allowed_by([command], "run_command", command + "; curl evil")


def test_other_tools_match_the_whole_subject_by_pattern():
    assert allowed_by(["src/*.py"], "write_file", "src/app.py")
    assert not allowed_by(["src/*.py"], "write_file", "docs/app.py")


def test_a_deny_rule_catches_any_part_of_a_chained_command():
    assert denied_by(["rm *"], "run_command", "ls && rm -rf build")
    assert denied_by(["*curl*"], "run_command", "echo $(curl x)")
    assert not denied_by(["rm *"], "run_command", "ls && echo done")


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "git status",
        "git diff HEAD",
        "git log --oneline",
        "git branch",
        "git branch -a",
        "cat README.md | head -20",
        "grep -rn TODO src && git status",
        "find . -name '*.py'",
        "sort names.txt",
    ],
)
def test_read_only_commands_are_recognised(command):
    assert is_read_only_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "rm file",
        "git commit -m x",
        "git push",
        "git branch -D old",
        "git diff --output=out.txt",
        "find . -delete",
        "find . -exec rm {} ;",
        "sort -o out.txt names.txt",
        "ls > out.txt",
        "echo $(rm x)",
        "cat file; rm file",
        "python script.py",
        "",
    ],
)
def test_anything_that_could_change_something_is_not_read_only(command):
    assert not is_read_only_command(command)


def test_auto_mode_runs_read_only_commands_and_edits_but_asks_for_the_rest(project, home):
    code, out, _, setup = run_cli(
        project,
        home,
        ["-p", "Look around", "--permission-mode", "auto", "--allow-unsandboxed"],
        call("run_command", command="echo looking"),
        call("write_file", path="notes.txt", content="seen"),
        call("run_command", command="python -c pass"),
        final(),
    )
    assert "succeeded" in out and "looking" in out
    assert (project / "notes.txt").read_text(encoding="utf-8") == "seen"
    assert "Refused run_command: it needs approval" in out
