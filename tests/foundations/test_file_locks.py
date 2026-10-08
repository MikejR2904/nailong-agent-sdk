import errno
import sys
import time
import types

import pytest

from nailong_agent_sdk.foundations import atomic_io
from nailong_agent_sdk.foundations.atomic_io import exclusive_file_lock
from nailong_agent_sdk.foundations.errors import AgentSdkError

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
