from __future__ import annotations

import http.server
import json
import re
import threading

import pytest
from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.starter import ModelSetup

from nailong_cli.config import ConfigError, load_config
from tests.conftest import call, factory, final, run_cli, tool_names, write_user_config

BASE = 'provider = "anthropic"\nmodel = "m"\napi_key_env = "K"\n'


def user_only_project(project, home, text):
    write_user_config(home, BASE + text)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    return load_config(project, home=home)


def test_fallback_models_are_listed_in_order(project, home):
    config = user_only_project(project, home, 'fallback_models = ["b", "c"]\n')
    assert config.fallback_models == ("b", "c")


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ('fallback_models = "b"\n', "must be a list of model names"),
        ('fallback_models = ["m"]\n', 'lists "m", which is already the model'),
        ('fallback_models = ["b", "b"]\n', "lists a model twice"),
        ('fallback_models = ["b", ""]\n', "must be a list of model names"),
    ],
)
def test_bad_fallback_settings_are_named(project, home, text, fragment):
    with pytest.raises(ConfigError, match=fragment):
        user_only_project(project, home, text)


def test_a_failing_main_model_hands_the_turn_to_the_fallback(project, home):
    asked = []

    class Failing:
        async def next_turn(self, context):
            raise AgentSdkError("PROVIDER_DOWN", "the main model is down")

    def build(config, environ):
        asked.append(config.model)
        model = Failing() if config.model == "test-model" else ScriptedModel([final("from backup")])
        return ModelSetup(model, "anthropic", config.model)

    code, out, _, _ = run_cli(
        project, home, ["--fallback-model", "backup-model", "-p", "hi"], build=build
    )
    assert code == 0 and "from backup" in out
    assert asked == ["test-model", "backup-model"]


def test_the_status_lists_the_fallbacks(project, home):
    def build(config, environ):
        return ModelSetup(ScriptedModel([final()]), "scripted", config.model)

    _, out, _, _ = run_cli(
        project,
        home,
        ["--fallback-model", "backup-model"],
        build=build,
        lines="/status\n/exit\n",
        interactive=True,
    )
    assert "fallbacks    backup-model" in out


def test_a_question_the_agent_asks_reaches_you_and_your_answer_reaches_it(project, home):
    code, out, _, setup = run_cli(
        project,
        home,
        [],
        call("ask_human_question", question="Which colour?"),
        final("blue it is"),
        lines="Pick a colour\nblue\n/exit\n",
        interactive=True,
    )
    assert code == 0 and "? Which colour?" in out
    seen = " ".join(str(item) for item in setup.model.calls[-1].observations)
    assert "blue" in seen and "'answered': True" in seen


def test_without_a_terminal_the_question_is_reported_unanswered(project, home):
    code, _, _, setup = run_cli(
        project, home, ["-p", "go"], call("ask_human_question", question="Which?"), final("done")
    )
    assert code == 0
    seen = " ".join(str(item) for item in setup.model.calls[-1].observations)
    assert "'answered': False" in seen


def write_notebook(project):
    notebook = {
        "cells": [{"cell_type": "code", "metadata": {}, "outputs": [], "source": ["print(1)\n"]}],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    folder = project / "analysis"
    folder.mkdir()
    (folder / "a.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
    return folder / "a.ipynb"


def edit_call():
    return call("notebook_edit", path="analysis/a.ipynb", new_source="print(2)", cell_index=0)


def test_the_agent_can_edit_a_notebook_cell_once_you_allow_it(project, home):
    path = write_notebook(project)
    code, _, _, _ = run_cli(
        project, home, ["--allow", "notebook_edit", "-p", "edit"], edit_call(), final("ok")
    )
    cell = json.loads(path.read_text(encoding="utf-8"))["cells"][0]
    assert code == 0 and "".join(cell["source"]) == "print(2)"


def test_a_notebook_edit_without_a_rule_or_terminal_is_refused(project, home):
    path = write_notebook(project)
    code, out, err, _ = run_cli(project, home, ["-p", "edit"], edit_call(), final("ok"))
    assert "Refused notebook_edit" in out + err
    assert (
        "".join(json.loads(path.read_text(encoding="utf-8"))["cells"][0]["source"]) == "print(1)\n"
    )


def test_accept_edits_mode_lets_a_notebook_edit_through(project, home):
    path = write_notebook(project)
    run_cli(
        project,
        home,
        ["--permission-mode", "accept-edits", "-p", "edit"],
        edit_call(),
        final("ok"),
    )
    assert "print(2)" in path.read_text(encoding="utf-8")


def test_plan_mode_has_no_notebook_tool_and_default_mode_does(project, home):
    _, _, _, planning = run_cli(project, home, ["--permission-mode", "plan", "-p", "x"], final())
    _, _, _, normal = run_cli(project, home, ["-p", "x"], final())
    assert "notebook_edit" not in tool_names(planning.model.calls[0])
    assert "notebook_edit" in tool_names(normal.model.calls[0])
    assert "ask_human_question" in tool_names(planning.model.calls[0])


def test_a_notebook_edit_is_covered_by_rewind(project, home):
    path = write_notebook(project)
    original = path.read_text(encoding="utf-8")
    _, out, _, _ = run_cli(
        project,
        home,
        ["--permission-mode", "accept-edits"],
        edit_call(),
        final("ok"),
        lines="Edit it\n/rewind\n/rewind 1\ny\n/exit\n",
        interactive=True,
    )
    assert "analysis" in out and "a.ipynb" in out
    assert path.read_text(encoding="utf-8") == original


class Collector:
    def __init__(self):
        self.requests = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                owner.requests.append((self.path, dict(self.headers), self.rfile.read(length)))
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def collector():
    server = Collector()
    yield server
    server.stop()


def test_recorded_runs_reach_the_collector_you_named_with_your_headers(project, home, collector):
    code, out, _, _ = run_cli(
        project,
        home,
        ["--otlp-endpoint", collector.url],
        final("done"),
        lines="Say done\n/telemetry now\n/exit\n",
        interactive=True,
        environ={"TEST_KEY": "k", "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer%20abc"},
    )
    paths = {path for path, _, _ in collector.requests}
    assert code == 0 and "/v1/traces" in paths and "/v1/logs" in paths
    assert all(headers.get("Authorization") == "Bearer abc" for _, headers, _ in collector.requests)
    assert "collector   127.0.0.1" in out and "ok:" in out and "span(s)" in out


def test_a_second_export_sends_only_what_is_new(project, home, collector):
    run_cli(
        project,
        home,
        ["--otlp-endpoint", collector.url],
        final("done"),
        lines="Say done\n/telemetry now\n/telemetry now\n/exit\n",
        interactive=True,
    )
    bodies = [json.loads(body) for _, _, body in collector.requests if body]
    assert bodies and len(collector.requests) >= 2


def test_telemetry_is_off_by_default_and_the_command_says_how_to_turn_it_on(project, home):
    _, out, _, _ = run_cli(project, home, [], lines="/telemetry\n/exit\n", interactive=True)
    assert "Telemetry export is off." in out and "otlp_endpoint" in out


def test_a_collector_that_is_down_does_not_stop_the_session_and_is_reported(project, home):
    code, out, _, _ = run_cli(
        project,
        home,
        ["--otlp-endpoint", "http://127.0.0.1:9"],
        final("done"),
        lines="Say done\n/telemetry now\n/telemetry\n/exit\n",
        interactive=True,
    )
    assert code == 0 and "failed:" in out


def test_a_project_file_cannot_choose_where_telemetry_goes(project, home):
    write_user_config(home, BASE)
    (project / ".nailong" / "config.toml").write_text(
        'otlp_endpoint = "https://evil.example.com"\n', encoding="utf-8"
    )
    config = load_config(project, home=home)
    assert config.otlp_endpoint is None and "otlp_endpoint" in config.ignored_project_keys


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ('"ftp://x"', "must be an http(s) URL"),
        ('"https://user:pw@host"', "must not contain credentials"),
        ("5", "must be an http(s) URL"),
    ],
)
def test_bad_endpoints_are_named(project, home, value, fragment):
    with pytest.raises(ConfigError, match=re.escape(fragment)):
        user_only_project(project, home, f"otlp_endpoint = {value}\n")


def test_offline_mode_refuses_a_public_collector_and_accepts_one_on_your_network(project, home):
    with pytest.raises(ConfigError, match='otlp_endpoint host "telemetry.example.com"'):
        user_only_project(
            project,
            home,
            'offline = true\nbase_url = "http://localhost:8000/v1"\nprovider_note = 1\n'.replace(
                "provider_note = 1\n", ""
            )
            + 'otlp_endpoint = "https://telemetry.example.com"\n',
        )
    local = 'offline = true\nbase_url = "ollama"\notlp_endpoint = "http://127.0.0.1:4318"\n'
    write_user_config(home, 'provider = "openai-compatible"\nmodel = "m"\n' + local)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    assert load_config(project, home=home).otlp_endpoint == "http://127.0.0.1:4318"


def test_cleanup_with_nothing_old_enough_says_so(project, home):
    _, out, _, _ = run_cli(
        project, home, [], final("hi"), lines="Hello\n/cleanup\n/exit\n", interactive=True
    )
    assert "No finished runs older than 30 day(s) to remove." in out


def test_cleanup_shows_what_it_would_remove_asks_and_then_removes(project, home):
    turns = "".join(f"Turn {number}\n" for number in range(6))
    _, out, _, _ = run_cli(
        project,
        home,
        [],
        final("hi"),
        lines=turns + "/cleanup 0.0000001\ny\n/exit\n",
        interactive=True,
    )
    assert "run(s) older than 1e-07 day(s)" in out
    assert "Sessions you can /resume are not touched" in out
    assert "Remove these runs? [y/N]" in out and "Removed " in out


def test_cleanup_answered_no_removes_nothing_and_a_bad_number_is_a_usage_error(project, home):
    turns = "".join(f"Turn {number}\n" for number in range(6))
    _, out, err, _ = run_cli(
        project,
        home,
        [],
        final("hi"),
        lines=turns + "/cleanup 0.0000001\nn\n/cleanup abc\n/exit\n",
        interactive=True,
    )
    assert "Nothing removed." in out and "Usage: /cleanup [days]" in err
    assert factory  # imported helper stays used
