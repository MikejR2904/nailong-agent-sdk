# Copyright (c) 2026 David Michael Indraputra

"""Answers already paid for, keyed by what was asked, so a rerun costs nothing."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from nailong_agent_sdk.integrations.jev.models import JevDecisionRequest, JevDecisionResult

from ..schema import digest_of


def cache_key(request: JevDecisionRequest) -> str:
    return digest_of(
        {
            "model": request.model,
            "state": request.state_digest,
            "questions": request.question_spec.digest,
        }
    )


class AnswerCache:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._results: dict[str, JevDecisionResult] = {}
        if path is not None and path.is_file():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    self._results[entry["key"]] = JevDecisionResult.model_validate(entry["result"])
                except (ValueError, KeyError) as error:
                    raise ValueError(
                        f"line {number} of the answer cache {path} is not a stored answer: "
                        f"{type(error).__name__}: {error}"
                    ) from error

    def __len__(self) -> int:
        return len(self._results)

    def get(self, key: str) -> JevDecisionResult | None:
        with self._lock:
            return self._results.get(key)

    def put(self, key: str, result: JevDecisionResult) -> None:
        with self._lock:
            self._results[key] = result
            if self._path is None:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            entry = {"key": key, "result": result.model_dump(mode="json")}
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
