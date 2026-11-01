# Copyright (c) 2026 David Michael Indraputra

"""Public verification keys with validity windows and revocation, for verifiers with no secret."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, field_validator

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from .ed25519 import KEY_BYTES, Ed25519Signer
from .run_checkpoints import CheckpointSigner

KEYRING_SCHEMA = "agent-sdk-keyring-v1"


def parse_instant(text: str, label: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f'{label} "{text}" is not an ISO 8601 time: {error}') from error
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


class VerificationKey(StrictModel):
    key_id: str = Field(min_length=1, max_length=128)
    public_key: str = Field(min_length=KEY_BYTES * 2, max_length=KEY_BYTES * 2)
    not_before_utc: str | None = None
    not_after_utc: str | None = None
    revoked_at_utc: str | None = None

    @field_validator("public_key")
    @classmethod
    def public_key_is_hex(cls, value: str) -> str:
        try:
            bytes.fromhex(value)
        except ValueError as error:
            raise ValueError(f"public_key must be {KEY_BYTES * 2} hexadecimal characters: {error}")
        return value.lower()

    @field_validator("not_before_utc", "not_after_utc", "revoked_at_utc")
    @classmethod
    def instants_parse(cls, value: str | None, info: Any) -> str | None:
        if value is not None:
            parse_instant(value, info.field_name)
        return value


class KeyRing:
    """Resolves the key a record names to a verifier that is valid at the time the record claims."""

    def __init__(self, keys: Sequence[VerificationKey]) -> None:
        seen: set[str] = set()
        for key in keys:
            if key.key_id in seen:
                raise ValueError(f'The key id "{key.key_id}" appears twice in the key ring.')
            seen.add(key.key_id)
        self._keys = {key.key_id: key for key in keys}

    @property
    def keys(self) -> list[VerificationKey]:
        return list(self._keys.values())

    @classmethod
    def of(cls, *signers: CheckpointSigner | Ed25519Signer) -> KeyRing:
        keys = []
        for signer in signers:
            if not isinstance(signer, Ed25519Signer):
                raise TypeError(
                    f'The signer "{signer.signer_id}" is a {type(signer).__name__}; only Ed25519 '
                    "keys can be published in a key ring because they have a public half."
                )
            keys.append(VerificationKey(key_id=signer.signer_id, public_key=signer.public_key_hex))
        return cls(keys)

    @classmethod
    def from_file(cls, path: Path) -> KeyRing:
        try:
            document = Path(path).read_text(encoding="utf-8")
            return cls.from_json(document)
        except (OSError, ValueError, ValidationError) as error:
            raise AgentSdkError(
                "KEYRING_UNREADABLE",
                f'The key ring "{path}" cannot be read: {type(error).__name__}: {error}',
                {"path": str(path)},
            ) from error

    @classmethod
    def from_json(cls, text: str) -> KeyRing:
        import json

        document = json.loads(text)
        if document.get("schema_version") != KEYRING_SCHEMA:
            raise ValueError(
                f'schema_version is "{document.get("schema_version")}", expected "{KEYRING_SCHEMA}"'
            )
        return cls([VerificationKey.model_validate(item) for item in document["keys"]])

    def to_json(self) -> str:
        import json

        return json.dumps(
            {
                "schema_version": KEYRING_SCHEMA,
                "keys": [key.model_dump(mode="json") for key in self._keys.values()],
            },
            indent=2,
            sort_keys=True,
        )

    def write(self, path: Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    def revoked(self, key_id: str, at_utc: str) -> KeyRing:
        key = self._known(key_id)
        return self._replaced(key.model_copy(update={"revoked_at_utc": at_utc}))

    def rotated(self, old_key_id: str, new_key: VerificationKey, at_utc: str) -> KeyRing:
        old = self._known(old_key_id)
        retired = old.model_copy(update={"not_after_utc": at_utc})
        successor = new_key.model_copy(update={"not_before_utc": at_utc})
        return KeyRing(
            [*(k for k in self._keys.values() if k.key_id != old_key_id), retired, successor]
        )

    def verifier(self) -> KeyRingVerifier:
        return KeyRingVerifier(self)

    def resolve(
        self, key_id: str, at_utc: str | None
    ) -> tuple[CheckpointSigner | None, str | None]:
        key = self._keys.get(key_id)
        if key is None:
            return None, (
                f'the key ring holds no key "{key_id}" (known: {sorted(self._keys)}), so the '
                "signature cannot be attributed"
            )
        problem = self._window_problem(key, at_utc)
        if problem is not None:
            return None, problem
        return Ed25519Signer(public_key=key.public_key, signer_id=key.key_id), None

    def _known(self, key_id: str) -> VerificationKey:
        key = self._keys.get(key_id)
        if key is None:
            raise ValueError(f'The key ring holds no key "{key_id}" (known: {sorted(self._keys)}).')
        return key

    def _replaced(self, key: VerificationKey) -> KeyRing:
        return KeyRing([key if item.key_id == key.key_id else item for item in self._keys.values()])

    @staticmethod
    def _window_problem(key: VerificationKey, at_utc: str | None) -> str | None:
        if at_utc is None:
            if key.revoked_at_utc is not None:
                return f'key "{key.key_id}" was revoked at {key.revoked_at_utc}'
            return None
        try:
            moment = parse_instant(at_utc, "the signing time")
        except ValueError as error:
            return (
                f'key "{key.key_id}" cannot be checked against a time that does not parse: {error}'
            )
        if key.not_before_utc and moment < parse_instant(key.not_before_utc, "not_before_utc"):
            return (
                f'key "{key.key_id}" was not valid until {key.not_before_utc} but the record '
                f"claims {at_utc}"
            )
        if key.not_after_utc and moment >= parse_instant(key.not_after_utc, "not_after_utc"):
            return (
                f'key "{key.key_id}" expired at {key.not_after_utc} but the record claims {at_utc}'
            )
        if key.revoked_at_utc and moment >= parse_instant(key.revoked_at_utc, "revoked_at_utc"):
            return (
                f'key "{key.key_id}" was revoked at {key.revoked_at_utc} but the record claims '
                f"{at_utc}"
            )
        return None


class KeyRingVerifier:
    """A verifier that picks the key by the id a record names; it cannot sign."""

    def __init__(self, ring: KeyRing) -> None:
        self._ring = ring

    @property
    def signer_id(self) -> str:
        return "keyring"

    @property
    def ring(self) -> KeyRing:
        return self._ring

    def sign(self, message: bytes) -> str:
        raise AgentSdkError(
            "KEYRING_CANNOT_SIGN",
            "A key ring holds public keys only, so it can verify signatures but not make them.",
        )

    def verify(self, message: bytes, signature: str) -> bool:
        raise AgentSdkError(
            "KEYRING_NEEDS_SIGNER_ID",
            "A key ring picks the key by the id a record names and by the time it claims, so "
            "it cannot verify a bare signature; call resolve(signer_id, at_utc) first.",
        )

    def resolve(
        self, signer_id: str, at_utc: str | None
    ) -> tuple[CheckpointSigner | None, str | None]:
        return self._ring.resolve(signer_id, at_utc)
