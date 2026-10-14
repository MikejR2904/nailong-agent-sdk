import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from nailong_agent_sdk.tools.sandbox import DockerSandbox, DockerUnavailableError, NativeSandbox
from nailong_agent_sdk.tools.sandbox_models import DockerSandboxOptions, EnvironmentPolicy
from nailong_agent_sdk.tools.supervisor import CommandTemplate
from tests.support.process_cases import PY, template
from tests.support.tools import run


def test_native_sandbox_scratch_cwd_and_cleanup(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "visible.txt").write_text("hello", "utf-8")
    tpl = template(
        "pwd", "import os; print(os.getcwd()); print(sorted(os.listdir('.')))", timeout=30
    )
    record = run(NativeSandbox().run(tpl, cwd=workspace))
    lines = record.output.strip().splitlines()
    assert Path(lines[0]).name.startswith("nailong-sandbox-")
    assert lines[1] == "[]"
    assert [p.name for p in workspace.iterdir()] == ["visible.txt"]
    parent = tmp_path / "scratch_parent"
    record = run(NativeSandbox(scratch_parent=parent).run(tpl, cwd=workspace))
    assert str(parent) in record.output and list(parent.iterdir()) == []


def test_native_sandbox_environment_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_SECRET_TOKEN", "s3cr3t-value")
    show = "import os; print(os.environ.get('AUDIT_SECRET_TOKEN', 'ABSENT'))"
    tpl = template("env", show)
    default = run(NativeSandbox().run(tpl, cwd=tmp_path))
    keep = ["SYSTEMROOT", "PATH", "TEMP", "TMP", "WINDIR"] if os.name == "nt" else ["PATH"]
    policy = run(
        NativeSandbox().run(
            tpl, cwd=tmp_path, environment=EnvironmentPolicy(allowed_variable_names=keep)
        )
    )
    assert policy.output.strip() == "ABSENT"
    assert default.output.strip() == "ABSENT", "NativeSandbox inherited host secrets with no policy"


def test_native_sandbox_timeout_and_cancel_clean_up_scratch(tmp_path):
    parent = tmp_path / "sp"
    tpl = template("slow", "import time; time.sleep(60)", timeout=1)
    record = run(NativeSandbox(scratch_parent=parent).run(tpl, cwd=tmp_path))
    assert record.timed_out and list(parent.iterdir()) == []

    long_tpl = template("long", "import time; time.sleep(60)", timeout=60)

    async def scenario():
        task = asyncio.create_task(NativeSandbox(scratch_parent=parent).run(long_tpl, cwd=tmp_path))
        await asyncio.sleep(1.0)
        task.cancel()
        try:
            return await task
        except asyncio.CancelledError:
            return None

    run(scenario())
    assert list(parent.iterdir()) == []


FAKE_DOCKER = r"""
import json, os, pathlib, sys, time
log = pathlib.Path(os.environ["FAKE_DOCKER_DIR"])
args = sys.argv[1:]
with (log / "calls.jsonl").open("a") as handle:
    handle.write(json.dumps(args) + "\n")
mode = os.environ.get("FAKE_DOCKER_MODE", "echo")
if args[0] == "kill":
    if mode == "kill_fail":
        sys.stderr.write("Error response from daemon: No such container\n")
        sys.exit(1)
    (log / "stop.flag").write_text("1")
    sys.exit(0)
if args[0] == "run":
    if "--env-file" in args:
        source = pathlib.Path(args[args.index("--env-file") + 1])
        (log / "envfile_copy.txt").write_text(source.read_text())
    if mode == "echo":
        print("container-output")
        sys.exit(0)
    heartbeat = log / "heartbeat.txt"
    while not (log / "stop.flag").exists():
        heartbeat.write_text(str(time.time()))
        time.sleep(0.2)
    sys.exit(137)
sys.exit(2)
"""


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    state = tmp_path / "fake_docker_state"
    state.mkdir()
    script = tmp_path / "fake_docker.py"
    script.write_text(FAKE_DOCKER, "utf-8")
    if os.name == "nt":
        binary = tmp_path / "fake_docker.cmd"
        binary.write_text(f'@echo off\r\n"{PY}" "{script}" %*\r\n', "utf-8")
    else:
        binary = tmp_path / "fake_docker"
        binary.write_text(f'#!/bin/sh\nexec "{PY}" "{script}" "$@"\n', "utf-8")
        binary.chmod(0o755)
    monkeypatch.setenv("FAKE_DOCKER_DIR", str(state))
    yield binary, state
    (state / "stop.flag").write_text("1")
    time.sleep(0.6)


def test_docker_sandbox_argv_and_env_file(tmp_path, fake_docker):
    binary, state = fake_docker
    workspace = tmp_path / "ws"
    workspace.mkdir()
    options = DockerSandboxOptions(
        image="img:1", memory_bytes=1024, cpu_limit=0.5, extra_binds=["/a:/b"]
    )
    sandbox = DockerSandbox(options, docker_binary=str(binary))
    tpl = CommandTemplate(name="t", command=["tool", "--flag"], timeout_seconds=30)
    record = run(
        sandbox.run(
            tpl,
            cwd=workspace,
            environment=EnvironmentPolicy(literal_variables={"SAFE": "1"}),
        )
    )
    assert record.successful and record.output.strip() == "container-output"
    argv = json.loads((state / "calls.jsonl").read_text().splitlines()[0])
    assert argv[0] == "run" and "--rm" in argv and argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv and argv[argv.index("--memory") + 1] == "1024"
    assert argv[argv.index("--cpus") + 1] == "0.5" and argv[-3:] == ["img:1", "tool", "--flag"]
    assert (state / "envfile_copy.txt").read_text().strip() == "SAFE=1"
    leftovers = list(Path(os.environ.get("TEMP", "/tmp")).glob("nailong-sandbox-env-*.env"))
    assert leftovers == []
    no_env = DockerSandbox(options, docker_binary=str(binary))
    run(no_env.run(tpl, cwd=workspace))
    last = json.loads((state / "calls.jsonl").read_text().splitlines()[-1])
    assert "--env-file" not in last


def test_docker_unavailable_and_missing_cwd_messages(tmp_path):
    options = DockerSandboxOptions(image="img")
    tpl = CommandTemplate(name="t", command=["x"], timeout_seconds=5)
    with pytest.raises(DockerUnavailableError, match="was not found on PATH"):
        run(DockerSandbox(options, docker_binary="definitely-no-docker-xyz").run(tpl, cwd=tmp_path))
    with pytest.raises(ValueError, match="does not exist"):
        run(DockerSandbox(options).run(tpl, cwd=tmp_path / "nope"))


def test_docker_env_file_rejects_or_escapes_newlines(tmp_path):
    policy = EnvironmentPolicy(literal_variables={"A": "x\nINJECTED=1", "B": "ok"})
    path = DockerSandbox._write_env_file(policy)
    try:
        lines = path.read_text("utf-8").splitlines()
    finally:
        path.unlink()
    assert not any(line.startswith("INJECTED=") for line in lines), lines


def test_docker_timeout_with_failed_kill_does_not_hang(tmp_path, fake_docker, monkeypatch):
    binary, state = fake_docker
    monkeypatch.setenv("FAKE_DOCKER_MODE", "kill_fail")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = DockerSandbox(DockerSandboxOptions(image="img"), docker_binary=str(binary))
    tpl = CommandTemplate(name="t", command=["x"], timeout_seconds=1)
    try:
        record = run(sandbox.run(tpl, cwd=workspace), timeout=15)
        outcome = ("returned", record.exit_kind.value, record.termination_path)
    except TimeoutError:
        outcome = ("hung", None, None)
    assert outcome[0] == "returned", outcome


def test_docker_kill_nonzero_exit_is_not_reported_as_kill(tmp_path, fake_docker, monkeypatch):
    binary, state = fake_docker
    monkeypatch.setenv("FAKE_DOCKER_MODE", "hang")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = DockerSandbox(DockerSandboxOptions(image="img"), docker_binary=str(binary))
    tpl = CommandTemplate(name="t", command=["x"], timeout_seconds=1)
    record = run(sandbox.run(tpl, cwd=workspace), timeout=30)
    assert record.timed_out and record.termination_path == ["DOCKER_KILL"]
    (state / "calls.jsonl").write_text("")
    monkeypatch.setenv("FAKE_DOCKER_MODE", "kill_fail")
    (state / "stop.flag").unlink(missing_ok=True)
    original = DockerSandbox._kill_container

    async def probe(self, name):
        path = await original(self, name)
        return path

    async def scenario():
        task = asyncio.create_task(sandbox.run(tpl, cwd=workspace))
        await asyncio.sleep(4.0)
        (state / "stop.flag").write_text("1")
        return await asyncio.wait_for(task, 20)

    record = run(scenario(), timeout=40)
    assert record.termination_path == ["DOCKER_KILL_FAILED"], record.termination_path


def test_docker_cancellation_leaks_the_container_process(tmp_path, fake_docker, monkeypatch):
    binary, state = fake_docker
    monkeypatch.setenv("FAKE_DOCKER_MODE", "hang")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = DockerSandbox(DockerSandboxOptions(image="img"), docker_binary=str(binary))
    tpl = CommandTemplate(name="t", command=["x"], timeout_seconds=120)

    async def scenario():
        task = asyncio.create_task(sandbox.run(tpl, cwd=workspace))
        await asyncio.sleep(2.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(1.0)
        first = (state / "heartbeat.txt").read_text()
        await asyncio.sleep(1.0)
        second = (state / "heartbeat.txt").read_text()
        calls = (state / "calls.jsonl").read_text().splitlines()
        return first != second, calls

    still_running, calls = run(scenario(), timeout=60)
    assert not still_running, (
        "docker run process kept running after the awaiting task was cancelled"
    )


def test_an_invalid_variable_name_leaves_no_environment_file_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    policy = EnvironmentPolicy(
        literal_variables={"API_TOKEN": "super-secret-value", "BAD NAME": "x"}
    )
    sandbox = DockerSandbox(DockerSandboxOptions(image="img"))
    tpl = CommandTemplate(name="t", command=["true"], timeout_seconds=5)
    with pytest.raises(ValueError, match="'BAD NAME' cannot be passed to a container"):
        run(sandbox.run(tpl, cwd=tmp_path, environment=policy))
    with pytest.raises(ValueError, match="'BAD NAME' cannot be passed to a container"):
        DockerSandbox._write_env_file(policy)
    assert list(tmp_path.glob("nailong-sandbox-env-*")) == []


def test_a_failed_env_file_write_removes_the_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    policy = EnvironmentPolicy(literal_variables={"A": "1"})

    def broken(descriptor, mode, **kwargs):
        os.close(descriptor)
        raise OSError("disk full")

    monkeypatch.setattr(os, "fdopen", broken)
    with pytest.raises(OSError, match="disk full"):
        DockerSandbox._write_env_file(policy)
    assert list(tmp_path.glob("nailong-sandbox-env-*")) == []
