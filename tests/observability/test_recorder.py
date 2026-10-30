import json
import sqlite3
import threading

import pytest

pytest.importorskip("cryptography")

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.foundations.hashing import canonical_hash
from nailong_agent_sdk.observability.audit_log import AuditLogEntry, AuditTranscriptStore
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.recorder import (
    RecorderClient,
    RecorderCore,
    RecorderMirror,
    RecorderServer,
    RecorderVerdict,
    compare_with_recorder,
    spawn_recorder,
)
from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.observability.transparency import (
    TreeHeadVerdict,
    tree_head_name,
    verify_tree_heads,
)
from tests.support.agents import agent, arun, definition, final, task, tool_call
from tests.support.base_agent_cases import ECHO, echo_executor

APPEND, READ = "append-token-aaaa", "read-token-bbbb"
HASH = [f"{index:064x}" for index in range(1, 40)]


@pytest.fixture
def signer():
    return Ed25519Signer.generate("recorder")


@pytest.fixture
def core(tmp_path, signer):
    return RecorderCore(tmp_path / "recorder", signer, head_every=4)


@pytest.fixture
def served(core):
    server = RecorderServer(core, append_token=APPEND, read_token=READ)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def writer(server, **options):
    return RecorderClient("127.0.0.1", server.port, append_token=APPEND, **options)


def verifier_client(server):
    return RecorderClient("127.0.0.1", server.port, append_token=APPEND, read_token=READ)


def chain(core, run="r1", store="telemetry", count=6):
    previous = None
    for index in range(count):
        core.append(run, store, index + 1, HASH[index], previous, f"record {index + 1}")
        previous = HASH[index]


def test_a_chained_stream_is_held_and_its_heads_verify_with_the_public_key(core, signer):
    chain(core, count=9)
    assert [item.sequence for item in core.entries("r1", "telemetry")] == list(range(1, 10))
    head_sink = core.head_sink()
    name = tree_head_name("r1", "telemetry")
    verified = verify_tree_heads(HASH[:9], head_sink, name, signer.verifier())
    assert verified.verdict is TreeHeadVerdict.ADVANCED and verified.published_size == 8
    core.seal("r1")
    assert verify_tree_heads(HASH[:9], head_sink, name, signer.verifier()).ok


def test_a_repeated_record_is_idempotent_and_a_different_one_for_the_same_place_is_a_conflict(core):
    chain(core)
    assert core.append("r1", "telemetry", 3, HASH[2], HASH[1], None)["duplicate"] is True
    with pytest.raises(AgentSdkError) as conflict:
        core.append("r1", "telemetry", 3, "f" * 64, HASH[1], None)
    assert conflict.value.code == "RECORDER_CONFLICT"
    assert "rewritten after the record was sent" in str(conflict.value)
    line = json.loads((core._directory / "conflicts.jsonl").read_text("utf-8").splitlines()[0])
    assert line["sequence"] == 3 and line["held"] == HASH[2] and line["offered"] == "f" * 64
    assert len(core.entries("r1", "telemetry")) == 6


def test_a_gap_a_broken_chain_a_bad_hash_and_an_unknown_ledger_are_refused_by_name(core):
    chain(core, count=3)
    cases = [
        (("r1", "telemetry", 5, HASH[4], HASH[3], None), "RECORDER_GAP", "record 4 was never"),
        (
            ("r1", "telemetry", 4, HASH[3], "e" * 64, None),
            "RECORDER_CHAIN_BROKEN",
            "names eeeeeeeeeeee as its predecessor",
        ),
        (("r1", "telemetry", 4, "zz", HASH[2], None), "RECORDER_HASH_INVALID", "hexadecimal"),
        (("r1", "metrics", 1, HASH[0], None, None), "RECORDER_STORE_UNKNOWN", "known:"),
    ]
    for arguments, code, text in cases:
        with pytest.raises(AgentSdkError) as refused:
            core.append(*arguments)
        assert refused.value.code == code and text in str(refused.value)
    assert len(core.entries("r1", "telemetry")) == 3


def test_the_recorder_reloads_what_it_held_and_keeps_signing_the_same_stream(tmp_path, signer):
    first = RecorderCore(tmp_path / "recorder", signer, head_every=4)
    chain(first, count=5)
    second = RecorderCore(tmp_path / "recorder", signer, head_every=4)
    assert len(second.entries("r1", "telemetry")) == 5
    second.append("r1", "telemetry", 6, HASH[5], HASH[4], None)
    second.seal("r1")
    assert verify_tree_heads(HASH[:6], second.head_sink(), "r1.telemetry.merkle", signer).ok


def test_an_over_the_wire_append_needs_the_append_token_and_reading_needs_another(served):
    client = writer(served)
    client.append("r1", "audit", 1, HASH[0], None, "first")
    with pytest.raises(AgentSdkError) as unread:
        client.entries("r1", "audit")
    assert unread.value.code == "RECORDER_READ_TOKEN_MISSING"
    assert [item.record for item in verifier_client(served).entries("r1", "audit")] == ["first"]
    bad = RecorderClient("127.0.0.1", served.port, append_token="wrong")
    with pytest.raises(AgentSdkError) as refused:
        bad.append("r1", "audit", 2, HASH[1], HASH[0], None)
    assert refused.value.code == "RECORDER_TOKEN_REFUSED" and "append token" in str(refused.value)
    wrong_read = RecorderClient("127.0.0.1", served.port, append_token=APPEND, read_token=APPEND)
    with pytest.raises(AgentSdkError, match="the read token is required for read"):
        wrong_read.entries("r1", "audit")


def test_an_unreachable_recorder_is_named(served):
    client = writer(served)
    served.shutdown()
    served.server_close()
    client.close()
    with pytest.raises(AgentSdkError) as failed:
        client.append("r1", "audit", 1, HASH[0], None, None)
    assert failed.value.code == "RECORDER_UNREACHABLE" and str(served.port) in str(failed.value)


def open_ledgers(root):
    return TelemetryStore(root), AuditTranscriptStore(root)


def close_ledgers(*stores):
    for item in stores:
        item.close()


def run_agent(telemetry, audit, run_id="r1"):
    subject, _ = agent(
        [tool_call("c1", "echo", {"text": "a"}), tool_call("c2", "echo", {"text": "b"}), final()],
        telemetry=telemetry,
        audit_logs=audit,
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
    )
    return arun(subject.run(task(run_id)))


def mirrored_run(tmp_path, served, **options):
    telemetry, audit = open_ledgers(tmp_path / "service")
    mirror = RecorderMirror(writer(served), **options)
    mirror.attach(telemetry, audit)
    run_agent(telemetry, audit)
    mirror.finish("r1")
    return telemetry, audit, mirror


def test_a_mirrored_run_agrees_with_the_recorder_record_for_record(tmp_path, served, signer):
    telemetry, audit, mirror = mirrored_run(tmp_path, served)
    try:
        results = compare_with_recorder(
            telemetry, audit, "r1", verifier_client(served), signer.verifier()
        )
        assert {item.verdict for item in results.values()} == {RecorderVerdict.MATCHES}
        assert results["telemetry"].recorded > 8 and results["audit"].recorded > 4
        assert mirror.stats.failed == 0 and mirror.stats.sent >= 12
    finally:
        close_ledgers(telemetry, audit)


def delete_telemetry_tail(root, count):
    connection = sqlite3.connect(root / ".agent-telemetry" / "telemetry.sqlite3")
    try:
        connection.execute(
            "DELETE FROM events WHERE run_id = 'r1' AND sequence > "
            "(SELECT MAX(sequence) FROM events WHERE run_id = 'r1') - ?",
            (count,),
        )
        connection.commit()
    finally:
        connection.close()


def test_removing_the_newest_events_is_found_and_the_events_can_be_read_back(
    tmp_path, served, signer
):
    telemetry, audit, _ = mirrored_run(tmp_path, served)
    held = len(telemetry.list_events("r1", limit=500))
    close_ledgers(telemetry, audit)
    delete_telemetry_tail(tmp_path / "service", 3)
    telemetry, audit = open_ledgers(tmp_path / "service")
    try:
        assert telemetry.chain_break("r1") is None
        results = compare_with_recorder(
            telemetry, audit, "r1", verifier_client(served), signer.verifier()
        )
        cut = results["telemetry"]
        assert cut.verdict is RecorderVerdict.TRUNCATED and cut.removed_tail == 3
        assert cut.first_difference == held - 2
        assert results["audit"].verdict is RecorderVerdict.MATCHES
        lost = verifier_client(served).entries("r1", "telemetry")[-3:]
        assert [item.sequence for item in lost] == [held - 2, held - 1, held]
        assert all(json.loads(item.record)["sequence"] == item.sequence for item in lost)
    finally:
        close_ledgers(telemetry, audit)


def edit_and_reseal(lines, index):
    sealed = list(lines)
    previous = (
        AuditLogEntry.model_validate_json(sealed[index - 1]).integrity_hash if index else None
    )
    for position in range(index, len(sealed)):
        entry = AuditLogEntry.model_validate_json(sealed[position])
        payload = {**entry.payload, "tampered": True} if position == index else entry.payload
        draft = entry.model_copy(
            update={"payload": payload, "previous_hash": previous, "integrity_hash": ""}
        )
        done = draft.model_copy(
            update={"integrity_hash": canonical_hash(draft.model_dump(mode="json"))}
        )
        sealed[position] = done.model_dump_json()
        previous = done.integrity_hash
    return sealed


def test_an_audit_entry_edited_and_the_chain_resealed_is_found_though_the_chain_verifies(
    tmp_path, served, signer
):
    telemetry, audit, _ = mirrored_run(tmp_path, served)
    close_ledgers(telemetry, audit)
    path = tmp_path / "service" / ".agent-audit-logs" / "r1.jsonl"
    lines = path.read_text("utf-8").splitlines()
    lines = edit_and_reseal(lines, 2)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    telemetry, audit = open_ledgers(tmp_path / "service")
    try:
        assert audit.chain_break("r1") is None
        results = compare_with_recorder(
            telemetry, audit, "r1", verifier_client(served), signer.verifier()
        )
        assert results["audit"].verdict is RecorderVerdict.DIVERGED
        assert results["audit"].first_difference == 3
        assert results["telemetry"].verdict is RecorderVerdict.MATCHES
    finally:
        close_ledgers(telemetry, audit)


def test_a_recorder_whose_own_heads_do_not_verify_is_not_trusted(tmp_path, served):
    telemetry, audit, _ = mirrored_run(tmp_path, served)
    try:
        other = Ed25519Signer.generate("recorder").verifier()
        results = compare_with_recorder(telemetry, audit, "r1", verifier_client(served), other)
        assert {item.verdict for item in results.values()} == {RecorderVerdict.RECORDER_UNVERIFIED}
        assert "signature" in results["telemetry"].detail
    finally:
        close_ledgers(telemetry, audit)


def test_a_run_the_recorder_never_saw_has_no_record(tmp_path, served, signer):
    telemetry, audit = open_ledgers(tmp_path / "service")
    try:
        run_agent(telemetry, audit)
        results = compare_with_recorder(
            telemetry, audit, "r1", verifier_client(served), signer.verifier()
        )
        assert {item.verdict for item in results.values()} == {RecorderVerdict.NO_RECORD}
    finally:
        close_ledgers(telemetry, audit)


def emit(telemetry, run_id="x"):
    return telemetry.emit(
        "bench.event",
        TelemetryContext(run_id=run_id),
        actor=TelemetryActor(kind="system", identifier="t", role="runtime"),
        authority=TelemetryAuthority.DETERMINISTIC,
        status="ok",
    )


def test_a_required_mirror_stops_the_run_when_the_recorder_is_gone(tmp_path, served):
    telemetry = TelemetryStore(tmp_path / "service")
    mirror = RecorderMirror(writer(served), required=True)
    mirror.attach(telemetry)
    emit(telemetry)
    served.shutdown()
    served.server_close()
    mirror._client.close()
    try:
        with pytest.raises(AgentSdkError) as stopped:
            emit(telemetry)
        assert stopped.value.code == "RECORDER_UNREACHABLE"
        assert len(telemetry.list_events("x", limit=10)) == 2
    finally:
        telemetry.close()


def test_a_mirror_that_is_not_required_lets_the_run_finish_and_says_it_stopped_mirroring(
    tmp_path, served
):
    telemetry = TelemetryStore(tmp_path / "service")
    mirror = RecorderMirror(writer(served))
    mirror.attach(telemetry)
    emit(telemetry)
    served.shutdown()
    served.server_close()
    mirror._client.close()
    try:
        emit(telemetry)
        emit(telemetry)
        assert len(telemetry.list_events("x", limit=10)) == 3
        assert mirror.stats.sent == 1 and mirror.stats.failed == 1
        assert ("x", "telemetry") in mirror.stats.degraded
        assert "RECORDER_UNREACHABLE" in mirror.stats.last_error
    finally:
        telemetry.close()


def test_an_asynchronous_mirror_delivers_after_the_run_and_flush(tmp_path, served, signer):
    telemetry, audit, mirror = mirrored_run(tmp_path, served, asynchronous=True)
    try:
        assert mirror.flush(10) and mirror.pending() == 0
        results = compare_with_recorder(
            telemetry, audit, "r1", verifier_client(served), signer.verifier()
        )
        assert {item.verdict for item in results.values()} == {RecorderVerdict.MATCHES}
    finally:
        mirror.close()
        close_ledgers(telemetry, audit)


def test_the_ledger_listener_runs_after_the_record_is_persisted_and_a_failing_one_is_contained(
    tmp_path,
):
    telemetry = TelemetryStore(tmp_path / "service")
    seen = []
    telemetry.add_append_listener(lambda event: seen.append(event.sequence))
    telemetry.add_append_listener(lambda event: 1 / 0)
    try:
        emit(telemetry)
        emit(telemetry)
        assert seen == [1, 2]

        def refuse(event):
            raise RuntimeError("no")

        telemetry.add_append_listener(refuse, required=True)
        with pytest.raises(RuntimeError, match="no"):
            emit(telemetry)
        assert len(telemetry.list_events("x", limit=10)) == 3
    finally:
        telemetry.close()


def test_a_recorder_in_another_process_holds_the_key_and_catches_a_cut_the_agent_made(tmp_path):
    handle = spawn_recorder(tmp_path / "recorder-home", head_every=5)
    try:
        assert handle.alive()
        key_file = tmp_path / "recorder-home" / "recorder.key"
        assert key_file.is_file()
        private = key_file.read_text("utf-8").strip()
        service = tmp_path / "service"
        telemetry, audit = open_ledgers(service)
        mirror = RecorderMirror(handle.client())
        mirror.attach(telemetry, audit)
        run_agent(telemetry, audit)
        mirror.finish("r1")
        close_ledgers(telemetry, audit)
        assert private not in "".join(
            path.read_text("utf-8", errors="ignore")
            for path in service.rglob("*")
            if path.is_file() and path.suffix in {".jsonl", ".json"}
        )
        delete_telemetry_tail(service, 2)
        telemetry, audit = open_ledgers(service)
        try:
            results = compare_with_recorder(
                telemetry,
                audit,
                "r1",
                handle.client(with_read=True),
                handle.verifier(),
            )
            assert results["telemetry"].verdict is RecorderVerdict.TRUNCATED
            assert results["audit"].verdict is RecorderVerdict.MATCHES
        finally:
            close_ledgers(telemetry, audit)
        with pytest.raises(AgentSdkError) as unread:
            handle.client().entries("r1", "telemetry")
        assert unread.value.code == "RECORDER_READ_TOKEN_MISSING"
    finally:
        handle.stop()
    assert not handle.alive()


def test_a_recorder_that_was_killed_keeps_what_it_received(tmp_path):
    handle = spawn_recorder(tmp_path / "recorder-home", head_every=5)
    telemetry = TelemetryStore(tmp_path / "service")
    mirror = RecorderMirror(handle.client())
    mirror.attach(telemetry)
    try:
        for _ in range(7):
            emit(telemetry)
    finally:
        handle.process.kill()
        handle.process.wait(timeout=10)
        emit(telemetry)
        telemetry.close()
        handle.stop()
    assert mirror.stats.sent == 7 and mirror.stats.failed == 1
    reborn = RecorderCore(tmp_path / "recorder-home", handle.verifier(), head_every=5)
    assert len(reborn.entries("x", "telemetry")) == 7
