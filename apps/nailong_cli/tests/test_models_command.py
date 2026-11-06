from __future__ import annotations

import http.server
import json
import threading

import pytest

from nailong_cli.model_commands import fetch_models, models_request
from tests.conftest import run_cli


class Service(http.server.BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *args):
        return None

    def do_GET(self):
        Service.seen.append({"path": self.path, "auth": self.headers.get("Authorization")})
        if self.path.startswith("/v1/models"):
            body = json.dumps(
                {"data": [{"id": "zeta-1"}, {"id": "alpha-big"}, {"id": "alpha-small"}]}
            )
            self.send_response(200)
        else:
            body = "{}"
            self.send_response(404)
        data = body.encode()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def service():
    Service.seen = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Service)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()
    httpd.server_close()


def test_the_request_is_built_for_each_provider_kind():
    url, headers = models_request("anthropic", None, "k")
    assert url.startswith("https://api.anthropic.com/v1/models") and headers["x-api-key"] == "k"
    url, headers = models_request("openai-compatible", "http://h/v1/", "k")
    assert url == "http://h/v1/models" and headers == {"Authorization": "Bearer k"}
    with pytest.raises(ValueError, match="no base_url"):
        models_request("openai-compatible", None, None)


def test_models_are_listed_sorted_and_failures_name_the_status(service):
    assert fetch_models(service + "/models", {}) == ["alpha-big", "alpha-small", "zeta-1"]
    with pytest.raises(ValueError, match="HTTP 404"):
        fetch_models(service + "/nothing", {})


def test_the_command_lists_matching_models_with_the_key_sent(project, home, service):
    (project / ".nailong" / "config.toml").write_text(
        f'provider = "openai-compatible"\nmodel = "alpha-big"\napi_key_env = "TEST_KEY"\n'
        f'base_url = "{service}"\n',
        encoding="utf-8",
    )
    code, out, err, _ = run_cli(project, home, [], lines="/models alpha\n/exit\n", interactive=True)
    assert code == 0, err
    assert "alpha-small" in out and "zeta-1" not in out and "* " in out
    assert Service.seen[0]["auth"] == "Bearer k"


def test_web_can_be_switched_on_and_off_inside_a_session(project, home):
    from tests.conftest import final, tool_names

    _, out, err, setup = run_cli(
        project,
        home,
        [],
        final(),
        final(),
        lines="/web\n/web on\n/web on\nHi\n/web off\n/web search on\nHello\n/exit\n",
        interactive=True,
    )
    assert "web access (fetch pages and documents): off" in out
    assert "web is now on for this session" in out and "web is already on" in out
    assert "web_search is now on for this session" in out
    first, second = (tool_names(c) for c in setup.model.calls[:2])
    assert "fetch_document" in first and "web_fetch" in first
    assert "fetch_document" not in second and "web_search" in second


def test_web_on_is_refused_while_offline_and_bad_arguments_are_explained(project, home):
    from tests.conftest import write_user_config
    from tests.test_local import LOCAL

    write_user_config(home, LOCAL + "offline = true\n")
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    _, out, err, _ = run_cli(
        project, home, [], lines="/web on\n/web maybe\n/exit\n", interactive=True
    )
    assert "offline is on in your settings" in out + err
    assert "Usage: /web on|off" in out + err


def test_a_link_in_a_message_hints_at_web_when_it_is_off(project, home):
    from tests.conftest import final

    _, out, _, _ = run_cli(
        project,
        home,
        [],
        final(),
        lines="Read https://arxiv.org/pdf/2604.01658\n/exit\n",
        interactive=True,
    )
    assert "web access is off; /web on lets the agent fetch it" in out
