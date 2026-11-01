import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.keyring import KeyRing
from nailong_agent_sdk.observability.run_checkpoints import HmacSha256Signer, RunHeadCheckpoint
from nailong_agent_sdk.observability.signed_records import (
    KeyResolver,
    SignatureFailure,
    check_signature,
    resolve_verifier,
)
from nailong_agent_sdk.observability.transparency import MerkleTreeHead
from tests.support.fake_tsa import FakeTsa

NOW = datetime.now(UTC)
KEY = b"signed-record-key-0123456789abcd"


def head(signer, *, claimed=NOW, timestamp=None, **changes):
    draft = MerkleTreeHead(
        name="r1.audit.merkle",
        size=3,
        root="ab" * 32,
        published_at_utc=claimed.isoformat(),
        signer_id=signer.signer_id,
        timestamp=timestamp,
        **changes,
    )
    return draft.model_copy(update={"signature": signer.sign(draft.signed_message())})


def test_a_record_signed_by_the_verifiers_key_passes_with_no_stamp():
    signer = HmacSha256Signer(KEY, signer_id="ops")
    check = check_signature(head(signer), signer, claimed_utc=NOW.isoformat())
    assert check.ok and check.failure is None and not check.stamped and not check.stamp_verified
    assert check.verifier is signer


def test_a_record_whose_signature_was_edited_fails_as_a_bad_signature_naming_the_signer():
    signer = HmacSha256Signer(KEY, signer_id="ops")
    record = head(signer).model_copy(update={"size": 99})
    check = check_signature(record, signer, claimed_utc=NOW.isoformat())
    assert check.failure is SignatureFailure.BAD_SIGNATURE and check.detail == "ops"


def test_a_record_naming_a_different_signer_than_the_verifier_is_a_wrong_signer():
    signed_as = HmacSha256Signer(KEY, signer_id="ops")
    verifier = HmacSha256Signer(KEY, signer_id="someone-else")
    check = check_signature(head(signed_as), verifier, claimed_utc=NOW.isoformat())
    assert check.failure is SignatureFailure.WRONG_SIGNER and check.detail == "someone-else"


def test_a_key_ring_that_does_not_hold_the_key_gives_an_untrusted_key_with_the_reason():
    signer = Ed25519Signer.generate("laptop")
    ring = KeyRing.of(Ed25519Signer.generate("other")).verifier()
    check = check_signature(head(signer), ring, claimed_utc=NOW.isoformat())
    assert check.failure is SignatureFailure.UNTRUSTED_KEY
    assert 'no key "laptop"' in check.detail and check.verifier is None


def test_a_revoked_key_is_untrusted_for_a_record_claiming_a_time_after_the_revocation():
    signer = Ed25519Signer.generate("laptop")
    ring = KeyRing.of(signer).revoked("laptop", (NOW - timedelta(days=1)).isoformat())
    check = check_signature(head(signer), ring.verifier(), claimed_utc=NOW.isoformat())
    assert check.failure is SignatureFailure.UNTRUSTED_KEY and "was revoked" in check.detail


def stamped_head(signer, authority):
    from nailong_agent_sdk.foundations.jcs import schema_message
    from nailong_agent_sdk.observability.timestamp import stamp_content

    draft = MerkleTreeHead(
        name="r1.audit.merkle",
        size=3,
        root="ab" * 32,
        published_at_utc=datetime.now(UTC).isoformat(),
        signer_id=signer.signer_id,
    )
    token = stamp_content(
        draft.model_dump(mode="json"), draft.schema_version, authority, schema_message
    )
    draft = draft.model_copy(update={"timestamp": token})
    return draft.model_copy(update={"signature": signer.sign(draft.signed_message())})


def test_a_verified_stamp_supplies_the_time_the_key_is_checked_against():
    authority = FakeTsa(clock=lambda: datetime.now(UTC))
    signer = Ed25519Signer.generate("laptop")
    record = stamped_head(signer, authority)
    ring = KeyRing.of(signer).verifier()
    check = check_signature(
        record,
        ring,
        claimed_utc=record.published_at_utc,
        content=record.model_dump(mode="json"),
        trusted_roots=[authority.root],
    )
    assert check.ok and check.stamped and check.stamp_verified


def test_a_stamp_from_an_authority_nobody_trusts_is_an_untrusted_stamp():
    authority = FakeTsa(clock=lambda: datetime.now(UTC))
    signer = Ed25519Signer.generate("laptop")
    record = stamped_head(signer, authority)
    check = check_signature(
        record,
        KeyRing.of(signer).verifier(),
        claimed_utc=record.published_at_utc,
        content=record.model_dump(mode="json"),
        trusted_roots=[FakeTsa().root],
    )
    assert check.failure is SignatureFailure.UNTRUSTED_STAMP and check.stamped
    assert not check.stamp_verified and "is invalid" in check.detail


def test_without_roots_a_stamp_is_counted_but_never_believed():
    authority = FakeTsa(clock=lambda: datetime.now(UTC))
    signer = Ed25519Signer.generate("laptop")
    record = stamped_head(signer, authority)
    check = check_signature(
        record,
        KeyRing.of(signer).verifier(),
        claimed_utc=record.published_at_utc,
        content=record.model_dump(mode="json"),
    )
    assert check.ok and check.stamped and not check.stamp_verified


def test_only_a_verifier_that_can_resolve_keys_is_treated_as_one():
    plain = HmacSha256Signer(KEY, signer_id="ops")
    ring = KeyRing.of(Ed25519Signer.generate("laptop")).verifier()
    assert not isinstance(plain, KeyResolver) and isinstance(ring, KeyResolver)
    assert resolve_verifier(plain, "anything", None) == (plain, None)


def test_every_signed_record_check_goes_through_the_one_function():
    source = Path("src/nailong_agent_sdk")
    callers = []
    for path in source.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name != "signed_records.py" and re.search(r"(?<![\w.])resolve_verifier\(", text):
            callers.append(path.name)
    assert callers == [], (
        "These modules call resolve_verifier directly instead of check_signature, so their "
        f"records skip the shared stamp, key and signer checks: {callers}"
    )


def test_the_checkpoint_model_satisfies_the_same_protocol():
    signer = HmacSha256Signer(KEY, signer_id="ops")
    draft = RunHeadCheckpoint(
        run_id="r1",
        run_root_label="x",
        telemetry_sequence=1,
        telemetry_head="aa" * 32,
        audit_sequence=1,
        audit_head="bb" * 32,
        created_at_utc=NOW.isoformat(),
        signer_id="ops",
    )
    signed = draft.model_copy(update={"signature": signer.sign(draft.signed_message())})
    assert check_signature(signed, signer, claimed_utc=NOW.isoformat()).ok
