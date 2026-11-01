from datetime import UTC, datetime, timedelta

import pytest

from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.clock_check import find_time_regressions
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.keyring import KeyRing, VerificationKey
from nailong_agent_sdk.observability.recorder import (
    RecorderCore,
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
from tests.support.recorder_cases import (
    attested_profile,
    run_with_profile,
    serve,
    stop,
)
from tests.tools.test_artifact_history_integrity import written


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
