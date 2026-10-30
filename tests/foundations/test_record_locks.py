import subprocess
import sys
import threading
import time

import pytest

from nailong_agent_sdk.foundations.atomic_io import file_fingerprint
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.foundations.record_locks import RecordLocks
from tests.support.processes import child_environment

HOLDER = """
import sys
import time
from pathlib import Path

from nailong_agent_sdk.foundations.record_locks import RecordLocks

locks = RecordLocks(Path(sys.argv[1]), timeout_code="HOLDER_LOCK_TIMEOUT")
with locks.hold(sys.argv[2]):
    print("locked", flush=True)
    time.sleep(600)
"""


def locks_in(tmp_path, **options):
    return RecordLocks(tmp_path / ".locks", timeout_code="TEST_LOCK_TIMEOUT", **options)


def test_a_record_lock_excludes_other_processes_until_the_holder_is_killed(tmp_path):
    directory = tmp_path / ".locks"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(directory), "run-1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_environment(),
    )
    try:
        assert holder.stdout.readline().strip() == "locked", holder.stderr.read()
        locks = locks_in(tmp_path, timeout_seconds=0.3)
        with pytest.raises(AgentSdkError) as raised, locks.hold("run-1"):
            pass
        assert raised.value.code == "TEST_LOCK_TIMEOUT"
        assert '"run-1.lock"' in str(raised.value)
        with locks.hold("run-2"):
            pass
        holder.kill()
        holder.wait(timeout=30)
        started = time.monotonic()
        with locks_in(tmp_path, timeout_seconds=10).hold("run-1"):
            pass
        assert time.monotonic() - started < 5
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=30)


def test_the_thread_holding_a_record_lock_may_take_it_again_and_other_threads_wait(tmp_path):
    locks = locks_in(tmp_path, timeout_seconds=0.3)
    outcome = {}

    def other_thread():
        try:
            with locks.hold("run-1"):
                outcome["acquired"] = True
        except AgentSdkError as error:
            outcome["code"] = error.code

    with locks.hold("run-1"):
        with locks.hold("run-1"):
            worker = threading.Thread(target=other_thread)
            worker.start()
            worker.join(timeout=30)
        assert outcome == {"code": "TEST_LOCK_TIMEOUT"}
    with locks.hold("run-1"):
        pass


def test_two_lock_objects_over_one_directory_share_one_lock_per_thread(tmp_path):
    first, second = locks_in(tmp_path), locks_in(tmp_path)
    with first.hold("run-1"), second.hold("run-1"):
        pass


def test_a_record_id_never_leaves_the_lock_directory(tmp_path):
    locks = locks_in(tmp_path)
    with locks.hold("../../outside"):
        pass
    created = sorted(path.relative_to(tmp_path).parts[0] for path in tmp_path.rglob("*.lock"))
    assert created and set(created) == {".locks"}
    assert not (tmp_path.parent / "outside.lock").exists()


def test_a_fingerprint_changes_when_a_file_is_replaced_and_is_none_when_absent(tmp_path):
    target = tmp_path / "record.json"
    assert file_fingerprint(target) is None
    target.write_text("one", "utf-8")
    first = file_fingerprint(target)
    replacement = tmp_path / "next.json"
    replacement.write_text("two!", "utf-8")
    replacement.replace(target)
    assert first is not None and file_fingerprint(target) not in {None, first}
