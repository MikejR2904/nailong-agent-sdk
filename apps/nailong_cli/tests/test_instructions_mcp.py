from __future__ import annotations

import json
import sys

import pytest

from nailong_cli import mcp_config
from nailong_cli.config import ConfigError
from nailong_cli.memory import load_memory, memory_text, nested_note
from tests.conftest import call, final, instructions_text, run_cli
from tests.test_extras import STDIO_SERVER
from tests.test_sdk_features import user_only_project


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def labels(files):
    return [(item.label, item.path.name) for item in files]


def test_instruction_files_load_from_the_repository_down_to_the_project(tmp_path, home):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    project = repo / "services" / "api"
    project.mkdir(parents=True)
    write(repo / "AGENTS.md", "Repo rules")
    write(repo / "services" / "CLAUDE.md", "Services rules")
    write(project / "AGENTS.md", "API rules")
    write(project / "NAILONG.local.md", "My private notes")
    write(project / ".nailong" / "rules" / "style.md", "Use snake_case")
    files = load_memory(project, home)
    assert labels(files) == [
        ("parent", "AGENTS.md"),
        ("parent", "CLAUDE.md"),
        ("project", "AGENTS.md"),
        ("local", "NAILONG.local.md"),
        ("rule", "style.md"),
    ]
    assert "Repo rules" in memory_text(files) and "Use snake_case" in memory_text(files)


def test_a_project_that_is_its_own_repository_reads_only_its_own_folder(tmp_path, home):
    project = tmp_path / "plain"
    (project / ".git").mkdir(parents=True)
    write(tmp_path / "AGENTS.md", "Not mine")
    write(project / "AGENTS.md", "Mine")
    assert [item.text for item in load_memory(project, home)] == ["Mine"]


def test_at_imports_pull_in_files_but_not_from_outside_loops_or_missing(tmp_path, home):
    project = tmp_path / "p"
    write(project / "docs" / "style.md", "snake_case everywhere")
    write(project / "docs" / "again.md", "see @docs/again.md")
    write(tmp_path / "secret.txt", "outside")
    write(
        project / "AGENTS.md",
        "Style: @docs/style.md\nLoop: @docs/again.md\nGone: @docs/none.md\nOut: @../secret.txt\n",
    )
    text = load_memory(project, home)[0].text
    assert "snake_case everywhere" in text
    assert "[not imported: docs/again.md would include itself]" in text or "again.md" in text
    assert "[not imported: docs/none.md was not found]" in text
    assert "outside the project" in text and "outside" not in text.replace("outside the", "")


def test_an_email_address_or_code_span_is_not_an_import(tmp_path, home):
    project = tmp_path / "p"
    write(project / "AGENTS.md", "Mail me at dev@example.com or use `@decorator.name`.")
    text = load_memory(project, home)[0].text
    assert text == "Mail me at dev@example.com or use `@decorator.name`."


def test_folders_with_their_own_instruction_files_are_listed_and_noise_is_skipped(tmp_path):
    project = tmp_path / "p"
    write(project / "web" / "AGENTS.md", "web")
    write(project / "node_modules" / "pkg" / "AGENTS.md", "noise")
    write(project / "a" / "b" / "c" / "d" / "AGENTS.md", "too deep")
    note = nested_note(project)
    assert "- web/AGENTS.md" in note and "node_modules" not in note and "d/AGENTS.md" not in note
    assert nested_note(tmp_path / "empty") == ""


def test_the_model_is_told_about_the_instruction_folders_and_local_notes(project, home):
    write(project / "web" / "AGENTS.md", "web rules")
    write(project / "NAILONG.local.md", "keep it short")
    _, _, _, setup = run_cli(project, home, [], final(), lines="Hi\n/exit\n", interactive=True)
    text = instructions_text(setup.model.calls[0])
    assert "keep it short" in text and "- web/AGENTS.md" in text


def test_mcp_servers_are_added_listed_and_removed_through_a_user_file(home):
    mcp_config.add_server(home, "files", {"command": "npx", "args": ["-y", "srv"]})
    assert mcp_config.load_managed(home) == {"files": {"command": "npx", "args": ["-y", "srv"]}}
    with pytest.raises(ValueError, match="already added"):
        mcp_config.add_server(home, "files", {"url": "https://x"})
    with pytest.raises(ValueError, match="must be 1 to 32 letters"):
        mcp_config.add_server(home, "bad name", {"url": "https://x"})
    mcp_config.remove_server(home, "files")
    with pytest.raises(ValueError, match='"files" was not added'):
        mcp_config.remove_server(home, "files")


def test_words_become_a_command_or_an_address():
    assert mcp_config.spec_from_words(["npx", "-y", "srv"]) == {
        "command": "npx",
        "args": ["-y", "srv"],
    }
    assert mcp_config.spec_from_words(["https://host/mcp"]) == {"url": "https://host/mcp"}
    with pytest.raises(ValueError, match="takes no further words"):
        mcp_config.spec_from_words(["https://host/mcp", "extra"])
    with pytest.raises(ValueError, match="Give a command"):
        mcp_config.spec_from_words([])


def test_a_server_added_in_one_session_is_connected_in_the_next(project, home, tmp_path):
    script = tmp_path / "server.py"
    script.write_text(STDIO_SERVER, encoding="utf-8")
    _, out, err, _ = run_cli(
        project,
        home,
        [],
        lines=f"/mcp add demo {sys.executable} {script}\n/mcp preset browser\n/exit\n",
        interactive=True,
    )
    assert "added demo" in out and "added browser" in out, err
    assert set(mcp_config.load_managed(home)) == {"demo", "browser"}
    mcp_config.remove_server(home, "browser")
    _, listing, _, _ = run_cli(project, home, [], lines="/mcp\n/exit\n", interactive=True)
    assert "demo:" in listing and "mcp__demo__echo" in listing


def test_the_mcp_command_explains_itself_when_misused(project, home):
    _, out, err, _ = run_cli(
        project, home, [], lines="/mcp add onlyname\n/mcp remove ghost\n/exit\n", interactive=True
    )
    assert "Usage: /mcp" in out and '"ghost" was not added' in err


SERVERS = {"srv": {"command": "x", "allow_tools": ["read"], "deny_tools": ["wipe"]}}


def test_per_tool_rules_refuse_what_is_denied_or_not_allowed_and_name_the_setting():
    assert mcp_config.refusal_for(SERVERS, "mcp__srv__read") is None
    assert "deny_tools" in mcp_config.refusal_for(SERVERS, "mcp__srv__wipe")
    assert "allows only read" in mcp_config.refusal_for(SERVERS, "mcp__srv__other")
    assert mcp_config.refusal_for(SERVERS, "mcp__else__anything") is None
    assert mcp_config.refusal_for(SERVERS, "read_file") is None


def test_a_deny_rule_stops_a_real_mcp_call_in_a_session(project, home, tmp_path):
    script = tmp_path / "server.py"
    script.write_text(STDIO_SERVER, encoding="utf-8")
    user_only_project(
        project,
        home,
        f"[mcp.demo]\ncommand = {json.dumps(sys.executable)}\nargs = [{json.dumps(str(script))}]\n"
        'deny_tools = ["echo"]\n',
    )
    code, out, _, _ = run_cli(
        project,
        home,
        ["--allow", "mcp__demo__echo:*"],
        call("mcp__demo__echo", text="hi"),
        final("done"),
        lines="Echo\n/exit\n",
        interactive=True,
    )
    assert code == 0 and "deny_tools" in out and "echo:hi" not in out


def test_bad_tool_lists_in_the_mcp_settings_are_named(project, home):
    with pytest.raises(ConfigError, match="allow_tools must be a list of tool names"):
        user_only_project(project, home, '[mcp.demo]\ncommand = "x"\nallow_tools = "echo"\n')
