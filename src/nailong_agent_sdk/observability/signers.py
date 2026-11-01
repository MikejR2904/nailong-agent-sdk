# Copyright (c) 2026 David Michael Indraputra

"""What signs and verifies a record, and the shared-secret signer built on the standard library."""

from __future__ import annotations

import hashlib
import hmac
from typing import Protocol

MIN_HMAC_KEY_BYTES = 32
MAX_SIGNER_ID_CHARS = 128


class CheckpointSigner(Protocol):
    @property
    def signer_id(self) -> str: ...

    def sign(self, message: bytes) -> str: ...

    def verify(self, message: bytes, signature: str) -> bool: ...


class HmacSha256Signer:
    """Shared-secret signer from the standard library; the key never appears in a repr."""

    def __init__(self, key: bytes | str, *, signer_id: str = "hmac-sha256") -> None:
        material = key.encode("utf-8") if isinstance(key, str) else bytes(key)
        if len(material) < MIN_HMAC_KEY_BYTES:
            raise ValueError(
                f"An HMAC checkpoint key must be at least {MIN_HMAC_KEY_BYTES} bytes, "
                f"got {len(material)}."
            )
        if not signer_id.strip() or len(signer_id) > MAX_SIGNER_ID_CHARS:
            raise ValueError(
                f"signer_id must be 1 to {MAX_SIGNER_ID_CHARS} non-blank characters, "
                f"got {signer_id!r}."
            )
        self._key = material
        self._signer_id = signer_id

    @property
    def signer_id(self) -> str:
        return self._signer_id

    def sign(self, message: bytes) -> str:
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def verify(self, message: bytes, signature: str) -> bool:
        return hmac.compare_digest(self.sign(message).encode(), signature.encode())

    def __repr__(self) -> str:
        return f"HmacSha256Signer(signer_id={self._signer_id!r})"
