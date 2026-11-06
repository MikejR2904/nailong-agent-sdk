from __future__ import annotations

import json
import sys

import pytest

from nailong_cli.config import ConfigError, load_config
from nailong_cli.post_edit import changed_files, checkers_from_table
from tests.conftest import call, final, run_cli, write_user_config
from tests.test_sdk_features import BASE, user_only_project

BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]


def tool_file(tmp_path, name, code):
    path = tmp_path / name
    path.write_text(code, encoding="utf-8")
    return path


def settings(python_script, kind, extensions=".txt"):
    return (
        f"[{kind}.mine]\ncommand = [{json.dumps(sys.executable)}, {json.dumps(str(python_script))},"
        f' "$FILE"]\nextensions = ["{extensions}"]\n'
    )


UPPER = "import sys, pathlib\np = pathlib.Path(sys.argv[1])\np.write_text(p.read_text().upper())\n"
COMPLAIN = "import sys\nprint('bad: line 1 looks wrong')\nsys.exit(1)\n"
FAIL = "import sys\nprint('cannot format this')\nsys.exit(3)\n"


def test_a_formatter_runs_on_the_file_the_agent_wrote(project, home, tmp_path):
    script = tool_file(tmp_path, "upper.py", UPPER)
    user_only_project(project, home, settings(script, "formatters"))
    code, _, err, _ = run_cli(
        project,
        home,
        BYPASS,
        call("write_file", path="note.txt", content="hello"),
        final("done"),
        lines="Write\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    assert (project / "note.txt").read_text(encoding="utf-8") == "HELLO"


def test_a_formatter_only_touches_the_extensions_it_names(project, home, tmp_path):
    script = tool_file(tmp_path, "upper.py", UPPER)
    user_only_project(project, home, settings(script, "formatters"))
    run_cli(
        project,
        home,
        BYPASS,
        call("write_file", path="note.md", content="hello"),
        final("done"),
        lines="Write\n/exit\n",
        interactive=True,
    )
    assert (project / "note.md").read_text(encoding="utf-8") == "hello"


def test_a_failing_formatter_is_reported_quietly_and_the_run_goes_on(project, home, tmp_path):
    script = tool_file(tmp_path, "fail.py", FAIL)
    user_only_project(project, home, settings(script, "formatters"))
    code, out, _, _ = run_cli(
        project,
        home,
        BYPASS,
        call("write_file", path="note.txt", content="hello"),
        final("done"),
        lines="Write\n/exit\n",
        interactive=True,
    )
    assert code == 0 and "formatter mine failed on note.txt: cannot format this" in out
    assert (project / "note.txt").read_text(encoding="utf-8") == "hello"


def test_a_check_that_finds_problems_tells_the_agent_on_its_next_step(project, home, tmp_path):
    script = tool_file(tmp_path, "complain.py", COMPLAIN)
    user_only_project(project, home, settings(script, "diagnostics"))
    code, _, err, setup = run_cli(
        project,
        home,
        BYPASS,
        call("write_file", path="note.txt", content="hello"),
        final("done"),
        lines="Write\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    seen = [
        item.message for item in setup.model.calls[1].observations if item.kind == "user-message"
    ]
    assert seen and "Checks found problems after your edit" in seen[0]
    assert "mine on note.txt" in seen[0] and "bad: line 1 looks wrong" in seen[0]


def test_a_clean_file_or_a_failed_edit_sends_nothing(project, home, tmp_path):
    script = tool_file(tmp_path, "quiet.py", "print('fine')\n")
    user_only_project(project, home, settings(script, "diagnostics"))
    _, _, _, setup = run_cli(
        project,
        home,
        BYPASS,
        call("write_file", path="note.txt", content="hello"),
        final("done"),
        lines="Write\n/exit\n",
        interactive=True,
    )
    assert not [i for i in setup.model.calls[1].observations if i.kind == "user-message"]


def test_a_patch_names_every_file_it_changed_but_not_deleted_ones():
    from nailong_agent_sdk.starter import ToolEvent

    patch = (
        "*** Begin Patch\n*** Add File: a.txt\n+x\n*** Update File: b.py\n*** Move to: c.py\n"
        "-1\n+2\n*** Delete File: d.txt\n*** End Patch"
    )
    assert changed_files(ToolEvent("apply_patch", {"patch": patch}, 1)) == ["a.txt", "c.py"]
    assert changed_files(ToolEvent("write_file", {"path": "z.py"}, 1)) == ["z.py"]


@pytest.mark.parametrize(
    ("table", "fragment"),
    [
        ({"x": {"extensions": [".py"]}}, 'formatters.x needs command = \\["program"'),
        ({"x": {"command": ["f", "$FILE"]}}, "formatters.x needs extensions"),
        ({"x": {"command": "f", "extensions": [".py"]}}, "formatters.x needs command"),
    ],
)
def test_a_bad_checker_entry_is_named(table, fragment):
    with pytest.raises(ValueError, match=fragment):
        checkers_from_table(table, "formatters")


def test_settings_that_run_programs_belong_to_the_user_file(project, home):
    write_user_config(home, BASE)
    (project / ".nailong" / "config.toml").write_text(
        "format_on_edit = true\n[formatters.evil]\n"
        'command = ["x", "$FILE"]\nextensions = [".py"]\n',
        encoding="utf-8",
    )
    config = load_config(project, home=home)
    assert config.format_on_edit is False and config.formatters == {}
    assert "format_on_edit" in config.ignored_project_keys
    with pytest.raises(ConfigError, match="needs extensions"):
        user_only_project(project, home, '[diagnostics.bad]\ncommand = ["x"]\n')
