# Copyright (c) 2026 David Michael Indraputra

"""The one check every signed record goes through: stamp, key, signer and signature."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..foundations.jcs import schema_message
from .signers import CheckpointSigner
from .timestamp import DEFAULT_MAX_SKEW_SECONDS, timestamp_problem

if TYPE_CHECKING:
    from cryptography.x509 import Certificate


class SignedRecord(Protocol):
    schema_version: str
    signer_id: str
    signature: str

    def signed_message(self) -> bytes: ...


@runtime_checkable
class KeyResolver(Protocol):
    def resolve(
        self, signer_id: str, at_utc: str | None
    ) -> tuple[CheckpointSigner | None, str | None]: ...


class SignatureFailure(StrEnum):
    UNTRUSTED_STAMP = "untrusted-stamp"
    UNTRUSTED_KEY = "untrusted-key"
    WRONG_SIGNER = "wrong-signer"
    BAD_SIGNATURE = "bad-signature"


@dataclass(frozen=True)
class SignatureCheck:
    failure: SignatureFailure | None
    detail: str
    verifier: CheckpointSigner | None
    stamped: bool = False
    stamp_verified: bool = False

    @property
    def ok(self) -> bool:
        return self.failure is None


def resolve_verifier(
    verifier: CheckpointSigner, signer_id: str, at_utc: str | None
) -> tuple[CheckpointSigner | None, str | None]:
    if isinstance(verifier, KeyResolver):
        return verifier.resolve(signer_id, at_utc)
    return verifier, None


def check_signature(
    record: SignedRecord,
    verifier: CheckpointSigner,
    *,
    claimed_utc: str,
    content: dict[str, Any] | None = None,
    trusted_roots: Sequence[Certificate] | None = None,
    max_skew_seconds: float = DEFAULT_MAX_SKEW_SECONDS,
) -> SignatureCheck:
    stamp = getattr(record, "timestamp", None)
    signing_time, stamped, stamp_verified = claimed_utc, False, False
    if stamp is not None and content is not None:
        stamped = True
        moment, problem = timestamp_problem(
            stamp,
            content,
            record.schema_version,
            schema_message,
            trusted_roots,
            claimed_utc,
            max_skew_seconds,
        )
        if problem is not None:
            return SignatureCheck(SignatureFailure.UNTRUSTED_STAMP, problem, None, stamped=True)
        if moment is not None:
            stamp_verified, signing_time = True, moment.isoformat()
    resolved, problem = resolve_verifier(verifier, record.signer_id, signing_time)
    if resolved is None:
        return SignatureCheck(
            SignatureFailure.UNTRUSTED_KEY,
            problem or "the key is not trusted",
            None,
            stamped,
            stamp_verified,
        )
    if record.signer_id != resolved.signer_id:
        return SignatureCheck(
            SignatureFailure.WRONG_SIGNER, resolved.signer_id, resolved, stamped, stamp_verified
        )
    if not resolved.verify(record.signed_message(), record.signature):
        return SignatureCheck(
            SignatureFailure.BAD_SIGNATURE, resolved.signer_id, resolved, stamped, stamp_verified
        )
    return SignatureCheck(None, "", resolved, stamped, stamp_verified)
