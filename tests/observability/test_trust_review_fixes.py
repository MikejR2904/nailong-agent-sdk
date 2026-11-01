import hashlib
import socket
import threading
import time
from datetime import timedelta

import pytest

pytest.importorskip("cryptography")

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from nailong_agent_sdk.agent.evidence_profile import (
    EvidenceProfile,
    EvidenceSettings,
    RecorderSettings,
)
from nailong_agent_sdk.agent.model import ScriptedModel
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability import recorder as recorder_module
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.keyring import KeyRing
from nailong_agent_sdk.observability.recorder import (
    RecorderClient,
    RecorderCore,
    RecorderMirror,
    RecorderServer,
)
from nailong_agent_sdk.observability.run_checkpoints import (
    FileCheckpointSink,
    export_run_head_checkpoint,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.observability.timestamp import (
    MAX_RESPONSE_BYTES,
    Rfc3161Authority,
    parse_token,
)
from tests.support.agents import FnExecutor, arun, definition, final, ok, task, tool, tool_call
from tests.support.fake_tsa import NOW, FakeTsa, certificate, name

DIGEST = hashlib.sha256(b"evidence").digest()
DAY = timedelta(days=1)


def chained_authority(*, ca_flag, path_length=None, key_cert_sign=True, intermediate_hash=None):
    authority = FakeTsa()
    key = ec.generate_private_key(ec.SECP256R1())
    builder = (
        x509.CertificateBuilder()
        .subject_name(name("Intermediate"))
        .issuer_name(authority.root.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - DAY)
        .not_valid_after(NOW + 300 * DAY)
        .add_extension(x509.BasicConstraints(ca=ca_flag, path_length=path_length), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=key_cert_sign,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    intermediate = builder.sign(authority.root_key, intermediate_hash or hashes.SHA256())
    authority.trust_anchor = authority.root
    authority.leaf = certificate(
        name("TSA Under Intermediate"),
        intermediate.subject,
        authority.key,
        key,
        ca=False,
        eku=True,
        valid_from=NOW - DAY,
        valid_to=NOW + 300 * DAY,
    )
    authority.root = intermediate
    return authority


def verify_chain(authority):
    return parse_token(authority.stamp(DIGEST), DIGEST, [authority.trust_anchor])


def test_a_ca_intermediate_between_the_root_and_the_authority_is_accepted():
    authority = chained_authority(ca_flag=True)
    assert verify_chain(authority).signer_subject == "CN=TSA Under Intermediate"


def test_an_end_entity_certificate_cannot_issue_an_authority_certificate():
    authority = chained_authority(ca_flag=False)
    with pytest.raises(AgentSdkError) as failure:
        verify_chain(authority)
    assert failure.value.code == "TIMESTAMP_CERTIFICATE_INVALID"
    assert "its basicConstraints say it is not a certificate authority" in failure.value.message
    assert "Intermediate" in failure.value.message


def test_an_intermediate_whose_key_usage_forbids_signing_certificates_is_refused():
    authority = chained_authority(ca_flag=True, key_cert_sign=False)
    with pytest.raises(AgentSdkError) as failure:
        verify_chain(authority)
    assert "key usage does not allow signing certificates" in failure.value.message


def test_an_intermediate_with_a_path_length_of_zero_still_serves_a_leaf_directly_below_it():
    authority = chained_authority(ca_flag=True, path_length=0)
    assert verify_chain(authority).signer_subject == "CN=TSA Under Intermediate"


def test_an_ed25519_trusted_root_no_longer_crashes_the_check():
    key = ed25519.Ed25519PrivateKey.generate()
    authority = FakeTsa()
    root = (
        x509.CertificateBuilder()
        .subject_name(name("Ed Root"))
        .issuer_name(name("Ed Root"))
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(NOW - DAY)
        .not_valid_after(NOW + 30 * DAY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, None)
    )
    authority.leaf = certificate(
        name("TSA"),
        root.subject,
        authority.key,
        key,
        ca=False,
        eku=True,
        valid_from=NOW - DAY,
        valid_to=NOW + 30 * DAY,
    )
    authority.root = root
    assert parse_token(authority.stamp(DIGEST), DIGEST, [root]).signer_subject == "CN=TSA"


def test_an_authority_that_answers_with_more_than_a_token_could_need_is_refused():
    class Flood(FakeTsa):
        def respond(self, request):
            return b"\x00" * (MAX_RESPONSE_BYTES + 10)

    flood = Flood()
    server = flood.serve()
    try:
        with pytest.raises(AgentSdkError) as failure:
            Rfc3161Authority(f"http://127.0.0.1:{server.server_port}").stamp(DIGEST)
        assert failure.value.code == "TIMESTAMP_RESPONSE_TOO_LARGE"
    finally:
        server.shutdown()
        server.server_close()


def test_a_checkpoint_is_not_written_when_its_fresh_stamp_does_not_verify_against_the_roots(
    tmp_path,
):
    services = AgentRuntimeServices.open(tmp_path / "run")
    try:
        subject = services.create_agent(
            definition(tools=[tool("echo")], max_iterations=5),
            ScriptedModel([tool_call("c0", "echo", {"i": 0}), final()]),
            require_tool_authorization=False,
            tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments})),
        )
        arun(subject.run(task("run-a")))
        sink = FileCheckpointSink(tmp_path / "elsewhere")
        with pytest.raises(AgentSdkError) as failure:
            export_run_head_checkpoint(
                services.telemetry,
                services.audit_logs,
                "run-a",
                Ed25519Signer.generate(),
                sink,
                timestamper=FakeTsa(),
                trusted_roots=[FakeTsa().root],
            )
        assert failure.value.code == "TIMESTAMP_RESPONSE_INVALID"
        assert sink.read_all("run-a") == []
    finally:
        services.audit_logs.close()
        services.telemetry.close()


APPEND, READ = "append-token-aaaa", "read-token-bbbb"


def serve(core, port=0):
    server = RecorderServer(core, append_token=APPEND, read_token=READ, port=port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_required_recorder_keeps_refusing_records_after_its_first_failure(tmp_path):
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    server = serve(core)
    port = server.port
    client = RecorderClient("127.0.0.1", port, append_token=APPEND, timeout_seconds=1)
    mirror = RecorderMirror(client, required=True)
    audit = AuditTranscriptStore(tmp_path / "run")
    telemetry = TelemetryStore(tmp_path / "run")
    mirror.attach(telemetry, audit)
    try:
        audit.append("r1", "one", {})
        server.shutdown()
        server.server_close()
        client.close()
        codes = []
        for number in (2, 3, 4):
            with pytest.raises(AgentSdkError) as failure:
                audit.append("r1", f"e{number}", {})
            codes.append(failure.value.code)
        assert codes == ["RECORDER_UNREACHABLE", "RECORDER_DEGRADED", "RECORDER_DEGRADED"]
        assert "backfill()" in failure.value.message

        revived = serve(core, port)
        try:
            sent = mirror.backfill(telemetry, audit, "r1")
            assert sent["audit"] == audit.entry_count("r1")
            assert len(core.entries("r1", "audit")) == audit.entry_count("r1")
            audit.append("r1", "after", {})
            assert len(core.entries("r1", "audit")) == audit.entry_count("r1")
        finally:
            revived.shutdown()
            revived.server_close()
    finally:
        audit.close()
        telemetry.close()


def test_an_optional_recorder_still_degrades_quietly(tmp_path):
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    server = serve(core)
    client = RecorderClient("127.0.0.1", server.port, append_token=APPEND, timeout_seconds=1)
    mirror = RecorderMirror(client, required=False)
    audit = AuditTranscriptStore(tmp_path / "run")
    mirror.attach(audit=audit)
    try:
        audit.append("r1", "one", {})
        server.shutdown()
        server.server_close()
        client.close()
        audit.append("r1", "two", {})
        audit.append("r1", "three", {})
        assert audit.entry_count("r1") == 3 and ("r1", "audit") in mirror.stats.degraded
    finally:
        audit.close()


def test_a_run_adopted_after_it_began_is_caught_up_by_backfill(tmp_path):
    audit = AuditTranscriptStore(tmp_path / "run")
    telemetry = TelemetryStore(tmp_path / "run")
    audit.append("r1", "before-1", {})
    audit.append("r1", "before-2", {})
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    server = serve(core)
    mirror = RecorderMirror(
        RecorderClient("127.0.0.1", server.port, append_token=APPEND), required=True
    )
    mirror.attach(telemetry, audit)
    try:
        with pytest.raises(AgentSdkError) as gap:
            audit.append("r1", "after", {})
        assert gap.value.code == "RECORDER_GAP"
        mirror.backfill(telemetry, audit, "r1")
        assert len(core.entries("r1", "audit")) == audit.entry_count("r1")
    finally:
        server.shutdown()
        server.server_close()
        audit.close()
        telemetry.close()


def test_a_connection_that_sends_nothing_is_closed_after_the_idle_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder_module._Handler, "timeout", 0.3)
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("r"))
    server = serve(core)
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as stalled:
            started = time.monotonic()
            assert stalled.recv(10) == b""
            assert time.monotonic() - started < 4
    finally:
        server.shutdown()
        server.server_close()


def profile_settings(tmp_path, **recorder):
    key = tmp_path / "w.key"
    key.write_text(Ed25519Signer.generate("ops").private_key_hex(), encoding="utf-8")
    return key, EvidenceSettings(
        signing_key_file=key,
        signer_id="ops",
        checkpoint_directory=tmp_path / "elsewhere",
        recorder=RecorderSettings(
            host=recorder.get("host", "127.0.0.1"),
            port=7000,
            append_token_variable="A",
            allow_remote_plaintext=recorder.get("allow", False),
        ),
    )


def test_a_recorder_on_another_host_is_refused_unless_plaintext_is_accepted_in_writing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("A", "t" * 12)
    _, remote = profile_settings(tmp_path, host="10.1.2.3")
    with pytest.raises(ValueError, match="not a loopback address and no tls_ca_file"):
        EvidenceProfile(remote)
    _, allowed = profile_settings(tmp_path, host="10.1.2.3", allow=True)
    assert EvidenceProfile(allowed).mirror() is not None


def test_a_profile_whose_ring_does_not_accept_its_own_signing_key_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("A", "t" * 12)
    key, settings = profile_settings(tmp_path)
    stranger = KeyRing.of(Ed25519Signer.generate("someone-else"))
    ring = tmp_path / "ring.json"
    stranger.write(ring)
    broken = settings.model_copy(update={"keyring_file": ring})
    with pytest.raises(ValueError, match='signs as "ops" but its key ring does not accept'):
        EvidenceProfile(broken)


def test_running_a_task_through_the_profile_seals_the_recorders_heads_at_checkpoint_time(
    tmp_path, monkeypatch
):
    core = RecorderCore(tmp_path / "rec", Ed25519Signer.generate("recorder"), head_every=1000)
    server = serve(core)
    monkeypatch.setenv("A", APPEND)
    key, settings = profile_settings(tmp_path)
    settings = settings.model_copy(
        update={
            "recorder": settings.recorder.model_copy(update={"port": server.port}),
            "require_recorder": True,
        }
    )
    evidence = EvidenceProfile(settings)
    services = AgentRuntimeServices.open(tmp_path / "run", evidence=evidence)
    try:
        subject = services.create_agent(
            definition(tools=[tool("echo")], max_iterations=5),
            ScriptedModel([tool_call("c0", "echo", {"i": 0}), final()]),
            require_tool_authorization=False,
            tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments})),
        )
        arun(subject.run(task("run-a")))
        assert core.head_sink().read_all("run-a.audit.merkle") == []
        assert services.checkpoint_run("run-a") is not None
        newest = core.head_sink().read_all("run-a.audit.merkle")[-1]
        assert f'"size":{len(core.entries("run-a", "audit"))}' in newest.replace(" ", "")
    finally:
        services.audit_logs.close()
        services.telemetry.close()
        server.shutdown()
        server.server_close()


def test_a_truncated_audit_payload_keeps_who_and_what_and_digests_what_it_dropped(tmp_path):
    store = AuditTranscriptStore(tmp_path)
    big = "x" * 20_000
    entry = store.append(
        "r1",
        "tool-result",
        {"tool": "write_artifact", "tool_call_id": "c1", "arguments": {"content": big}, "n": 7},
    )
    payload = entry.payload
    assert payload["truncated"] is True and len(payload["preview"]) == 8192
    assert payload["tool"] == "write_artifact" and payload["tool_call_id"] == "c1"
    assert payload["n"] == 7
    omitted = payload["omitted_fields"]["arguments"]
    assert omitted["chars"] > 20_000 and len(omitted["sha256"]) == 64
    assert "omitted_fields" in payload and "arguments" not in payload
    store.close()


def test_a_tool_request_is_recorded_with_a_digest_of_its_arguments_before_it_runs(tmp_path):
    from nailong_agent_sdk.foundations.jcs import jcs_hash

    services = AgentRuntimeServices.open(tmp_path / "run")
    try:
        subject = services.create_agent(
            definition(tools=[tool("echo")], max_iterations=5),
            ScriptedModel([tool_call("c0", "echo", {"i": 0, "name": "café"}), final()]),
            require_tool_authorization=False,
            tool_executor=FnExecutor(lambda t, c: ok({"echo": c.call.arguments})),
        )
        arun(subject.run(task("run-a")))
        entries = list(services.audit_logs.iter_entries("run-a"))
        names = [item.event_type for item in entries]
        requested = next(item for item in entries if item.event_type == "tool-requested")
        assert names.index("tool-requested") < names.index("tool-result")
        assert requested.payload["details"]["arguments_sha256"] == jcs_hash(
            {"i": 0, "name": "café"}
        )
    finally:
        services.audit_logs.close()
        services.telemetry.close()


@pytest.mark.parametrize("run_id", ["r x", "r x", "r\x85x", "r\x0bx", "r\x1cx"])
def test_a_run_id_with_an_exotic_line_separator_still_checkpoints_and_verifies(tmp_path, run_id):
    from nailong_agent_sdk.observability.run_checkpoints import (
        CheckpointVerdict,
        HmacSha256Signer,
        verify_run_head_checkpoints,
    )
    from nailong_agent_sdk.observability.telemetry_models import (
        TelemetryActor,
        TelemetryAuthority,
        TelemetryContext,
    )

    signer = HmacSha256Signer(b"k" * 32)
    telemetry = TelemetryStore(tmp_path / "run")
    audit = AuditTranscriptStore(tmp_path / "run")
    try:
        telemetry.emit(
            "x",
            TelemetryContext(run_id=run_id),
            actor=TelemetryActor(kind="t", identifier="t"),
            authority=TelemetryAuthority.SYSTEM,
            status="ok",
        )
        audit.append(run_id, "e", {})
        sink = FileCheckpointSink(tmp_path / "elsewhere")
        export_run_head_checkpoint(telemetry, audit, run_id, signer, sink)
        result = verify_run_head_checkpoints(telemetry, audit, run_id, sink, signer)
        assert result.verdict is CheckpointVerdict.MATCHES
    finally:
        telemetry.close()
        audit.close()
