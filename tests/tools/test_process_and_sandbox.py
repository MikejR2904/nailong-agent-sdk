import asyncio
import json
import os
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

from nailong_agent_sdk.tools.sandbox import DockerSandbox, DockerUnavailableError, NativeSandbox
from nailong_agent_sdk.tools.sandbox_models import DockerSandboxOptions, EnvironmentPolicy
from nailong_agent_sdk.tools.supervisor import (
    CommandTemplate,
    ProcessExitKind,
    ProcessSupervisor,
    ResourceLimits,
    RetryPolicy,
)
from tests.support.processes import process_alive
from tests.support.tools import run

PY = sys.executable
posix_only = pytest.mark.skipif(os.name != "posix", reason="resource limits are applied on POSIX")


def template(name, code, *, timeout=30, **extra):
    return CommandTemplate(
        name=name, command=[PY, "-c", textwrap.dedent(code)], timeout_seconds=timeout, **extra
    )


def execute(tmp_path, tpl, *, env=None, timeout=90, supervisor=None):
    sup = supervisor or ProcessSupervisor([tpl])
    return run(sup.execute(tpl.name, cwd=tmp_path, env=env), timeout=timeout)


def test_success_nonzero_and_record_shape(tmp_path):
    ok = execute(tmp_path, template("ok", "print('hi')"))
    assert ok.successful and ok.return_code == 0 and ok.output.strip() == "hi"
    assert ok.pid and ok.duration_ns > 0 and ok.attempts == 1 and ok.exit_signal is None
    bad = execute(tmp_path, template("bad", "import sys; print('x'); sys.exit(7)"))
    assert not bad.successful and bad.return_code == 7
    assert (
        bad.exit_kind is ProcessExitKind.EXIT_NONZERO and bad.error_code == "PROCESS_EXIT_NONZERO"
    )
    assert bad.output.strip() == "x"


def test_missing_cwd_and_unknown_template_messages(tmp_path):
    sup = ProcessSupervisor([template("ok", "print(1)")])
    with pytest.raises(ValueError, match='No registered command template exists for "zzz"'):
        run(sup.execute("zzz", cwd=tmp_path))
    with pytest.raises(ValueError, match="working directory .* does not exist"):
        run(sup.execute("ok", cwd=tmp_path / "missing"))


def test_timeout_kills_the_whole_process_tree(tmp_path):
    heartbeat = tmp_path / "grandchild_heartbeat.txt"
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text(
        "import time, pathlib\n"
        f"p = pathlib.Path(r'{heartbeat}')\n"
        "while True:\n"
        "    p.write_text(str(time.time()))\n"
        "    time.sleep(0.2)\n",
        "utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, r'{grandchild}'])\n"
        "time.sleep(60)\n",
        "utf-8",
    )
    tpl = CommandTemplate(name="tree", command=[PY, str(parent)], timeout_seconds=2)
    started = time.monotonic()
    record = execute(tmp_path, tpl)
    elapsed = time.monotonic() - started
    assert record.exit_kind is ProcessExitKind.TIMED_OUT and record.timed_out
    assert record.error_code == "PROCESS_TIMEOUT" and elapsed < 15
    time.sleep(0.8)
    before = heartbeat.read_text()
    time.sleep(1.0)
    after = heartbeat.read_text()
    assert before == after, "grandchild still writing after timeout termination"


def test_orphan_holding_output_pipe_does_not_defeat_timeout(tmp_path):
    code = """
        import subprocess, sys
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(14)"])
        print("parent exits now")
    """
    started = time.monotonic()
    execute(tmp_path, template("orphan", code, timeout=2))
    elapsed = time.monotonic() - started
    assert elapsed < 6, f"execute returned after {elapsed:.1f}s with timeout_seconds=2"


def test_output_cap_with_large_output(tmp_path):
    code = """
        import sys
        chunk = "x" * 1_000_000
        for _ in range(50):
            sys.stdout.write(chunk)
        sys.stdout.flush()
    """
    started = time.monotonic()
    record = execute(tmp_path, template("flood", code, max_output_bytes=1000))
    elapsed = time.monotonic() - started
    assert record.output_bytes_retained == 1000 == len(record.output.encode())
    assert record.output_bytes_total >= 50_000_000
    assert record.output_truncated_bytes == record.output_bytes_total - 1000
    assert elapsed < 30


def test_non_utf8_output_is_replaced_not_crashed(tmp_path):
    code = "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe end'); sys.stdout.flush()"
    record = execute(tmp_path, template("bin", code))
    assert record.successful and record.output.startswith("ok ") and "\ufffd" in record.output


def test_cancellation_terminates_the_process_and_propagates(tmp_path):
    pid_file = tmp_path / "child.pid"
    code = f"""
        import os, pathlib, time
        pathlib.Path(r"{pid_file}").write_text(str(os.getpid()))
        time.sleep(60)
    """
    events = []

    class Recorder:
        def emit(self, name, context, **kwargs):
            events.append(kwargs.get("status"))

    tpl = template("sleepy", code)
    sup = ProcessSupervisor(
        [tpl], telemetry=Recorder(), telemetry_context_factory=lambda n: object()
    )

    async def scenario():
        task = asyncio.create_task(sup.execute("sleepy", cwd=tmp_path))
        deadline = time.monotonic() + 15
        while not pid_file.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return task.cancelled()
        return False

    assert run(scenario()) is True
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not process_alive(pid)
    assert events[-2:] == ["terminating", "cancelled"], events


def test_asyncio_timeout_around_execute_still_raises(tmp_path):
    tpl = template("sleepy", "import time; time.sleep(60)")
    sup = ProcessSupervisor([tpl])

    async def scenario():
        try:
            async with asyncio.timeout(1.0):
                record = await sup.execute("sleepy", cwd=tmp_path)
            return "returned", record.exit_kind.value
        except TimeoutError:
            return "timeout", None

    outcome = run(scenario())
    assert outcome[0] == "timeout", outcome


def test_retry_policy_attempts_and_backoff(tmp_path):
    counter = tmp_path / "count.txt"
    code = f"""
        import pathlib, sys
        p = pathlib.Path(r"{counter}")
        n = int(p.read_text()) + 1 if p.exists() else 1
        p.write_text(str(n))
        sys.exit(0 if n >= 3 else 1)
    """
    retry = RetryPolicy(
        max_attempts=3,
        retryable_exit_kinds=[ProcessExitKind.EXIT_NONZERO],
        base_backoff_seconds=0.2,
        idempotent=True,
    )
    started = time.monotonic()
    record = execute(tmp_path, template("flaky", code, retry_policy=retry))
    elapsed = time.monotonic() - started
    assert record.successful and record.attempts == 3 and elapsed >= 0.2 + 0.4
    counter.unlink()
    not_idempotent = RetryPolicy(
        max_attempts=3, retryable_exit_kinds=[ProcessExitKind.EXIT_NONZERO], idempotent=False
    )
    single = execute(tmp_path, template("once", code, retry_policy=not_idempotent))
    assert not single.successful and single.attempts == 1 and counter.read_text() == "1"


def test_environment_inheritance_and_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_SECRET_TOKEN", "s3cr3t-value")
    show = "import os; print(os.environ.get('AUDIT_SECRET_TOKEN', 'ABSENT'))"
    inherited = execute(tmp_path, template("inherit", show))
    assert inherited.output.strip() == "s3cr3t-value"
    keep = ["SYSTEMROOT", "PATH", "TEMP", "TMP", "WINDIR"] if os.name == "nt" else ["PATH"]
    scrubbed = execute(
        tmp_path,
        template("scrub", show, environment=EnvironmentPolicy(allowed_variable_names=keep)),
    )
    assert scrubbed.successful and scrubbed.output.strip() == "ABSENT"
    literal = execute(
        tmp_path,
        template(
            "literal",
            "import os; print(os.environ['FOO'])",
            environment=EnvironmentPolicy(
                allowed_variable_names=keep, literal_variables={"FOO": "bar"}
            ),
        ),
    )
    assert literal.output.strip() == "bar"
    override = execute(
        tmp_path,
        template("override", "import os; print(os.environ['ONLY'])"),
        env={**os.environ, "ONLY": "explicit"},
    )
    assert override.output.strip() == "explicit"


def test_missing_binary_raises_naming_the_template_and_binary_and_emits_a_terminal_state(
    tmp_path,
):
    events = []

    class Recorder:
        def emit(self, name, context, **kwargs):
            events.append((name, kwargs.get("status")))

    tpl = CommandTemplate(name="ghost", command=["definitely-not-a-binary-xyz"], timeout_seconds=5)
    sup = ProcessSupervisor(
        [tpl], telemetry=Recorder(), telemetry_context_factory=lambda name: object()
    )
    with pytest.raises(FileNotFoundError, match='"ghost" could not start .definitely-not-a-binary'):
        run(sup.execute("ghost", cwd=tmp_path))
    assert events[-1] == ("watchdog.failed", "failed"), events


def test_watchdog_event_sequences(tmp_path):
    events = []

    class Recorder:
        def emit(self, name, context, **kwargs):
            events.append(name)

    sup = ProcessSupervisor(
        [template("ok", "print(1)"), template("slow", "import time; time.sleep(30)", timeout=1)],
        telemetry=Recorder(),
        telemetry_context_factory=lambda name: object(),
    )
    run(sup.execute("ok", cwd=tmp_path))
    assert events == ["watchdog.registered", "watchdog.started", "watchdog.completed"]
    events.clear()
    run(sup.execute("slow", cwd=tmp_path))
    assert events == [
        "watchdog.registered",
        "watchdog.started",
        "watchdog.terminating",
        "watchdog.killed",
        "watchdog.timed-out",
    ]


def test_resource_limit_reporting_is_honest_on_this_platform(tmp_path):
    record = execute(
        tmp_path,
        template("limits", "print(1)", resource_limits=ResourceLimits(cpu_seconds=5)),
    )
    if os.name == "nt":
        assert record.resource_limits_enforced == []
        assert record.resource_limits_unsupported == ["cpu_seconds"]


@posix_only
def test_posix_limits_are_applied_to_the_child_and_reported(tmp_path):
    limits = ResourceLimits(cpu_seconds=5, max_file_bytes=1_000_000)
    record = execute(tmp_path, template("limits", "print(1)", resource_limits=limits))
    assert record.successful
    assert sorted(record.resource_limits_enforced) == ["cpu_seconds", "max_file_bytes"]
    assert record.resource_limits_unsupported == []


@posix_only
def test_a_child_that_spins_past_its_cpu_limit_is_reported_as_resource_limited(tmp_path):
    spinner = template("spin", "while True: pass", resource_limits=ResourceLimits(cpu_seconds=1))
    record = execute(tmp_path, spinner)
    assert not record.successful and record.resource_limited
    assert record.exit_kind is ProcessExitKind.RESOURCE_LIMIT
    assert record.exit_signal == "SIGXCPU" and record.error_code == "PROCESS_RESOURCE_LIMIT"


@posix_only
def test_a_child_killed_for_writing_past_the_file_limit_is_reported_as_resource_limited(tmp_path):
    writer = CommandTemplate(
        name="big",
        command=["sh", "-c", "exec head -c 2000000 /dev/zero > big.bin"],
        timeout_seconds=30,
        resource_limits=ResourceLimits(max_file_bytes=100_000),
    )
    record = execute(tmp_path, writer)
    assert record.exit_kind is ProcessExitKind.RESOURCE_LIMIT and record.exit_signal == "SIGXFSZ"
    assert (tmp_path / "big.bin").stat().st_size <= 100_000


@posix_only
def test_a_child_that_ignores_the_cpu_signal_is_still_stopped_by_the_hard_limit(tmp_path):
    stubborn = template(
        "stubborn",
        """
        import signal

        signal.signal(signal.SIGXCPU, signal.SIG_IGN)
        while True:
            pass
        """,
        resource_limits=ResourceLimits(cpu_seconds=1),
    )
    started = time.monotonic()
    record = execute(tmp_path, stubborn)
    assert not record.successful and record.exit_signal == "SIGKILL"
    assert time.monotonic() - started < 20


def test_concurrent_executions(tmp_path):
    tpl = template("quick", "import os; print(os.getpid())")
    sup = ProcessSupervisor([tpl])

    async def scenario():
        return await asyncio.gather(*(sup.execute("quick", cwd=tmp_path) for _ in range(24)))

    records = run(scenario(), timeout=120)
    assert all(r.successful for r in records)
    assert len({r.output.strip() for r in records}) == 24


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
