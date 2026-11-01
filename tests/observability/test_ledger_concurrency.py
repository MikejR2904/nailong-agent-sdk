import threading
import time

import pytest

pytest.importorskip("cryptography")

from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.recorder import (
    RecorderClient,
    RecorderCore,
    RecorderMirror,
    RecorderServer,
)
from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore

APPEND, READ = "append-token-aaaa", "read-token-bbbb"
DELAY = 0.04


class SlowCore(RecorderCore):
    def append(self, *arguments, **keywords):
        time.sleep(DELAY)
        return super().append(*arguments, **keywords)


def serve(core):
    server = RecorderServer(core, append_token=APPEND, read_token=READ)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def slow_recorder(tmp_path):
    core = SlowCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    server = serve(core)
    client = RecorderClient("127.0.0.1", server.port, append_token=APPEND, read_token=READ)
    telemetry = TelemetryStore(tmp_path / "run")
    audit = AuditTranscriptStore(tmp_path / "run")
    RecorderMirror(client, required=True).attach(telemetry, audit)
    yield core, telemetry, audit
    client.close()
    audit.close()
    telemetry.close()
    server.shutdown()
    server.server_close()


def run_in_threads(targets):
    errors = []

    def guarded(target):
        try:
            target()
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=guarded, args=(target,)) for target in targets]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads), "a thread is stuck: deadlock"
    return time.perf_counter() - started, errors


def emit(telemetry, run_id):
    telemetry.emit(
        "demo.event",
        TelemetryContext(run_id=run_id),
        actor=TelemetryActor(kind="t", identifier="t"),
        authority=TelemetryAuthority.SYSTEM,
        status="ok",
    )


def test_audit_runs_do_not_wait_for_each_others_trip_to_a_slow_recorder(slow_recorder):
    core, _, audit = slow_recorder
    runs, per_run = 6, 8
    elapsed, errors = run_in_threads(
        [
            lambda run=f"run-{n}": [audit.append(run, "e", {"i": i}) for i in range(per_run)]
            for n in range(runs)
        ]
    )
    assert errors == []
    assert elapsed < 0.6 * runs * per_run * DELAY
    for n in range(runs):
        assert len(core.entries(f"run-{n}", "audit")) == per_run


def test_telemetry_runs_do_not_wait_for_each_others_trip_to_a_slow_recorder(slow_recorder):
    core, telemetry, _ = slow_recorder
    runs, per_run = 6, 8
    elapsed, errors = run_in_threads(
        [lambda run=f"run-{n}": [emit(telemetry, run) for _ in range(per_run)] for n in range(runs)]
    )
    assert errors == []
    assert elapsed < 0.6 * runs * per_run * DELAY
    for n in range(runs):
        assert len(core.entries(f"run-{n}", "telemetry")) == per_run


def test_several_threads_writing_one_run_reach_the_recorder_in_order_without_a_gap(slow_recorder):
    core, telemetry, audit = slow_recorder
    elapsed, errors = run_in_threads(
        [
            lambda: [audit.append("shared", "a", {}) for _ in range(12)],
            lambda: [audit.append("shared", "b", {}) for _ in range(12)],
            lambda: [emit(telemetry, "shared") for _ in range(12)],
            lambda: [emit(telemetry, "shared") for _ in range(12)],
        ]
    )
    assert errors == []
    for store in ("audit", "telemetry"):
        held = core.entries("shared", store, include_records=False)
        assert [item.sequence for item in held] == list(range(1, 25))
        assert [item.previous for item in held[1:]] == [item.hash for item in held[:-1]]
    assert audit.chain_break("shared") is None and telemetry.chain_break("shared") is None


def test_pruning_and_quiescing_while_other_runs_append_never_deadlocks(tmp_path):
    audit = AuditTranscriptStore(tmp_path)
    stop = threading.Event()

    def appender(run):
        def work():
            for index in range(150):
                audit.append(run, "e", {"i": index})

        return work

    def housekeeping():
        while not stop.is_set():
            audit.append("victim", "e", {})
            audit.quiesce("run-0")
            audit.footprint("run-1")
            audit.prune_run("victim")

    helper = threading.Thread(target=housekeeping, daemon=True)
    helper.start()
    elapsed, errors = run_in_threads([appender(f"run-{n}") for n in range(6)])
    stop.set()
    helper.join(timeout=30)
    assert not helper.is_alive() and errors == []
    for n in range(6):
        assert audit.entry_count(f"run-{n}") == 150 and audit.chain_break(f"run-{n}") is None
    audit.close()
