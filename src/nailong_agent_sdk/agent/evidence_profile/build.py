# Copyright (c) 2026 David Michael Indraputra

"""Turns evidence settings into the working parts: keys, sink, clients, authority and roots."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...foundations.errors import AgentSdkError
from ...observability.ed25519 import Ed25519Signer
from ...observability.keyring import KeyRing
from ...observability.recorder import RecorderClient, client_ssl_context, scoped_append_token
from ...observability.run_checkpoints import CheckpointSink, FileCheckpointSink
from ...observability.timestamp import Rfc3161Authority, TimestampAuthority
from ...observability.witness import HttpWitnessSink
from .settings import EvidenceSettings

if TYPE_CHECKING:
    from cryptography.x509 import Certificate


@dataclass
class Components:
    signer: Ed25519Signer | None
    ring: KeyRing | None
    sink: CheckpointSink | None
    writer: RecorderClient | None
    reader: RecorderClient | None
    recorder_verifier: Ed25519Signer | None
    timestamper: TimestampAuthority | None
    roots: list[Certificate]


def build_components(settings: EvidenceSettings, environ: Mapping[str, str]) -> Components:
    signer = load_signer(settings)
    ring = load_ring(settings, signer)
    sink = build_sink(settings, environ)
    if signer is not None and sink is None:
        raise ValueError(
            "A profile that signs checkpoints needs somewhere outside the run root to keep "
            "them: set checkpoint_directory or witness."
        )
    writer, reader = build_recorder(settings, environ)
    recorder_verifier = load_recorder_verifier(settings)
    timestamper = (
        Rfc3161Authority(settings.timestamp_authority_url)
        if settings.timestamp_authority_url
        else None
    )
    roots = load_roots(settings)
    if settings.require_timestamps and not roots:
        raise ValueError(
            "require_timestamps needs trusted_roots: a stamp that cannot be verified proves "
            "nothing about when the record existed."
        )
    if settings.require_final_attestation and (reader is None or recorder_verifier is None):
        raise ValueError(
            "require_final_attestation needs a recorder with a read token and a "
            "public_key_file, because only the recorder can say the run was closed."
        )
    require_own_key_valid(signer, ring)
    return Components(signer, ring, sink, writer, reader, recorder_verifier, timestamper, roots)


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def read_secret(
    environ: Mapping[str, str], variable: str | None, file: Path | None, label: str
) -> str | None:
    if file is not None:
        try:
            value = Path(file).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise AgentSdkError(
                "EVIDENCE_PROFILE_TOKEN_MISSING",
                f'The {label} token file "{file}" cannot be read: {type(error).__name__}: {error}',
                {"file": str(file)},
            ) from error
        if not value:
            raise AgentSdkError(
                "EVIDENCE_PROFILE_TOKEN_MISSING",
                f'The {label} token file "{file}" is empty.',
                {"file": str(file)},
            )
        return value
    if variable is None:
        return None
    value = environ.get(variable, "")
    if not value:
        raise AgentSdkError(
            "EVIDENCE_PROFILE_TOKEN_MISSING",
            f"The {label} token is read from the environment variable {variable}, which is not "
            "set or is empty.",
            {"variable": variable},
        )
    return value


def load_signer(settings: EvidenceSettings) -> Ed25519Signer | None:
    if settings.signing_key_file is None:
        return None
    key = Path(settings.signing_key_file).read_text(encoding="utf-8").strip()
    return Ed25519Signer(key, signer_id=settings.signer_id)


def load_ring(settings: EvidenceSettings, signer: Ed25519Signer | None) -> KeyRing | None:
    if settings.keyring_file is not None:
        return KeyRing.from_file(settings.keyring_file)
    if signer is not None:
        return KeyRing.of(signer)
    if settings.recorder is None:
        raise ValueError(
            "An evidence profile needs signing_key_file or keyring_file to check host-signed "
            "evidence, or a recorder whose attestation replaces it; none is set."
        )
    return None


def build_sink(settings: EvidenceSettings, environ: Mapping[str, str]) -> CheckpointSink | None:
    if settings.witness is not None:
        witness = settings.witness
        append = read_secret(environ, witness.append_token_variable, None, "witness append")
        return HttpWitnessSink(
            witness.url,
            append_token=append or "",
            read_token=read_secret(environ, witness.read_token_variable, None, "witness read"),
            allow_insecure_http=witness.allow_insecure_http,
        )
    if settings.checkpoint_directory is None:
        if settings.recorder is None:
            raise ValueError(
                "An evidence profile needs somewhere outside the run root to keep evidence: "
                "set checkpoint_directory, witness or a recorder."
            )
        return None
    return FileCheckpointSink(settings.checkpoint_directory)


def build_recorder(
    settings: EvidenceSettings, environ: Mapping[str, str]
) -> tuple[RecorderClient | None, RecorderClient | None]:
    recorder = settings.recorder
    if recorder is None:
        if settings.require_recorder:
            raise ValueError(
                "require_recorder is set but no recorder is configured; add a recorder "
                "section or set require_recorder to false."
            )
        return None, None
    encrypted = recorder.tls_ca_file is not None
    if not is_loopback(recorder.host) and not encrypted and not recorder.allow_remote_plaintext:
        raise ValueError(
            f'The recorder host "{recorder.host}" is not a loopback address and no '
            "tls_ca_file is set, so its tokens and every record would cross the network in "
            "clear text; set tls_ca_file, tunnel the connection, or set allow_remote_plaintext."
        )
    secret = read_secret(
        environ, recorder.append_token_variable, recorder.append_token_file, "recorder append"
    )
    if not secret:
        raise ValueError(
            "A recorder needs an append token: set append_token_variable or append_token_file."
        )
    token: str | Callable[[str], str] = secret
    if recorder.scoped_append:

        def token(run_id: str, _secret: str = secret) -> str:
            return scoped_append_token(_secret, run_id)

    read = read_secret(
        environ, recorder.read_token_variable, recorder.read_token_file, "recorder read"
    )
    tls = (
        client_ssl_context(
            recorder.tls_ca_file,
            certificate_file=recorder.tls_client_certificate_file,
            key_file=recorder.tls_client_key_file,
        )
        if recorder.tls_ca_file is not None
        else None
    )
    options: dict[str, Any] = {"tls": tls, "server_hostname": recorder.tls_server_hostname}
    writer = RecorderClient(recorder.host, recorder.port, append_token=token, **options)
    reader = (
        RecorderClient(recorder.host, recorder.port, append_token=token, read_token=read, **options)
        if read is not None
        else None
    )
    return writer, reader


def load_recorder_verifier(settings: EvidenceSettings) -> Ed25519Signer | None:
    recorder = settings.recorder
    if recorder is None or recorder.public_key_file is None:
        return None
    public = Path(recorder.public_key_file).read_text(encoding="utf-8").strip()
    return Ed25519Signer(public_key=public, signer_id=recorder.signer_id)


def load_roots(settings: EvidenceSettings) -> list[Certificate]:
    from cryptography import x509

    roots = []
    for path in settings.trusted_roots:
        try:
            roots.append(x509.load_pem_x509_certificate(Path(path).read_bytes()))
        except (OSError, ValueError) as error:
            raise AgentSdkError(
                "EVIDENCE_PROFILE_ROOT_UNREADABLE",
                f'The trusted root "{path}" cannot be read as a PEM certificate: '
                f"{type(error).__name__}: {error}",
                {"path": str(path)},
            ) from error
    return roots


def require_own_key_valid(signer: Ed25519Signer | None, ring: KeyRing | None) -> None:
    if signer is None or ring is None:
        return
    _, problem = ring.resolve(signer.signer_id, datetime.now(UTC).isoformat())
    if problem is not None:
        raise ValueError(
            f'The profile signs as "{signer.signer_id}" but its key ring does not '
            f"accept that key now: {problem}. Evidence written with it would fail verification."
        )
