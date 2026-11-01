# Copyright (c) 2026 David Michael Indraputra

"""What an evidence profile is told: where evidence is kept, who signs it and what is required."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from ...foundations.contracts import StrictModel
from ...foundations.errors import AgentSdkError

PROFILE_SCHEMA = "agent-sdk-evidence-profile-v1"
DEFAULT_CLOCK_SKEW_SECONDS = 5.0


class WitnessSettings(StrictModel):
    url: str = Field(min_length=1)
    append_token_variable: str = Field(min_length=1)
    read_token_variable: str | None = None
    allow_insecure_http: bool = False


class RecorderSettings(StrictModel):
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65_535)
    append_token_variable: str | None = None
    read_token_variable: str | None = None
    append_token_file: Path | None = None
    read_token_file: Path | None = None
    scoped_append: bool = False
    public_key_file: Path | None = None
    signer_id: str = "recorder"
    tls_ca_file: Path | None = None
    tls_client_certificate_file: Path | None = None
    tls_client_key_file: Path | None = None
    tls_server_hostname: str | None = None
    allow_remote_plaintext: bool = False


class EvidenceSettings(StrictModel):
    schema_version: str = PROFILE_SCHEMA
    signing_key_file: Path | None = None
    keyring_file: Path | None = None
    signer_id: str | None = None
    checkpoint_directory: Path | None = None
    witness: WitnessSettings | None = None
    recorder: RecorderSettings | None = None
    timestamp_authority_url: str | None = None
    trusted_roots: list[Path] = Field(default_factory=list)
    require_recorder: bool = True
    require_timestamps: bool = False
    require_final_attestation: bool = False
    require_ordered_time: bool = False
    max_receipt_delay_seconds: float | None = Field(default=None, gt=0)
    clock_skew_tolerance_seconds: float = Field(default=DEFAULT_CLOCK_SKEW_SECONDS, ge=0)

    @classmethod
    def load(cls, path: Path) -> EvidenceSettings:
        try:
            return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise AgentSdkError(
                "EVIDENCE_PROFILE_UNREADABLE",
                f'The evidence profile "{path}" cannot be read: {type(error).__name__}: {error}',
                {"path": str(path)},
            ) from error
