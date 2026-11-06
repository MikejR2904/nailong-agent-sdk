from __future__ import annotations

import pytest

from nailong_cli.config import ConfigError, load_config
from nailong_cli.memory import load_memory
from tests.conftest import write_user_config
from tests.test_sdk_features import BASE


def test_env_and_file_references_in_the_user_file_are_filled_in(project, home):
    secret = home / ".config" / "nailong" / "team.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("platform\n", encoding="utf-8")
    write_user_config(
        home,
        BASE + 'instructions = "Team {file:team.txt}, region {env:REGION}, none [{env:NOPE}]"\n',
    )
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    config = load_config(project, home=home, environ={"REGION": "eu"})
    assert config.instructions == "Team platform, region eu, none []"


def test_a_file_reference_may_be_absolute_and_a_missing_one_is_named(project, home, tmp_path):
    target = tmp_path / "token.txt"
    target.write_text("abc", encoding="utf-8")
    write_user_config(home, BASE + f'instructions = "{{file:{target.as_posix()}}}"\n')
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    assert load_config(project, home=home, environ={}).instructions == "abc"
    write_user_config(home, BASE + 'instructions = "{file:missing.txt}"\n')
    with pytest.raises(ConfigError, match=r"\{file:missing.txt\} cannot be read"):
        load_config(project, home=home, environ={})


def test_a_project_file_is_never_filled_in_so_it_cannot_read_your_secrets(project, home):
    write_user_config(home, BASE)
    (project / ".nailong" / "config.toml").write_text(
        'instructions = "{env:SECRET_KEY} {file:~/.ssh/id_rsa}"\n', encoding="utf-8"
    )
    config = load_config(project, home=home, environ={"SECRET_KEY": "hunter2"})
    assert config.instructions == "{env:SECRET_KEY} {file:~/.ssh/id_rsa}"


def test_listed_instruction_files_and_globs_are_added_after_the_standard_ones(project, home):
    (project / "docs").mkdir()
    (project / "docs" / "a.md").write_text("Rule A", encoding="utf-8")
    (project / "docs" / "b.md").write_text("Rule B", encoding="utf-8")
    (project / "CONTRIBUTING.md").write_text("How to contribute", encoding="utf-8")
    (project / "AGENTS.md").write_text("Standard", encoding="utf-8")
    files = load_memory(project, home, ("CONTRIBUTING.md", "docs/*.md"))
    assert [(item.label, item.text) for item in files] == [
        ("project", "Standard"),
        ("listed", "How to contribute"),
        ("listed", "Rule A"),
        ("listed", "Rule B"),
    ]


def test_a_listed_pattern_cannot_reach_outside_the_project(project, home, tmp_path):
    (tmp_path / "outside.md").write_text("secret", encoding="utf-8")
    assert load_memory(project, home, ("../outside.md",)) == []


def test_the_instruction_files_setting_must_be_a_list_of_names(project, home):
    write_user_config(home, BASE + 'instruction_files = "docs/*.md"\n')
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="instruction_files must be a list"):
        load_config(project, home=home, environ={})
