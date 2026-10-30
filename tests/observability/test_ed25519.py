import pytest

pytest.importorskip("cryptography")

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.observability.ed25519 import Ed25519Signer


def test_a_signature_verifies_with_the_public_key_alone():
    signer = Ed25519Signer.generate()
    signature = signer.sign(b"message")
    verifier = signer.verifier()
    assert verifier.verify(b"message", signature) and not verifier.can_sign
    assert verifier.signer_id == signer.signer_id and signer.signer_id.startswith("ed25519:")


def test_an_edited_message_signature_or_another_key_fails_verification():
    signer, other = Ed25519Signer.generate(), Ed25519Signer.generate()
    signature = signer.sign(b"message")
    assert not signer.verify(b"messagE", signature)
    changed = "01" if signature.endswith("00") else "00"
    assert not signer.verify(b"message", signature[:-2] + changed)
    assert not signer.verify(b"message", "not hex")
    assert not signer.verify(b"message", signature[:20])
    assert not other.verify(b"message", signature)


def test_a_verifier_that_holds_no_private_key_cannot_sign_and_says_so():
    verifier = Ed25519Signer.generate("run-signer").verifier()
    with pytest.raises(AgentSdkError) as refused:
        verifier.sign(b"message")
    assert refused.value.code == "ED25519_SIGNING_KEY_MISSING"
    assert 'Signer "run-signer" holds only a public key' in str(refused.value)


def test_keys_are_checked_for_length_and_for_belonging_together():
    with pytest.raises(AgentSdkError, match="An Ed25519 private key is 32 bytes, got 5"):
        Ed25519Signer(b"short")
    with pytest.raises(AgentSdkError, match="both are missing"):
        Ed25519Signer()
    first, second = Ed25519Signer.generate(), Ed25519Signer.generate()
    private = bytes(32)
    with pytest.raises(AgentSdkError, match="does not belong to the private key"):
        Ed25519Signer(private, public_key=second.public_key_hex)
    assert first.public_key_hex != second.public_key_hex
    with pytest.raises(AgentSdkError, match="signer_id must be 1 to 128"):
        Ed25519Signer.generate("   ")


def test_a_key_given_as_hex_reproduces_the_same_signature():
    first = Ed25519Signer(bytes(range(32)))
    second = Ed25519Signer(bytes(range(32)).hex())
    assert first.public_key_hex == second.public_key_hex
    assert first.sign(b"m") == second.sign(b"m")
