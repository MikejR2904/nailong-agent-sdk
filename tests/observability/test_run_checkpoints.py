import json
import sqlite3

import pytest

from nailong_agent_sdk.developer_tools.cli import main
from nailong_agent_sdk.foundations.contracts import AgentRunStatus
from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.foundations.hashing import canonical_hash, canonical_json
from nailong_agent_sdk.foundations.identifiers import file_safe_name
from nailong_agent_sdk.observability.audit_log import AuditLogEntry, AuditTranscriptStore
from nailong_agent_sdk.observability.run_checkpoints import (
    CHECKPOINT_SCHEMA,
    CheckpointVerdict,
    HmacSha256Signer,
    RunHeadCheckpoint,
    TerminalLinkState,
    build_run_head_checkpoint,
    checkpoint_path,
    export_run_head_checkpoint,
    read_checkpoints,
    verify_run_head_checkpoints,
    verify_terminal_link,
)
from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
    TelemetryEvent,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from tests.support.agents import agent, arun, definition, final, task

RUN_ID = "checkpointed-run"
nl = chr(10)
KEY = b"checkpoint-key-0123456789abcdef!"
SIGNER = HmacSha256Signer(KEY, signer_id="ops-hmac")


@pytest.fixture
def recorded(tmp_path):
    run_root = tmp_path / "run"
    telemetry = TelemetryStore(run_root)
    audit = AuditTranscriptStore(run_root)
    subject, _ = agent([final()], definition_=definition(), telemetry=telemetry, audit_logs=audit)
    result = arun(subject.run(task(RUN_ID)))
    assert result.status is AgentRunStatus.COMPLETED
    yield run_root, telemetry, audit, tmp_path / "elsewhere"
    telemetry.close()
    audit.close()


def append_telemetry(telemetry, run_id=RUN_ID):
    return telemetry.emit(
        "agent.after-the-fact",
        TelemetryContext(run_id=run_id),
        actor=TelemetryActor(kind="system", identifier="test"),
        authority=TelemetryAuthority.DETERMINISTIC,
        status="ok",
    )


def truncate_telemetry(telemetry, keep, run_id=RUN_ID):
    connection = sqlite3.connect(telemetry.database_path)
    connection.execute("DELETE FROM events WHERE run_id = ? AND sequence > ?", (run_id, keep))
    connection.commit()
    connection.close()


def audit_file(run_root):
    return run_root / ".agent-audit-logs" / f"{file_safe_name(RUN_ID)}.jsonl"


def truncate_audit(audit, run_root, keep):
    audit.close()
    path = audit_file(run_root)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("".join(f"{line}\n" for line in lines[:keep]), encoding="utf-8")


def rewrite_audit(audit, run_root, sequence, change):
    audit.close()
    path = audit_file(run_root)
    previous = None
    sealed_lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = AuditLogEntry.model_validate_json(line)
        payload = dict(entry.payload)
        if entry.sequence == sequence:
            change(payload)
        draft = entry.model_copy(
            update={"payload": payload, "previous_hash": previous, "integrity_hash": ""}
        )
        sealed = draft.model_copy(
            update={"integrity_hash": canonical_hash(draft.model_dump(mode="json"))}
        )
        previous = sealed.integrity_hash
        sealed_lines.append(sealed.model_dump_json())
    path.write_text("".join(f"{line}\n" for line in sealed_lines), encoding="utf-8")


def rewrite_telemetry(telemetry, sequence, change, run_id=RUN_ID):
    connection = sqlite3.connect(telemetry.database_path)
    rows = connection.execute(
        "SELECT event_json FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
    ).fetchall()
    previous = None
    for (raw,) in rows:
        event = TelemetryEvent.parse_stored(raw)
        payload = dict(event.payload)
        if event.sequence == sequence:
            change(payload)
        draft = event.model_copy(
            update={"payload": payload, "previous_event_hash": previous, "integrity_hash": ""}
        )
        sealed = draft.model_copy(
            update={"integrity_hash": canonical_hash(draft.model_dump(mode="json"))}
        )
        previous = sealed.integrity_hash
        connection.execute(
            "UPDATE events SET event_json = ?, integrity_hash = ?, previous_event_hash = ?, "
            "payload_json = ? WHERE run_id = ? AND sequence = ?",
            (
                sealed.model_dump_json(),
                sealed.integrity_hash,
                sealed.previous_event_hash,
                canonical_json(sealed.payload),
                run_id,
                sealed.sequence,
            ),
        )
    connection.commit()
    connection.close()


def verify(telemetry, audit, destination, signer=SIGNER):
    return verify_run_head_checkpoints(
        telemetry, audit, RUN_ID, checkpoint_path(destination, RUN_ID), signer
    )


def test_an_hmac_signer_refuses_a_short_key_and_never_shows_its_key():
    with pytest.raises(ValueError, match="at least 32 bytes, got 9"):
        HmacSha256Signer(b"too-short")
    with pytest.raises(ValueError, match="signer_id must be 1 to 128"):
        HmacSha256Signer(KEY, signer_id=" ")
    assert KEY.decode() not in repr(SIGNER) and "ops-hmac" in repr(SIGNER)
    signature = SIGNER.sign(b"message")
    assert SIGNER.verify(b"message", signature)
    assert not SIGNER.verify(b"other", signature)
    assert not HmacSha256Signer(b"x" * 32).verify(b"message", signature)


def test_a_checkpoint_names_both_heads_and_verifies_against_the_run(recorded):
    run_root, telemetry, audit, destination = recorded
    checkpoint = export_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER, destination)
    events = list(telemetry.iter_events(RUN_ID))
    entries = list(audit.iter_entries(RUN_ID))
    assert checkpoint.schema_version == CHECKPOINT_SCHEMA and checkpoint.signer_id == "ops-hmac"
    assert checkpoint.telemetry_sequence == events[-1].sequence
    assert checkpoint.telemetry_head == events[-1].integrity_hash
    assert checkpoint.audit_sequence == entries[-1].sequence
    assert checkpoint.audit_head == entries[-1].integrity_hash
    assert read_checkpoints(checkpoint_path(destination, RUN_ID)) == [checkpoint]
    assert run_root not in checkpoint_path(destination, RUN_ID).parents
    verification = verify(telemetry, audit, destination)
    assert verification.verdict is CheckpointVerdict.MATCHES and verification.ok
    assert verification.checkpoints == 1


def test_a_run_that_grew_is_advanced_until_it_is_checkpointed_again(recorded):
    _, telemetry, audit, destination = recorded
    export_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER, destination)
    append_telemetry(telemetry)
    audit.append(RUN_ID, "after-the-fact", {"note": "late"})
    advanced = verify(telemetry, audit, destination)
    assert advanced.verdict is CheckpointVerdict.ADVANCED and advanced.ok
    assert advanced.current_telemetry_sequence == advanced.checkpointed_telemetry_sequence + 1
    export_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER, destination)
    assert verify(telemetry, audit, destination).verdict is CheckpointVerdict.MATCHES
    assert len(read_checkpoints(checkpoint_path(destination, RUN_ID))) == 2


def test_newest_telemetry_events_removed_after_a_checkpoint_are_reported(recorded):
    _, telemetry, audit, destination = recorded
    first = export_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER, destination)
    assert telemetry.chain_break(RUN_ID) is None
    truncate_telemetry(telemetry, first.telemetry_sequence - 2)
    assert telemetry.chain_break(RUN_ID) is None
    truncated = verify(telemetry, audit, destination)
    assert truncated.verdict is CheckpointVerdict.TRUNCATED and not truncated.ok
    assert f"holds {first.telemetry_sequence - 2} of the {first.telemetry_sequence}" in (
        truncated.detail
    )
    before = checkpoint_path(destination, RUN_ID).read_text(encoding="utf-8")
    with pytest.raises(AgentSdkError) as refused:
        export_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER, destination)
    assert refused.value.code == "RUN_CHECKPOINT_MISMATCH"
    assert "truncated" in str(refused.value) and "Nothing was appended" in str(refused.value)
    assert checkpoint_path(destination, RUN_ID).read_text(encoding="utf-8") == before


def test_newest_audit_entries_removed_after_a_checkpoint_are_reported(recorded):
    run_root, telemetry, audit, destination = recorded
    first = export_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER, destination)
    truncate_audit(audit, run_root, first.audit_sequence - 1)
    assert audit.chain_break(RUN_ID) is None
    truncated = verify(telemetry, audit, destination)
    assert truncated.verdict is CheckpointVerdict.TRUNCATED
    assert "the audit chain holds" in truncated.detail


def test_a_run_rewritten_with_a_recomputed_chain_is_reported_as_diverged(recorded):
    run_root, telemetry, audit, destination = recorded
    export_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER, destination)
    rewrite_audit(audit, run_root, 2, lambda payload: payload.update({"forged": True}))
    assert audit.chain_break(RUN_ID) is None
    diverged = verify(telemetry, audit, destination)
    assert diverged.verdict is CheckpointVerdict.DIVERGED
    assert "audit entry" in diverged.detail and "no longer" in diverged.detail


def test_a_telemetry_chain_rewritten_and_resealed_is_reported_as_diverged(recorded):
    _, telemetry, audit, destination = recorded
    export_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER, destination)
    rewrite_telemetry(telemetry, 2, lambda payload: payload.update({"forged": True}))
    assert telemetry.chain_break(RUN_ID) is None
    diverged = verify(telemetry, audit, destination)
    assert diverged.verdict is CheckpointVerdict.DIVERGED
    assert "telemetry event" in diverged.detail


def test_a_chain_edited_without_resealing_is_reported_as_broken(recorded):
    _, telemetry, audit, destination = recorded
    export_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER, destination)
    connection = sqlite3.connect(telemetry.database_path)
    raw = connection.execute(
        "SELECT event_json FROM events WHERE run_id = ? AND sequence = 3", (RUN_ID,)
    ).fetchone()[0]
    changed = json.loads(raw)
    changed["status"] = "tampered"
    connection.execute(
        "UPDATE events SET event_json = ? WHERE run_id = ? AND sequence = 3",
        (json.dumps(changed), RUN_ID),
    )
    connection.commit()
    connection.close()
    broken = verify(telemetry, audit, destination)
    assert broken.verdict is CheckpointVerdict.CHAIN_BROKEN
    assert "telemetry chain" in broken.detail and "sequence 3" in broken.detail
    with pytest.raises(AgentSdkError) as unsigned:
        build_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER)
    assert unsigned.value.code == "RUN_CHECKPOINT_CHAIN_BROKEN"
    assert "A broken chain is not signed" in str(unsigned.value)


def test_an_edited_checkpoint_or_the_wrong_key_fails_its_signature(recorded):
    _, telemetry, audit, destination = recorded
    export_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER, destination)
    path = checkpoint_path(destination, RUN_ID)
    original = path.read_text(encoding="utf-8")
    other_key = HmacSha256Signer(b"y" * 32, signer_id="ops-hmac")
    wrong_key = verify(telemetry, audit, destination, other_key)
    assert wrong_key.verdict is CheckpointVerdict.SIGNATURE_INVALID
    assert "fails its signature" in wrong_key.detail
    other_id = HmacSha256Signer(KEY, signer_id="someone-else")
    assert 'signed by "ops-hmac" but the verifier is "someone-else"' in (
        verify(telemetry, audit, destination, other_id).detail
    )
    record = json.loads(original)
    record["telemetry_sequence"] = 1
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    edited = verify(telemetry, audit, destination)
    assert edited.verdict is CheckpointVerdict.SIGNATURE_INVALID
    with pytest.raises(AgentSdkError) as refused:
        export_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER, destination)
    assert refused.value.code == "RUN_CHECKPOINT_MISMATCH"


def test_a_destination_inside_the_run_root_is_refused(recorded):
    run_root, telemetry, audit, _ = recorded
    for inside in (run_root, run_root / "checkpoints"):
        with pytest.raises(AgentSdkError) as refused:
            export_run_head_checkpoint(telemetry, audit, RUN_ID, SIGNER, inside)
        assert refused.value.code == "RUN_CHECKPOINT_INSIDE_RUN_ROOT"
        assert "another volume or host" in str(refused.value)


def test_a_run_nothing_was_recorded_for_is_not_signed(recorded):
    _, telemetry, audit, destination = recorded
    with pytest.raises(AgentSdkError) as absent:
        export_run_head_checkpoint(telemetry, audit, "no-such-run", SIGNER, destination)
    assert absent.value.code == "RUN_CHECKPOINT_RUN_ABSENT"
    assert not checkpoint_path(destination, "no-such-run").exists()


def test_missing_empty_or_garbled_checkpoint_files_are_verdicts_not_exceptions(recorded):
    _, telemetry, audit, destination = recorded
    path = checkpoint_path(destination, RUN_ID)
    assert verify(telemetry, audit, destination).verdict is CheckpointVerdict.UNREADABLE
    destination.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    assert verify(telemetry, audit, destination).verdict is CheckpointVerdict.NO_CHECKPOINT
    path.write_text("not json\n", encoding="utf-8")
    garbled = verify(telemetry, audit, destination)
    assert garbled.verdict is CheckpointVerdict.UNREADABLE and "Line 1" in garbled.detail
    other = RunHeadCheckpoint(
        run_id="another-run",
        run_root_label="x",
        telemetry_sequence=1,
        audit_sequence=1,
        created_at_utc="2026-01-01T00:00:00+00:00",
        signer_id="ops-hmac",
    )
    other = other.model_copy(update={"signature": SIGNER.sign(other.signed_message())})
    mismatched = verify_run_head_checkpoints(telemetry, audit, RUN_ID, [other], SIGNER)
    assert mismatched.verdict is CheckpointVerdict.UNREADABLE
    assert 'is of run "another-run"' in mismatched.detail


def test_the_terminal_events_commit_to_each_others_heads(recorded):
    _, telemetry, audit, _ = recorded
    (event,) = telemetry.list_events(RUN_ID, event_types=["agent.profile-completed"])
    entries = audit.list_entries(RUN_ID)
    profile = next(entry for entry in entries if entry.event_type == "profile-completed")
    terminal = next(entry for entry in entries if entry.event_type == "terminal-link")
    before = entries[profile.sequence - 2]
    assert terminal.sequence == profile.sequence + 1 == entries[-1].sequence
    assert event.payload["audit_head"] == {
        "entries": before.sequence,
        "head_hash": before.integrity_hash,
    }
    assert terminal.payload == {
        "telemetry_head": {"sequence": event.sequence, "integrity_hash": event.integrity_hash}
    }
    assert "telemetry_head" not in profile.payload
    link = verify_terminal_link(telemetry, audit, RUN_ID)
    assert link.linked, link.detail
    assert f"Audit entry {terminal.sequence} commits to telemetry event {event.sequence}" in (
        link.detail
    )


def test_a_removed_link_entry_is_reported_as_half_a_link_not_as_absent(recorded):
    run_root, telemetry, audit, _ = recorded
    last = audit.footprint(RUN_ID).entries
    truncate_audit(audit, run_root, last - 1)
    assert audit.chain_break(RUN_ID) is None
    link = verify_terminal_link(telemetry, audit, RUN_ID)
    assert link.state is TerminalLinkState.BROKEN
    assert "Only one half of the cross-link" in link.detail


def test_a_run_from_before_the_link_entry_existed_still_verifies_through_its_old_layout(
    recorded,
):
    run_root, telemetry, audit, _ = recorded
    (event,) = telemetry.list_events(RUN_ID, event_types=["agent.profile-completed"])
    entries = audit.list_entries(RUN_ID)
    rewrite_audit(
        audit,
        run_root,
        entries[-2].sequence,
        lambda payload: payload.update(
            {"telemetry_head": {"sequence": event.sequence, "integrity_hash": event.integrity_hash}}
        ),
    )
    path = audit_file(run_root)
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines[:-1]]
    path.write_text(nl.join(kept) + nl, encoding="utf-8")
    link = verify_terminal_link(telemetry, audit, RUN_ID)
    assert link.state is TerminalLinkState.LINKED, link.detail


def test_a_rewritten_audit_entry_breaks_the_terminal_link_even_with_a_valid_chain(recorded):
    run_root, telemetry, audit, _ = recorded
    rewrite_audit(audit, run_root, 3, lambda payload: payload.update({"forged": True}))
    assert audit.chain_break(RUN_ID) is None
    link = verify_terminal_link(telemetry, audit, RUN_ID)
    assert not link.linked
    assert "recorded audit entry" in link.detail and "but it is" in link.detail


def test_a_rewritten_terminal_event_breaks_the_terminal_link(recorded):
    _, telemetry, audit, _ = recorded
    (event,) = telemetry.list_events(RUN_ID, event_types=["agent.profile-completed"])
    rewrite_telemetry(telemetry, event.sequence, lambda payload: payload.update({"x": 1}))
    assert telemetry.chain_break(RUN_ID) is None
    link = verify_terminal_link(telemetry, audit, RUN_ID)
    assert not link.linked and "recorded telemetry event" in link.detail


def test_a_run_without_a_terminal_pair_says_so(recorded):
    _, telemetry, audit, _ = recorded
    link = verify_terminal_link(telemetry, audit, "no-such-run")
    assert not link.linked and "has no terminal pair" in link.detail


def test_the_developer_command_checkpoints_and_verifies_a_run(recorded, tmp_path, capsys):
    run_root, telemetry, audit, destination = recorded
    telemetry.close()
    audit.close()
    key_file = tmp_path / "checkpoint.key"
    key_file.write_text(KEY.decode() + "\n", encoding="utf-8")
    arguments = [str(run_root), RUN_ID]
    assert (
        main(
            [
                "checkpoint-run",
                *arguments,
                str(destination),
                "--key-file",
                str(key_file),
                "--signer-id",
                "ops-hmac",
            ]
        )
        == 0
    )
    written = json.loads(capsys.readouterr().out)
    assert written["run_id"] == RUN_ID and written["signer_id"] == "ops-hmac"
    assert KEY.decode() not in json.dumps(written)
    assert (
        main(
            [
                "verify-checkpoints",
                *arguments,
                str(destination),
                "--key-file",
                str(key_file),
                "--signer-id",
                "ops-hmac",
            ]
        )
        == 0
    )
    verified = json.loads(capsys.readouterr().out)
    assert verified["verification"]["verdict"] == "matches" and verified["ok"] is True
    assert verified["terminal_link"]["state"] == "linked"
    truncated = TelemetryStore(run_root)
    truncate_telemetry(truncated, written["telemetry_sequence"] - 1)
    truncated.close()
    assert (
        main(
            [
                "verify-checkpoints",
                *arguments,
                str(checkpoint_path(destination, RUN_ID)),
                "--key-file",
                str(key_file),
                "--signer-id",
                "ops-hmac",
            ]
        )
        == 1
    )
    reported = json.loads(capsys.readouterr().out)
    assert reported["verification"]["verdict"] == "truncated" and reported["ok"] is False


def test_the_developer_command_refuses_a_short_key_without_printing_it(recorded, tmp_path, capsys):
    run_root, telemetry, audit, destination = recorded
    telemetry.close()
    audit.close()
    key_file = tmp_path / "short.key"
    key_file.write_text("short-secret", encoding="utf-8")
    status = main(
        ["checkpoint-run", str(run_root), RUN_ID, str(destination), "--key-file", str(key_file)]
    )
    captured = capsys.readouterr()
    assert status == 3
    assert "at least 32 bytes, got 12" in captured.err and "short-secret" not in captured.err
