# Copyright (c) 2026 David Michael Indraputra

"""Narrow, local-only Git adapter for specification versions and variant worktrees."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..foundations.atomic_io import replace_atomic
from .gate_models import DependencyGraph, GapReport, UnifiedSpecification, VersionMetadata
from .git_models import (
    GitApproval,
    GitCommandError,
    GitRepositoryState,
    SpecificationLockRecord,
    SpecificationSnapshotRecord,
    StructuralSpecificationDiff,
    VariantWorktreeRecord,
    VersionBump,
    VersionClassification,
)

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,120}$")


class GitRepositoryAdapter:
    """Repository-scoped Git commands only; no generic shell, remote, or destructive APIs."""

    def __init__(self, repository_root: Path) -> None:
        self._root = repository_root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def state(self) -> GitRepositoryState:
        top_level = Path(self._git("rev-parse", "--show-toplevel").strip()).resolve()
        if top_level != self._root:
            raise ValueError(
                "Configured Git repository root must be the actual repository top-level."
            )
        head = self._git("rev-parse", "HEAD").strip()
        tree = self._git("rev-parse", "HEAD^{tree}").strip()
        branch = self._git("symbolic-ref", "--short", "-q", "HEAD", check=False).strip() or None
        clean = not bool(self._git("status", "--porcelain").strip())
        tags = [
            item
            for item in self._git("tag", "--list", "v*", "--sort=v:refname").splitlines()
            if item
        ]
        return GitRepositoryState(
            repository_root=str(self._root),
            head_commit=head,
            tree_id=tree,
            branch=branch,
            clean=clean,
            tags=tags,
        )

    def diff_names(self, base_ref: str | None = None) -> list[str]:
        arguments = ["diff", "--name-status"]
        if base_ref:
            arguments.append(f"{base_ref}..HEAD")
        output = self._git(*arguments).strip()
        return [line for line in output.splitlines() if line]

    def tag_exists(self, tag_name: str) -> bool:
        return bool(self._git("tag", "--list", tag_name).strip())

    def create_annotated_tag(self, tag_name: str, message: str) -> None:
        self._git("tag", "-a", tag_name, "-m", message)

    def delete_tag(self, tag_name: str) -> None:
        self._git("tag", "-d", tag_name)

    def tag_object_id(self, tag_name: str) -> str:
        return self._git("rev-parse", f"{tag_name}^{{tag}}").strip()

    def resolve_commit(self, ref: str) -> str:
        """Resolve one non-option revision to an immutable commit object ID."""

        if not ref or ref.startswith("-"):
            raise ValueError("Git revision must be a non-empty, non-option reference.")
        return self._git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").strip()

    def create_worktree(self, path: Path, branch: str, base_ref: str) -> None:
        self._git("worktree", "add", "-b", branch, str(path), base_ref)

    def worktrees(self) -> list[dict[str, str]]:
        output = self._git("worktree", "list", "--porcelain")
        values: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in output.splitlines():
            if not line:
                if current:
                    values.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        if current:
            values.append(current)
        return values

    def _git(self, *arguments: str, check: bool = True) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=self._root,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if check and completed.returncode != 0:
            raise GitCommandError(tuple(arguments), completed.stdout, completed.stderr)
        return completed.stdout


class SpecificationVersionService:
    """Classify, verify, and locally tag a complete approved specification state."""

    def __init__(self, lock_root: Path) -> None:
        self._lock_root = lock_root.resolve() / ".agent-git-locks"
        self._lock_root.mkdir(parents=True, exist_ok=True)
        self._snapshots = self._lock_root / "specification-snapshots"
        self._snapshots.mkdir(parents=True, exist_ok=True)

    def classify(
        self,
        repository: GitRepositoryAdapter,
        version: str,
        specification: UnifiedSpecification,
        dependency_graph: DependencyGraph,
    ) -> VersionClassification:
        """Classify from persisted structured snapshots, never path-name keywords."""

        _parse_semver(version)
        state = repository.state()
        previous_tag = state.tags[-1] if state.tags else None
        changed = repository.diff_names(previous_tag) if previous_tag else repository.diff_names()
        if previous_tag is None:
            return VersionClassification(
                recommended_bump=VersionBump.MAJOR,
                rationale=["Initial approved specification baseline."],
                changed_paths=changed,
                previous_tag=previous_tag,
            )
        previous = self._load_snapshot(previous_tag)
        diff = structural_specification_diff(
            previous.specification,
            previous.dependency_graph,
            specification,
            dependency_graph,
        )
        if diff.has_breaking_change:
            return VersionClassification(
                recommended_bump=VersionBump.MAJOR,
                rationale=_major_rationale(diff),
                changed_paths=changed,
                previous_tag=previous_tag,
                structural_diff=diff,
            )
        return VersionClassification(
            recommended_bump=(
                VersionBump.MINOR if diff.added_requirement_ids else VersionBump.PATCH
            ),
            rationale=(
                [f"New requirements added: {', '.join(diff.added_requirement_ids)}."]
                if diff.added_requirement_ids
                else ["No breaking structural change or requirement addition was observed."]
            ),
            changed_paths=changed,
            previous_tag=previous_tag,
            structural_diff=diff,
        )

    def _snapshot_path(self, tag_name: str) -> Path:
        if not re.fullmatch(r"v" + _SEMVER.pattern[1:-1], tag_name):
            raise ValueError("Specification snapshot tags must use vMAJOR.MINOR.PATCH.")
        return self._snapshots / f"{tag_name}.snapshot.json"

    def _load_snapshot(self, tag_name: str) -> SpecificationSnapshotRecord:
        target = self._snapshot_path(tag_name)
        if not target.is_file():
            raise ValueError(
                f'Previous tag "{tag_name}" has no persisted structured specification snapshot. '
                "Create an explicitly approved baseline lock before automatic classification."
            )
        snapshot = SpecificationSnapshotRecord.model_validate_json(
            target.read_text(encoding="utf-8")
        )
        if _sha256(snapshot.specification.model_dump(mode="json")) != snapshot.specification_digest:
            raise ValueError("Persisted specification snapshot digest does not match its content.")
        if (
            _sha256(snapshot.dependency_graph.model_dump(mode="json"))
            != snapshot.dependency_graph_digest
        ):
            raise ValueError(
                "Persisted dependency graph snapshot digest does not match its content."
            )
        return snapshot

    def _persist_snapshot(
        self,
        *,
        tag_name: str,
        version: str,
        specification: UnifiedSpecification,
        dependency_graph: DependencyGraph,
        specification_digest: str,
        dependency_graph_digest: str,
    ) -> SpecificationSnapshotRecord:
        snapshot = SpecificationSnapshotRecord(
            version=version,
            tag_name=tag_name,
            specification=specification,
            dependency_graph=dependency_graph,
            specification_digest=specification_digest,
            dependency_graph_digest=dependency_graph_digest,
        )
        _atomic_json(self._snapshot_path(tag_name), snapshot.model_dump(mode="json"))
        return snapshot

    def create_lock(
        self,
        repository: GitRepositoryAdapter,
        specification: UnifiedSpecification,
        dependency_graph: DependencyGraph,
        gap_report: GapReport,
        metadata: VersionMetadata,
        approval: GitApproval,
    ) -> SpecificationLockRecord:
        if not approval.approved or approval.action != "create-specification-lock":
            raise ValueError("An approved create-specification-lock decision is required.")
        if not metadata.soft_locked:
            raise ValueError(
                "Git version locks require an accepted Gate 1 soft-lock metadata record."
            )
        if metadata.version != specification.version:
            raise ValueError("Version metadata must match the unified specification version.")
        requested = _parse_semver(metadata.version)
        state = repository.state()
        if not state.clean:
            raise ValueError(
                "Specification repository must be clean before creating a version tag."
            )
        tag_name = f"v{metadata.version}"
        if repository.tag_exists(tag_name):
            raise ValueError(f'Specification tag "{tag_name}" already exists.')
        classification = self.classify(
            repository,
            metadata.version,
            specification,
            dependency_graph,
        )
        if metadata.change_kind.value != classification.recommended_bump.value:
            raise ValueError(
                "Version metadata change_kind does not match the deterministic structural "
                "classification."
            )
        previous = (
            _parse_semver(classification.previous_tag[1:]) if classification.previous_tag else None
        )
        if previous is not None and not _satisfies_bump(
            previous, requested, classification.recommended_bump
        ):
            raise ValueError(
                f"Version {metadata.version} does not satisfy the required "
                f"{classification.recommended_bump.value} bump."
            )
        specification_digest = _sha256(specification.model_dump(mode="json"))
        dependency_graph_digest = _sha256(dependency_graph.model_dump(mode="json"))
        if metadata.unified_specification_hash != specification_digest:
            raise ValueError(
                "Version metadata unified_specification_hash does not match supplied specification."
            )
        repository.create_annotated_tag(
            tag_name,
            f"Gate 1 soft-lock specification {metadata.version}; approval {approval.approval_id}",
        )
        lock_target = self._lock_root / f"{metadata.version}.lock.json"
        snapshot_target = self._snapshot_path(tag_name)
        try:
            record = SpecificationLockRecord(
                version=metadata.version,
                tag_name=tag_name,
                repository_root=state.repository_root,
                head_commit=state.head_commit,
                tree_id=state.tree_id,
                tag_object_id=repository.tag_object_id(tag_name),
                specification_digest=specification_digest,
                version_metadata=metadata,
                classification=classification,
                approval=approval,
                created_at_utc=datetime.now(UTC).isoformat(),
                gap_report_hash=_sha256(gap_report.model_dump(mode="json")),
                dependency_graph_hash=dependency_graph_digest,
                snapshot_digest=_sha256(
                    {
                        "specification_digest": specification_digest,
                        "dependency_graph_digest": dependency_graph_digest,
                    }
                ),
            )
            self._persist_snapshot(
                tag_name=tag_name,
                version=metadata.version,
                specification=specification,
                dependency_graph=dependency_graph,
                specification_digest=specification_digest,
                dependency_graph_digest=dependency_graph_digest,
            )
            _atomic_json(lock_target, record.model_dump(mode="json"))
            return record
        except Exception:
            repository.delete_tag(tag_name)
            lock_target.unlink(missing_ok=True)
            snapshot_target.unlink(missing_ok=True)
            raise

    def create_variant_worktree(
        self,
        repository: GitRepositoryAdapter,
        *,
        name: str,
        branch: str,
        base_ref: str,
        specification_tag: str,
        purpose: str,
        approval: GitApproval,
    ) -> VariantWorktreeRecord:
        if not approval.approved or approval.action != "create-variant-worktree":
            raise ValueError("An approved create-variant-worktree decision is required.")
        if not _SAFE_BRANCH.fullmatch(branch) or branch.startswith("-") or ".." in branch:
            raise ValueError("Variant branch name is invalid.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", name):
            raise ValueError("Variant name is invalid.")
        if not repository.tag_exists(specification_tag):
            raise ValueError("Variant worktrees must reference an existing specification tag.")
        resolved_base = repository.resolve_commit(base_ref)
        resolved_tag = repository.resolve_commit(specification_tag)
        if resolved_base != resolved_tag:
            raise ValueError(
                "Variant worktree base_ref must resolve to the approved specification tag commit."
            )
        worktree_root = self._lock_root / "worktrees"
        worktree_root.mkdir(exist_ok=True)
        target = (worktree_root / name).resolve()
        if target.exists():
            raise ValueError("Variant worktree path already exists.")
        repository.create_worktree(target, branch, resolved_base)
        state = GitRepositoryAdapter(target).state()
        record = VariantWorktreeRecord(
            name=name,
            path=str(target),
            branch=branch,
            head_commit=state.head_commit,
            specification_tag=specification_tag,
            base_ref=base_ref,
            base_commit=resolved_base,
            purpose=purpose,
            approval=approval,
            created_at_utc=datetime.now(UTC).isoformat(),
        )
        _atomic_json(self._lock_root / "worktrees" / f"{name}.json", record.model_dump(mode="json"))
        return record


def structural_specification_diff(
    previous: UnifiedSpecification,
    previous_graph: DependencyGraph,
    current: UnifiedSpecification,
    current_graph: DependencyGraph,
) -> StructuralSpecificationDiff:
    """Compare stable requirement IDs and declared dependency edges exactly.

    Source locators and acceptance-check metadata remain outside the breaking
    surface. Their preservation is still verified by the Gate 1 lock digest.
    """

    previous_requirements = {requirement.id: requirement for requirement in previous.requirements}
    current_requirements = {requirement.id: requirement for requirement in current.requirements}
    common = sorted(set(previous_requirements) & set(current_requirements))
    changed_fields: dict[str, list[str]] = {}
    for requirement_id in common:
        prior = previous_requirements[requirement_id]
        present = current_requirements[requirement_id]
        fields = [
            field_name
            for field_name in ("text", "category", "fields")
            if getattr(prior, field_name) != getattr(present, field_name)
        ]
        if fields:
            changed_fields[requirement_id] = fields

    prior_edges = {(edge.source_id, edge.target_id) for edge in previous_graph.edges}
    current_edges = {(edge.source_id, edge.target_id) for edge in current_graph.edges}
    return StructuralSpecificationDiff(
        added_requirement_ids=sorted(set(current_requirements) - set(previous_requirements)),
        removed_requirement_ids=sorted(set(previous_requirements) - set(current_requirements)),
        modified_requirement_ids=sorted(changed_fields),
        changed_requirement_fields=changed_fields,
        added_dependency_edges=sorted(current_edges - prior_edges),
        removed_dependency_edges=sorted(prior_edges - current_edges),
    )


def _major_rationale(diff: StructuralSpecificationDiff) -> list[str]:
    rationale: list[str] = []
    if diff.removed_requirement_ids:
        rationale.append(f"Requirements removed: {', '.join(diff.removed_requirement_ids)}.")
    for requirement_id in diff.modified_requirement_ids:
        fields = ", ".join(diff.changed_requirement_fields[requirement_id])
        rationale.append(f"Existing requirement changed: {requirement_id} ({fields}).")
    if diff.added_dependency_edges:
        rationale.append("Dependency edges added to the locked specification graph.")
    if diff.removed_dependency_edges:
        rationale.append("Dependency edges removed from the locked specification graph.")
    return rationale


def _parse_semver(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ValueError(
            "Specification versions must use MAJOR.MINOR.PATCH without prerelease suffixes."
        )
    return tuple(int(group) for group in match.groups())


def _satisfies_bump(
    previous: tuple[int, int, int],
    requested: tuple[int, int, int],
    required: VersionBump,
) -> bool:
    if requested <= previous:
        return False
    major, minor, patch = previous
    if required is VersionBump.MAJOR:
        return requested[0] > major and requested[1:] == (0, 0)
    if required is VersionBump.MINOR:
        return requested[0] == major and requested[1] > minor and requested[2] == 0
    return requested[0] == major and requested[1] == minor and requested[2] > patch


def _sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    import hashlib

    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(target: Path, value: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    replace_atomic(temporary, target)
