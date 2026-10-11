import errno
import subprocess
import sys
import time
import types

import pytest

from nailong_agent_sdk.foundations import atomic_io
from nailong_agent_sdk.foundations.atomic_io import exclusive_file_lock
from nailong_agent_sdk.foundations.errors import AgentSdkError
from tests.support.processes import child_environment

LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8


def fake_fcntl(busy_attempts):
    calls = []

    def flock(descriptor, operation):
        calls.append((descriptor, operation))
        if len(calls) <= busy_attempts and operation & LOCK_NB:
            raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")

    module = types.SimpleNamespace(LOCK_EX=LOCK_EX, LOCK_NB=LOCK_NB, LOCK_UN=LOCK_UN, flock=flock)
    return module, calls


def test_a_held_lock_times_out_with_the_code_the_caller_chose(tmp_path):
    path = tmp_path / "state.lock"
    with exclusive_file_lock(path):
        started = time.monotonic()
        with (
            pytest.raises(AgentSdkError) as raised,
            exclusive_file_lock(path, timeout_seconds=0.2, timeout_code="STATE_LOCK_TIMEOUT"),
        ):
            pass
        waited = time.monotonic() - started
    assert raised.value.code == "STATE_LOCK_TIMEOUT"
    assert '"state.lock"' in str(raised.value) and "0.2s" in str(raised.value)
    assert raised.value.details == {"lock_path": str(path), "timeout_seconds": 0.2}
    assert 0.15 <= waited < 10
    with exclusive_file_lock(path, timeout_seconds=1):
        pass


def test_the_posix_lock_polls_without_blocking_until_it_is_free(tmp_path, monkeypatch):
    module, calls = fake_fcntl(busy_attempts=3)
    monkeypatch.setitem(sys.modules, "fcntl", module)
    atomic_io._acquire_posix_lock(7, tmp_path / "x.lock", 5.0, "X_LOCK_TIMEOUT")
    assert calls == [(7, LOCK_EX | LOCK_NB)] * 4


def test_the_posix_lock_gives_up_with_the_callers_code_naming_the_file(tmp_path, monkeypatch):
    module, calls = fake_fcntl(busy_attempts=10**9)
    monkeypatch.setitem(sys.modules, "fcntl", module)
    path = tmp_path / "x.lock"
    started = time.monotonic()
    with pytest.raises(AgentSdkError) as raised:
        atomic_io._acquire_posix_lock(7, path, 0.05, "X_LOCK_TIMEOUT")
    assert time.monotonic() - started >= 0.05
    assert raised.value.code == "X_LOCK_TIMEOUT"
    assert '"x.lock"' in str(raised.value) and "0.05s" in str(raised.value)
    assert raised.value.details == {"lock_path": str(path), "timeout_seconds": 0.05}
    assert isinstance(raised.value.__cause__, BlockingIOError)
    assert len(calls) >= 2


def test_the_posix_lock_is_released_with_unlock(monkeypatch):
    module, calls = fake_fcntl(busy_attempts=0)
    monkeypatch.setitem(sys.modules, "fcntl", module)
    atomic_io._release_posix_lock(7)
    assert calls == [(7, LOCK_UN)]


HOLDER = """
import sys
import time
from pathlib import Path

from nailong_agent_sdk.foundations.atomic_io import exclusive_file_lock

with exclusive_file_lock(Path(sys.argv[1]), timeout_seconds=10):
    print("locked", flush=True)
    time.sleep(600)
"""


def test_a_lock_held_by_another_process_is_released_when_that_process_is_killed(tmp_path):
    path = tmp_path / "held.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_environment(),
    )
    try:
        assert holder.stdout.readline().strip() == "locked", holder.stderr.read()
        with (
            pytest.raises(AgentSdkError) as raised,
            exclusive_file_lock(path, timeout_seconds=0.3, timeout_code="HELD_LOCK_TIMEOUT"),
        ):
            pass
        assert raised.value.code == "HELD_LOCK_TIMEOUT"
        holder.kill()
        holder.wait(timeout=30)
        started = time.monotonic()
        with exclusive_file_lock(path, timeout_seconds=10):
            pass
        assert time.monotonic() - started < 5
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=30)
