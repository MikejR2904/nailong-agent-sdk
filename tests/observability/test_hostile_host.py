import threading
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("cryptography")

from nailong_agent_sdk.agent.evidence_profile import (
    EvidenceProfile,
    EvidenceSettings,
    RecorderSettings,
)
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.attestation import (
    AttestationVerdict,
    RunAttestation,
    verify_attestations,
)
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.clock_check import find_time_regressions
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.keyring import KeyRing, VerificationKey
from nailong_agent_sdk.observability.recorder import (
    RecorderClient,
    RecorderCore,
    RecorderMirror,
    RecorderServer,
    client_ssl_context,
    scoped_append_token,
    server_ssl_context,
)
from nailong_agent_sdk.observability.run_checkpoints import FileCheckpointSink, HmacSha256Signer
from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
    TelemetryEvent,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.tools.artifact_anchor import (
    ArtifactAnchorVerdict,
    anchor_artifact_log,
    verify_artifact_anchor,
)
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call
from tests.support.pki import make_pki
from tests.tools.test_artifact_history_integrity import written

APPEND, READ = "append-token-aaaa", "read-token-bbbb"


def serve(core, **options):
    server = RecorderServer(core, append_token=APPEND, read_token=READ, **options)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def stop(server):
    server.shutdown()
    server.server_close()


def real_entry(root, run_id="r1", count=2):
    audit = AuditTranscriptStore(root)
    entries = [audit.append(run_id, f"e{index}", {"i": index}) for index in range(count)]
    return audit, entries


def test_a_recorder_that_verifies_records_refuses_text_that_does_not_match_the_hash(tmp_path):
    audit, entries = real_entry(tmp_path / "run")
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"), verify_records=True)
    first = entries[0]
    try:
        core.append("r1", "audit", 1, first.integrity_hash, None, first.model_dump_json())
        second = entries[1]
        forged_text = second.model_copy(update={"payload": {"i": 99}}).model_dump_json()
        with pytest.raises(AgentSdkError) as failure:
            core.append("r1", "audit", 2, second.integrity_hash, first.integrity_hash, forged_text)
        assert failure.value.code == "RECORDER_RECORD_INVALID"
        assert "integrity hash" in failure.value.message
        with pytest.raises(AgentSdkError) as wrong_run:
            core.append("other", "audit", 1, first.integrity_hash, None, first.model_dump_json())
        assert "run id" in wrong_run.value.message
        with pytest.raises(AgentSdkError) as bare:
            core.append("r1", "audit", 2, second.integrity_hash, first.integrity_hash, None)
        assert bare.value.code == "RECORDER_RECORD_REQUIRED"
        with pytest.raises(AgentSdkError) as garbage:
            core.append("r1", "audit", 2, second.integrity_hash, first.integrity_hash, "nonsense")
        assert garbage.value.code == "RECORDER_RECORD_INVALID"
        core.append(
            "r1", "audit", 2, second.integrity_hash, first.integrity_hash, second.model_dump_json()
        )
        assert len(core.entries("r1", "audit")) == 2
    finally:
        audit.close()


def test_the_recorder_stamps_each_record_with_its_own_clock_and_keeps_the_claimed_time(tmp_path):
    audit, entries = real_entry(tmp_path / "run", count=1)
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"), verify_records=True)
    try:
        entry = entries[0]
        before = datetime.now(UTC)
        core.append("r1", "audit", 1, entry.integrity_hash, None, entry.model_dump_json())
        (held,) = core.entries("r1", "audit")
        assert held.claimed_at_utc == entry.occurred_at_utc
        assert before <= datetime.fromisoformat(held.received_at_utc) <= datetime.now(UTC)
        reloaded = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
        assert reloaded.entries("r1", "audit")[0].received_at_utc == held.received_at_utc
    finally:
        audit.close()


def test_the_recorder_signs_what_it_holds_and_a_verifier_with_its_public_key_checks_the_ledger(
    tmp_path,
):
    key = Ed25519Signer.generate("recorder")
    core = RecorderCore(tmp_path / "rec", key, verify_records=True)
    server = serve(core)
    client = RecorderClient("127.0.0.1", server.port, append_token=APPEND, read_token=READ)
    telemetry = TelemetryStore(tmp_path / "run")
    audit = AuditTranscriptStore(tmp_path / "run")
    RecorderMirror(client, required=True).attach(telemetry, audit)
    try:
        for index in range(3):
            audit.append("r1", f"e{index}", {})
        client.seal("r1", final=False)
        lines = client.attestation_lines("r1")
        good = verify_attestations(telemetry, audit, "r1", lines, key.verifier())
        assert good.verdict is AttestationVerdict.MATCHES and not good.final
        assert (
            verify_attestations(
                telemetry, audit, "r1", lines, key.verifier(), require_final=True
            ).verdict
            is AttestationVerdict.NOT_FINAL
        )
        client.seal("r1", final=True)
        sealed = verify_attestations(
            telemetry,
            audit,
            "r1",
            client.attestation_lines("r1"),
            key.verifier(),
            require_final=True,
        )
        assert sealed.verdict is AttestationVerdict.MATCHES and sealed.final
        assert client.status("r1")["final"] is True
        audit.append("r1", "after", {})
        later = verify_attestations(
            telemetry, audit, "r1", client.attestation_lines("r1"), key.verifier()
        )
        assert (
            later.verdict is AttestationVerdict.ADVANCED and "after the final seal" in later.detail
        )
        stranger = Ed25519Signer.generate("recorder")
        forged = verify_attestations(telemetry, audit, "r1", lines, stranger.verifier())
        assert forged.verdict is AttestationVerdict.SIGNATURE_INVALID
        edited = RunAttestation.model_validate_json(lines[-1]).model_copy(
            update={"audit_sequence": 1}
        )
        assert (
            verify_attestations(
                telemetry, audit, "r1", [edited.model_dump_json()], key.verifier()
            ).verdict
            is AttestationVerdict.SIGNATURE_INVALID
        )
    finally:
        client.close()
        audit.close()
        telemetry.close()
        stop(server)


def test_a_host_that_truncates_or_rewrites_its_ledger_after_the_recorder_attested_is_caught(
    tmp_path,
):
    key = Ed25519Signer.generate("recorder")
    core = RecorderCore(tmp_path / "rec", key)
    server = serve(core)
    client = RecorderClient("127.0.0.1", server.port, append_token=APPEND, read_token=READ)
    telemetry = TelemetryStore(tmp_path / "run")
    audit = AuditTranscriptStore(tmp_path / "run")
    RecorderMirror(client, required=True).attach(telemetry, audit)
    try:
        for index in range(4):
            audit.append("r1", f"e{index}", {})
        client.seal("r1", final=True)
        lines = client.attestation_lines("r1")
        audit.close()
        path = tmp_path / "run" / ".agent-audit-logs" / "r1.jsonl"
        original = path.read_text("utf-8")
        path.write_text("".join(original.splitlines(keepends=True)[:2]), encoding="utf-8")
        reopened = AuditTranscriptStore(tmp_path / "run")
        cut = verify_attestations(telemetry, reopened, "r1", lines, key.verifier())
        assert (
            cut.verdict is AttestationVerdict.TRUNCATED
            and "holds 2 of the 4 attested" in cut.detail
        )
        reopened.close()
        path.write_text(original.replace('"e3"', '"x3"'), encoding="utf-8")
        edited = AuditTranscriptStore(tmp_path / "run")
        broken = verify_attestations(telemetry, edited, "r1", lines, key.verifier())
        assert broken.verdict is AttestationVerdict.CHAIN_BROKEN
        edited.close()
    finally:
        client.close()
        telemetry.close()
        stop(server)


def test_a_scoped_token_opens_one_run_and_the_server_secret_itself_opens_none(tmp_path):
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    server = serve(core, scoped_append=True)
    mine = RecorderClient(
        "127.0.0.1", server.port, append_token=lambda run: scoped_append_token(APPEND, run)
    )
    try:
        mine.append("run-a", "audit", 1, "ab" * 32, None)
        leaked = scoped_append_token(APPEND, "run-a")
        thief = RecorderClient("127.0.0.1", server.port, append_token=leaked)
        thief.append("run-a", "audit", 2, "cd" * 32, "ab" * 32)
        with pytest.raises(AgentSdkError) as other_run:
            thief.append("run-b", "audit", 1, "ef" * 32, None)
        assert other_run.value.code == "RECORDER_TOKEN_REFUSED"
        assert "append token for this run" in other_run.value.message
        master = RecorderClient("127.0.0.1", server.port, append_token=APPEND)
        with pytest.raises(AgentSdkError) as admin:
            master.append("run-a", "audit", 3, "01" * 32, "cd" * 32)
        assert admin.value.code == "RECORDER_TOKEN_REFUSED"
        assert core.entries("run-b", "audit") == [] and len(core.entries("run-a", "audit")) == 2
    finally:
        mine.close()
        stop(server)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return make_pki(tmp_path_factory.mktemp("pki"))


def test_a_recorder_over_tls_with_client_certificates_refuses_everyone_else(tmp_path, pki):
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    server = serve(
        core,
        tls=server_ssl_context(pki["server_cert"], pki["server_key"], client_ca_file=pki["ca"]),
    )
    try:
        good = RecorderClient(
            "127.0.0.1",
            server.port,
            append_token=APPEND,
            tls=client_ssl_context(
                pki["ca"], certificate_file=pki["client_cert"], key_file=pki["client_key"]
            ),
            server_hostname="localhost",
        )
        assert good.append("r1", "audit", 1, "ab" * 32, None)["size"] == 1
        good.close()
        anonymous = RecorderClient(
            "127.0.0.1",
            server.port,
            append_token=APPEND,
            tls=client_ssl_context(pki["ca"]),
            server_hostname="localhost",
            timeout_seconds=2,
        )
        with pytest.raises(AgentSdkError) as failure:
            anonymous.append("r1", "audit", 2, "cd" * 32, "ab" * 32)
        assert failure.value.code == "RECORDER_UNREACHABLE"
        plain = RecorderClient("127.0.0.1", server.port, append_token=APPEND, timeout_seconds=2)
        with pytest.raises(AgentSdkError):
            plain.append("r1", "audit", 2, "cd" * 32, "ab" * 32)
        other = make_pki(tmp_path / "other", "other")
        impostor_trust = RecorderClient(
            "127.0.0.1",
            server.port,
            append_token=APPEND,
            tls=client_ssl_context(
                other["ca"], certificate_file=pki["client_cert"], key_file=pki["client_key"]
            ),
            server_hostname="localhost",
            timeout_seconds=2,
        )
        with pytest.raises(AgentSdkError):
            impostor_trust.append("r1", "audit", 2, "cd" * 32, "ab" * 32)
        assert len(core.entries("r1", "audit")) == 1
    finally:
        stop(server)


def test_a_client_refuses_a_server_whose_name_is_not_in_its_certificate(tmp_path, pki):
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    server = serve(core, tls=server_ssl_context(pki["server_cert"], pki["server_key"]))
    try:
        wrong_name = RecorderClient(
            "127.0.0.1",
            server.port,
            append_token=APPEND,
            tls=client_ssl_context(pki["ca"]),
            server_hostname="recorder.example",
            timeout_seconds=2,
        )
        with pytest.raises(AgentSdkError) as failure:
            wrong_name.append("r1", "audit", 1, "ab" * 32, None)
        assert failure.value.code == "RECORDER_UNREACHABLE"
    finally:
        stop(server)


def test_the_clock_check_reports_where_times_run_backwards():
    report = find_time_regressions(
        [
            (1, "2026-01-02T00:00:00+00:00"),
            (2, "2026-01-01T00:00:00+00:00"),
            (3, "2026-01-03T00:00:00+00:00"),
            (4, "not a time"),
            (5, "2026-01-02T23:59:59+00:00"),
        ]
    )
    assert report.checked == 5 and report.unparsable == 1 and report.total_regressions == 2
    assert report.regressions[0].sequence == 2 and report.regressions[0].seconds_back == 86_400
    assert not report.ordered
    assert find_time_regressions([(1, "2026-01-01T00:00:00Z"), (2, "2026-01-01T00:00:00Z")]).ordered
    tolerant = find_time_regressions(
        [(1, "2026-01-01T00:00:10+00:00"), (2, "2026-01-01T00:00:08+00:00")], tolerance_seconds=5
    )
    assert tolerant.ordered


def test_a_telemetry_payload_over_the_bound_keeps_who_and_what_and_its_digest(tmp_path):
    store = TelemetryStore(tmp_path, max_payload_chars=512)
    try:
        event = store.emit(
            "demo.big",
            TelemetryContext(run_id="r1"),
            actor=TelemetryActor(kind="t", identifier="t"),
            authority=TelemetryAuthority.SYSTEM,
            status="ok",
            payload={"tool": "write_artifact", "body": "x" * 5_000},
        )
        assert event.payload["truncated"] is True and event.payload["tool"] == "write_artifact"
        assert event.payload["omitted_fields"]["body"]["chars"] > 5_000
        assert store.chain_break("r1") is None
        with pytest.raises(ValueError, match="at least 256"):
            TelemetryStore(tmp_path / "small", max_payload_chars=10)
    finally:
        store.close()


def test_an_artifact_anchor_signed_before_a_rotation_still_verifies_through_the_ring(tmp_path):
    old, new = Ed25519Signer.generate("old"), Ed25519Signer.generate("new")
    store = written(tmp_path / "ws", 4)
    sink = FileCheckpointSink(tmp_path / "anchors")
    now = datetime.now(UTC)
    anchor_artifact_log(store, sink, "run-1", old, now=now - timedelta(days=2))
    store.write_text("a.txt", "later\n")
    anchor_artifact_log(store, sink, "run-1", new, now=now)
    rotated_at = (now - timedelta(days=1)).isoformat()
    ring = KeyRing.of(old).rotated(
        "old", VerificationKey(key_id="new", public_key=new.public_key_hex), rotated_at
    )
    result = verify_artifact_anchor(store, sink, "run-1", ring.verifier())
    assert result.verdict is ArtifactAnchorVerdict.MATCHES
    revoked = KeyRing.of(old, new).revoked("old", (now - timedelta(days=3)).isoformat())
    bad = verify_artifact_anchor(store, sink, "run-1", revoked.verifier())
    assert bad.verdict is ArtifactAnchorVerdict.SIGNATURE_INVALID and "was revoked" in bad.detail


def run_with_profile(tmp_path, evidence, task_id="run-a", finish=True):
    services = AgentRuntimeServices.open(tmp_path / "run", evidence=evidence)
    subject = services.create_agent(
        definition(tools=[tool("echo")], max_iterations=5),
        ScriptedModel([tool_call("c0", "echo", {"i": 0}), final()]),
        require_tool_authorization=False,
        tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments})),
    )
    assert arun(subject.run(task(task_id))).status is AgentRunStatus.COMPLETED
    if finish:
        assert arun(services.checkpoint_after_run(task_id)) is None
    return services


def attested_profile(tmp_path, monkeypatch, key, port, **settings):
    token_file = tmp_path / "append.token"
    token_file.write_text(APPEND, encoding="utf-8")
    read_file = tmp_path / "read.token"
    read_file.write_text(READ, encoding="utf-8")
    public = tmp_path / "recorder.pub"
    public.write_text(key.public_key_hex, encoding="utf-8")
    return EvidenceProfile(
        EvidenceSettings(
            recorder=RecorderSettings(
                host="127.0.0.1",
                port=port,
                append_token_file=token_file,
                read_token_file=read_file,
                scoped_append=True,
                public_key_file=public,
                signer_id="recorder",
            ),
            **settings,
        )
    )


def test_a_profile_with_no_key_on_the_host_relies_on_the_recorders_signature(tmp_path, monkeypatch):
    key = Ed25519Signer.generate("recorder")
    core = RecorderCore(tmp_path / "rec", key, verify_records=True)
    server = serve(core, scoped_append=True)
    evidence = attested_profile(
        tmp_path, monkeypatch, key, server.port, require_final_attestation=True
    )
    assert evidence.checkpointer() is None
    services = run_with_profile(tmp_path, evidence)
    try:
        report = evidence.verify(services, "run-a")
        found = {item.check: item for item in report.findings}
        assert report.ok, [item for item in report.findings if not item.ok]
        assert "sealed as final" in found["recorder-attestation"].detail
        assert found["run-heads"].ok and "attestation is the evidence" in found["run-heads"].detail
        assert found["receipt-times"].ok and found["time-order"].ok
        services.audit_logs.close()
        path = tmp_path / "run" / ".agent-audit-logs" / "run-a.jsonl"
        lines = path.read_text("utf-8").splitlines(keepends=True)
        path.write_text("".join(lines[:-3]), encoding="utf-8")
        cut = AuditTranscriptStore(tmp_path / "run")
        failed = {
            item.check: item
            for item in evidence.verify_stores(
                tmp_path / "run", services.telemetry, cut, "run-a"
            ).findings
        }
        assert not failed["recorder"].ok and not failed["recorder-attestation"].ok
        assert "truncated" in failed["recorder-attestation"].detail
        cut.close()
    finally:
        services.telemetry.close()
        stop(server)


def test_a_host_that_never_closes_the_run_with_the_recorder_fails_a_profile_that_requires_it(
    tmp_path, monkeypatch
):
    key = Ed25519Signer.generate("recorder")
    core = RecorderCore(tmp_path / "rec", key)
    server = serve(core, scoped_append=True)
    evidence = attested_profile(
        tmp_path, monkeypatch, key, server.port, require_final_attestation=True
    )
    services = run_with_profile(tmp_path, evidence, finish=False)
    try:
        services.recorder_mirror.finish("run-a", final=False)
        report = evidence.verify(services, "run-a")
        found = {item.check: item for item in report.findings}
        assert not report.ok and not found["recorder-attestation"].ok
        assert "never received the host's closing seal" in found["recorder-attestation"].detail
    finally:
        services.audit_logs.close()
        services.telemetry.close()
        stop(server)


def test_a_backdated_event_is_reported_and_fails_a_profile_that_requires_ordered_time(
    tmp_path, monkeypatch
):
    key = Ed25519Signer.generate("recorder")
    core = RecorderCore(tmp_path / "rec", key)
    server = serve(core, scoped_append=True)
    plain = attested_profile(tmp_path, monkeypatch, key, server.port)
    services = run_with_profile(tmp_path, plain)
    try:
        services.telemetry.append(
            TelemetryEvent(
                event_type="x",
                occurred_at_utc="2020-01-01T00:00:00+00:00",
                occurred_at_monotonic_ns=1,
                context=TelemetryContext(run_id="run-a"),
                actor=TelemetryActor(kind="t", identifier="t"),
                authority=TelemetryAuthority.SYSTEM,
                status="ok",
            )
        )
        found = {item.check: item for item in plain.verify(services, "run-a").findings}
        assert found["time-order"].ok and "reported, not required" in found["time-order"].detail
        strict = attested_profile(
            tmp_path, monkeypatch, key, server.port, require_ordered_time=True
        )
        strict_found = {item.check: item for item in strict.verify(services, "run-a").findings}
        assert not strict_found["time-order"].ok
        assert "regression(s), first at sequence" in strict_found["time-order"].detail
    finally:
        services.audit_logs.close()
        services.telemetry.close()
        stop(server)


def test_a_record_held_back_before_it_reached_the_recorder_is_reported(tmp_path, monkeypatch):
    key = Ed25519Signer.generate("recorder")
    core = RecorderCore(tmp_path / "rec", key)
    server = serve(core, scoped_append=True)
    evidence = attested_profile(
        tmp_path, monkeypatch, key, server.port, max_receipt_delay_seconds=30
    )
    services = AgentRuntimeServices.open(tmp_path / "run", evidence=evidence)
    try:
        stale = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        services.telemetry.append(
            TelemetryEvent(
                event_type="held-back",
                occurred_at_utc=stale,
                occurred_at_monotonic_ns=1,
                context=TelemetryContext(run_id="run-b"),
                actor=TelemetryActor(kind="t", identifier="t"),
                authority=TelemetryAuthority.SYSTEM,
                status="ok",
            )
        )
        services.recorder_mirror.finish("run-b", final=True)
        found = {item.check: item for item in evidence.verify(services, "run-b").findings}
        assert not found["receipt-times"].ok
        assert "more than 30s after they claim" in found["receipt-times"].detail
    finally:
        services.audit_logs.close()
        services.telemetry.close()
        stop(server)


def test_hmac_checkpoint_signers_still_work_alongside_the_recorder_mode(tmp_path):
    signer = HmacSha256Signer(b"k" * 32, signer_id="legacy")
    assert signer.verify(b"m", signer.sign(b"m"))


def test_records_a_host_wrote_but_never_sent_fail_a_profile_that_requires_a_final_attestation(
    tmp_path, monkeypatch
):
    key = Ed25519Signer.generate("recorder")
    core = RecorderCore(tmp_path / "rec", key)
    server = serve(core, scoped_append=True)
    evidence = attested_profile(
        tmp_path, monkeypatch, key, server.port, require_final_attestation=True
    )
    services = run_with_profile(tmp_path, evidence)
    try:
        assert evidence.verify(services, "run-a").ok
        services.audit_logs._append_listeners = type(services.audit_logs._append_listeners)("audit")
        services.audit_logs.append("run-a", "quietly-written", {})
        found = {item.check: item for item in evidence.verify(services, "run-a").findings}
        assert not found["recorder"].ok and "never received" in found["recorder"].detail
        assert not found["recorder-attestation"].ok
    finally:
        services.audit_logs.close()
        services.telemetry.close()
        stop(server)
