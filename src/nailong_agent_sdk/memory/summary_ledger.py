# Copyright (c) 2026 David Michael Indraputra

"""The summaries of one episode store and where each one lives.

A summary is active until a parent supersedes it and archived when it is moved to durable
storage. The ledger keeps one small reference for every summary it has ever held, so a
reference from a record or a parent resolves to the summary wherever it is.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..foundations.errors import AgentSdkError
from ..foundations.hashing import sha256_hex
from .episode_archive import summary_snapshot_text
from .episode_models import (
    QuarantinedSummary,
    SemanticEpisodeSummary,
    SummaryArchive,
    SummaryLifecycle,
    SummaryRef,
)
from .semantic_compaction import episode_order_key


class SummaryLedger:
    def __init__(self, archive: SummaryArchive | None) -> None:
        self._archive = archive
        self._resident: dict[str, SemanticEpisodeSummary] = {}
        self._index: dict[str, SummaryRef] = {}
        self._covers: dict[str, str] = {}
        self._quarantined: list[QuarantinedSummary] = []

    @property
    def archive(self) -> SummaryArchive | None:
        return self._archive

    def resident(self) -> list[SemanticEpisodeSummary]:
        return sorted(self._resident.values(), key=lambda item: episode_order_key(item.summary_id))

    def active(self) -> list[SemanticEpisodeSummary]:
        return [
            item
            for item in self.resident()
            if self._index[item.summary_id].lifecycle is SummaryLifecycle.ACTIVE
        ]

    def refs(self) -> list[SummaryRef]:
        return sorted(self._index.values(), key=lambda ref: episode_order_key(ref.summary_id))

    def ref(self, summary_id: str) -> SummaryRef | None:
        return self._index.get(summary_id)

    def lifecycle_of(self, summary_id: str) -> SummaryLifecycle | None:
        ref = self._index.get(summary_id)
        return ref.lifecycle if ref is not None else None

    def is_resident(self, summary_id: str) -> bool:
        return summary_id in self._resident

    def covering(self, episode_id: str) -> str | None:
        return self._covers.get(episode_id)

    def quarantined(self) -> list[QuarantinedSummary]:
        return list(self._quarantined)

    def load(self, summary_id: str) -> SemanticEpisodeSummary:
        ref = self._index.get(summary_id)
        if ref is None:
            raise AgentSdkError(
                "SUMMARY_UNKNOWN", f'Summary "{summary_id}" is not known to this store.'
            )
        resident = self._resident.get(summary_id)
        if resident is not None:
            return resident
        if ref.archive_ref is None or self._archive is None:
            raise AgentSdkError(
                "SUMMARY_NOT_RETRIEVABLE",
                f'Summary "{summary_id}" is neither resident nor archived '
                f"(lifecycle {ref.lifecycle.value}).",
                {"summary_id": summary_id},
            )
        loaded = self._archive.get_summary(ref.archive_ref)
        if loaded.integrity_hash != ref.integrity_hash:
            raise AgentSdkError(
                "SUMMARY_ARCHIVE_CORRUPT",
                f'Archived summary "{summary_id}" ({ref.archive_ref}) differs from the integrity '
                "hash the store recorded for it.",
                {"summary_id": summary_id, "archive_id": ref.archive_ref},
            )
        return loaded

    def top_for(self, summary_id: str) -> str:
        current = summary_id
        seen = {current}
        while True:
            ref = self._index.get(current)
            if ref is None or ref.parent_summary_id is None or ref.parent_summary_id in seen:
                return current
            current = ref.parent_summary_id
            seen.add(current)

    def add(self, summary: SemanticEpisodeSummary, covers: Iterable[str] = ()) -> None:
        self._resident[summary.summary_id] = summary
        self._index[summary.summary_id] = SummaryRef(
            summary_id=summary.summary_id,
            level=summary.level,
            integrity_hash=summary.integrity_hash,
            summary_tokens=summary.summary_tokens,
        )
        for episode_id in covers:
            self._covers[episode_id] = summary.summary_id

    def supersede(
        self, parent: SemanticEpisodeSummary, children: Sequence[SemanticEpisodeSummary]
    ) -> None:
        self.add(parent)
        for child in children:
            ref = self._index[child.summary_id]
            self._index[child.summary_id] = ref.model_copy(
                update={
                    "lifecycle": SummaryLifecycle.SUPERSEDED,
                    "parent_summary_id": parent.summary_id,
                }
            )

    def awaiting_archive(self) -> list[SemanticEpisodeSummary]:
        return [
            summary
            for summary in self.resident()
            if self._index[summary.summary_id].lifecycle is SummaryLifecycle.SUPERSEDED
        ]

    def write_to_archive(self, summary: SemanticEpisodeSummary) -> str:
        if self._archive is None:
            raise AgentSdkError(
                "SUMMARY_ARCHIVE_UNAVAILABLE",
                f'Summary "{summary.summary_id}" cannot be archived: this store has no summary '
                "archive.",
            )
        receipt = self._archive.put_summary(summary)
        confirmed = self._archive.get_summary(receipt.archive_id)
        if summary_snapshot_text(confirmed) != summary_snapshot_text(summary):
            raise AgentSdkError(
                "SUMMARY_ARCHIVE_CORRUPT",
                f'Archived copy of summary "{summary.summary_id}" differs from the original; '
                "it stays resident.",
                {"summary_id": summary.summary_id},
            )
        if sha256_hex(summary_snapshot_text(summary)) != receipt.content_hash:
            raise AgentSdkError(
                "SUMMARY_ARCHIVE_CORRUPT",
                f'Archive receipt for summary "{summary.summary_id}" carries a different hash.',
                {"summary_id": summary.summary_id},
            )
        return receipt.archive_id

    def mark_archived(
        self, summary_id: str, archive_ref: str, *, lifecycle: SummaryLifecycle | None = None
    ) -> None:
        ref = self._index[summary_id]
        self._index[summary_id] = ref.model_copy(
            update={
                "archive_ref": archive_ref,
                "lifecycle": lifecycle or ref.lifecycle,
            }
        )
        self._resident.pop(summary_id, None)

    def quarantine(self, item: QuarantinedSummary, limit: int) -> None:
        if limit <= 0:
            return
        self._quarantined.append(item)
        del self._quarantined[:-limit]

    def export(
        self,
    ) -> tuple[list[SemanticEpisodeSummary], list[SummaryRef], list[QuarantinedSummary]]:
        return self.resident(), self.refs(), self.quarantined()

    def adopt(
        self,
        summaries: Iterable[SemanticEpisodeSummary],
        refs: Iterable[SummaryRef],
        covers: dict[str, str],
        quarantined: Iterable[QuarantinedSummary],
    ) -> None:
        self._resident = {summary.summary_id: summary for summary in summaries}
        self._index = {ref.summary_id: ref for ref in refs}
        self._covers = dict(covers)
        self._quarantined = list(quarantined)
