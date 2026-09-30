# Copyright (c) 2026 David Michael Indraputra

"""Task-scoped typed episode memory for one BaseAgent invocation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ..foundations.contracts import EpisodeKind, EpisodeSummary
from ..foundations.errors import AgentSdkError


class InMemoryEpisodeGraph:
    """Enforce exploratory/action memory and action-to-exploratory edges only.

    This implements the framework's episode-graph rule set (updated PDF, p. 62):
    exploratory episodes are frontier observations; action episodes may depend
    only on the exploratory episodes they consumed.
    """

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._episodes: dict[str, EpisodeSummary] = {}
        self._payloads: dict[str, Any] = {}
        self._next_id = 1

    def add_exploratory(self, summary: str, payload: Any = None) -> EpisodeSummary:
        return self._add(EpisodeKind.EXPLORATORY, summary, [], payload)

    def add_action(
        self,
        summary: str,
        consumed_episode_ids: list[str] | None = None,
        payload: Any = None,
    ) -> EpisodeSummary:
        dependency_ids = consumed_episode_ids or []
        for episode_id in dependency_ids:
            consumed = self._episodes.get(episode_id)
            if consumed is None:
                raise AgentSdkError(
                    "EPISODE_GRAPH_INVARIANT",
                    f'Action episode references unknown exploratory episode "{episode_id}".',
                )
            if consumed.kind is not EpisodeKind.EXPLORATORY:
                raise AgentSdkError(
                    "EPISODE_GRAPH_INVARIANT",
                    "Action episodes may depend only on exploratory episodes; "
                    f'"{episode_id}" is {consumed.kind.value}.',
                )
        return self._add(EpisodeKind.ACTION, summary, dependency_ids, payload)

    def list(self) -> list[EpisodeSummary]:
        return list(self._episodes.values())

    def _add(
        self,
        kind: EpisodeKind,
        summary: str,
        dependency_ids: list[str],
        payload: Any,
    ) -> EpisodeSummary:
        if not summary.strip():
            raise AgentSdkError("EPISODE_GRAPH_INVARIANT", "Episode summary must be non-empty.")
        episode = EpisodeSummary(
            id=f"episode-{self._next_id}",
            kind=kind,
            summary=summary,
            created_at=self._now().isoformat(),
            dependency_ids=list(dependency_ids),
        )
        self._next_id += 1
        self._episodes[episode.id] = episode
        self._payloads[episode.id] = payload
        return episode
