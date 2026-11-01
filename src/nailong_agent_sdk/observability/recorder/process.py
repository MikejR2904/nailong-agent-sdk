# Copyright (c) 2026 David Michael Indraputra

"""Starting a recorder as a separate process."""

from __future__ import annotations

import json
import os
import queue
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from ..ed25519 import Ed25519Signer
from .client import RecorderClient
from .protocol import APPEND_TOKEN_VARIABLE, READ_TOKEN_VARIABLE, recorder_error


@dataclass
class RecorderProcess:
    directory: Path
    host: str
    port: int
    append_token: str
    read_token: str
    public_key_hex: str
    signer_id: str
    process: subprocess.Popen[str]

    def client(self, *, with_read: bool = False, timeout_seconds: float = 5.0) -> RecorderClient:
        return RecorderClient(
            self.host,
            self.port,
            append_token=self.append_token,
            read_token=self.read_token if with_read else None,
            timeout_seconds=timeout_seconds,
        )

    def verifier(self) -> Ed25519Signer:
        return Ed25519Signer(public_key=self.public_key_hex, signer_id=self.signer_id)

    def alive(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


def spawn_recorder(
    directory: Path,
    *,
    head_every: int = 50,
    durable: bool = True,
    startup_timeout_seconds: float = 30.0,
) -> RecorderProcess:
    append_token, read_token = secrets.token_hex(16), secrets.token_hex(16)
    environment = {
        **os.environ,
        APPEND_TOKEN_VARIABLE: append_token,
        READ_TOKEN_VARIABLE: read_token,
    }
    command = [
        sys.executable,
        "-m",
        "nailong_agent_sdk.observability.recorder",
        "--directory",
        str(directory),
        "--head-every",
        str(head_every),
        *([] if durable else ["--no-fsync"]),
    ]
    process = subprocess.Popen(
        command,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready: queue.Queue[str] = queue.Queue()
    threading.Thread(
        target=lambda: ready.put(process.stdout.readline() if process.stdout else ""),
        daemon=True,
    ).start()
    try:
        line = ready.get(timeout=startup_timeout_seconds)
        info = json.loads(line)
    except (queue.Empty, ValueError) as error:
        process.kill()
        stderr = process.stderr.read() if process.stderr else ""
        raise recorder_error(
            "RECORDER_START_FAILED",
            f"The recorder process did not report that it was listening within "
            f"{startup_timeout_seconds}s: {type(error).__name__}: {error}. {stderr[-300:]}",
        ) from error
    return RecorderProcess(
        directory=directory,
        host=str(info["host"]),
        port=int(info["port"]),
        append_token=append_token,
        read_token=read_token,
        public_key_hex=str(info["public_key"]),
        signer_id=str(info["signer_id"]),
        process=process,
    )
