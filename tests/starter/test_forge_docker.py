import asyncio
import os
import shutil
import subprocess

import pytest

from nailong_agent_sdk.starter import Forge
from nailong_agent_sdk.starter.forge_execution import RUNNER
from nailong_agent_sdk.tools.sandbox import DockerSandbox
from nailong_agent_sdk.tools.supervisor import ProcessExecutionRecord, ProcessExitKind

ADD_SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"],
    "additionalProperties": False,
}
ADD = {
    "name": "add",
    "description": "Add.",
    "input_schema": ADD_SCHEMA,
    "source": "def run(a, b):\n    return a + b\n",
    "tests": [{"arguments": {"a": 1, "b": 2}, "expect": 3}],
}


def arun(coro):
    return asyncio.run(coro)


class Recording:
    def __init__(self, result="3"):
        self.calls = []
        self._result = result

    async def run(self, template, *, cwd, environment=None):
        self.calls.append(
            {"template": template, "cwd": cwd, "environment": environment, "existed": cwd.is_dir()}
        )
        output = f"\n@@NAILONG-RESULT@@ {self._result}\n"
        return ProcessExecutionRecord(
            template_name=template.name,
            command=template.command,
            started_at_utc="2026-01-01T00:00:00+00:00",
            ended_at_utc="2026-01-01T00:00:01+00:00",
            duration_ns=1,
            return_code=0,
            exit_kind=ProcessExitKind.SUCCEEDED,
            output=output,
            output_bytes_total=len(output),
            output_bytes_retained=len(output),
            output_truncated_bytes=0,
        )


def test_docker_is_the_default_and_the_container_is_locked_down(tmp_path):
    forge = Forge(tmp_path / "forge")
    assert forge.isolation == "docker" and isinstance(forge._sandbox, DockerSandbox)
    options = forge._sandbox._options
    assert options.image == "python:3.12-slim"
    assert options.network_disabled and options.read_only_root
    assert options.memory_bytes == 256 * 1024 * 1024 and options.cpu_limit == 1.0
    assert options.drop_capabilities and options.no_new_privileges
    assert options.pids_limit == 64 and options.user == "65534:65534"
    assert options.extra_binds == [f"{(tmp_path / 'forge').resolve()}:/forge:ro"]
    assert (tmp_path / "forge" / "runner" / "forge_runner.py").read_text(
        encoding="utf-8"
    ) == RUNNER.read_text(encoding="utf-8")


def test_the_docker_command_line_carries_every_isolation_flag(tmp_path):
    forge = Forge(
        tmp_path / "forge",
        docker_image="python:3.13-alpine",
        memory_bytes=1 << 27,
        reviewer=lambda proposal: True,
    )
    recorder = Recording()
    forge._sandbox = recorder
    arun(forge.propose(**ADD))
    template = recorder.calls[0]["template"]
    real = DockerSandbox(forge_options(forge))
    argv = real._build_argv(template, tmp_path, None, "container-1")
    joined = " ".join(argv)
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--pids-limit 64",
        "--user 65534:65534",
        f"--memory {1 << 27}",
        "--cpus 1.0",
    ):
        assert flag in joined, flag
    assert ":/forge:ro" in joined and "python:3.13-alpine" in argv
    assert argv[argv.index("python:3.13-alpine") + 1 :][:3] == [
        "python",
        "-I",
        "/forge/runner/forge_runner.py",
    ]


def forge_options(forge):
    from nailong_agent_sdk.starter.forge_execution import container_options

    return container_options(forge._directory, "python:3.13-alpine", 1 << 27, 1.0)


def test_tests_and_calls_use_container_paths_a_fresh_directory_and_no_host_environment(tmp_path):
    forge = Forge(tmp_path / "forge", reviewer=lambda proposal: True)
    recorder = Recording()
    forge._sandbox = recorder
    assert arun(forge.propose(**ADD))["status"] == "active"
    assert arun(forge.call("forged_add", {"a": 1, "b": 2})) == 3
    test_call, real_call = recorder.calls
    assert test_call["template"].command == [
        "python",
        "-I",
        "/forge/runner/forge_runner.py",
        "/forge/proposals/p-0001.py",
        '{"a": 1, "b": 2}',
    ]
    assert real_call["template"].command[3] == "/forge/active/forged_add_v1.py"
    for call in recorder.calls:
        assert call["environment"] is None and call["existed"]
        assert call["cwd"].parent == (tmp_path / "forge" / "scratch").resolve()
        assert not call["cwd"].exists()


def test_native_isolation_must_be_asked_for_and_other_values_are_refused(tmp_path):
    native = Forge(tmp_path / "n", isolation="native")
    assert native.isolation == "native" and not isinstance(native._sandbox, DockerSandbox)
    with pytest.raises(ValueError, match='isolation must be "docker" or "native"'):
        Forge(tmp_path / "x", isolation="chroot")


IMAGE = os.environ.get("NAILONG_DOCKER_TEST_IMAGE", "python:3.13-alpine")


@pytest.fixture(scope="module")
def image():
    if shutil.which("docker") is None:
        pytest.skip("the docker binary is not on PATH")
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("the docker daemon did not answer within 30 seconds")
    if info.returncode != 0:
        pytest.skip(f"no reachable docker daemon: {info.stderr.strip()[:200]}")
    inspected = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    if inspected.returncode != 0:
        pytest.skip(
            f"image {IMAGE} is not present locally; pull it or set NAILONG_DOCKER_TEST_IMAGE"
        )
    return IMAGE


@pytest.mark.docker
def test_a_forged_tool_really_runs_inside_the_container(tmp_path, image):
    forge = Forge(
        tmp_path / "forge", docker_image=image, reviewer=lambda p: True, timeout_seconds=60
    )
    assert arun(forge.propose(**ADD))["status"] == "active"
    assert arun(forge.call("forged_add", {"a": 20, "b": 22})) == 42


@pytest.mark.docker
def test_a_forged_tool_cannot_use_the_network_or_write_to_its_own_source(tmp_path, image):
    source = (
        "import socket\n"
        "def run(kind):\n"
        "    if kind == 'network':\n"
        "        socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
        "    if kind == 'write':\n"
        "        open('/forge/proposals/p-0001.py', 'a').write('x')\n"
        "    return 'escaped'\n"
    )
    schema = {
        "type": "object",
        "properties": {"kind": {"type": "string"}},
        "required": ["kind"],
        "additionalProperties": False,
    }
    forge = Forge(
        tmp_path / "forge", docker_image=image, reviewer=lambda p: True, timeout_seconds=60
    )
    for kind in ("network", "write"):
        result = arun(
            forge.propose(
                name=f"probe_{kind}",
                description="Probe.",
                input_schema=schema,
                source=source,
                tests=[{"arguments": {"kind": kind}, "expect": "escaped"}],
            )
        )
        assert result["status"] == "failed-tests", kind
        assert "escaped" not in str(result["failures"])
