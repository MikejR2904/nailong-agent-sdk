import http.server
import ipaddress
import socket
import threading
import time

import pytest

import nailong_agent_sdk.tools.core.helpers as helpers
from nailong_agent_sdk.tools.core.helpers import _assert_public_http_url, _fetch_public_text


class _Handler(http.server.BaseHTTPRequestHandler):
    routes = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        route = self.routes.get(self.path)
        if route is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        route(self)


def _serve():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture
def server():
    _Handler.routes = {}
    instance = _serve()
    yield instance
    instance.shutdown()
    instance.server_close()


def _fake_getaddrinfo(mapping):
    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        if host in mapping:
            value = mapping[host]
            addresses = value() if callable(value) else value
            family = socket.AF_INET6 if ":" in addresses[0] else socket.AF_INET
            return [
                (family, socket.SOCK_STREAM, 6, "", (address, port or 0)) for address in addresses
            ]
        return real(host, port, *args, **kwargs)

    return fake


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://LOCALHOST./",
        "http://[::1]/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://017700000001/",
        "http://127.1/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:10.0.0.1]/",
        "http://[fe80::1]/",
        "http://224.0.0.1/",
        "http://240.0.0.1/",
        "http://user:pw@127.0.0.1/",
        "http://public.example@127.0.0.1/",
        "ftp://example.com/",
        "file:///etc/passwd",
        "http:///nohost",
        "gopher://127.0.0.1/",
    ],
)
def test_assert_public_http_url_rejects(url):
    with pytest.raises(ValueError) as excinfo:
        _assert_public_http_url(url)
    assert str(excinfo.value)


@pytest.mark.parametrize(
    "address",
    [
        "100.64.0.1",
        "100.100.100.200",
        "100.127.255.254",
        "198.18.0.1",
        "192.0.0.1",
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        "64:ff9b::7f00:1",
        "64:ff9b::a00:1",
        "2002:7f00:1::1",
        "2001::1",
        "fc00::1",
        "::1",
    ],
)
def test_ssrf_guard_on_special_purpose_ranges(address):
    candidate = ipaddress.ip_address(address)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"special.test": [address]}))
            _assert_public_http_url("http://special.test/")
        passed = True
    except ValueError:
        passed = False
    assert not passed, f"{address} (is_global={candidate.is_global}) passed the SSRF guard"


def test_guard_allows_a_public_address():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"ok.test": ["93.184.216.34"]}))
        _assert_public_http_url("http://ok.test/")


def test_guard_rejects_mixed_public_and_private_resolution():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo({"mixed.test": ["93.184.216.34", "10.0.0.1"]})
        )
        with pytest.raises(ValueError, match="resolved to"):
            _assert_public_http_url("http://mixed.test/")


def test_guard_unresolvable_host_message():
    with pytest.raises(ValueError, match="could not resolve host"):
        _assert_public_http_url("http://definitely-not-a-real-host.invalid/")


def test_dns_rebinding_toctou_reaches_loopback(server):
    port = server.server_address[1]
    _Handler.routes["/secret"] = lambda handler: (
        handler.send_response(200),
        handler.send_header("Content-Type", "text/plain"),
        handler.send_header("Content-Length", "13"),
        handler.end_headers(),
        handler.wfile.write(b"LOOPBACK-DATA"),
    )
    calls = {"count": 0}

    def resolver():
        calls["count"] += 1
        return ["93.184.216.34"] if calls["count"] == 1 else ["127.0.0.1"]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"rebind.test": resolver}))
        try:
            result = _fetch_public_text(
                f"http://rebind.test:{port}/secret", 1000, deadline_seconds=3
            )
            leaked = "LOOPBACK-DATA" in result["content"]
        except Exception:
            leaked = False
    assert not leaked, "fetch reached 127.0.0.1 after the guard resolved a public address"
    assert calls["count"] == 1, "the host name was resolved again after the guard validated it"


def test_redirect_to_private_target_is_blocked(server):
    port = server.server_address[1]

    def redirect(handler):
        handler.send_response(302)
        handler.send_header("Location", "http://169.254.169.254/latest/meta-data/")
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    _Handler.routes["/r"] = redirect
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"redir.test": ["127.0.0.1"]}))
        patch.setattr(helpers, "_assert_public_http_url", _guard_allow_first_only())
        with pytest.raises(ValueError, match="rejects private|169.254"):
            _fetch_public_text(f"http://redir.test:{port}/r", 1000)


def _guard_allow_first_only():
    real = helpers._assert_public_http_url
    seen = {"first": True}

    def guard(url, tool_name="web_fetch"):
        if seen["first"]:
            seen["first"] = False
            return None
        return real(url, tool_name)

    return guard


def test_fetch_content_types_and_http_errors(server):
    port = server.server_address[1]

    def make(content_type, body, status=200):
        def route(handler):
            handler.send_response(status)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        return route

    _Handler.routes.update(
        {
            "/text": make("text/plain; charset=utf-8", "héllo".encode()),
            "/json": make("application/json", b'{"a": 1}'),
            "/html": make("text/html", b"<html><script>x</script>hi</html>"),
            "/image": make("image/png", b"\x89PNG"),
            "/zip": make("application/zip", b"PK"),
            "/e404": make("text/plain", b"nope", 404),
            "/e429": make("text/plain", b"slow down", 429),
            "/e503": make("text/plain", b"later", 503),
            "/e418": make("text/plain", b"teapot", 418),
        }
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(helpers, "_assert_public_http_url", lambda url, tool_name="web_fetch": None)
        base = f"http://127.0.0.1:{port}"
        text = _fetch_public_text(base + "/text", 1000)
        assert text["content"] == "héllo" and text["untrusted_content"] is True
        assert _fetch_public_text(base + "/json", 1000)["content_type"] == "application/json"
        assert "<script>" in _fetch_public_text(base + "/html", 1000)["content"]
        for path in ("/image", "/zip"):
            with pytest.raises(ValueError, match="rejects non-textual content type"):
                _fetch_public_text(base + path, 1000)
        for path, fragment in (
            ("/e404", "HTTP 404"),
            ("/e429", "rate-limiting"),
            ("/e503", "overloaded"),
        ):
            with pytest.raises(ValueError, match=fragment):
                _fetch_public_text(base + path, 1000)
        with pytest.raises(ValueError, match="HTTP 418"):
            _fetch_public_text(base + "/e418", 1000)


def test_fetch_redirect_limit_and_loop(server):
    port = server.server_address[1]

    def loop(handler):
        handler.send_response(302)
        handler.send_header("Location", f"http://127.0.0.1:{port}/loop")
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    _Handler.routes["/loop"] = loop
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(helpers, "_assert_public_http_url", lambda url, tool_name="web_fetch": None)
        with pytest.raises(ValueError, match="redirect limit"):
            _fetch_public_text(f"http://127.0.0.1:{port}/loop", 1000)


def test_fetch_truncation_is_bounded(server):
    port = server.server_address[1]
    body = b"A" * 5_000_000

    def big(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        try:
            handler.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass

    _Handler.routes["/big"] = big
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(helpers, "_assert_public_http_url", lambda url, tool_name="web_fetch": None)
        result = _fetch_public_text(f"http://127.0.0.1:{port}/big", 1000)
    assert len(result["content"]) == 1000 and result["truncated"] is True


def test_slow_drip_response_is_cut_off_at_the_total_deadline(server):
    port = server.server_address[1]
    drip_seconds = 6.0

    def drip(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain")
        handler.send_header("Content-Length", "4000")
        handler.end_headers()
        try:
            end = time.monotonic() + drip_seconds
            sent = 0
            while sent < 4000 and time.monotonic() < end:
                handler.wfile.write(b"x")
                handler.wfile.flush()
                sent += 1
                time.sleep(0.1)
            handler.wfile.write(b"y" * (4000 - sent))
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass

    _Handler.routes["/drip"] = drip
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(helpers, "_assert_public_http_url", lambda url, tool_name="web_fetch": None)
        started = time.monotonic()
        result = _fetch_public_text(f"http://127.0.0.1:{port}/drip", 2000, deadline_seconds=2)
        elapsed = time.monotonic() - started
    assert elapsed < 4, (
        f"fetch held a worker thread for {elapsed:.1f}s while the peer dripped bytes"
    )
    assert result["truncated"] is True and result["deadline_exceeded"] is True
    assert 0 < len(result["content"]) < 4000


def test_a_server_that_never_sends_headers_fails_at_the_deadline(server):
    port = server.server_address[1]

    def stall(handler):
        time.sleep(6)

    _Handler.routes["/stall"] = stall
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(helpers, "_assert_public_http_url", lambda url, tool_name="web_fetch": None)
        started = time.monotonic()
        with pytest.raises(ValueError, match="timed out waiting for a response"):
            _fetch_public_text(f"http://127.0.0.1:{port}/stall", 1000, deadline_seconds=1.5)
        assert time.monotonic() - started < 5


def test_strip_html_and_search_parser_edge_cases():
    from nailong_agent_sdk.tools.core.helpers import _strip_html

    assert _strip_html("<b>bold</b> &amp; <i>it</i>") == "bold & it"
    started = time.monotonic()
    _strip_html("<" * 20_000)
    _strip_html("<a" * 10_000)
    assert time.monotonic() - started < 5
