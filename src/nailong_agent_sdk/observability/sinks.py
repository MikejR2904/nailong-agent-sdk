# Copyright (c) 2026 David Michael Indraputra

"""Where signed records are kept outside the run root: the sink interface and the file sink."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from ..foundations.atomic_io import exclusive_file_lock, fsync_directory
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import sha256_hex
from ..foundations.identifiers import file_safe_name
from ..foundations.logging import get_logger
from ..foundations.text import split_lines
from .signed_records import resolve_verifier as resolve_verifier
from .signers import MAX_SIGNER_ID_CHARS as MAX_SIGNER_ID_CHARS
from .signers import MIN_HMAC_KEY_BYTES as MIN_HMAC_KEY_BYTES
from .signers import CheckpointSigner as CheckpointSigner
from .signers import HmacSha256Signer as HmacSha256Signer

_logger = get_logger("recorder")


def checkpoint_path(destination: Path, run_id: str) -> Path:
    return destination / f"{file_safe_name(run_id)}.checkpoints.jsonl"


class CheckpointSink(Protocol):
    def location(self, run_id: str) -> str: ...

    def read_all(self, run_id: str) -> list[str]: ...

    def append(self, run_id: str, record: str) -> None: ...

    def is_inside(self, root: Path) -> bool: ...


class FileCheckpointSink:
    """One append-only JSONL file per run under a directory the operator keeps elsewhere."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory.resolve()

    @property
    def directory(self) -> Path:
        return self._directory

    def location(self, run_id: str) -> str:
        return str(checkpoint_path(self._directory, run_id))

    def read_all(self, run_id: str) -> list[str]:
        path = checkpoint_path(self._directory, run_id)
        if not path.exists():
            return []
        return read_lines(path)

    def append(self, run_id: str, record: str) -> None:
        path = checkpoint_path(self._directory, run_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with exclusive_file_lock(
                path.with_suffix(".lock"), timeout_code="RUN_CHECKPOINT_LOCK_TIMEOUT"
            ):
                if path.exists():
                    _discard_unterminated_tail(path)
                with path.open("ab") as stream:
                    stream.write(record.encode("utf-8"))
                    stream.write(b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            fsync_directory(path.parent)
        except OSError as error:
            raise AgentSdkError(
                "RUN_CHECKPOINT_WRITE_FAILED",
                f'Could not append a checkpoint to "{path}": {type(error).__name__}: {error}',
                {"path": str(path)},
            ) from error

    def is_inside(self, root: Path) -> bool:
        resolved = root.resolve()
        return self._directory == resolved or resolved in self._directory.parents


def read_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise AgentSdkError(
            "RUN_CHECKPOINT_UNREADABLE",
            f'The checkpoint file "{path}" cannot be read: {type(error).__name__}: {error}',
            {"path": str(path)},
        ) from error
    if text and not text.endswith("\n"):
        text = text[: text.rfind("\n") + 1]
    return split_lines(text)


def _discard_unterminated_tail(path: Path) -> None:
    """Drop the half of a record a crash cut off; an acknowledged append ends with a newline."""

    data = path.read_bytes()
    if not data or data.endswith(b"\n"):
        return
    keep = data.rfind(b"\n") + 1
    _logger.warning(
        'The checkpoint file "%s" ended in %d unterminated bytes (sha256 %s), a write cut off '
        "by a crash; they were discarded before the next record was appended.",
        path,
        len(data) - keep,
        sha256_hex(data[keep:]),
    )
    with path.open("r+b") as stream:
        stream.truncate(keep)
        stream.flush()
        os.fsync(stream.fileno())
