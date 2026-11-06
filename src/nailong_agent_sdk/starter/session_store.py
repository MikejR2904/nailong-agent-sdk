# Copyright (c) 2026 David Michael Indraputra

"""A session's turns on disk as a hash chain, so resuming and forking can be checked."""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..foundations.errors import AgentSdkError
from .chain_log import ChainedLog

SESSION_DIRECTORY = ".agent-sessions"
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_DROPPED_ON_COPY = frozenset({"kind", "number", "previous_hash", "hash"})


def validate_session_id(session_id: str) -> str:
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError(
            f'Session id "{session_id}" must be 1 to 64 characters: letters, digits, ".", "_" '
            'or "-", starting with a letter or digit.'
        )
    return session_id


class SessionStore:
    def __init__(self, root: Path) -> None:
        self._directory = root / SESSION_DIRECTORY
        self._lock = threading.Lock()

    def exists(self, session_id: str) -> bool:
        return self._log(session_id).exists()

    def ids(self) -> list[str]:
        if not self._directory.is_dir():
            return []
        return sorted(path.stem for path in self._directory.glob("*.jsonl"))

    def delete(self, session_id: str) -> None:
        with self._lock:
            log = self._log(session_id)
            if not log.exists():
                raise AgentSdkError(
                    "SESSION_NOT_FOUND",
                    f'Session "{session_id}" does not exist; the sessions are {self.ids()}.',
                )
            self._directory.joinpath(f"{session_id}.jsonl").unlink()

    def latest(self) -> str | None:
        if not self._directory.is_dir():
            return None
        files = list(self._directory.glob("*.jsonl"))
        return max(files, key=lambda path: path.stat().st_mtime_ns).stem if files else None

    def create(
        self, session_id: str, agent: str, forked_from: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self._lock:
            log = self._log(session_id)
            if log.exists():
                raise AgentSdkError(
                    "SESSION_EXISTS",
                    f'Session "{session_id}" already exists; resume it instead of creating it.',
                )
            return log.append(
                {
                    "kind": "start",
                    "session": session_id,
                    "agent": agent,
                    "created_at": _now(),
                    "forked_from": forked_from,
                },
                new=True,
            )

    def append(self, session_id: str, turn: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            log = self._existing(session_id)
            number = sum(1 for record in log.read() if record["kind"] == "turn") + 1
            return log.append({"kind": "turn", "number": number, "at": _now(), **turn})

    def add_summary(self, session_id: str, text: str, upto: int) -> dict[str, Any]:
        with self._lock:
            log = self._existing(session_id)
            turns = sum(1 for record in log.read() if record["kind"] == "turn")
            if not text.strip():
                raise ValueError("A summary needs text.")
            if not 1 <= upto <= turns:
                raise ValueError(
                    f'Session "{session_id}" has {turns} turn(s), so a summary cannot cover '
                    f"turn {upto}."
                )
            return log.append({"kind": "summary", "upto": upto, "text": text, "at": _now()})

    def records(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return self._existing(session_id).read()

    def fork(self, source: str, target: str, agent: str, at_turn: int | None) -> dict[str, Any]:
        records = self.records(source)
        turns = [record for record in records if record["kind"] == "turn"]
        keep = len(turns) if at_turn is None else at_turn
        if not 0 <= keep <= len(turns):
            raise AgentSdkError(
                "SESSION_TURN_UNKNOWN",
                f'Session "{source}" has {len(turns)} turn(s), so it cannot be forked at '
                f"turn {keep}.",
            )
        origin = turns[keep - 1]["hash"] if keep else records[0]["hash"]
        header = self.create(target, agent, {"session": source, "turn": keep, "hash": origin})
        for record in records[1:]:
            if record["kind"] == "turn" and record["number"] > keep:
                continue
            if record["kind"] == "summary" and record["upto"] > keep:
                continue
            fields = {k: v for k, v in record.items() if k not in _DROPPED_ON_COPY}
            if record["kind"] == "turn":
                self.append(target, fields)
            else:
                self._copy(target, record["kind"], fields)
        return header

    def verify(self, session_id: str) -> int:
        return sum(1 for record in self.records(session_id) if record["kind"] == "turn")

    def _copy(self, session_id: str, kind: str, fields: dict[str, Any]) -> None:
        with self._lock:
            self._existing(session_id).append({"kind": kind, **fields})

    def _log(self, session_id: str) -> ChainedLog:
        validate_session_id(session_id)
        return ChainedLog(self._directory / f"{session_id}.jsonl", f'Session "{session_id}"')

    def _existing(self, session_id: str) -> ChainedLog:
        log = self._log(session_id)
        if not log.exists():
            raise AgentSdkError(
                "SESSION_NOT_FOUND",
                f'There is no session "{session_id}" in {self._directory}; create it first.',
            )
        return log


def _now() -> str:
    return datetime.now(UTC).isoformat()
