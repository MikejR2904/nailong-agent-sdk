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
    only on the exploratory episodes they consumed. The graph keeps summaries
    only: payloads belong to ``InMemoryEpisodeStore`` and the result journal, so
    compaction can actually release them.
    """

    def __init__(
        self, now: Callable[[], datetime] | None = None, *, retained_limit: int | None = None
    ) -> None:
        if retained_limit is not None and retained_limit < 1:
            raise ValueError("retained_limit must be at least 1 when set.")
        self._now = now or (lambda: datetime.now(UTC))
        self._retained_limit = retained_limit
        self._episodes: dict[str, EpisodeSummary] = {}
        self._kinds: dict[str, EpisodeKind] = {}
        self._next_id = 1

    def add_exploratory(self, summary: str, payload: Any = None) -> EpisodeSummary:
        """Add a frontier observation; ``payload`` is accepted for call-site compatibility only."""

        return self._add(EpisodeKind.EXPLORATORY, summary, [])

    def add_action(
        self,
        summary: str,
        consumed_episode_ids: list[str] | None = None,
        payload: Any = None,
    ) -> EpisodeSummary:
        dependency_ids = consumed_episode_ids or []
        for episode_id in dependency_ids:
            consumed_kind = self._kinds.get(episode_id)
            if consumed_kind is None:
                raise AgentSdkError(
                    "EPISODE_GRAPH_INVARIANT",
                    f'Action episode references unknown exploratory episode "{episode_id}".',
                )
            if consumed_kind is not EpisodeKind.EXPLORATORY:
                raise AgentSdkError(
                    "EPISODE_GRAPH_INVARIANT",
                    "Action episodes may depend only on exploratory episodes; "
                    f'"{episode_id}" is {consumed_kind.value}.',
                )
        return self._add(EpisodeKind.ACTION, summary, dependency_ids)

    def list(self) -> list[EpisodeSummary]:
        return list(self._episodes.values())

    @property
    def total(self) -> int:
        return len(self._kinds)

    @property
    def omitted(self) -> int:
        return len(self._kinds) - len(self._episodes)

    def kind_of(self, episode_id: str) -> EpisodeKind | None:
        return self._kinds.get(episode_id)

    def _add(
        self,
        kind: EpisodeKind,
        summary: str,
        dependency_ids: list[str],
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
        self._kinds[episode.id] = kind
        if self._retained_limit is not None and len(self._episodes) > self._retained_limit:
            del self._episodes[next(iter(self._episodes))]
        return episode
