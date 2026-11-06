"""The agents working right now and the prompts typed while they worked."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

MAIN = "main"


@dataclass
class LiveAgent:
    id: str
    label: str
    token: Any
    agent: Any
    started: float


class LiveWork:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents: dict[str, LiveAgent] = {}
        self._queue: list[str] = []

    def add(self, id: str, label: str, token: Any, agent: Any) -> None:
        with self._lock:
            self._agents[id] = LiveAgent(id, label, token, agent, time.monotonic())

    def remove(self, id: str) -> None:
        with self._lock:
            self._agents.pop(id, None)

    def agents(self) -> list[LiveAgent]:
        with self._lock:
            return list(self._agents.values())

    def cancel(self, id: str) -> bool:
        with self._lock:
            live = self._agents.get(id)
        if live is None:
            return False
        live.token.cancel()
        return True

    def cancel_all(self) -> list[str]:
        ids = [live.id for live in self.agents()]
        for id in ids:
            self.cancel(id)
        return ids

    def tell(self, message: str) -> tuple[list[str], dict[str, str]]:
        delivered: list[str] = []
        refused: dict[str, str] = {}
        for live in self.agents():
            if live.agent is None:
                refused[live.id] = "this agent cannot be told anything while it works"
                continue
            try:
                live.agent.say(message)
            except ValueError as error:
                refused[live.id] = str(error)
            else:
                delivered.append(live.id)
        return delivered, refused

    def enqueue(self, text: str) -> int:
        with self._lock:
            self._queue.append(text)
            return len(self._queue)

    def enqueue_first(self, texts: list[str]) -> None:
        with self._lock:
            self._queue[:0] = texts

    def queued(self) -> list[str]:
        with self._lock:
            return list(self._queue)

    def pop(self) -> str | None:
        with self._lock:
            return self._queue.pop(0) if self._queue else None

    def clear_queue(self) -> int:
        with self._lock:
            count, self._queue = len(self._queue), []
        return count
