from __future__ import annotations

import io

import pytest
from nailong_agent_sdk import ScriptedModel
from nailong_agent_sdk.starter import ModelSetup

from nailong_cli.console import Console

CONFIG = 'provider = "anthropic"\nmodel = "test-model"\napi_key_env = "TEST_KEY"\n'


class FakeKeyring:
    def __init__(self):
        self.saved = {}

    def get_password(self, service, name):
        return self.saved.get((service, name))

    def set_password(self, service, name, key):
        self.saved[(service, name)] = key

    def delete_password(self, service, name):
        del self.saved[(service, name)]

    def get_keyring(self):
        return self


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    from nailong_cli import credentials

    store = FakeKeyring()
    monkeypatch.setattr(credentials, "_keyring", lambda: store)
    return store


@pytest.fixture(autouse=True)
def anchor_home(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("NAILONG_ANCHOR_HOME", str(tmp_path_factory.mktemp("anchor-home")))


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "project"
    (path / ".nailong").mkdir(parents=True)
    (path / ".nailong" / "config.toml").write_text(CONFIG, encoding="utf-8")
    return path


def make_console(lines: str = "", *, interactive: bool = False):
    stdin, stdout, stderr = io.StringIO(lines), io.StringIO(), io.StringIO()
    return Console(stdin, stdout, stderr, color=False, interactive=interactive), stdout, stderr


def final(answer: str = "done") -> dict:
    return {"type": "final", "output": {"status": "complete", "answer": answer}}


def call(tool_name: str, **arguments: object) -> dict:
    return {
        "type": "tool-call",
        "call": {"id": f"c-{tool_name}", "name": tool_name, "arguments": arguments},
    }


def factory(*turns: dict):
    setup = ModelSetup(ScriptedModel(list(turns)), "scripted", "scripted-1")

    def build(config, environ):
        return setup

    build.setup = setup
    return build


def run_cli(project, home, argv, *turns, lines="", interactive=False, environ=None, build=None):
    from nailong_cli.cli import main

    console, out, err = make_console(lines, interactive=interactive)
    builder = build or factory(*turns)
    code = main(
        argv,
        environ=environ if environ is not None else {"TEST_KEY": "k"},
        console=console,
        model_factory=builder,
        home=home,
        cwd=project,
    )
    return code, out.getvalue(), err.getvalue(), getattr(builder, "setup", None)


def tool_names(model_call) -> list[str]:
    for section in model_call.prompt.model_dump()["sections"]:
        if section["kind"] == "tools":
            value = section["value"]
            return [item["name"] for item in value] if isinstance(value, list) else []
    return []


def instructions_text(model_call) -> str:
    for section in model_call.prompt.model_dump()["sections"]:
        if section["kind"] == "instructions":
            return section["value"]["text"]
    return ""


def write_user_config(home, text: str) -> None:
    path = home / ".config" / "nailong" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
