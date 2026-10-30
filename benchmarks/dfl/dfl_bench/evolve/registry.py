"""Versioned model registry with gate-checked promotion and rollback. Append-only history."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from nailong_agent_sdk.foundations.errors import AgentSdkError
from nailong_agent_sdk.memory.utility_models import LoadedUtilityModel, load_utility_model

from .gate import GateDecision


class ModelRegistry:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._versions = self._root / "versions"
        self._history = self._root / "history.jsonl"
        self._versions.mkdir(parents=True, exist_ok=True)
        self._history.touch()
        self._events = [
            json.loads(line)
            for line in self._history.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _append(self, event: dict[str, Any]) -> dict[str, Any]:
        event = {"seq": len(self._events) + 1, **event}
        with self._history.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
        self._events.append(event)
        return event

    def _registered(self, version: str) -> dict[str, Any]:
        for event in self._events:
            if event["event"] == "register" and event["version"] == version:
                return event
        raise AgentSdkError(
            "REGISTRY_VERSION_UNKNOWN",
            f'Model version "{version}" is not registered in "{self._root}".',
            {"version": version},
        )

    def _promoted_stack(self) -> list[str]:
        stack: list[str] = []
        for event in self._events:
            if event["event"] == "promote":
                stack.append(event["version"])
            elif event["event"] == "rollback" and stack:
                stack.pop()
            elif event["event"] == "disable":
                stack.clear()
        return stack

    def register(self, artifact: Path, gate: GateDecision) -> str:
        loaded = load_utility_model(artifact)
        target = self._versions / f"{loaded.digest}.json"
        if not target.exists():
            shutil.copyfile(artifact, target)
        self._append(
            {
                "event": "register",
                "version": loaded.digest,
                "gate": gate.to_json(),
                "training": loaded.training,
            }
        )
        return loaded.digest

    def promote(self, version: str, *, approver: str, reason: str) -> None:
        if not approver.strip() or not reason.strip():
            raise ValueError("Promotion requires a non-empty approver and reason.")
        registered = self._registered(version)
        gate = registered["gate"]
        if not gate["promote"]:
            raise AgentSdkError(
                "REGISTRY_GATE_NOT_PASSED",
                f'Version "{version}" did not pass the promotion gate: '
                + "; ".join(gate["reasons"]),
                {"version": version},
            )
        self._append(
            {"event": "promote", "version": version, "approver": approver, "reason": reason}
        )

    def rollback(self, *, approver: str, reason: str) -> str | None:
        if not approver.strip() or not reason.strip():
            raise ValueError("Rollback requires a non-empty approver and reason.")
        stack = self._promoted_stack()
        if not stack:
            raise AgentSdkError(
                "REGISTRY_NOTHING_TO_ROLL_BACK",
                f'No version has been promoted in "{self._root}"; the static default is already '
                "in effect.",
            )
        self._append(
            {"event": "rollback", "from": stack[-1], "approver": approver, "reason": reason}
        )
        remaining = self._promoted_stack()
        return remaining[-1] if remaining else None

    def disable(self, *, approver: str, reason: str) -> None:
        """Stop serving every learned version; static PCKP is in effect until the next promote."""

        if not approver.strip() or not reason.strip():
            raise ValueError("Disabling requires a non-empty approver and reason.")
        self._append(
            {
                "event": "disable",
                "from": self.current_version(),
                "approver": approver,
                "reason": reason,
            }
        )

    def current_version(self) -> str | None:
        stack = self._promoted_stack()
        return stack[-1] if stack else None

    def current(self) -> LoadedUtilityModel | None:
        version = self.current_version()
        return None if version is None else self.load(version)

    def load(self, version: str) -> LoadedUtilityModel:
        self._registered(version)
        return load_utility_model(self._versions / f"{version}.json")

    def history(self) -> list[dict[str, Any]]:
        return list(self._events)
