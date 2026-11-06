import threading
import time

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.recorder.mirror import RecorderMirror


class Client:
    def __init__(self, *, fail=False, delay=0.0, gate=None):
        self.sent = []
        self.fail = fail
        self.delay = delay
        self.gate = gate

    def append(self, run_id, store, sequence, record_hash, previous, record=None):
        if self.gate is not None:
            self.gate.wait(10)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise AgentSdkError("RECORDER_UNREACHABLE", "the recorder is down")
        self.sent.append((run_id, store, sequence))

    def close(self):
        return None


def send(mirror, sequence, run="run-1"):
    mirror._send(run, "audit", sequence, f"{sequence:064x}", None, None)


def test_delivered_records_are_acknowledged_and_nothing_is_left_to_resend(tmp_path):
    client = Client()
    mirror = RecorderMirror(client, outbox=tmp_path / "outbox.jsonl")
    send(mirror, 1)
    send(mirror, 2)
    assert client.sent == [("run-1", "audit", 1), ("run-1", "audit", 2)]
    assert mirror.unacknowledged() == 0


def test_records_a_down_recorder_refused_are_resent_by_a_restarted_process(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    down = RecorderMirror(Client(fail=True), outbox=outbox)
    send(down, 1)
    send(down, 2)
    assert down.unacknowledged() == 2 and down.stats.failed == 1
    restarted_client = Client()
    restarted = RecorderMirror(restarted_client, outbox=outbox)
    assert restarted.unacknowledged() == 2
    assert restarted.recover() == {"sent": 2, "failed": 0}
    assert restarted_client.sent == [("run-1", "audit", 1), ("run-1", "audit", 2)]
    assert restarted.unacknowledged() == 0 and outbox.read_text(encoding="utf-8") == ""


def test_a_recover_that_still_cannot_reach_the_recorder_keeps_everything(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    send(RecorderMirror(Client(fail=True), outbox=outbox), 1)
    still_down = RecorderMirror(Client(fail=True), outbox=outbox)
    assert still_down.recover() == {"sent": 0, "failed": 1}
    assert still_down.unacknowledged() == 1


def test_a_process_killed_with_records_queued_loses_none_of_them(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    gate = threading.Event()
    stuck = RecorderMirror(Client(gate=gate), asynchronous=True, outbox=outbox)
    for sequence in (1, 2, 3):
        send(stuck, sequence)
    assert stuck.unacknowledged() == 3
    survivor_client = Client()
    survivor = RecorderMirror(survivor_client, outbox=outbox)
    assert survivor.recover()["sent"] == 3
    assert sorted(survivor_client.sent) == [("run-1", "audit", n) for n in (1, 2, 3)]
    gate.set()
    stuck.close()


def test_flush_waits_for_the_record_the_worker_is_still_sending(tmp_path):
    client = Client(delay=0.3)
    mirror = RecorderMirror(client, asynchronous=True)
    send(mirror, 1)
    time.sleep(0.05)
    assert mirror.flush(5) is True
    assert client.sent == [("run-1", "audit", 1)]
    mirror.close()


def test_a_record_cut_off_by_a_crash_is_ignored_but_damage_elsewhere_is_named(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    send(RecorderMirror(Client(fail=True), outbox=outbox), 1)
    with outbox.open("a", encoding="utf-8") as handle:
        handle.write('{"id":2,"run":"run-1","sto')
    assert RecorderMirror(Client(), outbox=outbox).unacknowledged() == 1
    outbox.write_text('{"id":1\n{"ack":1}\n', encoding="utf-8")
    with pytest.raises(AgentSdkError) as damaged:
        RecorderMirror(Client(), outbox=outbox)
    assert damaged.value.code == "RECORDER_OUTBOX_CORRUPT" and "Line 1" in damaged.value.message


def test_records_written_by_a_process_that_was_killed_are_all_sent_by_the_next_one(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    outbox = tmp_path / "outbox.jsonl"
    script = Path(__file__).parents[1] / "support" / "outbox_writer.py"
    child = subprocess.Popen(
        [sys.executable, str(script), str(outbox)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "ready"
    finally:
        child.kill()
        child.wait()
    client = Client()
    survivor = RecorderMirror(client, outbox=outbox)
    assert survivor.unacknowledged() == 3
    assert survivor.recover() == {"sent": 3, "failed": 0}
    assert sorted(client.sent) == [("run-k", "audit", n) for n in (1, 2, 3)]


def test_health_says_ok_behind_or_degraded_and_whether_delivery_is_durable(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    healthy = RecorderMirror(Client(), outbox=outbox)
    send(healthy, 1)
    assert healthy.health() == {
        "state": "ok", "sent": 1, "failed": 0, "queued": 0,
        "unacknowledged": 0, "degraded": [], "last_error": None, "durable": True,
    }  # fmt: skip
    down = RecorderMirror(Client(fail=True), outbox=tmp_path / "down.jsonl")
    send(down, 1)
    report = down.health()
    assert report["state"] == "degraded" and report["degraded"] == ["run-1:audit"]
    assert report["unacknowledged"] == 1 and "RECORDER_UNREACHABLE" in report["last_error"]
    assert RecorderMirror(Client()).health()["durable"] is False
