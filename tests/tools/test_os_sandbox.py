import asyncio
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nailong_agent_sdk.tools import os_sandbox
from nailong_agent_sdk.tools.os_sandbox import (
    BubblewrapSandbox,
    OsSandboxOptions,
    OsSandboxUnavailableError,
    SeatbeltSandbox,
    bubblewrap_argv,
    default_os_sandbox,
    seatbelt_profile,
)
from nailong_agent_sdk.tools.sandbox_models import EnvironmentPolicy
from nailong_agent_sdk.tools.supervisor import (
    CommandTemplate,
    ProcessExecutionRecord,
    ProcessExitKind,
)

PROJECT = Path("/work/project")
OPTIONS = OsSandboxOptions(hide_paths=["/home/dev"])


def template(*command, timeout=5.0):
    return CommandTemplate(name="shell", command=list(command), timeout_seconds=timeout)


def position(argv, *pieces):
    for index in range(len(argv) - len(pieces) + 1):
        if argv[index : index + len(pieces)] == list(pieces):
            return index
    raise AssertionError(f"{pieces} not found in {argv}")


def posix_project(monkeypatch):
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: self)


def test_bubblewrap_hides_the_network_and_home_and_binds_only_the_project_writable(monkeypatch):
    posix_project(monkeypatch)
    argv = bubblewrap_argv("/usr/bin/bwrap", OPTIONS, template("sh", "-c", "ls"), PROJECT, {})
    assert argv[0] == "/usr/bin/bwrap" and "--unshare-net" in argv
    assert "--die-with-parent" in argv and "--new-session" in argv and "--unshare-pid" in argv
    root_at = position(argv, "--ro-bind", "/", "/")
    hide_at = position(argv, "--tmpfs", "/home/dev")
    bind_at = position(argv, "--bind", "/work/project", "/work/project")
    assert root_at < hide_at < bind_at
    assert argv[position(argv, "--chdir", "/work/project") :][2] == "--clearenv"
    assert argv[argv.index("--") + 1 :] == ["sh", "-c", "ls"]
    writable = [argv[i + 1] for i, a in enumerate(argv) if a == "--bind"]
    assert writable == ["/work/project"]


def test_bubblewrap_options_open_exactly_what_they_name(monkeypatch):
    posix_project(monkeypatch)
    options = OsSandboxOptions(
        network=True,
        hide_paths=["/home/dev"],
        extra_read_only=["/home/dev/.pyenv"],
        extra_writable=["/var/cache/build"],
    )
    argv = bubblewrap_argv("bwrap", options, template("true"), PROJECT, {"PATH": "/usr/bin"})
    assert "--unshare-net" not in argv
    assert position(argv, "--tmpfs", "/home/dev") < position(
        argv, "--ro-bind", "/home/dev/.pyenv", "/home/dev/.pyenv"
    )
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--bind"] == [
        "/work/project",
        "/var/cache/build",
    ]
    assert position(argv, "--setenv", "HOME", "/tmp") and position(
        argv, "--setenv", "PATH", "/usr/bin"
    )
    assert argv.index("--clearenv") < argv.index("--setenv")


@pytest.mark.parametrize("bad", ["relative/path", "", "/ok\x00bad"])
def test_sandbox_paths_must_be_absolute_and_free_of_nul(monkeypatch, bad):
    posix_project(monkeypatch)
    with pytest.raises(ValueError, match="must be absolute and contain no NUL"):
        bubblewrap_argv("bwrap", OsSandboxOptions(hide_paths=[bad]), template("true"), PROJECT, {})


def test_seatbelt_profile_denies_by_default_and_allows_only_the_project_for_writes(monkeypatch):
    posix_project(monkeypatch)
    profile = seatbelt_profile(OPTIONS, PROJECT)
    lines = profile.splitlines()
    assert lines[:2] == ["(version 1)", "(deny default)"]
    assert "(allow network*)" not in profile
    writes = next(line for line in lines if line.startswith("(allow file-write* (subpath"))
    assert (
        '"/work/project"' in writes and '"/private/tmp"' in writes and '"/home/dev"' not in writes
    )


def test_seatbelt_hidden_paths_are_denied_after_reads_are_allowed(monkeypatch):
    posix_project(monkeypatch)
    options = OsSandboxOptions(hide_paths=["/Users/dev"], extra_read_only=["/Users/dev/.pyenv"])
    lines = seatbelt_profile(options, Path("/Users/dev/code/app")).splitlines()
    allow_reads = lines.index("(allow file-read*)")
    deny = lines.index('(deny file-read* file-write* (subpath "/Users/dev"))')
    toolchain = lines.index('(allow file-read* (subpath "/Users/dev/.pyenv"))')
    project = lines.index('(allow file-read* file-write* (subpath "/Users/dev/code/app"))')
    assert allow_reads < deny < toolchain < project


def test_seatbelt_network_is_off_unless_asked_and_paths_cannot_break_out_of_a_string(monkeypatch):
    posix_project(monkeypatch)
    assert "(allow network*)" in seatbelt_profile(
        OsSandboxOptions(network=True, hide_paths=[]), PROJECT
    )
    tricky = OsSandboxOptions(hide_paths=['/tmp/a")(allow file-write* (subpath "/'])
    profile = seatbelt_profile(tricky, PROJECT)
    assert r"/tmp/a\")(allow file-write* (subpath \"/" in profile
    with pytest.raises(ValueError, match="control characters"):
        seatbelt_profile(OsSandboxOptions(hide_paths=["/tmp/a\nb"]), PROJECT)


class FakeSupervisor:
    seen = []

    def __init__(self, templates):
        self.templates = {item.name: item for item in templates}

    async def execute(self, name, *, cwd, env=None):
        FakeSupervisor.seen.append((self.templates[name], cwd, env))
        return ProcessExecutionRecord(
            template_name=name,
            command=self.templates[name].command,
            started_at_utc="2026-01-01T00:00:00+00:00",
            ended_at_utc="2026-01-01T00:00:01+00:00",
            duration_ns=1,
            return_code=0,
            exit_kind=ProcessExitKind.SUCCEEDED,
            output="ok",
            output_bytes_total=2,
            output_bytes_retained=2,
            output_truncated_bytes=0,
        )


def pretend(monkeypatch, platform, binary=True):
    posix_project(monkeypatch)
    FakeSupervisor.seen = []
    monkeypatch.setattr(os_sandbox, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(
        os_sandbox,
        "shutil",
        SimpleNamespace(which=lambda name: f"/usr/bin/{name}" if binary else None),
    )
    monkeypatch.setattr(os_sandbox, "ProcessSupervisor", FakeSupervisor)


def test_bubblewrap_runs_the_wrapped_command_through_the_supervisor(monkeypatch):
    pretend(monkeypatch, "linux")
    original = template("sh", "-c", "make", timeout=7.0)
    record = asyncio.run(
        BubblewrapSandbox(OPTIONS).run(
            original,
            cwd=PROJECT,
            environment=EnvironmentPolicy(literal_variables={"CI": "1"}),
        )
    )
    wrapped, cwd, env = FakeSupervisor.seen[0]
    assert record.output == "ok" and cwd == PROJECT and env == {"CI": "1"}
    assert wrapped.command[0] == "/usr/bin/bwrap" and wrapped.command[-3:] == ["sh", "-c", "make"]
    assert wrapped.timeout_seconds == 7.0 and wrapped.environment is None
    assert original.command == ["sh", "-c", "make"]


def test_seatbelt_runs_the_wrapped_command_through_the_supervisor(monkeypatch):
    pretend(monkeypatch, "darwin")
    asyncio.run(SeatbeltSandbox(OPTIONS).run(template("sh", "-c", "make"), cwd=PROJECT))
    wrapped, _, _ = FakeSupervisor.seen[0]
    assert wrapped.command[:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert wrapped.command[2].startswith("(version 1)") and wrapped.command[-3:] == [
        "sh",
        "-c",
        "make",
    ]


def test_a_backend_says_why_it_cannot_run_here(monkeypatch):
    with pytest.raises(OsSandboxUnavailableError, match="works only on Linux"):
        pretend(monkeypatch, "win32")
        asyncio.run(BubblewrapSandbox().run(template("true"), cwd=PROJECT))
    with pytest.raises(OsSandboxUnavailableError, match="works only on macOS"):
        asyncio.run(SeatbeltSandbox().run(template("true"), cwd=PROJECT))
    pretend(monkeypatch, "linux", binary=False)
    with pytest.raises(OsSandboxUnavailableError, match='"bwrap"\\) was not found on PATH'):
        asyncio.run(BubblewrapSandbox().run(template("true"), cwd=PROJECT))
    pretend(monkeypatch, "darwin", binary=False)
    with pytest.raises(OsSandboxUnavailableError, match='"sandbox-exec"\\) was not found'):
        asyncio.run(SeatbeltSandbox().run(template("true"), cwd=PROJECT))


def test_default_os_sandbox_picks_the_tool_for_the_platform_or_nothing(monkeypatch):
    pretend(monkeypatch, "linux")
    assert isinstance(default_os_sandbox(), BubblewrapSandbox)
    pretend(monkeypatch, "darwin")
    assert isinstance(default_os_sandbox(), SeatbeltSandbox)
    pretend(monkeypatch, "win32")
    assert default_os_sandbox() is None
    pretend(monkeypatch, "linux", binary=False)
    assert default_os_sandbox() is None


def shell(command, timeout=30):
    return CommandTemplate(name="shell", command=["sh", "-c", command], timeout_seconds=timeout)


def run_in(sandbox, command, project):
    return asyncio.run(sandbox.run(shell(command), cwd=project))


@pytest.fixture
def real_sandbox(tmp_path):
    if BubblewrapSandbox.available():
        probe = asyncio.run(
            BubblewrapSandbox(OsSandboxOptions(hide_paths=[])).run(shell("true"), cwd=tmp_path)
        )
        if not probe.successful:
            pytest.skip(f"bubblewrap is installed but cannot start here: {probe.output[:200]}")
        return BubblewrapSandbox(OsSandboxOptions())
    if SeatbeltSandbox.available():
        return SeatbeltSandbox(OsSandboxOptions())
    pytest.skip("neither Linux bubblewrap nor macOS sandbox-exec is available here")


@pytest.mark.os_sandbox
def test_a_real_sandbox_lets_the_project_be_written_but_not_the_rest(real_sandbox, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ok = run_in(real_sandbox, "echo hi > inside.txt && cat inside.txt", project)
    assert ok.successful and "hi" in ok.output and (project / "inside.txt").exists()
    outside = tmp_path / "outside.txt"
    blocked = run_in(real_sandbox, f"echo no > {outside}", project)
    assert not blocked.successful and not outside.exists()


@pytest.mark.os_sandbox
def test_a_real_sandbox_has_no_network(real_sandbox, tmp_path):
    probe = (
        f"{shutil.which('python3') or sys.executable} -c "
        "\"import socket; socket.create_connection(('1.1.1.1', 80), timeout=3)\""
    )
    result = run_in(real_sandbox, probe, tmp_path)
    assert not result.successful


@pytest.mark.os_sandbox
def test_a_real_sandbox_hides_the_home_directory(real_sandbox, tmp_path):
    secret = Path.home() / ".nailong-sandbox-probe"
    secret.write_text("secret", encoding="utf-8")
    try:
        project = tmp_path / "p"
        project.mkdir()
        result = run_in(real_sandbox, f"cat {secret}", project)
        assert "secret" not in result.output and not result.successful
    finally:
        secret.unlink(missing_ok=True)
    assert os.name == "posix"
