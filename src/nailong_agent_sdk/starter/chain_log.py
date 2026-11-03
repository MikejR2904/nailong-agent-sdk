# Copyright (c) 2026 David Michael Indraputra

"""An append-only JSONL file whose records are chained by hash, so edits are detectable."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..foundations.errors import AgentSdkError
from ..foundations.hashing import sha256_hex
from ..foundations.jcs import jcs_json

GENESIS = "genesis"


class ChainedLog:
    def __init__(self, path: Path, label: str) -> None:
        self._path = path
        self._label = label

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.is_file()

    def append(self, record: dict[str, Any], *, new: bool = False) -> dict[str, Any]:
        previous = GENESIS if new else self._last_hash()
        sealed = _seal({**record, "previous_hash": previous})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("x" if new else "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(sealed, separators=(",", ":"), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return sealed

    def read(self) -> list[dict[str, Any]]:
        if not self._path.is_file():
            return []
        records: list[dict[str, Any]] = []
        previous = GENESIS
        for number, line in enumerate(self._path.read_text(encoding="utf-8").split("\n"), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise self._broken(number, "the line is not valid JSON") from error
            if not isinstance(record, dict) or record.get("previous_hash") != previous:
                raise self._broken(number, "it does not follow the record before it")
            claimed = record.pop("hash", None)
            if claimed != _digest(record):
                raise self._broken(number, "its content does not match its hash")
            record["hash"] = claimed
            records.append(record)
            previous = claimed
        return records

    def _last_hash(self) -> str:
        records = self.read()
        return records[-1]["hash"] if records else GENESIS

    def _broken(self, line_number: int, why: str) -> AgentSdkError:
        return AgentSdkError(
            "CHAIN_LOG_BROKEN",
            f"{self._label} fails verification at line {line_number} of {self._path}: {why}.",
        )


def _digest(record: dict[str, Any]) -> str:
    return sha256_hex(jcs_json(record))


def _seal(record: dict[str, Any]) -> dict[str, Any]:
    return {**record, "hash": _digest(record)}
