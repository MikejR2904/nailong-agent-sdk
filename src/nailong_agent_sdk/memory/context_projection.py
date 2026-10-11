# Copyright (c) 2026 David Michael Indraputra

"""Bounded, deterministic model-context projection over durable tool evidence.

The execution journal retains complete structured tool results.  The model receives only
projected observations, episode summaries, and opaque handles.  This implements the
framework's typed episode/compaction boundary rather than replaying a raw transcript.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from ..foundations.atomic_io import replace_atomic, unique_temporary_path
from ..foundations.contracts import (
    AgentPrompt,
    CompactedEpisodeStub,
    ContextProjectionMetadata,
    EpisodeSummary,
    ModelObservation,
    ProjectedToolResult,
    StrictModel,
    ToolCall,
    ToolExecutionResult,
    ToolResultHandle,
)
from ..foundations.hashing import canonical_hash, canonical_json, estimate_tokens, sha256_hex
from ..foundations.identifiers import file_safe_name
from .episode_models import CompactionResult, CompactionStatus
from .episode_store import InMemoryEpisodeStore

_HANDLE_ID = re.compile(r"result-[0-9]{1,12}")
_RUN_INDEX_DIRECTORY = ".runs"
_HIGH_WATER_FILE = ".high-water"


class JournalHandleFootprint(StrictModel):
    handle_id: str
    content_hash: str
    byte_count: int = Field(ge=0)


class JournalFootprint(StrictModel):
    run_id: str
    handles: list[JournalHandleFootprint] = Field(default_factory=list)
    bytes: int = Field(default=0, ge=0)


class ContextProjectionPolicy(StrictModel):
    """Deterministic bounds for the provider-visible context of one agent task."""

    context_token_budget: int = Field(default=12_000, ge=256, le=1_000_000)
    episode_token_budget: int = Field(default=6_000, ge=0, le=1_000_000)
    tool_result_preview_chars: int = Field(default=1_024, ge=32, le=100_000)
    compacted_stub_token_budget: int = Field(default=1_500, ge=0, le=1_000_000)
    compacted_summary_chars: int = Field(default=160, ge=32, le=2_000)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if self.episode_token_budget > self.context_token_budget:
            raise ValueError("episode_token_budget must not exceed context_token_budget")


class ToolResultJournal(Protocol):
    """Durable-capable store for full results that must not be replayed verbatim."""

    def record(self, call: ToolCall, result: ToolExecutionResult) -> ToolResultHandle: ...

    def read(self, handle_id: str) -> dict[str, Any]: ...


def journal_for_run(journal: ToolResultJournal, run_id: str) -> ToolResultJournal:
    scoped = getattr(journal, "for_run", None)
    return scoped(run_id) if callable(scoped) else journal


class InMemoryToolResultJournal:
    """In-process journal for a bounded task invocation and deterministic tests."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._next_id = 1

    def record(self, call: ToolCall, result: ToolExecutionResult) -> ToolResultHandle:
        payload = {"call": call.model_dump(mode="json"), "result": result.model_dump(mode="json")}
        return self._store(payload)

    def read(self, handle_id: str) -> dict[str, Any]:
        if handle_id not in self._records:
            raise ValueError(f'Unknown tool result handle "{handle_id}".')
        return self._records[handle_id]

    def for_run(self, run_id: str) -> ToolResultJournal:
        return self

    def _store(self, payload: dict[str, Any]) -> ToolResultHandle:
        encoded = canonical_json(payload).encode("utf-8")
        handle = ToolResultHandle(
            handle_id=f"result-{self._next_id}",
            content_hash=sha256_hex(encoded),
            byte_count=len(encoded),
            truncated=False,
        )
        self._next_id += 1
        self._records[handle.handle_id] = payload
        return handle


class FileToolResultJournal(InMemoryToolResultJournal):
    """Atomic file-backed journal suitable for a persisted run root.

    It stores complete structured tool results below `.agent-tool-results/` while
    returning only opaque metadata to the model-facing projection.
    """

    def __init__(self, run_root: Path, *, read_only: bool = False) -> None:
        super().__init__()
        self._root = run_root.resolve() / ".agent-tool-results"
        self._read_only = read_only
        if not read_only:
            self._root.mkdir(parents=True, exist_ok=True)
        self._next_id = _next_handle_number(self._root)

    def _require_writable(self, operation: str) -> None:
        if self._read_only:
            raise RuntimeError(
                f'Tool result journal at "{self._root}" was opened read-only; '
                f'"{operation}" is not available.'
            )

    def record(self, call: ToolCall, result: ToolExecutionResult) -> ToolResultHandle:
        self._require_writable("record")
        payload = {"call": call.model_dump(mode="json"), "result": result.model_dump(mode="json")}
        encoded = canonical_json(payload)
        while True:
            handle_id = f"result-{self._next_id}"
            self._next_id += 1
            try:
                with (self._root / f"{handle_id}.json").open("x", encoding="utf-8") as stream:
                    stream.write(encoded)
            except FileExistsError:
                continue
            break
        raw = encoded.encode("utf-8")
        self._records[handle_id] = payload
        return ToolResultHandle(
            handle_id=handle_id,
            content_hash=sha256_hex(raw),
            byte_count=len(raw),
            truncated=False,
        )

    def for_run(self, run_id: str) -> ToolResultJournal:
        return _RunScopedJournal(self, run_id)

    def handles_of(self, run_id: str) -> list[str]:
        index = self._index_path(run_id)
        if not index.is_file():
            return []
        listed = dict.fromkeys(
            line.strip() for line in index.read_text(encoding="utf-8").splitlines() if line.strip()
        )
        return list(listed)

    def footprint(self, run_id: str) -> JournalFootprint:
        handles: list[JournalHandleFootprint] = []
        for handle_id in self.handles_of(run_id):
            target = self._root / f"{handle_id}.json"
            if target.is_file():
                handles.append(
                    JournalHandleFootprint(
                        handle_id=handle_id,
                        content_hash=journal_content_hash(self._root, handle_id),
                        byte_count=target.stat().st_size,
                    )
                )
        return JournalFootprint(
            run_id=run_id, handles=handles, bytes=sum(item.byte_count for item in handles)
        )

    def prune_run(self, run_id: str, *, dry_run: bool = False) -> JournalFootprint:
        if not dry_run:
            self._require_writable("prune_run")
        footprint = self.footprint(run_id)
        if dry_run:
            return footprint
        numbers = [int(item.handle_id.removeprefix("result-")) for item in footprint.handles]
        if numbers:
            self._raise_high_water(max(numbers))
        for item in footprint.handles:
            (self._root / f"{item.handle_id}.json").unlink(missing_ok=True)
        self._index_path(run_id).unlink(missing_ok=True)
        return footprint

    def _index_handle(self, run_id: str, handle_id: str) -> None:
        index = self._index_path(run_id)
        index.parent.mkdir(parents=True, exist_ok=True)
        with index.open("a", encoding="utf-8") as stream:
            stream.write(f"{handle_id}\n")

    def _index_path(self, run_id: str) -> Path:
        return self._root / _RUN_INDEX_DIRECTORY / f"{file_safe_name(run_id)}.handles"

    def _raise_high_water(self, number: int) -> None:
        if number <= _read_high_water(self._root):
            return
        target = self._root / _HIGH_WATER_FILE
        temporary = unique_temporary_path(target)
        temporary.write_text(str(number), encoding="utf-8")
        replace_atomic(temporary, target)

    def read(self, handle_id: str) -> dict[str, Any]:
        if handle_id in self._records:
            return super().read(handle_id)
        if _HANDLE_ID.fullmatch(handle_id) is None:
            raise ValueError(
                f"Tool result handle {handle_id!r} is not valid: handles look like "
                '"result-<number>".'
            )
        target = self._root / f"{handle_id}.json"
        if not target.is_file():
            raise ValueError(f'Unknown tool result handle "{handle_id}".')
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ValueError(
                f'Tool result handle "{handle_id}" is corrupt: {type(error).__name__}: {error}'
            ) from error
        self._records[handle_id] = payload
        return payload


@dataclass(frozen=True)
class ContextProjection:
    observations: tuple[ModelObservation, ...]
    episodes: tuple[EpisodeSummary, ...]
    metadata: ContextProjectionMetadata
    compaction: CompactionResult
    compacted_episodes: tuple[CompactedEpisodeStub, ...] = ()


class ContextProjector:
    """Build a bounded context view from immutable prompt and typed task memory."""

    def __init__(self, policy: ContextProjectionPolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> ContextProjectionPolicy:
        return self._policy

    def project(
        self,
        prompt: AgentPrompt,
        observations: Sequence[ModelObservation],
        episode_summaries: Sequence[EpisodeSummary],
        memory: InMemoryEpisodeStore,
        protected_episode_ids: frozenset[str] = frozenset(),
        relevance_query: str = "",
    ) -> ContextProjection:
        """Compact durable episodes, then retain bounded projected evidence.

        ``relevance_query`` is deterministic task-scoped text supplied by the
        harness.  The store records only its digest in the PASK decision dossier.
        """

        compaction = memory.compact(
            self._policy.episode_token_budget,
            protected_episode_ids=protected_episode_ids,
            relevance_query=relevance_query,
        )
        if compaction.status is CompactionStatus.CONTEXT_DEADLOCK:
            return ContextProjection(
                observations=(),
                episodes=(),
                metadata=ContextProjectionMetadata(
                    estimated_tokens=estimate_tokens(prompt),
                    context_token_budget=self._policy.context_token_budget,
                    episode_token_budget=self._policy.episode_token_budget,
                    compacted_episode_ids=compaction.compacted_episode_ids,
                    omitted_observation_count=len(observations),
                ),
                compaction=compaction,
            )

        retained_episodes = tuple(
            summary
            for summary in episode_summaries
            if (record := memory.get(summary.id)) is not None and record.state.value != "compacted"
        )
        eligible_observations = [
            observation
            for observation in observations
            if observation.episode_id is None
            or (
                (record := memory.get(observation.episode_id)) is not None
                and record.state.value != "compacted"
            )
        ]
        base_tokens = estimate_tokens(prompt) + estimate_tokens(retained_episodes)
        stubs, omitted_stubs, stub_tokens = _compacted_stubs(
            [
                summary
                for summary in episode_summaries
                if (record := memory.get(summary.id)) is not None
                and record.state.value == "compacted"
            ],
            observations,
            self._policy.compacted_summary_chars,
            min(
                self._policy.compacted_stub_token_budget,
                max(0, self._policy.context_token_budget - base_tokens),
            ),
        )
        base_tokens += stub_tokens
        selected: list[ModelObservation] = []
        omitted = len(observations) - len(eligible_observations)
        for observation in reversed(eligible_observations):
            candidate_tokens = estimate_tokens(observation)
            if base_tokens + candidate_tokens > self._policy.context_token_budget:
                omitted += 1
                continue
            selected.append(observation)
            base_tokens += candidate_tokens
        selected.reverse()
        return ContextProjection(
            observations=tuple(selected),
            episodes=retained_episodes,
            metadata=ContextProjectionMetadata(
                estimated_tokens=base_tokens,
                context_token_budget=self._policy.context_token_budget,
                episode_token_budget=self._policy.episode_token_budget,
                compacted_episode_ids=compaction.compacted_episode_ids,
                omitted_observation_count=omitted,
                omitted_compacted_count=omitted_stubs,
            ),
            compaction=compaction,
            compacted_episodes=stubs,
        )

    def project_tool_result(
        self,
        call: ToolCall,
        result: ToolExecutionResult,
        journal: ToolResultJournal,
    ) -> ProjectedToolResult:
        handle = journal.record(call, result)
        preview, truncated = _bounded_preview(result.output, self._policy.tool_result_preview_chars)
        return ProjectedToolResult(
            status=result.status,
            handle=handle.model_copy(update={"truncated": truncated}),
            preview=preview,
            error=_truncate_text(result.error, self._policy.tool_result_preview_chars),
        )


def _compacted_stubs(
    summaries: Sequence[EpisodeSummary],
    observations: Sequence[ModelObservation],
    summary_chars: int,
    token_allowance: int,
) -> tuple[tuple[CompactedEpisodeStub, ...], int, int]:
    results = {
        observation.episode_id: observation
        for observation in observations
        if observation.episode_id is not None and observation.result is not None
    }
    selected: list[CompactedEpisodeStub] = []
    omitted = 0
    used = 0
    for summary in reversed(summaries):
        observation = results.get(summary.id)
        projected = observation.result if observation is not None else None
        stub = CompactedEpisodeStub(
            episode_id=summary.id,
            kind=summary.kind,
            summary=_truncate_text(summary.summary, summary_chars) or "",
            tool_name=observation.tool_name if observation is not None else None,
            status=projected.status if projected is not None else None,
            iteration=observation.iteration if observation is not None else None,
            handle_id=projected.handle.handle_id if projected is not None else None,
        )
        cost = estimate_tokens(stub.model_dump(mode="json"))
        if used + cost > token_allowance:
            omitted += 1
            continue
        selected.append(stub)
        used += cost
    selected.reverse()
    return tuple(selected), omitted, used


def _bounded_preview(value: Any, max_chars: int) -> tuple[Any | None, bool]:
    if value is None:
        return None, False
    encoded = canonical_json(value)
    if len(encoded) <= max_chars:
        return value, False
    return {
        "kind": "truncated-json-preview",
        "preview": encoded[:max_chars],
        "full_result_available_via_handle": True,
    }, True


def _truncate_text(value: str | None, max_chars: int) -> str | None:
    if value is None or len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}… [truncated]"


def journal_content_hash(journal_root: Path, handle_id: str) -> str:
    if _HANDLE_ID.fullmatch(handle_id) is None:
        raise ValueError(
            f'Tool result handle {handle_id!r} is not valid: handles look like "result-<number>".'
        )
    target = journal_root / f"{handle_id}.json"
    if not target.is_file():
        raise ValueError(f'Tool result handle "{handle_id}" has no journal file at "{target}".')
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(
            f'Tool result handle "{handle_id}" is corrupt: {type(error).__name__}: {error}'
        ) from error
    return canonical_hash(payload)


def _next_handle_number(root: Path) -> int:
    highest = _read_high_water(root)
    for path in root.glob("result-*.json"):
        suffix = path.stem.removeprefix("result-")
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest + 1


def _read_high_water(root: Path) -> int:
    target = root / _HIGH_WATER_FILE
    if not target.is_file():
        return 0
    text = target.read_text(encoding="utf-8").strip()
    return int(text) if text.isdigit() else 0


class _RunScopedJournal:
    def __init__(self, journal: FileToolResultJournal, run_id: str) -> None:
        self._journal = journal
        self._run_id = run_id

    def record(self, call: ToolCall, result: ToolExecutionResult) -> ToolResultHandle:
        handle = self._journal.record(call, result)
        self._journal._index_handle(self._run_id, handle.handle_id)
        return handle

    def read(self, handle_id: str) -> dict[str, Any]:
        return self._journal.read(handle_id)
