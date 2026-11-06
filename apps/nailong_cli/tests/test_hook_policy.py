from __future__ import annotations

import sys

import pytest

from nailong_cli.config import ConfigError, load_config
from nailong_cli.hooks import hook_environment
from tests.conftest import call, final, run_cli, write_user_config
from tests.test_sdk_features import BASE

BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]


def script_hook(tmp_path, event, body, extra=""):
    script = tmp_path / f"{event}_hook.py"
    script.write_text(body, encoding="utf-8")
    return f'[[hooks]]\nevent = "{event}"\ncommand = \'"{sys.executable}" "{script}"\'\n{extra}'


def test_a_hook_gets_a_small_environment_and_only_the_extra_names_you_list(
    project, home, tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret")
    monkeypatch.setenv("JIRA_URL", "https://jira.example")
    marker = tmp_path / "seen.txt"
    body = (
        "import os, pathlib\n"
        f"pathlib.Path(r'{marker}').write_text(\n"
        "    ','.join(name for name in ('OPENROUTER_API_KEY', 'JIRA_URL', 'PATH', 'NAILONG_TOOL')\n"
        "             if name in os.environ))\n"
    )
    write_user_config(
        home, BASE + 'hook_env = ["JIRA_URL"]\n' + script_hook(tmp_path, "post_tool", body)
    )
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    (project / "a.txt").write_text("x", encoding="utf-8")
    code, _, err, _ = run_cli(
        project,
        home,
        [],
        call("read_file", path="a.txt"),
        final("done"),
        lines="Read\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    seen = marker.read_text(encoding="utf-8").split(",")
    assert "OPENROUTER_API_KEY" not in seen
    assert {"JIRA_URL", "PATH", "NAILONG_TOOL"} <= set(seen)


def test_the_environment_helper_never_passes_a_name_nobody_listed(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "x")
    assert "SECRET_TOKEN" not in hook_environment()
    assert hook_environment(("SECRET_TOKEN",))["SECRET_TOKEN"] == "x"


def test_a_failing_pre_tool_hook_blocks_by_default_and_only_warns_when_asked(
    project, home, tmp_path
):
    failing = "import sys\nsys.exit(3)\n"
    for policy, written in (("", False), ('on_failure = "warn"\n', True)):
        target = project / f"w{int(written)}.txt"
        write_user_config(home, BASE + script_hook(tmp_path, "pre_tool", failing, policy))
        (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
        _, out, err, _ = run_cli(
            project,
            home,
            BYPASS,
            call("write_file", path=target.name, content="hi"),
            final("done"),
            lines="Write\n/exit\n",
            interactive=True,
        )
        assert target.exists() is written, out + err
        if written:
            assert "failed (continuing)" in out
        else:
            assert "Hook refused write_file" in out + err or not target.exists()


def test_a_hook_that_times_out_is_reported_as_a_timeout_not_an_empty_failure(
    project, home, tmp_path
):
    slow = "import time\ntime.sleep(30)\n"
    write_user_config(home, BASE + script_hook(tmp_path, "pre_tool", slow, "timeout_seconds = 1\n"))
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    (project / "a.txt").write_text("x", encoding="utf-8")
    _, out, err, setup = run_cli(
        project,
        home,
        [],
        call("read_file", path="a.txt"),
        final("done"),
        lines="Read\n/exit\n",
        interactive=True,
    )
    later = " ".join(str(item) for item in setup.model.calls[1].observations)
    assert "timed out" in later


def test_settings_are_checked_for_the_new_hook_keys(project, home):
    write_user_config(home, BASE + 'hook_env = ["ok", "not valid!"]\n')
    with pytest.raises(ConfigError, match="hook_env must be a list of environment variable names"):
        load_config(project, home=home)
    write_user_config(
        home, BASE + '[[hooks]]\nevent = "pre_tool"\ncommand = "x"\non_failure = "ignore"\n'
    )
    with pytest.raises(ConfigError, match='on_failure must be "block"'):
        load_config(project, home=home)


def test_a_project_cannot_widen_the_hook_environment(project, home):
    write_user_config(home, BASE)
    (project / ".nailong" / "config.toml").write_text('hook_env = ["AWS_SECRET_ACCESS_KEY"]\n')
    assert load_config(project, home=home).hook_env == ()
