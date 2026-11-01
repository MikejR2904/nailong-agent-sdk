# Copyright (c) 2026 David Michael Indraputra

"""The recorder's command line: ``python -m nailong_agent_sdk.observability.recorder``."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from ..ed25519 import Ed25519Signer
from .core import RecorderCore
from .protocol import APPEND_TOKEN_VARIABLE, KEY_FILE, READ_TOKEN_VARIABLE
from .server import RecorderServer
from .tls import server_ssl_context


def _load_signer(directory: Path) -> Ed25519Signer:
    path = directory / KEY_FILE
    if path.is_file():
        return Ed25519Signer(path.read_text("utf-8").strip(), signer_id="recorder")
    directory.mkdir(parents=True, exist_ok=True)
    signer = Ed25519Signer.generate("recorder")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(signer.private_key_hex())
    return signer


def _secret(path: Path | None, variable: str) -> str:
    if path is not None:
        return path.read_text(encoding="utf-8").strip()
    return os.environ.get(variable, "")


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="nailong-recorder")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--head-every", type=int, default=50)
    parser.add_argument("--no-fsync", action="store_true")
    parser.add_argument("--append-token-file", type=Path)
    parser.add_argument("--read-token-file", type=Path)
    parser.add_argument("--scoped-append", action="store_true")
    parser.add_argument("--verify-records", action="store_true")
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument("--tls-client-ca", type=Path)
    arguments = parser.parse_args(argv)
    append_token = _secret(arguments.append_token_file, APPEND_TOKEN_VARIABLE)
    read_token = _secret(arguments.read_token_file, READ_TOKEN_VARIABLE)
    if not append_token or not read_token:
        print(
            f"The append and read tokens must both be given: in {APPEND_TOKEN_VARIABLE} and "
            f"{READ_TOKEN_VARIABLE} in the recorder's environment, or in the files named by "
            "--append-token-file and --read-token-file (files keep them out of child processes).",
            file=sys.stderr,
        )
        return 2
    if bool(arguments.tls_cert) != bool(arguments.tls_key):
        print("--tls-cert and --tls-key must be given together.", file=sys.stderr)
        return 2
    tls = (
        server_ssl_context(
            arguments.tls_cert, arguments.tls_key, client_ca_file=arguments.tls_client_ca
        )
        if arguments.tls_cert
        else None
    )
    signer = _load_signer(arguments.directory)
    core = RecorderCore(
        arguments.directory,
        signer,
        head_every=arguments.head_every,
        durable=not arguments.no_fsync,
        verify_records=arguments.verify_records,
    )
    server = RecorderServer(
        core,
        append_token=append_token,
        read_token=read_token,
        host=arguments.host,
        port=arguments.port,
        scoped_append=arguments.scoped_append,
        tls=tls,
    )
    print(
        json.dumps(
            {
                "host": arguments.host,
                "port": server.port,
                "public_key": signer.public_key_hex,
                "signer_id": signer.signer_id,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
