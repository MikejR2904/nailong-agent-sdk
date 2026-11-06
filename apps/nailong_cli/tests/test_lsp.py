from __future__ import annotations

import json
import sys

import pytest

from nailong_cli.config import ConfigError
from nailong_cli.lsp import LspClient, LspError, LspManager, ServerSpec, specs_from_table
from tests.conftest import call, final, run_cli
from tests.test_sdk_features import user_only_project

FAKE_SERVER = r"""
import json, sys

def read():
    length = 0
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":")[1])
    return json.loads(sys.stdin.buffer.read(length))

def send(message):
    body = json.dumps(message).encode()
    sys.stdout.buffer.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    sys.stdout.buffer.flush()

def diagnose(params, text):
    found = []
    for number, line in enumerate(text.splitlines()):
        if "BAD" in line:
            found.append({
                "range": {
                    "start": {"line": number, "character": line.index("BAD")},
                    "end": {"line": number, "character": 3},
                },
                "severity": 1, "message": "BAD is not allowed"})
    send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
          "params": {"uri": params["textDocument"]["uri"], "diagnostics": found}})

while True:
    message = read()
    if message is None:
        break
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
    elif method == "textDocument/didOpen":
        diagnose(message["params"], message["params"]["textDocument"]["text"])
    elif method == "textDocument/didChange":
        diagnose(message["params"], message["params"]["contentChanges"][0]["text"])
    elif method == "textDocument/definition":
        uri = message["params"]["textDocument"]["uri"]
        start = {"line": 0, "character": 4}
        send({"jsonrpc": "2.0", "id": message["id"],
              "result": {"uri": uri, "range": {"start": start, "end": start}}})
    elif method == "textDocument/references":
        uri = message["params"]["textDocument"]["uri"]
        spans = []
        for n in (0, 2):
            start = {"line": n, "character": 0}
            spans.append({"uri": uri, "range": {"start": start, "end": start}})
        send({"jsonrpc": "2.0", "id": message["id"], "result": spans})
    elif method == "textDocument/hover":
        send({"jsonrpc": "2.0", "id": message["id"],
              "result": {"contents": {"kind": "markdown", "value": "int counter"}}})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": message["id"], "result": None})
    elif method == "exit":
        break
"""


@pytest.fixture
def fake(tmp_path):
    path = tmp_path / "fake_lsp.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return ServerSpec("fake", (sys.executable, str(path)), (".txt",))


def test_a_server_reports_a_problem_with_line_column_and_message(tmp_path, fake):
    target = tmp_path / "a.txt"
    client = LspClient(fake, tmp_path)
    try:
        found = client.diagnose(target, "fine\nthis is BAD here\n")
        assert found == ["a.txt:2:9 error: BAD is not allowed"]
        assert client.diagnose(target, "all fine now\n") == []
        assert client.diagnose(target, "BAD\n") == ["a.txt:1:1 error: BAD is not allowed"]
    finally:
        client.close()


def test_a_server_that_cannot_start_is_named(tmp_path):
    spec = ServerSpec("ghost", ("definitely-not-a-real-server",), (".txt",))
    client = LspClient(spec, tmp_path)
    with pytest.raises(LspError, match="ghost could not start"):
        client.diagnose(tmp_path / "a.txt", "x")


def test_the_manager_picks_the_server_by_extension_and_formats_the_report(tmp_path, fake):
    (tmp_path / "a.txt").write_text("BAD\n", encoding="utf-8")
    (tmp_path / "a.md").write_text("BAD\n", encoding="utf-8")
    manager = LspManager(tmp_path, [fake], builtin=False)
    try:
        assert manager.diagnose(tmp_path / "a.md") == []
        report = manager.diagnose(tmp_path / "a.txt")
        assert report == ["fake (language server):\na.txt:1:1 error: BAD is not allowed"]
    finally:
        manager.close()


def test_a_server_that_fails_is_reported_once_and_then_left_alone(tmp_path):
    spec = ServerSpec("ghost", ("definitely-not-a-real-server",), (".txt",))
    manager = LspManager(tmp_path, [spec], builtin=False)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    first = manager.diagnose(tmp_path / "a.txt")
    assert first and "language server ghost stopped" in first[0]
    assert manager.diagnose(tmp_path / "a.txt") == []


def test_settings_for_servers_are_checked():
    assert specs_from_table({"x": {"command": ["s"], "extensions": [".py"]}})[0].name == "x"
    with pytest.raises(ValueError, match="lsp.x needs command"):
        specs_from_table({"x": {"extensions": [".py"]}})
    with pytest.raises(ValueError, match="lsp.x needs extensions"):
        specs_from_table({"x": {"command": ["s"]}})


def test_the_agent_hears_about_a_language_server_problem_after_its_edit(project, home, fake):
    user_only_project(
        project,
        home,
        f"[lsp.fake]\ncommand = [{json.dumps(sys.executable)}, {json.dumps(fake.command[1])}]\n"
        'extensions = [".txt"]\n',
    )
    code, _, err, setup = run_cli(
        project,
        home,
        ["--permission-mode", "bypass", "--allow-unsandboxed"],
        call("write_file", path="note.txt", content="line one\nBAD line\n"),
        final("done"),
        lines="Write\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    messages = [i.message for i in setup.model.calls[1].observations if i.kind == "user-message"]
    assert messages and "fake (language server)" in messages[0]
    assert "note.txt:2:1 error: BAD is not allowed" in messages[0]


def test_a_bad_lsp_table_stops_startup_with_its_name(project, home):
    with pytest.raises(ConfigError, match="lsp.broken needs command"):
        user_only_project(project, home, '[lsp.broken]\nextensions = [".py"]\n')


def test_definition_references_and_hover_come_back_as_project_relative_positions(tmp_path, fake):
    target = tmp_path / "a.txt"
    target.write_text("let x = 1\nuse x\nuse x\n", encoding="utf-8")
    manager = LspManager(tmp_path, [fake], builtin=False)
    try:
        assert manager.navigate(target, 2, 5, "definition") == ["a.txt:1:5"]
        assert manager.navigate(target, 1, 5, "references") == ["a.txt:1:1", "a.txt:3:1"]
        assert manager.navigate(target, 1, 5, "hover") == ["int counter"]
        with pytest.raises(ValueError, match="kind must be one of"):
            manager.navigate(target, 1, 1, "rename")
        (tmp_path / "b.md").write_text("x", encoding="utf-8")
        with pytest.raises(ValueError, match="No language server handles .md"):
            manager.navigate(tmp_path / "b.md", 1, 1, "definition")
    finally:
        manager.close()


def test_the_agent_can_ask_where_a_symbol_is_defined_and_cannot_leave_the_project(
    project, home, fake
):
    command = f"[{json.dumps(sys.executable)}, {json.dumps(fake.command[1])}]"
    user_only_project(
        project, home, "[lsp.fake]\ncommand = " + command + '\nextensions = [".txt"]\n'
    )
    (project / "a.txt").write_text("let x = 1\nuse x\n", encoding="utf-8")
    code, out, err, setup = run_cli(
        project,
        home,
        [],
        call("code_navigate", action="definition", path="a.txt", line=2, character=5),
        call("code_navigate", action="definition", path="../outside.txt", line=1),
        final("done"),
        lines="Where is x?\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    seen = " ".join(str(item) for item in setup.model.calls[1].observations)
    assert "a.txt:1:5" in seen
    later = " ".join(str(item) for item in setup.model.calls[2].observations)
    assert "outside the project" in later
