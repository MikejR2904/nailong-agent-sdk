# Copyright (c) 2026 David Michael Indraputra

"""Find which write introduced a defect into an artifact and which agent made that write."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel
from ..foundations.suffix_search import SuffixStrategy, find_suffix_start
from ..tools.artifacts import ArtifactStore, ArtifactVersion

DEFECT_SCHEMA_VERSION = "agent-sdk-defect-attribution-v1"
MAX_LATER_WRITERS = 20
MAX_LISTED_PROBES = 64
MAX_LISTED_PATHS_IN_ERROR = 20

type DefectCheck = Callable[[bytes, ArtifactVersion], bool]


class DefectWriter(StrictModel):
    """One write of an artifact and, as far as the write recorded it, who made it."""

    sequence: int = Field(ge=1)
    write_sequence: int = Field(ge=1)
    occurrence_id: str
    written_at_utc: str
    sha256: str
    agent_identity: str | None = None
    agent_run_id: str | None = None
    role: str | None = None
    node_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    tool: str | None = None
    tool_call_id: str | None = None
    iteration: int | None = None


class VersionVerdict(StrictModel):
    sequence: int = Field(ge=1)
    sha256: str
    defective: bool


class DefectAttribution(StrictModel):
    """Where in the history of one path a defect began, and who wrote that version."""

    schema_version: str = DEFECT_SCHEMA_VERSION
    relative_path: str
    versions: int = Field(ge=1)
    ok: bool
    check: str
    strategy: str
    evaluations: int = Field(ge=0)
    proven: bool = False
    probes: list[VersionVerdict] = Field(default_factory=list)
    born_defective: bool = False
    introduced_by: DefectWriter | None = None
    last_clean: DefectWriter | None = None
    later_writers: list[DefectWriter] = Field(default_factory=list)
    later_writers_omitted: int = Field(default=0, ge=0)
    summary: str


def attribute_defect(
    run_root: Path,
    relative_path: str,
    is_defective: DefectCheck,
    *,
    strategy: SuffixStrategy | str = SuffixStrategy.GALLOP,
    check: str = "custom check",
) -> DefectAttribution:
    """Locate the write after which an artifact is defective, reading nothing but the history."""

    if not run_root.is_dir():
        raise ValueError(f'Run root "{run_root}" does not exist or is not a directory.')
    chosen = _strategy(strategy)
    if not (run_root / ".agent-artifacts").is_dir():
        raise ValueError(_no_artifacts_message(run_root))
    store = ArtifactStore(run_root, read_only=True)
    versions = store.history(relative_path)
    if not versions:
        raise ValueError(
            f'No write of "{relative_path}" is recorded under "{run_root}"; paths with recorded '
            f"writes: {store.paths(limit=MAX_LISTED_PATHS_IN_ERROR)}."
        )
    verdicts: dict[str, bool] = {}

    def defective(index: int) -> bool:
        version = versions[index]
        if version.sha256 not in verdicts:
            verdicts[version.sha256] = bool(is_defective(store.read_bytes(version.sha256), version))
        return verdicts[version.sha256]

    search = find_suffix_start(len(versions), defective, strategy=chosen)
    probes = [
        VersionVerdict(
            sequence=versions[index].sequence, sha256=versions[index].sha256, defective=bad
        )
        for index, bad in search.probes[:MAX_LISTED_PROBES]
    ]
    if search.start is None:
        return DefectAttribution(
            relative_path=relative_path,
            versions=len(versions),
            ok=True,
            check=check,
            strategy=chosen.value,
            evaluations=len(verdicts),
            proven=search.exact,
            probes=probes,
            last_clean=_writer(versions[-1]),
            summary=(
                f'The latest version ({len(versions)}) of "{relative_path}" passes the check '
                f"({check}); {len(verdicts)} evaluation(s)."
            ),
        )
    introduced = versions[search.start]
    clean = versions[search.last_good] if search.last_good is not None else None
    later = [_writer(item) for item in versions[search.start + 1 :]]
    return DefectAttribution(
        relative_path=relative_path,
        versions=len(versions),
        ok=False,
        check=check,
        strategy=chosen.value,
        evaluations=len(verdicts),
        proven=search.exact,
        probes=probes,
        born_defective=clean is None,
        introduced_by=_writer(introduced),
        last_clean=_writer(clean) if clean is not None else None,
        later_writers=later[:MAX_LATER_WRITERS],
        later_writers_omitted=max(0, len(later) - MAX_LATER_WRITERS),
        summary=_summary(relative_path, len(versions), introduced, clean, check, len(verdicts)),
    )


def content_check(
    *,
    contains: str | None = None,
    lacks: str | None = None,
    matches: str | None = None,
    sha256: str | None = None,
) -> tuple[DefectCheck, str]:
    """Build one of the content checks the command line offers and a sentence describing it."""

    given = {
        name: value
        for name, value in (
            ("contains", contains),
            ("lacks", lacks),
            ("matches", matches),
            ("sha256", sha256),
        )
        if value is not None
    }
    if len(given) != 1:
        raise ValueError(
            f"exactly one of contains, lacks, matches or sha256 is required, got {sorted(given)}."
        )
    name, value = next(iter(given.items()))
    if name == "contains":
        return (
            lambda content, version: value in content.decode("utf-8", errors="replace"),
            f'content contains "{value}"',
        )
    if name == "lacks":
        return (
            lambda content, version: value not in content.decode("utf-8", errors="replace"),
            f'content lacks "{value}"',
        )
    if name == "matches":
        try:
            pattern = re.compile(value)
        except re.error as error:
            raise ValueError(
                f'matches pattern "{value}" is not a valid regular expression: {error}'
            ) from error
        return (
            lambda content, version: (
                pattern.search(content.decode("utf-8", errors="replace")) is not None
            ),
            f"content matches /{value}/",
        )
    return (lambda content, version: version.sha256 == value, f"content sha256 is {value}")


def _artifact_directories(run_root: Path) -> list[str]:
    found: list[str] = []
    for child in sorted(run_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / ".agent-artifacts").is_dir():
            found.append(child.name)
        if child.name == "workspaces":
            found.extend(
                f"workspaces/{item.name}"
                for item in sorted(child.iterdir())
                if (item / ".agent-artifacts").is_dir()
            )
    return found[:MAX_LISTED_PATHS_IN_ERROR]


def _no_artifacts_message(run_root: Path) -> str:
    candidates = _artifact_directories(run_root)
    hint = f" Directories under it that hold artifacts: {candidates}." if candidates else ""
    return (
        f'"{run_root}" holds no .agent-artifacts directory: pass the directory the artifacts were '
        f"written under, a task workspace or the run root of a graph run.{hint}"
    )


def _strategy(value: SuffixStrategy | str) -> SuffixStrategy:
    try:
        return SuffixStrategy(value)
    except ValueError as error:
        allowed = [item.value for item in SuffixStrategy]
        raise ValueError(f'strategy must be one of {allowed}, got "{value}".') from error


def _writer(version: ArtifactVersion) -> DefectWriter:
    manifest = version.manifest
    return DefectWriter(
        sequence=version.sequence,
        write_sequence=version.write_sequence,
        occurrence_id=version.occurrence_id,
        written_at_utc=version.written_at_utc,
        sha256=version.sha256,
        agent_identity=_text(manifest.get("agent_identity")),
        agent_run_id=_text(manifest.get("agent_run_id")),
        role=_text(manifest.get("role")),
        node_id=_text(manifest.get("node_id")),
        run_id=_text(manifest.get("run_id")),
        task_id=_text(manifest.get("task_id")),
        tool=_text(manifest.get("tool")),
        tool_call_id=_text(manifest.get("tool_call_id")),
        iteration=_integer(manifest.get("iteration")),
    )


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _summary(
    relative_path: str,
    count: int,
    introduced: ArtifactVersion,
    clean: ArtifactVersion | None,
    check: str,
    evaluations: int,
) -> str:
    writer = _writer(introduced)
    who = ", ".join(
        part
        for part in (
            f'agent "{writer.agent_identity}"' if writer.agent_identity else None,
            f'node "{writer.node_id}"' if writer.node_id else None,
            f'run "{writer.agent_run_id}"' if writer.agent_run_id else None,
            f'{writer.tool} call "{writer.tool_call_id}"'
            if writer.tool and writer.tool_call_id
            else None,
            f"iteration {writer.iteration}" if writer.iteration is not None else None,
        )
        if part
    )
    origin = (
        f"version {clean.sequence} was clean" if clean is not None else "no clean version exists"
    )
    return (
        f'"{relative_path}" has been defective ({check}) since version {introduced.sequence} of '
        f"{count}, written at {introduced.written_at_utc}"
        f"{' by ' + who if who else ' (the write recorded no writer)'}; {origin}; "
        f"{evaluations} evaluation(s)."
    )
