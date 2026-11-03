# Copyright (c) 2026 David Michael Indraputra

"""Sessions: continue a conversation across runs and processes, or fork it."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..agent.base_agent.types import CancellationToken
from ..foundations.contracts import SkillContext
from .session_store import SessionStore, validate_session_id

if TYPE_CHECKING:
    from .agent import Agent, Answer

DEFAULT_HISTORY_TURNS = 20
DEFAULT_HISTORY_CHARS = 8_000
_PROMPT_CHARS = 600
_ANSWER_CHARS = 1_200
_STORED_ANSWER_CHARS = 4_000
_TOOL_CHARS = 160
_TOOLS_PER_TURN = 8


class Session:
    def __init__(
        self,
        agent: Agent,
        store: SessionStore,
        session_id: str,
        history_turns: int,
        history_chars: int,
    ) -> None:
        if history_turns < 1 or history_chars < 1:
            raise ValueError("history_turns and history_chars must each be at least 1.")
        self._agent = agent
        self._store = store
        self._history_turns = history_turns
        self._history_chars = history_chars
        self.id = session_id

    @property
    def turns(self) -> list[dict[str, Any]]:
        return [record for record in self._store.records(self.id) if record["kind"] == "turn"]

    @property
    def forked_from(self) -> dict[str, Any] | None:
        return self._store.records(self.id)[0].get("forked_from")

    @property
    def summary(self) -> dict[str, Any] | None:
        summaries = [r for r in self._store.records(self.id) if r["kind"] == "summary"]
        return summaries[-1] if summaries else None

    def history(self) -> dict[str, Any]:
        summary = self.summary
        covered = summary["upto"] if summary else 0
        all_turns = self.turns
        pending = [record for record in all_turns if record["number"] > covered]
        turns = pending[-self._history_turns :]
        omitted = len(pending) - len(turns)
        kept: list[dict[str, Any]] = []
        used = len(summary["text"]) if summary else 0
        for record in reversed(turns):
            entry = _digest_turn(record)
            size = len(json.dumps(entry, separators=(",", ":")))
            if kept and used + size > self._history_chars:
                omitted += 1
                continue
            kept.append(entry)
            used += size
        kept.reverse()
        digest: dict[str, Any] = {
            "note": "A bounded digest of earlier turns of this session, oldest first.",
            "omitted_turns": omitted,
            "turns": kept,
        }
        if summary:
            digest["summary"] = {"covers_turns": covered, "text": summary["text"]}
        return digest

    def uncompacted_turns(self) -> list[dict[str, Any]]:
        summary = self.summary
        covered = summary["upto"] if summary else 0
        return [record for record in self.turns if record["number"] > covered]

    def compact(self, summary: str, upto: int | None = None) -> int:
        turns = self.turns
        covers = len(turns) if upto is None else upto
        self._store.add_summary(self.id, summary, covers)
        return covers

    async def run(
        self,
        prompt: str,
        *,
        input: dict[str, Any] | None = None,
        criteria: Sequence[str] = (),
        skills: Sequence[SkillContext] = (),
        images: Sequence[Path | dict[str, str]] = (),
        cancellation: CancellationToken | None = None,
    ) -> Answer:
        digest = self.history()
        task_input = dict(input or {})
        if digest["turns"] or "summary" in digest:
            task_input["session_history"] = digest
        answer = await self._agent.run(
            prompt,
            input=task_input,
            criteria=criteria,
            skills=skills,
            images=images,
            session=self.id,
            cancellation=cancellation,
        )
        self._store.append(self.id, _turn_record(prompt, answer))
        return answer

    def run_sync(
        self,
        prompt: str,
        *,
        input: dict[str, Any] | None = None,
        criteria: Sequence[str] = (),
        skills: Sequence[SkillContext] = (),
    ) -> Answer:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run(prompt, input=input, criteria=criteria, skills=skills))
        raise RuntimeError(
            "Session.run_sync was called inside a running event loop; use `await session.run(...)`."
        )

    def fork(self, name: str | None = None, at_turn: int | None = None) -> Session:
        target = validate_session_id(name) if name else f"{self.id}-fork-{uuid.uuid4().hex[:6]}"
        self._store.fork(self.id, target, self._agent.name, at_turn)
        return Session(self._agent, self._store, target, self._history_turns, self._history_chars)

    def verify(self) -> int:
        return self._store.verify(self.id)


def _turn_record(prompt: str, answer: Answer) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "status": answer.status,
        "answer": _clip(answer.text if answer.ok else (answer.reason or ""), _STORED_ANSWER_CHARS),
        "steps": answer.steps,
        "tools": [_clip(episode.summary, _TOOL_CHARS) for episode in answer.result.episodes],
    }


def _digest_turn(record: dict[str, Any]) -> dict[str, Any]:
    tools = record.get("tools", [])
    return {
        "turn": record["number"],
        "prompt": _clip(record["prompt"], _PROMPT_CHARS),
        "status": record["status"],
        "answer": _clip(record["answer"], _ANSWER_CHARS),
        "tools_used": tools[:_TOOLS_PER_TURN],
        "more_tools": max(0, len(tools) - _TOOLS_PER_TURN),
    }


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}...(truncated)"
