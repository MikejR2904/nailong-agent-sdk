# Copyright (c) 2026 David Michael Indraputra

"""An Ed25519 checkpoint signer whose verifying side holds only the public key."""

from __future__ import annotations

import hashlib
from typing import Any

from ..foundations.errors import AgentSdkError

KEY_BYTES = 32
SIGNATURE_BYTES = 64
MAX_SIGNER_ID_CHARS = 128


def _primitives() -> Any:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as error:
        raise AgentSdkError(
            "ED25519_UNAVAILABLE",
            "Ed25519 signing needs the 'cryptography' package, which is not installed: "
            "pip install 'cryptography>=43'.",
            {"import_error": f"{type(error).__name__}: {error}"},
        ) from error
    return InvalidSignature, serialization, ed25519


def _key_bytes(value: bytes | str, label: str) -> bytes:
    material = bytes.fromhex(value) if isinstance(value, str) else bytes(value)
    if len(material) != KEY_BYTES:
        raise AgentSdkError(
            "ED25519_KEY_INVALID",
            f"An Ed25519 {label} key is {KEY_BYTES} bytes, got {len(material)}.",
            {"key": label, "length": len(material)},
        )
    return material


class Ed25519Signer:
    """Signs with a private key when it has one and verifies with the public key it derives."""

    def __init__(
        self,
        private_key: bytes | str | None = None,
        *,
        public_key: bytes | str | None = None,
        signer_id: str | None = None,
    ) -> None:
        _, serialization, ed25519 = _primitives()
        if private_key is None and public_key is None:
            raise AgentSdkError(
                "ED25519_KEY_MISSING",
                "An Ed25519 signer needs a private key to sign or a public key to verify; "
                "both are missing.",
            )
        self._private = (
            ed25519.Ed25519PrivateKey.from_private_bytes(_key_bytes(private_key, "private"))
            if private_key is not None
            else None
        )
        derived = (
            self._private.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            if self._private is not None
            else None
        )
        if public_key is not None:
            given = _key_bytes(public_key, "public")
            if derived is not None and derived != given:
                raise AgentSdkError(
                    "ED25519_KEY_MISMATCH",
                    "The public key given does not belong to the private key given.",
                )
            derived = given
        assert derived is not None
        self._public_bytes = derived
        self._public = ed25519.Ed25519PublicKey.from_public_bytes(derived)
        label = signer_id or f"ed25519:{hashlib.sha256(derived).hexdigest()[:16]}"
        if not label.strip() or len(label) > MAX_SIGNER_ID_CHARS:
            raise AgentSdkError(
                "ED25519_SIGNER_ID_INVALID",
                f"signer_id must be 1 to {MAX_SIGNER_ID_CHARS} non-blank characters, "
                f"got {label!r}.",
            )
        self._signer_id = label

    @classmethod
    def generate(cls, signer_id: str | None = None) -> Ed25519Signer:
        _, serialization, ed25519 = _primitives()
        key = ed25519.Ed25519PrivateKey.generate()
        raw = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        return cls(raw, signer_id=signer_id)

    @property
    def signer_id(self) -> str:
        return self._signer_id

    @property
    def public_key_hex(self) -> str:
        return self._public_bytes.hex()

    @property
    def can_sign(self) -> bool:
        return self._private is not None

    def verifier(self) -> Ed25519Signer:
        return Ed25519Signer(public_key=self._public_bytes, signer_id=self._signer_id)

    def sign(self, message: bytes) -> str:
        if self._private is None:
            raise AgentSdkError(
                "ED25519_SIGNING_KEY_MISSING",
                f'Signer "{self._signer_id}" holds only a public key and cannot sign; '
                "the private key belongs to the process that writes the checkpoints.",
                {"signer_id": self._signer_id},
            )
        return self._private.sign(message).hex()

    def verify(self, message: bytes, signature: str) -> bool:
        invalid_signature, _, _ = _primitives()
        try:
            raw = bytes.fromhex(signature)
        except ValueError:
            return False
        if len(raw) != SIGNATURE_BYTES:
            return False
        try:
            self._public.verify(raw, message)
        except invalid_signature:
            return False
        return True
