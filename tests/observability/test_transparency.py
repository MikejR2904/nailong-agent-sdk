import sqlite3

import pytest

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.audit_log import AuditTranscriptStore
from nailong_agent_sdk.observability.run_checkpoints import FileCheckpointSink, HmacSha256Signer
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.observability.transparency import (
    InclusionProof,
    MerkleTreeHead,
    TreeHeadVerdict,
    prove_extension,
    prove_inclusion,
    publish_run_tree_heads,
    publish_tree_head,
    run_hashes,
    tree_head_name,
    verify_event_inclusion,
    verify_extension,
    verify_run_tree_heads,
    verify_tree_heads,
)
from tests.support.agents import definition, final, tool_call
from tests.support.base_agent_cases import ECHO, echo_executor
from tests.support.diagnosis_runs import record, tamper_event

SIGNER = HmacSha256Signer(b"tree-head-key-0123456789abcdef-1!", signer_id="heads")
OTHER = HmacSha256Signer(b"another-key-0123456789abcdef-12!", signer_id="heads")
HASHES = [f"{index:064x}" for index in range(1, 22)]
NAME = "r1.telemetry.merkle"


@pytest.fixture
def sink(tmp_path):
    return FileCheckpointSink(tmp_path / "elsewhere")


def test_a_published_head_matches_the_ledger_it_was_taken_from(sink):
    head = publish_tree_head(HASHES, sink, NAME, SIGNER)
    assert head.size == 21 and head.signer_id == "heads" and len(head.root) == 64
    result = verify_tree_heads(HASHES, sink, NAME, SIGNER)
    assert result.verdict is TreeHeadVerdict.MATCHES and result.ok and result.heads == 1


def test_new_records_after_the_head_read_as_advanced_until_it_is_published_again(sink):
    publish_tree_head(HASHES[:15], sink, NAME, SIGNER)
    result = verify_tree_heads(HASHES, sink, NAME, SIGNER)
    assert result.verdict is TreeHeadVerdict.ADVANCED and result.ok
    assert "extends the published 15 records by 6" in result.detail
    publish_tree_head(HASHES, sink, NAME, SIGNER)
    assert verify_tree_heads(HASHES, sink, NAME, SIGNER).verdict is TreeHeadVerdict.MATCHES


def test_cutting_off_the_newest_records_is_truncation(sink):
    publish_tree_head(HASHES, sink, NAME, SIGNER)
    result = verify_tree_heads(HASHES[:-3], sink, NAME, SIGNER)
    assert result.verdict is TreeHeadVerdict.TRUNCATED and not result.ok
    assert "3 newest records were removed" in result.detail


def test_a_resealed_ledger_whose_hashes_were_recomputed_diverges_from_the_head(sink):
    publish_tree_head(HASHES, sink, NAME, SIGNER)
    resealed = [*HASHES[:9], *(f"{0xAB00 + index:064x}" for index in range(12))]
    result = verify_tree_heads(resealed, sink, NAME, SIGNER)
    assert result.verdict is TreeHeadVerdict.DIVERGED
    assert "rewritten or resealed after it was published" in result.detail


def test_a_resealed_tail_after_the_newest_head_is_the_unprotected_window(sink):
    publish_tree_head(HASHES[:10], sink, NAME, SIGNER)
    resealed = [*HASHES[:10], *(f"{0xCD00 + index:064x}" for index in range(11))]
    assert verify_tree_heads(resealed, sink, NAME, SIGNER).verdict is TreeHeadVerdict.ADVANCED


def test_a_head_signed_with_another_key_is_refused(sink):
    publish_tree_head(HASHES, sink, NAME, OTHER)
    result = verify_tree_heads(HASHES, sink, NAME, SIGNER)
    assert result.verdict is TreeHeadVerdict.SIGNATURE_INVALID


def test_an_edited_head_is_refused(sink):
    head = publish_tree_head(HASHES, sink, NAME, SIGNER)
    for stored in sink.directory.glob("*.jsonl"):
        stored.write_text(stored.read_text("utf-8").replace(head.root, "0" * 64), encoding="utf-8")
    result = verify_tree_heads(HASHES, sink, NAME, SIGNER)
    assert result.verdict is TreeHeadVerdict.SIGNATURE_INVALID
    assert "does not carry a valid signature" in result.detail


def test_a_missing_or_unreadable_head_is_named(sink):
    result = verify_tree_heads(HASHES, sink, NAME, SIGNER)
    assert result.verdict is TreeHeadVerdict.NO_HEAD and "tamper-evident only" in result.detail
    sink.append(NAME, "not json")
    unreadable = verify_tree_heads(HASHES, sink, NAME, SIGNER)
    assert unreadable.verdict is TreeHeadVerdict.UNREADABLE and "Tree head 1" in unreadable.detail


def test_a_record_hash_that_is_not_hexadecimal_is_named(sink):
    with pytest.raises(ValueError, match="A record hash must be hexadecimal, got 'zz'"):
        publish_tree_head(["zz"], sink, NAME, SIGNER)


def test_a_log_free_verifier_accepts_an_honest_extension_and_refuses_a_forked_one(sink):
    older = publish_tree_head(HASHES[:13], sink, NAME, SIGNER)
    newer = publish_tree_head(HASHES, sink, NAME, SIGNER)
    proof = prove_extension(HASHES, 13)
    assert len(proof.hashes) <= 8
    assert verify_extension(older, newer, proof, SIGNER).ok
    forked_hashes = [*HASHES[:5], "f" * 64, *HASHES[6:]]
    forked = publish_tree_head(
        forked_hashes, FileCheckpointSink(sink.directory.parent / "fork"), NAME, SIGNER
    )
    check = verify_extension(older, forked, prove_extension(forked_hashes, 13), SIGNER)
    assert not check.ok and "rewritten or forked" in check.detail
    assert "valid signature" in verify_extension(older, newer, proof, OTHER).detail


def test_an_extension_proof_for_other_sizes_is_refused(sink):
    older = publish_tree_head(HASHES[:13], sink, NAME, SIGNER)
    newer = publish_tree_head(HASHES, sink, NAME, SIGNER)
    check = verify_extension(older, newer, prove_extension(HASHES, 7), SIGNER)
    assert not check.ok
    assert "proof is for sizes 7 and 21 but the heads are 13 and 21" in check.detail


def test_one_record_is_proved_to_be_in_the_log_without_the_log(sink):
    head = publish_tree_head(HASHES, sink, NAME, SIGNER)
    proof = prove_inclusion(HASHES, 11)
    assert len(proof.hashes) <= 5
    assert verify_event_inclusion(HASHES[11], proof, head, SIGNER).ok
    other = verify_event_inclusion(HASHES[12], proof, head, SIGNER)
    assert not other.ok and "is not in the 21 records" in other.detail
    assert not verify_event_inclusion(HASHES[11], proof, head, OTHER).ok
    stale = InclusionProof(index=proof.index, size=20, hashes=proof.hashes)
    detail = verify_event_inclusion(HASHES[11], stale, head, SIGNER).detail
    assert "tree of 20 records but the head covers 21" in detail


def test_a_head_signature_covers_the_size_and_root(sink):
    head = publish_tree_head(HASHES, sink, NAME, SIGNER)
    bigger = head.model_copy(update={"size": head.size + 1})
    assert not SIGNER.verify(bigger.signed_message(), bigger.signature)
    assert isinstance(head, MerkleTreeHead) and tree_head_name("r", "audit") == "r.audit.merkle"


def open_stores(root):
    return TelemetryStore(root), AuditTranscriptStore(root)


def close_stores(*stores):
    for item in stores:
        item.close()


def test_the_heads_of_a_real_run_verify_and_catch_a_truncation_the_chain_cannot(tmp_path, sink):
    record(
        tmp_path,
        "r1",
        [tool_call("c1", "echo", {"text": "a"}), final()],
        executor=echo_executor(),
        definition_=definition(tools=[ECHO]),
    )
    telemetry, audit = open_stores(tmp_path)
    try:
        heads = publish_run_tree_heads(telemetry, audit, "r1", sink, SIGNER)
        assert set(heads) == {"telemetry", "audit"} and heads["telemetry"].size > 5
        results = verify_run_tree_heads(telemetry, audit, "r1", sink, SIGNER)
        assert {item.verdict for item in results.values()} == {TreeHeadVerdict.MATCHES}
    finally:
        close_stores(telemetry, audit)
    connection = sqlite3.connect(tmp_path / ".agent-telemetry" / "telemetry.sqlite3")
    try:
        connection.execute(
            "DELETE FROM events WHERE run_id = ? AND sequence > "
            "(SELECT MAX(sequence) FROM events WHERE run_id = ?) - 3",
            ("r1", "r1"),
        )
        connection.commit()
    finally:
        connection.close()
    telemetry, audit = open_stores(tmp_path)
    try:
        assert telemetry.chain_break("r1") is None
        results = verify_run_tree_heads(telemetry, audit, "r1", sink, SIGNER)
        assert results["telemetry"].verdict is TreeHeadVerdict.TRUNCATED
        assert results["audit"].verdict is TreeHeadVerdict.MATCHES
    finally:
        close_stores(telemetry, audit)


def test_a_ledger_whose_chain_is_broken_is_never_signed(tmp_path, sink):
    record(tmp_path, "r1", [final()])
    tamper_event(tmp_path, "r1", 2)
    telemetry, audit = open_stores(tmp_path)
    try:
        with pytest.raises(AgentSdkError) as refused:
            publish_run_tree_heads(telemetry, audit, "r1", sink, SIGNER)
        assert refused.value.code == "TREE_HEAD_CHAIN_BROKEN"
        assert 'The telemetry chain of run "r1" is broken' in str(refused.value)
        assert run_hashes(telemetry, audit, "r1")["telemetry"]
    finally:
        close_stores(telemetry, audit)
