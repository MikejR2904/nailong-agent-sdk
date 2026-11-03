import re

import pytest

from nailong_cli.config import (
    ConfigError,
    load_config,
    project_config_path,
    user_config_path,
)

BASE = 'provider = "anthropic"\nmodel = "m"\napi_key_env = "K"\n'


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_project_file_overrides_the_user_file_and_flags_override_both(home, project):
    write(user_config_path(home), BASE + "max_steps = 5\n")
    write(project_config_path(project), 'model = "project-model"\nmax_steps = 7\n')
    config = load_config(project, home=home, overrides={"model": "flag-model", "provider": None})
    assert config.model == "flag-model" and config.max_steps == 7 and config.provider == "anthropic"
    assert config.workspace == project / ".nailong" / "workspace"


def test_missing_required_settings_are_named_with_where_to_set_them(home, tmp_path):
    with pytest.raises(ConfigError) as raised:
        load_config(tmp_path, home=home)
    message = str(raised.value)
    assert "provider, model" in message and str(project_config_path(tmp_path)) in message


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        (BASE + "colour = true\n", "unknown setting(s) ['colour']"),
        (BASE.replace("anthropic", "gemini-native"), "provider must be one of"),
        (BASE + 'sandbox = "chroot"\n', "sandbox must be one of"),
        (BASE.replace("anthropic", "openai-compatible"), "needs base_url"),
        (BASE + "max_steps = 0\n", "max_steps must be a whole number of at least 1"),
        (BASE + "timeout_seconds = -1\n", "timeout_seconds must be positive"),
        (BASE + "not toml [\n", "is not valid TOML"),
    ],
)
def test_a_bad_setting_is_refused_and_says_which(home, project, text, fragment):
    write(project_config_path(project), text)
    with pytest.raises(ConfigError, match=re.escape(fragment)):
        load_config(project, home=home)
