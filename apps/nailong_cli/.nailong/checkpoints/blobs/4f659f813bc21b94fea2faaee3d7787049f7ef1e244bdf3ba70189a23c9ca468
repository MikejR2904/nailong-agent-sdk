from __future__ import annotations

import pytest

from nailong_cli.build import build_model
from nailong_cli.config import ConfigError, load_config
from nailong_cli.network import is_loopback, is_private_host
from tests.conftest import run_cli, write_user_config

LOCAL = 'provider = "openai-compatible"\nmodel = "deepseek-local"\nbase_url = "vllm"\n'


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "10.1.2.3", "192.168.0.5", "172.16.4.4", "gpubox", "a.internal"],
)
def test_loopback_private_and_single_label_hosts_count_as_your_network(host):
    assert is_private_host(host)


@pytest.mark.parametrize("host", ["api.openai.com", "8.8.8.8", "openrouter.ai"])
def test_public_hosts_do_not(host):
    assert not is_private_host(host)


def test_only_loopback_is_trusted_for_plain_http_without_a_setting():
    assert is_loopback("127.0.0.1") and not is_loopback("192.168.0.5")


def test_a_local_server_needs_no_key_variable(project, home):
    write_user_config(home, LOCAL)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    config = load_config(project, home=home)
    assert config.api_key_env == "" and config.base_url == "vllm"
    setup = build_model(config, {})
    assert setup.name == "deepseek-local"


def test_a_named_key_variable_that_is_missing_is_still_an_error(project, home):
    write_user_config(home, LOCAL + 'api_key_env = "GPU_KEY"\n')
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    config = load_config(project, home=home)
    with pytest.raises(Exception, match='"GPU_KEY" named by api_key_env'):
        build_model(config, {})


def test_anthropic_still_needs_its_key_variable(project, home):
    write_user_config(home, 'provider = "anthropic"\nmodel = "m"\n')
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="provider anthropic needs api_key_env"):
        load_config(project, home=home)


def test_offline_accepts_a_server_on_your_network(project, home):
    write_user_config(home, LOCAL + "offline = true\n")
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    assert load_config(project, home=home).offline


@pytest.mark.parametrize(
    "extra, fragment",
    [
        ('base_url = "openrouter"\n', 'base_url host "openrouter.ai" is not loopback'),
        ("web = true\n", "web is on"),
        ("web_search = true\n", "web_search is on"),
        ('[mcp.remote]\nurl = "https://tools.example.com/mcp"\n', 'mcp server "remote"'),
    ],
)
def test_offline_names_each_setting_that_would_leave_your_network(project, home, extra, fragment):
    text = LOCAL.replace('base_url = "vllm"\n', "") + "offline = true\n" + extra
    if "base_url" not in extra:
        text = LOCAL + "offline = true\n" + extra
    write_user_config(home, text)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match=fragment):
        load_config(project, home=home)


def test_the_offline_flag_refuses_web_and_a_project_file_cannot_switch_it_off(project, home):
    write_user_config(home, LOCAL + "offline = true\n")
    (project / ".nailong" / "config.toml").write_text(
        "offline = false\nweb = true\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="web is on"):
        load_config(project, home=home)


def test_an_offline_run_with_a_public_endpoint_exits_64_naming_the_host(project, home):
    write_user_config(home, 'provider = "openai-compatible"\nmodel = "m"\nbase_url = "openai"\n')
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    code, _, err, _ = run_cli(project, home, ["--offline", "-p", "hi"])
    assert code == 64 and 'host "api.openai.com"' in err


def test_status_shows_whether_offline_is_on(project, home):
    write_user_config(home, LOCAL + "offline = true\n")
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    _, out, _, _ = run_cli(
        project, home, [], lines="/status\n/exit\n", interactive=True, environ={}
    )
    assert "offline      on" in out
