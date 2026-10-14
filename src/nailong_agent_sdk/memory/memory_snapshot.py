# Copyright (c) 2026 David Michael Indraputra

"""One-file, generation-numbered snapshots of episodic memory and their validation.

A snapshot carries the live records, the evicted index, the resident summaries, the summary
index and the counters together, so no combination of files from different generations can
be loaded. Validation checks every reference and hash and returns every problem it finds.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.hashing import canonical_hash
from .episode_archive import archive_id_for
from .episode_models import (
    EpisodeArchive,
    EpisodeRecord,
    EpisodeState,
    EvictedEpisode,
    QuarantinedSummary,
    SemanticEpisodeSummary,
    SummaryArchive,
    SummaryLifecycle,
    SummaryRef,
)

SNAPSHOT_SCHEMA = "episode-memory-snapshot-v1"
_MAX_PROBLEMS = 20


class EpisodeMemorySnapshot(StrictModel):
    schema_version: str = SNAPSHOT_SCHEMA
    generation: int = Field(ge=1)
    created_at_utc: str
    semantic_policy_hash: str | None = None
    next_episode_number: int = Field(ge=1)
    next_access_sequence: int = Field(ge=1)
    next_summary_number: int = Field(ge=1)
    records: list[EpisodeRecord] = Field(default_factory=list)
    evicted: list[EvictedEpisode] = Field(default_factory=list)
    summaries: list[SemanticEpisodeSummary] = Field(default_factory=list)
    summary_index: list[SummaryRef] = Field(default_factory=list)
    group_attempts: dict[str, int] = Field(default_factory=dict)
    quarantined: list[QuarantinedSummary] = Field(default_factory=list)
    checkpoint_graph_hash: str = ""
    integrity_hash: str = ""

    def computed_hash(self) -> str:
        return canonical_hash(
            self.model_copy(update={"integrity_hash": ""}).model_dump(mode="json")
        )

    def sealed(self) -> EpisodeMemorySnapshot:
        return self.model_copy(update={"integrity_hash": self.computed_hash()})

    def intact(self) -> bool:
        return bool(self.integrity_hash) and self.integrity_hash == self.computed_hash()


class SnapshotRejection(StrictModel):
    path: str
    generation: int | None = None
    reasons: list[str]


class RestoreReport(StrictModel):
    generation: int = Field(ge=1)
    path: str
    records: int = Field(ge=0)
    evicted: int = Field(ge=0)
    resident_summaries: int = Field(ge=0)
    archived_summaries: int = Field(ge=0)
    rejected: list[SnapshotRejection] = Field(default_factory=list)


def snapshot_problems(
    snapshot: EpisodeMemorySnapshot,
    *,
    archive: EpisodeArchive | None,
    summary_archive: SummaryArchive | None,
    verify_archives: bool = True,
) -> list[str]:
    problems: list[str] = []

    def add(message: str) -> None:
        if len(problems) < _MAX_PROBLEMS:
            problems.append(message)

    if snapshot.schema_version != SNAPSHOT_SCHEMA:
        add(f'schema_version is "{snapshot.schema_version}", expected "{SNAPSHOT_SCHEMA}"')
    if not snapshot.intact():
        add("the snapshot's integrity hash does not match its content")
    records = {record.id: record for record in snapshot.records}
    if len(records) != len(snapshot.records):
        add("two records share an episode id")
    evicted = {item.episode_id: item for item in snapshot.evicted}
    if len(evicted) != len(snapshot.evicted):
        add("two evicted entries share an episode id")
    both = sorted(set(records) & set(evicted))
    if both:
        add(f"episodes {both[:5]} are both active records and evicted entries")
    index = {ref.summary_id: ref for ref in snapshot.summary_index}
    if len(index) != len(snapshot.summary_index):
        add("two summary index entries share a summary id")
    resident = {summary.summary_id: summary for summary in snapshot.summaries}
    for summary_id, summary in resident.items():
        ref = index.get(summary_id)
        if ref is None:
            add(f'resident summary "{summary_id}" has no index entry')
            continue
        if not summary.intact():
            add(f'summary "{summary_id}" fails its own integrity hash')
        if summary.integrity_hash != ref.integrity_hash:
            add(f'summary "{summary_id}" differs from the integrity hash its index entry records')
    for summary_id, ref in index.items():
        _check_summary_ref(summary_id, ref, resident, index, summary_archive, verify_archives, add)
    for summary in snapshot.summaries:
        _check_summary_sources(summary, records, evicted, index, add)
    for record in snapshot.records:
        _check_record(record, records, evicted, index, archive, verify_archives, add)
    for item in snapshot.evicted:
        if item.summary_id is not None and item.summary_id not in index:
            add(f'evicted episode "{item.episode_id}" names unknown summary "{item.summary_id}"')
        if archive is not None and not archive.has(item.archive_id):
            add(f'evicted episode "{item.episode_id}" has no archive entry "{item.archive_id}"')
        if item.archive_id != archive_id_for(item.content_hash):
            add(f'evicted episode "{item.episode_id}" has an archive id that is not its hash')
    return problems


def _check_summary_ref(
    summary_id: str,
    ref: SummaryRef,
    resident: dict[str, SemanticEpisodeSummary],
    index: dict[str, SummaryRef],
    summary_archive: SummaryArchive | None,
    verify_archives: bool,
    add: Callable[[str], None],
) -> None:
    if ref.lifecycle is SummaryLifecycle.ACTIVE and summary_id not in resident:
        add(f'active summary "{summary_id}" is not resident')
    if summary_id not in resident and ref.archive_ref is None:
        add(f'summary "{summary_id}" is neither resident nor archived')
    if ref.parent_summary_id is not None and ref.parent_summary_id not in index:
        add(f'summary "{summary_id}" names unknown parent "{ref.parent_summary_id}"')
    if ref.archive_ref is None:
        return
    if summary_archive is None:
        add(f'summary "{summary_id}" is archived but this archive stores no summaries')
        return
    if not summary_archive.has_summary(ref.archive_ref):
        add(f'archived summary "{summary_id}" ({ref.archive_ref}) is missing')
        return
    if not verify_archives:
        return
    try:
        loaded = summary_archive.get_summary(ref.archive_ref)
    except Exception as error:
        add(f'archived summary "{summary_id}" is unreadable: {error}'[:300])
        return
    if loaded.integrity_hash != ref.integrity_hash:
        add(f'archived summary "{summary_id}" differs from its index entry')


def _check_summary_sources(
    summary: SemanticEpisodeSummary,
    records: dict[str, EpisodeRecord],
    evicted: dict[str, EvictedEpisode],
    index: dict[str, SummaryRef],
    add: Callable[[str], None],
) -> None:
    for child in summary.child_summary_ids:
        if child not in index:
            add(f'summary "{summary.summary_id}" names unknown child summary "{child}"')
    for position, episode_id in enumerate(summary.source_episode_ids):
        source = records.get(episode_id)
        if source is not None:
            source_hash, source_archive = source.content_hash, source.archive_id
        else:
            evicted_source = evicted.get(episode_id)
            if evicted_source is None:
                add(f'summary "{summary.summary_id}" cites unknown episode "{episode_id}"')
                continue
            source_hash, source_archive = evicted_source.content_hash, evicted_source.archive_id
        if source_hash is not None and summary.source_hashes[position] != source_hash:
            add(
                f'summary "{summary.summary_id}" records a different content hash for '
                f'"{episode_id}" than the episode itself'
            )
        if source_archive is not None and summary.archive_ids[position] != source_archive:
            add(
                f'summary "{summary.summary_id}" records a different archive id for '
                f'"{episode_id}" than the episode itself'
            )


def _check_record(
    record: EpisodeRecord,
    records: dict[str, EpisodeRecord],
    evicted: dict[str, EvictedEpisode],
    index: dict[str, SummaryRef],
    archive: EpisodeArchive | None,
    verify_archives: bool,
    add: Callable[[str], None],
) -> None:
    if record.summary_id is not None and record.summary_id not in index:
        add(f'episode "{record.id}" names unknown summary "{record.summary_id}"')
    if record.archive_id is not None:
        if record.content_hash is None:
            add(f'episode "{record.id}" has an archive id but no content hash')
        elif record.archive_id != archive_id_for(record.content_hash):
            add(f'episode "{record.id}" has an archive id that is not its content hash')
        if record.content is None:
            _check_released(record, archive, verify_archives, add)
    elif record.content is None and record.summary_id is not None:
        add(f'episode "{record.id}" has a summary but neither a payload nor an archive entry')
    for dependency in record.depends_on:
        target = records.get(dependency)
        if target is None and dependency not in evicted:
            add(f'episode "{record.id}" depends on unknown episode "{dependency}"')
        elif record.state is EpisodeState.COMPACTED or target is None:
            continue
        elif target.state is EpisodeState.COMPACTED:
            add(f'live episode "{record.id}" depends on compacted episode "{dependency}"')
        elif record.id not in target.depended_on_by:
            add(f'episode "{dependency}" does not list its dependent "{record.id}"')


def _check_released(
    record: EpisodeRecord,
    archive: EpisodeArchive | None,
    verify_archives: bool,
    add: Callable[[str], None],
) -> None:
    assert record.archive_id is not None
    if archive is None or not archive.has(record.archive_id):
        add(f'episode "{record.id}" released its payload but "{record.archive_id}" is missing')
        return
    if not verify_archives:
        return
    try:
        archived = archive.get(record.archive_id)
    except Exception as error:
        add(f'archived episode "{record.id}" is unreadable: {error}'[:300])
        return
    if archived.id != record.id:
        add(f'archive entry "{record.archive_id}" holds "{archived.id}", not "{record.id}"')
