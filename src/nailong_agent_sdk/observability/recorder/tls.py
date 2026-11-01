# Copyright (c) 2026 David Michael Indraputra

"""TLS contexts for the recorder's server and its clients."""

from __future__ import annotations

import ssl
from pathlib import Path


def server_ssl_context(
    certificate_file: Path, key_file: Path, *, client_ca_file: Path | None = None
) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(certificate_file), str(key_file))
    if client_ca_file is not None:
        context.load_verify_locations(str(client_ca_file))
        context.verify_mode = ssl.CERT_REQUIRED
    return context


def client_ssl_context(
    ca_file: Path,
    *,
    certificate_file: Path | None = None,
    key_file: Path | None = None,
) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(str(ca_file))
    if certificate_file is not None:
        context.load_cert_chain(str(certificate_file), str(key_file) if key_file else None)
    return context
