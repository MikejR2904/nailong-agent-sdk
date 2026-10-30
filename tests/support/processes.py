import os
import subprocess
import sys
import textwrap

from tests.support.paths import REPO_ROOT, SRC_ROOT


def child_environment(extra=None):
    paths = [str(SRC_ROOT), str(REPO_ROOT)]
    inherited = os.environ.get("PYTHONPATH")
    if inherited:
        paths.append(inherited)
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}
    if extra:
        environment.update(extra)
    return environment


def run_python(code, *, timeout=120, env_extra=None):
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=child_environment(env_extra),
        cwd=str(REPO_ROOT),
    )


RENDEZVOUS = """
import time as _time
from pathlib import Path as _Path


def rendezvous(directory, worker, peers, timeout=120.0):
    directory = _Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"ready-{worker}").write_text("ready", "utf-8")
    deadline = _time.monotonic() + timeout
    while len(list(directory.glob("ready-*"))) < peers:
        if _time.monotonic() > deadline:
            raise TimeoutError(f"worker {worker} waited {timeout:g}s for {peers} peers")
        _time.sleep(0.005)
"""


def meeting(directory, peers):
    return [str(directory / "ready"), str(peers)]


def run_workers(script, count, *args, timeout=240):
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent(script), str(index), *args],
            env=child_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(count)
    ]
    outputs = []
    for process in processes:
        out, err = process.communicate(timeout=timeout)
        outputs.append((process.returncode, out, err))
    return outputs


def process_alive(pid):
    if os.name == "nt":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        return str(pid) in listing
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def kill_process_tree(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    try:
        os.kill(pid, 9)
    except OSError:
        return
