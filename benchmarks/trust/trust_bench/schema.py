# Copyright (c) 2026 David Michael Indraputra

"""The common event schema every system under test is mapped into."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

EventKey = tuple[Any, ...]
StepRef = tuple[str, int]


class Kind(StrEnum):
    MODEL_CALL = "model-call"
    TOOL_CALL = "tool-call"
    TOOL_RESULT = "tool-result"
    WRITE = "write"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_of(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Event:
    kind: Kind
    agent_id: str | None = None
    model_index: int | None = None
    call_id: str | None = None
    name: str | None = None
    path: str | None = None
    digest: str | None = None
    status: str | None = None
    content: str | None = None
    truncated: bool = False

    @property
    def key(self) -> EventKey:
        if self.kind is Kind.MODEL_CALL:
            return ("M", self.agent_id, self.model_index)
        if self.kind is Kind.TOOL_CALL:
            return ("C", self.call_id)
        if self.kind is Kind.TOOL_RESULT:
            return ("R", self.call_id)
        return ("W", self.path, self.digest)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "agent_id": self.agent_id,
            "model_index": self.model_index,
            "call_id": self.call_id,
            "name": self.name,
            "path": self.path,
            "digest": self.digest,
            "status": self.status,
            "content": self.content,
            "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        return cls(
            kind=Kind(data["kind"]),
            agent_id=data.get("agent_id"),
            model_index=data.get("model_index"),
            call_id=data.get("call_id"),
            name=data.get("name"),
            path=data.get("path"),
            digest=data.get("digest"),
            status=data.get("status"),
            content=data.get("content"),
            truncated=bool(data.get("truncated", False)),
        )


@dataclass(frozen=True)
class ArtifactVersion:
    path: str
    digest: str
    call_id: str | None = None
    agent_id: str | None = None
    sequence: int = 0


@dataclass(frozen=True)
class ArtifactTruth:
    path: str
    digest: str
    call_id: str
    agent_id: str
    model_index: int
    sources: frozenset[str]


@dataclass
class SystemRecords:
    system: str
    events: list[Event]
    versions: dict[str, list[ArtifactVersion]]
    fetch_blob: Callable[[str], bytes | None]
    notes: list[str] = field(default_factory=list)


@dataclass
class RunTruth:
    case_id: str
    events: list[Event]
    artifacts: dict[str, ArtifactTruth]
