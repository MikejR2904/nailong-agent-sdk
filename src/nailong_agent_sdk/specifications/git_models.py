# Copyright (c) 2026 David Michael Indraputra

"""Git-backed specification version and variant-worktree contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field

from ..foundations.contracts import StrictModel
from .gate_models import DependencyGraph, UnifiedSpecification, VersionMetadata


class VersionBump(StrEnum):
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


class StructuralSpecificationDiff(StrictModel):
    """Deterministic ID- and edge-based comparison of two locked specifications."""

    added_requirement_ids: list[str] = Field(default_factory=list)
    removed_requirement_ids: list[str] = Field(default_factory=list)
    modified_requirement_ids: list[str] = Field(default_factory=list)
    changed_requirement_fields: dict[str, list[str]] = Field(default_factory=dict)
    added_dependency_edges: list[tuple[str, str]] = Field(default_factory=list)
    removed_dependency_edges: list[tuple[str, str]] = Field(default_factory=list)

    @property
    def has_breaking_change(self) -> bool:
        return bool(
            self.removed_requirement_ids
            or self.modified_requirement_ids
            or self.added_dependency_edges
            or self.removed_dependency_edges
        )


class SpecificationSnapshotRecord(StrictModel):
    """Content-addressed structured inputs retained with an approved version lock."""

    schema_version: str = "specification-version-snapshot-v1"
    version: str
    tag_name: str
    specification: UnifiedSpecification
    dependency_graph: DependencyGraph
    specification_digest: str
    dependency_graph_digest: str


class GitApproval(StrictModel):
    approved: bool
    approver_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    action: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    at_utc: str


class GitRepositoryState(StrictModel):
    repository_root: str
    head_commit: str
    tree_id: str
    branch: str | None = None
    clean: bool
    tags: list[str] = Field(default_factory=list)


class VersionClassification(StrictModel):
    recommended_bump: VersionBump
    rationale: list[str] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    previous_tag: str | None = None
    structural_diff: StructuralSpecificationDiff | None = None


class SpecificationLockRecord(StrictModel):
    schema_version: str = "specification-git-lock-v1"
    version: str
    tag_name: str
    repository_root: str
    head_commit: str
    tree_id: str
    tag_object_id: str
    specification_digest: str
    version_metadata: VersionMetadata
    classification: VersionClassification
    approval: GitApproval
    created_at_utc: str
    gap_report_hash: str
    dependency_graph_hash: str
    snapshot_digest: str


class VariantWorktreeRecord(StrictModel):
    schema_version: str = "variant-worktree-v2"
    name: str
    path: str
    branch: str
    head_commit: str
    specification_tag: str
    base_ref: str
    base_commit: str
    purpose: str
    approval: GitApproval
    created_at_utc: str


@dataclass(frozen=True)
class GitCommandError(RuntimeError):
    command: tuple[str, ...]
    stdout: str
    stderr: str

    def __str__(self) -> str:
        detail = self.stderr.strip() or self.stdout.strip()
        return f"Git command failed: git {' '.join(self.command)}: {detail}"
