import glob
import os
import shutil
import subprocess
import tempfile
import textwrap
import time

import pytest

from nailong_agent_sdk.tools.sandbox import DockerSandbox
from nailong_agent_sdk.tools.sandbox_models import DockerSandboxOptions, EnvironmentPolicy
from nailong_agent_sdk.tools.supervisor import CommandTemplate, ProcessExitKind
from tests.support.tools import run

pytestmark = pytest.mark.docker

IMAGE = os.environ.get("NAILONG_DOCKER_TEST_IMAGE", "python:3.13-alpine")
CONTAINER_PREFIX = "nailong-sandbox-"


def docker(*arguments, timeout=60):
    return subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=timeout)


def leftover_containers(wait_seconds=20):
    deadline = time.monotonic() + wait_seconds
    while True:
        listing = docker(
            "ps", "-a", "--filter", f"name={CONTAINER_PREFIX}", "--format", "{{.Names}}"
        )
        names = listing.stdout.split()
        if not names or time.monotonic() >= deadline:
            return names
        time.sleep(0.5)


def env_files():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "nailong-sandbox-env-*.env")))


def python_template(name, code, *, timeout=60):
    return CommandTemplate(
        name=name,
        command=["python", "-c", textwrap.dedent(code)],
        timeout_seconds=timeout,
    )


@pytest.fixture(scope="module")
def image():
    if shutil.which("docker") is None:
        pytest.skip("the docker binary is not on PATH")
    try:
        info = docker("info", "--format", "{{.ServerVersion}}", timeout=30)
    except subprocess.TimeoutExpired:
        pytest.skip("the docker daemon did not answer within 30 seconds")
    if info.returncode != 0:
        pytest.skip(f"no reachable docker daemon: {info.stderr.strip()[:200]}")
    if docker("image", "inspect", IMAGE).returncode != 0:
        if os.environ.get("NAILONG_DOCKER_TESTS_PULL") != "1":
            pytest.skip(
                f'image "{IMAGE}" is not present; set NAILONG_DOCKER_TESTS_PULL=1 to pull it'
            )
        pulled = docker("pull", IMAGE, timeout=900)
        assert pulled.returncode == 0, pulled.stderr
    return IMAGE


def test_a_command_runs_in_an_ephemeral_container_that_is_removed_afterwards(tmp_path, image):
    sandbox = DockerSandbox(DockerSandboxOptions(image=image))
    record = run(
        sandbox.run(python_template("hello", "print('hello from the container')"), cwd=tmp_path),
        timeout=180,
    )
    assert record.successful and record.output.strip() == "hello from the container"
    assert record.exit_kind is ProcessExitKind.SUCCEEDED
    assert leftover_containers() == []


def test_the_working_directory_is_the_only_host_path_the_container_can_write(tmp_path, image):
    sandbox = DockerSandbox(DockerSandboxOptions(image=image))
    code = """
        open("made-in-container.txt", "w").write("written inside")
        print(open("/workspace/made-in-container.txt").read())
    """
    record = run(sandbox.run(python_template("write", code), cwd=tmp_path), timeout=180)
    assert record.successful and record.output.strip() == "written inside"
    assert (tmp_path / "made-in-container.txt").read_text(encoding="utf-8") == "written inside"


def test_the_container_has_no_network_and_a_read_only_root_except_tmp(tmp_path, image):
    sandbox = DockerSandbox(DockerSandboxOptions(image=image))
    code = """
        import socket

        try:
            socket.create_connection(("1.1.1.1", 53), timeout=3)
            print("network-reachable")
        except OSError:
            print("network-blocked")
        try:
            open("/etc/should-not-exist", "w").write("x")
            print("root-writable")
        except OSError:
            print("root-read-only")
        open("/tmp/scratch.txt", "w").write("x")
        print("tmp-writable")
    """
    record = run(sandbox.run(python_template("isolation", code), cwd=tmp_path), timeout=180)
    assert record.output.split() == ["network-blocked", "root-read-only", "tmp-writable"]


def test_only_the_environment_the_policy_names_reaches_the_container(tmp_path, image, monkeypatch):
    monkeypatch.setenv("NAILONG_HOST_ONLY_VARIABLE", "host-secret")
    before = env_files()
    policy = EnvironmentPolicy(
        literal_variables={"SANDBOX_VALUE": "plain-value", "SANDBOX_MULTILINE": "a\nb"}
    )
    code = """
        import os

        for name in ("SANDBOX_VALUE", "SANDBOX_MULTILINE", "NAILONG_HOST_ONLY_VARIABLE"):
            print(name, repr(os.environ.get(name)))
    """
    sandbox = DockerSandbox(DockerSandboxOptions(image=image))
    record = run(
        sandbox.run(python_template("environment", code), cwd=tmp_path, environment=policy),
        timeout=180,
    )
    assert record.successful, record.output
    assert record.output.splitlines() == [
        "SANDBOX_VALUE 'plain-value'",
        "SANDBOX_MULTILINE 'a\\nb'",
        "NAILONG_HOST_ONLY_VARIABLE None",
    ]
    assert env_files() == before


def test_a_timeout_kills_and_removes_the_container(tmp_path, image):
    sandbox = DockerSandbox(DockerSandboxOptions(image=image))
    sleeper = python_template("sleeper", "import time; time.sleep(120)", timeout=3)
    started = time.monotonic()
    record = run(sandbox.run(sleeper, cwd=tmp_path), timeout=180)
    assert record.exit_kind is ProcessExitKind.TIMED_OUT and record.timed_out
    assert "DOCKER_KILL" in record.termination_path
    assert time.monotonic() - started < 60
    assert leftover_containers() == []


def test_a_container_that_exceeds_its_memory_limit_is_reported_as_resource_limited(tmp_path, image):
    options = DockerSandboxOptions(image=image, memory_bytes=64 * 1024 * 1024)
    sandbox = DockerSandbox(options)
    hungry = python_template("hungry", "data = b'x' * 400_000_000; print(len(data))")
    record = run(sandbox.run(hungry, cwd=tmp_path), timeout=180)
    assert record.exit_kind is ProcessExitKind.RESOURCE_LIMIT
    assert record.error_code == "DOCKER_RESOURCE_LIMIT" and record.return_code == 137
    assert record.resource_limits_enforced == ["memory_bytes"]
    assert leftover_containers() == []
