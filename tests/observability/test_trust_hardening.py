import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.retention import (
    RetentionPolicy,
    RunTombstone,
    pruned_heads_lookup,
    read_tombstones,
    tombstone_hash,
    tombstone_path,
)
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.recorder import RecorderCore
from nailong_agent_sdk.observability.run_checkpoints import (
    CheckpointVerdict,
    FileCheckpointSink,
    HmacSha256Signer,
    checkpoint_path,
    export_run_head_checkpoint,
    verify_run_head_checkpoints,
)
from nailong_agent_sdk.observability.transparency import (
    TreeHeadVerdict,
    prove_extension,
    publish_tree_head,
    verify_extension,
    verify_tree_heads,
)
from nailong_agent_sdk.observability.witness import HttpWitnessSink
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call

HASHES = [f"{index:064x}" for index in range(1, 10)]
SIGNER = HmacSha256Signer(b"hardening-key-0123456789abcdef-1", signer_id="ops")
OTHER_ID = HmacSha256Signer(b"hardening-key-0123456789abcdef-1", signer_id="someone-else")


class Redirecting(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, str | None]] = []

    def log_message(self, format, *args):
        return

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.seen.append(("POST", self.path, self.headers.get("Authorization")))
        self.send_response(302)
        self.send_header("Location", "/landing")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.seen.append(("GET", self.path, self.headers.get("Authorization")))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


def test_a_witness_that_redirects_a_post_is_a_failed_append_and_the_token_is_not_forwarded():
    Redirecting.seen = []
    server = HTTPServer(("127.0.0.1", 0), Redirecting)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        sink = HttpWitnessSink(
            f"http://127.0.0.1:{server.server_port}",
            append_token="append-token-1",
            allow_insecure_http=True,
        )
        with pytest.raises(AgentSdkError) as failure:
            sink.append("run-1", "record")
    finally:
        server.shutdown()
        server.server_close()
    assert failure.value.code == "WITNESS_WRITE_FAILED" and "HTTP 302" in failure.value.message
    assert [item[0] for item in Redirecting.seen] == ["POST"]


@pytest.mark.parametrize("sequence", [0, -1, -7])
def test_the_recorder_names_a_sequence_below_one_instead_of_indexing_with_it(tmp_path, sequence):
    core = RecorderCore(tmp_path, Ed25519Signer.generate("r"))
    core.append("r1", "audit", 1, "ab" * 32, None, None)
    with pytest.raises(AgentSdkError) as failure:
        core.append("r1", "audit", sequence, "cd" * 32, None, None)
    assert failure.value.code == "RECORDER_SEQUENCE_INVALID"
    assert f"offered sequence {sequence}" in failure.value.message
    assert len(core.entries("r1", "audit")) == 1


def recorder_file(tmp_path: Path) -> Path:
    return next((tmp_path / "records").glob("*.jsonl"))


def test_a_recorder_restarts_after_a_write_cut_off_by_a_crash(tmp_path):
    first = RecorderCore(tmp_path, Ed25519Signer.generate("r"))
    first.append("r1", "audit", 1, "ab" * 32, None, None)
    first.append("r1", "audit", 2, "cd" * 32, "ab" * 32, None)
    with recorder_file(tmp_path).open("ab") as handle:
        handle.write(b'{"sequence": 3, "hash": "ef')
    second = RecorderCore(tmp_path, Ed25519Signer.generate("r"))
    assert [item.sequence for item in second.entries("r1", "audit")] == [1, 2]
    second.append("r1", "audit", 3, "ef" * 32, "cd" * 32, None)
    third = RecorderCore(tmp_path, Ed25519Signer.generate("r"))
    assert [item.sequence for item in third.entries("r1", "audit")] == [1, 2, 3]


def test_a_complete_record_without_its_newline_is_kept_and_the_next_one_starts_a_new_line(tmp_path):
    first = RecorderCore(tmp_path, Ed25519Signer.generate("r"))
    first.append("r1", "audit", 1, "ab" * 32, None, None)
    path = recorder_file(tmp_path)
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    second = RecorderCore(tmp_path, Ed25519Signer.generate("r"))
    second.append("r1", "audit", 2, "cd" * 32, "ab" * 32, None)
    assert len(RecorderCore(tmp_path, Ed25519Signer.generate("r")).entries("r1", "audit")) == 2


def test_a_damaged_line_in_the_middle_stops_the_recorder_and_names_the_file_and_line(tmp_path):
    first = RecorderCore(tmp_path, Ed25519Signer.generate("r"))
    first.append("r1", "audit", 1, "ab" * 32, None, None)
    first.append("r1", "audit", 2, "cd" * 32, "ab" * 32, None)
    path = recorder_file(tmp_path)
    lines = path.read_bytes().splitlines()
    path.write_bytes(b"\n".join([lines[0], b"{broken", b""]))
    with pytest.raises(AgentSdkError) as failure:
        RecorderCore(tmp_path, Ed25519Signer.generate("r"))
    assert failure.value.code == "RECORDER_RECORDS_CORRUPT"
    assert "Line 2" in failure.value.message and path.name in failure.value.message


def test_a_record_file_with_a_skipped_sequence_stops_the_recorder(tmp_path):
    first = RecorderCore(tmp_path, Ed25519Signer.generate("r"))
    first.append("r1", "audit", 1, "ab" * 32, None, None)
    first.append("r1", "audit", 2, "cd" * 32, "ab" * 32, None)
    path = recorder_file(tmp_path)
    path.write_bytes(path.read_bytes().splitlines()[1] + b"\n")
    with pytest.raises(AgentSdkError) as failure:
        RecorderCore(tmp_path, Ed25519Signer.generate("r"))
    assert failure.value.code == "RECORDER_RECORDS_CORRUPT"
    assert "holds sequence 2 but sequence 1 was expected" in failure.value.message


def test_a_validly_signed_head_of_another_ledger_is_misbound(tmp_path):
    sink = FileCheckpointSink(tmp_path / "elsewhere")
    publish_tree_head(HASHES, sink, "other.audit.merkle", SIGNER)
    for line in sink.read_all("other.audit.merkle"):
        sink.append("r1.audit.merkle", line)
    result = verify_tree_heads(HASHES, sink, "r1.audit.merkle", SIGNER)
    assert result.verdict is TreeHeadVerdict.MISBOUND and not result.ok
    assert '"other.audit.merkle" instead of "r1.audit.merkle"' in result.detail


def test_a_head_that_names_another_signer_is_misbound(tmp_path):
    sink = FileCheckpointSink(tmp_path / "elsewhere")
    publish_tree_head(HASHES, sink, "r1.audit.merkle", SIGNER)
    result = verify_tree_heads(HASHES, sink, "r1.audit.merkle", OTHER_ID)
    assert result.verdict in {TreeHeadVerdict.SIGNATURE_INVALID, TreeHeadVerdict.MISBOUND}
    assert not result.ok


def test_an_extension_between_heads_of_two_different_ledgers_is_refused(tmp_path):
    sink = FileCheckpointSink(tmp_path / "elsewhere")
    older = publish_tree_head(HASHES[:4], sink, "r1.audit.merkle", SIGNER)
    newer = publish_tree_head(HASHES, sink, "r2.audit.merkle", SIGNER)
    proof = prove_extension(HASHES, 4, 9)
    check = verify_extension(older, newer, proof, SIGNER)
    assert not check.ok and "an extension is only defined within one ledger" in check.detail


POLICY = RetentionPolicy(older_than_seconds=3_600)


def later():
    from datetime import UTC, datetime, timedelta

    return datetime.now(UTC) + timedelta(days=2)


def run_task(services, task_id):
    subject = services.create_agent(
        definition(tools=[tool("echo")], max_iterations=5),
        ScriptedModel([tool_call(f"{task_id}-c0", "echo", {"i": 0}), final()]),
        require_tool_authorization=False,
        tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments})),
    )
    assert arun(subject.run(task(task_id))).status is AgentRunStatus.COMPLETED


@pytest.fixture
def world(tmp_path):
    services = AgentRuntimeServices.open(tmp_path / "run")
    yield services, tmp_path / "elsewhere"
    services.audit_logs.close()
    services.telemetry.close()


def verdict(services, destination, verifier):
    return verify_run_head_checkpoints(
        services.telemetry,
        services.audit_logs,
        "run-a",
        checkpoint_path(destination, "run-a"),
        SIGNER,
        pruned_lookup=pruned_heads_lookup(services.run_root, verifier),
    )


def prune_with(services, signer):
    from nailong_agent_sdk.agent.retention import RunRetention

    report = RunRetention(
        services.run_root,
        telemetry=services.telemetry,
        audit_logs=services.audit_logs,
        result_journal=services.result_journal,
        signer=signer,
    ).prune(POLICY, dry_run=False, now=later())
    assert report.runs


def checkpointed(services, destination):
    return export_run_head_checkpoint(
        services.telemetry, services.audit_logs, "run-a", SIGNER, destination
    )


def test_an_unsigned_tombstone_is_accepted_but_the_verdict_says_nobody_vouched_for_it(world):
    services, destination = world
    run_task(services, "run-a")
    checkpointed(services, destination)
    prune_with(services, None)
    result = verdict(services, destination, None)
    assert result.verdict is CheckpointVerdict.PRUNED and result.ok
    assert result.tombstone_signed is False
    assert "only the retention log's own word" in result.detail


def test_a_tombstone_signed_at_prune_time_is_verified_when_a_verifier_is_given(world):
    services, destination = world
    run_task(services, "run-a")
    checkpointed(services, destination)
    prune_with(services, SIGNER)
    [stone] = read_tombstones(services.run_root)
    assert stone.signer_id == "ops" and stone.signature
    result = verdict(services, destination, SIGNER)
    assert result.verdict is CheckpointVerdict.PRUNED and result.tombstone_signed is True
    assert "signature was verified" in result.detail


def forge(services, destination):
    checkpoint = export_run_head_checkpoint(
        services.telemetry, services.audit_logs, "run-a", SIGNER, destination
    )
    from datetime import UTC, datetime, timedelta

    from nailong_agent_sdk.memory.context_projection import JournalFootprint
    from nailong_agent_sdk.observability.audit_log import AuditFootprint
    from nailong_agent_sdk.observability.telemetry_models import TelemetryFootprint

    services.audit_logs.close()
    services.telemetry.close()
    import shutil

    shutil.rmtree(services.run_root)
    services.run_root.mkdir()
    reopened = AgentRuntimeServices.open(services.run_root)
    draft = RunTombstone(
        sequence=1,
        run_id="run-a",
        pruned_at_utc=(datetime.now(UTC) + timedelta(seconds=5)).isoformat(),
        last_event_at=datetime.now(UTC).isoformat(),
        policy=POLICY,
        telemetry=TelemetryFootprint(
            run_id="run-a",
            event_count=checkpoint.telemetry_sequence,
            metric_count=0,
            bytes=1,
            first_sequence=1,
            last_sequence=checkpoint.telemetry_sequence,
            head_hash=checkpoint.telemetry_head,
        ),
        audit=AuditFootprint(
            run_id="run-a",
            entries=checkpoint.audit_sequence,
            bytes=1,
            head_hash=checkpoint.audit_head,
        ),
        journal=JournalFootprint(run_id="run-a"),
    )
    forged = draft.model_copy(update={"integrity_hash": tombstone_hash(draft)})
    path = tombstone_path(reopened.run_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(forged.model_dump(mode="json"), sort_keys=True) + "\n", "utf-8")
    return reopened


def test_a_run_deleted_behind_a_forged_unsigned_tombstone_is_truncated_when_signatures_are_required(
    world,
):
    services, destination = world
    run_task(services, "run-a")
    reopened = forge(services, destination)
    try:
        accepted = verdict(reopened, destination, None)
        assert accepted.verdict is CheckpointVerdict.PRUNED and accepted.tombstone_signed is False
        required = verdict(reopened, destination, SIGNER)
        assert required.verdict is CheckpointVerdict.TRUNCATED and not required.ok
        assert "carries no signature" in required.detail
    finally:
        reopened.audit_logs.close()
        reopened.telemetry.close()


def test_a_tombstone_signed_by_another_key_does_not_prove_a_prune(world):
    services, destination = world
    run_task(services, "run-a")
    checkpointed(services, destination)
    prune_with(services, HmacSha256Signer(b"a-different-key-0123456789abcdef-9!", signer_id="ops"))
    result = verdict(services, destination, SIGNER)
    assert result.verdict is CheckpointVerdict.TRUNCATED and not result.ok
    assert "fails its signature" in result.detail


def test_signing_leaves_the_hash_of_an_older_unsigned_tombstone_unchanged(world):
    services, destination = world
    run_task(services, "run-a")
    prune_with(services, None)
    [stone] = read_tombstones(services.run_root)
    assert stone.signer_id is None and stone.signature == ""
    assert tombstone_hash(stone) == stone.integrity_hash
    signed = stone.model_copy(update={"signer_id": "ops", "signature": "00"})
    assert tombstone_hash(signed) == stone.integrity_hash
