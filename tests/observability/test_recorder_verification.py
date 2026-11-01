from datetime import UTC, datetime

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.attestation import (
    AttestationVerdict,
    RunAttestation,
    verify_attestations,
)
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.recorder import (
    RecorderClient,
    RecorderCore,
    RecorderMirror,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.recorder_cases import (
    APPEND,
    READ,
    real_entry,
    serve,
    stop,
)


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
