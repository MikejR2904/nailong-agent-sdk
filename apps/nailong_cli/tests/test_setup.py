from __future__ import annotations

import tomllib

import pytest
from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import ModelSetup

from nailong_cli import credentials
from nailong_cli.build import MissingKeyError, build_model
from nailong_cli.cli import main, not_a_terminal_notice
from nailong_cli.config import load_config, user_config_path
from nailong_cli.setup import SetupCancelled, run_setup
from tests.conftest import final, make_console


def saved(home):
    return tomllib.loads(user_config_path(home).read_text(encoding="utf-8"))


def test_setup_for_a_hosted_service_keeps_the_key_in_the_credential_store(tmp_path, fake_keyring):
    console, out, _ = make_console("1\nvendor/model-a\nsk-test-123\n", interactive=True)
    result = run_setup(console, tmp_path, {})
    assert saved(tmp_path) == {
        "provider": "openai-compatible",
        "base_url": "openrouter",
        "model": "vendor/model-a",
        "api_key_env": "OPENROUTER_API_KEY",
    }
    assert fake_keyring.saved == {("nailong-cli", "OPENROUTER_API_KEY"): "sk-test-123"}
    assert result.session_keys == {"OPENROUTER_API_KEY": "sk-test-123"}
    assert "sk-test-123" not in user_config_path(tmp_path).read_text(encoding="utf-8")
    assert "Welcome to nailong" in out.getvalue() and "Key saved" in out.getvalue()


def test_a_key_that_cannot_be_saved_is_used_for_this_session_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "_keyring", lambda: None)
    console, out, _ = make_console("1\nm\nsk-test-123\n", interactive=True)
    result = run_setup(console, tmp_path, {})
    assert result.session_keys == {"OPENROUTER_API_KEY": "sk-test-123"}
    assert "keyring package is not installed" in out.getvalue()
    assert "this session only" in out.getvalue()


def test_skipping_the_key_tells_you_what_to_set_instead(tmp_path, fake_keyring):
    console, out, _ = make_console("1\nm\n\n", interactive=True)
    result = run_setup(console, tmp_path, {})
    assert result.session_keys == {} and fake_keyring.saved == {}
    assert "Set OPENROUTER_API_KEY in your shell" in out.getvalue()
    assert saved(tmp_path)["api_key_env"] == "OPENROUTER_API_KEY"


def test_an_existing_key_is_reused_without_asking(tmp_path, fake_keyring):
    console, out, _ = make_console("1\nm\n", interactive=True)
    run_setup(console, tmp_path, {"OPENROUTER_API_KEY": "already"})
    assert "Using the key already available as OPENROUTER_API_KEY" in out.getvalue()


def test_setup_for_a_local_server_asks_whether_it_needs_a_key(tmp_path):
    console, _, _ = make_console("13\nserved-model\n1\n", interactive=True)
    run_setup(console, tmp_path, {})
    assert saved(tmp_path) == {
        "provider": "openai-compatible",
        "base_url": "vllm",
        "model": "served-model",
        "timeout_seconds": 300,
    }


def test_setup_for_a_url_on_your_network_allows_plain_http(tmp_path, fake_keyring):
    answers = "16\nnot-a-url\nhttp://gpu-box:8000/v1\nm\n2\nsecret-key\n"
    console, _, _ = make_console(answers, interactive=True)
    run_setup(console, tmp_path, {})
    settings = saved(tmp_path)
    assert settings["base_url"] == "http://gpu-box:8000/v1"
    assert settings["allow_insecure_http"] is True and settings["api_key_env"] == "NAILONG_API_KEY"
    assert fake_keyring.saved[("nailong-cli", "NAILONG_API_KEY")] == "secret-key"


def test_a_key_with_spaces_is_asked_for_again(tmp_path, fake_keyring):
    console, out, _ = make_console("2\nmodel-x\nnot a key\nrealkey\n", interactive=True)
    run_setup(console, tmp_path, {})
    assert "A key has no spaces" in out.getvalue()
    assert fake_keyring.saved[("nailong-cli", "ANTHROPIC_API_KEY")] == "realkey"


def test_setup_asks_before_replacing_an_existing_file(tmp_path):
    path = user_config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('model = "keep"\n', encoding="utf-8")
    console, _, _ = make_console("1\n", interactive=True)
    with pytest.raises(ValueError, match="left as it is"):
        run_setup(console, tmp_path, {})
    assert path.read_text(encoding="utf-8") == 'model = "keep"\n'


def test_a_cancelled_dialog_stops_setup_without_writing(tmp_path):
    class Cancelling:
        def select(self, title, options):
            raise SetupCancelled("Setup was cancelled.")

        def text(self, prompt, *, default="", secret=False):
            raise SetupCancelled("Setup was cancelled.")

    console, _, _ = make_console("", interactive=True)
    with pytest.raises(SetupCancelled):
        run_setup(console, tmp_path, {}, dialog=Cancelling())
    assert not user_config_path(tmp_path).exists()


def test_a_saved_key_satisfies_the_model_without_an_environment_variable(
    project, home, fake_keyring
):
    (project / ".nailong" / "config.toml").write_text(
        'provider = "openai-compatible"\nmodel = "m"\nbase_url = "openrouter"\n'
        'api_key_env = "OPENROUTER_API_KEY"\n',
        encoding="utf-8",
    )
    config = load_config(project, home=home)
    with pytest.raises(MissingKeyError, match="no key is saved for it"):
        build_model(config, {})
    fake_keyring.set_password("nailong-cli", "OPENROUTER_API_KEY", "from-store")
    assert build_model(config, {}).name == "m"


def test_forget_key_deletes_only_the_named_saved_key(tmp_path, fake_keyring):
    fake_keyring.set_password("nailong-cli", "OPENROUTER_API_KEY", "k")
    for variable, expected in (
        ("OPENROUTER_API_KEY", "Deleted"),
        ("OPENROUTER_API_KEY", "No saved key"),
    ):
        console, out, _ = make_console("")
        code = main(["--forget-key", variable], console=console, home=tmp_path, cwd=tmp_path)
        assert code == 0 and expected in out.getvalue()
    assert fake_keyring.saved == {}


def first_run(tmp_path, lines, captured=None):
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    setup = ModelSetup(ScriptedModel([final("hi there")]), "scripted", "s")

    def model_factory(config, environ):
        if captured is not None:
            captured.update(environ)
        return setup

    console, out, err = make_console(lines, interactive=True)
    code = main(
        [], environ={}, console=console, model_factory=model_factory, home=home, cwd=project
    )
    return code, out.getvalue(), err.getvalue(), project, home


def test_first_run_without_settings_offers_setup_then_starts(tmp_path):
    code, out, _, project, home = first_run(tmp_path, "y\n12\nllama3\n1\nHello\n/exit\n")
    assert code == 0 and "No settings found" in out and "hi there" in out
    assert load_config(project, home=home).base_url == "ollama"


def test_the_key_typed_in_setup_is_used_straight_away(tmp_path):
    seen = {}
    lines = "y\n1\nvendor/m\nthe-typed-key\nHello\n/exit\n"
    code, _, _, _, _ = first_run(tmp_path, lines, seen)
    assert code == 0 and seen["OPENROUTER_API_KEY"] == "the-typed-key"


def test_declining_setup_keeps_the_specific_error(tmp_path):
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    console, _, err = make_console("n\n", interactive=True)
    code = main([], environ={}, console=console, home=home, cwd=project)
    assert code == 64 and "Missing setting(s): provider, model" in err.getvalue()


def test_without_a_terminal_the_error_points_to_setup(tmp_path):
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    console, _, err = make_console("")
    code = main([], environ={}, console=console, home=home, cwd=project)
    assert code == 64 and "nailong --setup" in err.getvalue()


def test_the_setup_flag_needs_a_terminal_and_saves_without_starting(tmp_path):
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    refused, _, err = make_console("")
    assert main(["--setup"], console=refused, home=home, cwd=project) == 64
    assert "needs a terminal" in err.getvalue()
    console, out, _ = make_console("6\nm\n1\n", interactive=True)
    assert main(["--setup"], environ={}, console=console, home=home, cwd=project) == 0
    assert "Run nailong to start" in out.getvalue() and saved(home)["model"] == "m"


def test_the_plain_mode_notice_names_git_bash_and_the_fix():
    assert "not a terminal" in not_a_terminal_notice({})
    assert "winpty nailong" not in not_a_terminal_notice({})
    assert "winpty nailong" in not_a_terminal_notice({"MSYSTEM": "MINGW64"})
