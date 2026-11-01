import json
import socket
import threading

import pytest

pytest.importorskip("cryptography")

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.recorder import (
    MAX_LINE_BYTES,
    RECORDER_SCHEMA,
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


def serve(core):
    server = RecorderServer(core, append_token=APPEND, read_token=READ)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def stop(server):
    server.shutdown()
    server.server_close()


@pytest.fixture
def wired(tmp_path):
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    server = serve(core)
    client = RecorderClient("127.0.0.1", server.port, append_token=APPEND, read_token=READ)
    telemetry = TelemetryStore(tmp_path / "run")
    audit = AuditTranscriptStore(tmp_path / "run", max_payload_chars=8_192)
    RecorderMirror(client, required=True).attach(telemetry, audit)
    yield core, client, telemetry, audit
    client.close()
    audit.close()
    telemetry.close()
    stop(server)


def test_a_run_whose_text_is_far_over_one_megabyte_reads_back_in_pages(wired):
    core, client, _, audit = wired
    for index in range(400):
        audit.append("r1", "tool-result", {"i": index, "text": "x" * 7_000})
    with_text = client.entries("r1", "audit")
    assert [item.sequence for item in with_text] == list(range(1, 401))
    assert all(item.record and "x" * 100 in item.record for item in with_text)
    hashes_only = client.entries("r1", "audit", include_records=False)
    assert [item.hash for item in hashes_only] == [item.hash for item in with_text]
    assert all(item.record is None for item in hashes_only)
    assert all(item.received_at_utc and item.claimed_at_utc for item in hashes_only)


def test_a_page_stops_at_its_entry_limit_or_its_byte_budget_and_says_where_to_resume(wired):
    core, _, _, audit = wired
    for index in range(10):
        audit.append("r1", "e", {"i": index, "text": "y" * 3_000})
    first, resume = core.page("r1", "audit", after=0, max_entries=4)
    assert [item.sequence for item in first] == [1, 2, 3, 4] and resume == 4
    last, done = core.page("r1", "audit", after=8, max_entries=4)
    assert [item.sequence for item in last] == [9, 10] and done is None
    budgeted, resume = core.page("r1", "audit", after=0, max_entries=10, max_bytes=9_000)
    assert 1 <= len(budgeted) < 10 and resume == budgeted[-1].sequence
    smallest, resume = core.page("r1", "audit", after=0, max_entries=10, max_bytes=1)
    assert len(smallest) == 1 and resume == 1
    walked, cursor = [], 0
    while cursor is not None:
        page, cursor = core.page("r1", "audit", after=cursor, max_entries=3, max_bytes=7_000)
        walked.extend(page)
    assert [item.sequence for item in walked] == list(range(1, 11))
    assert walked == core.entries("r1", "audit")
    assert core.page("nobody", "audit") == ([], None)


def test_a_page_request_that_cannot_make_sense_is_refused_by_name(wired):
    core, *_ = wired
    with pytest.raises(AgentSdkError) as failure:
        core.page("r1", "audit", after=-1)
    assert failure.value.code == "RECORDER_REQUEST_INVALID" and "after=-1" in failure.value.message


def test_a_record_of_several_megabytes_is_accepted_and_read_back_whole(wired):
    core, client, telemetry, _ = wired
    telemetry.emit(
        "demo.big",
        TelemetryContext(run_id="big"),
        actor=TelemetryActor(kind="t", identifier="t"),
        authority=TelemetryAuthority.SYSTEM,
        status="ok",
        payload={"blob": "z" * 5_000_000},
    )
    (entry,) = client.entries("big", "telemetry")
    assert entry.record is not None and len(entry.record) > 5_000_000
    assert MAX_LINE_BYTES > len(entry.record)


def test_the_text_survives_a_restart_of_the_recorder_without_being_held_in_memory(wired, tmp_path):
    core, _, _, audit = wired
    for index in range(5):
        audit.append("r1", "e", {"i": index})
    held = core._streams[("r1", "audit")].entries
    assert all(item.record is None for item in held)
    reloaded = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    again = reloaded.entries("r1", "audit")
    assert [item.record for item in again] == [item.record for item in core.entries("r1", "audit")]
    assert all(item.record for item in again)


def raw_server(replies):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def run():
        connection, _ = listener.accept()
        reader = connection.makefile("rb")
        for reply in replies:
            reader.readline()
            connection.sendall(reply)
        connection.close()
        listener.close()

    threading.Thread(target=run, daemon=True).start()
    return listener.getsockname()[1]


def greeting(schema=RECORDER_SCHEMA):
    return json.dumps({"ok": True, "schema": schema}).encode() + b"\n"


def test_a_reply_larger_than_the_client_reads_is_a_named_error_not_a_json_error():
    port = raw_server([greeting(), b"x" * (MAX_LINE_BYTES + 100)])
    client = RecorderClient("127.0.0.1", port, append_token=APPEND, read_token=READ)
    with pytest.raises(AgentSdkError) as failure:
        client.entries("r1", "audit")
    assert failure.value.code == "RECORDER_REPLY_TOO_LARGE"
    assert str(MAX_LINE_BYTES) in failure.value.message


def test_a_reply_that_is_not_json_is_a_named_error():
    port = raw_server([greeting(), b"this is not json\n"])
    client = RecorderClient("127.0.0.1", port, append_token=APPEND, read_token=READ)
    with pytest.raises(AgentSdkError) as failure:
        client.entries("r1", "audit")
    assert failure.value.code == "RECORDER_REPLY_INVALID"


def test_a_recorder_of_another_protocol_version_is_refused_on_connect():
    port = raw_server([greeting("agent-sdk-recorder-v1")])
    client = RecorderClient("127.0.0.1", port, append_token=APPEND, read_token=READ)
    with pytest.raises(AgentSdkError) as failure:
        client.append("r1", "audit", 1, "ab" * 32, None)
    assert failure.value.code == "RECORDER_PROTOCOL_MISMATCH"
    assert (
        "agent-sdk-recorder-v1" in failure.value.message
        and RECORDER_SCHEMA in failure.value.message
    )
