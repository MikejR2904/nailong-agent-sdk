from __future__ import annotations

from nailong_cli import project_trust
from nailong_cli.config import load_config, project_config_path
from tests.conftest import CONFIG, final, run_cli

SHARED = (
    CONFIG
    + '[[hooks]]\nevent = "pre_tool"\ncommand = "echo checking"\n'
    + '[mcp.docs]\ncommand = "docs-server"\nargs = ["--stdio"]\n'
)


def setup_project(project):
    project_config_path(project).write_text(SHARED, encoding="utf-8")


def test_project_hooks_and_mcp_are_ignored_until_the_user_approves_them(project, home):
    setup_project(project)
    config = load_config(project, home=home)
    assert config.hooks == () and config.mcp_servers == {}
    assert "hooks" in config.ignored_project_keys and "mcp" in config.ignored_project_keys


def test_approved_project_settings_load_and_add_to_the_users_own(project, home):
    setup_project(project)
    path = project_config_path(project)
    from nailong_cli.config import _read

    project_trust.trust(home, path, project_trust.shared_part(_read(path)))
    config = load_config(project, home=home)
    assert [hook["command"] for hook in config.hooks] == ["echo checking"]
    assert list(config.mcp_servers) == ["docs"]
    assert "hooks" not in config.ignored_project_keys


def test_changing_the_file_after_approval_revokes_it(project, home):
    setup_project(project)
    path = project_config_path(project)
    from nailong_cli.config import _read

    project_trust.trust(home, path, project_trust.shared_part(_read(path)))
    path.write_text(SHARED.replace("echo checking", "echo something else"), encoding="utf-8")
    config = load_config(project, home=home)
    assert config.hooks == () and "hooks" in config.ignored_project_keys


def test_the_trust_flag_shows_what_will_run_and_records_a_yes(project, home):
    setup_project(project)
    code, out, _, _ = run_cli(
        project, home, ["--trust-project"], final(), lines="y\n", interactive=True
    )
    assert code == 0
    assert "echo checking" in out and "docs-server --stdio" in out and "Trusted" in out
    assert load_config(project, home=home).hooks != ()


def test_the_trust_flag_refuses_without_a_terminal(project, home):
    setup_project(project)
    code, _, err, _ = run_cli(project, home, ["--trust-project"], final(), lines="y\n")
    assert code == 64 and "needs a terminal" in err
    assert load_config(project, home=home).hooks == ()
