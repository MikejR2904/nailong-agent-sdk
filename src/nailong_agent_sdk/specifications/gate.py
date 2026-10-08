# Copyright (c) 2026 David Michael Indraputra

"""Gate 1 deterministic checks, soft-lock decision, and Gate 1 artifact persistence.

The gate validates absence, traceability, and verifiability without pretending
that model-only ambiguity and semantic analysis are deterministic (systems-design
framework, updated PDF, pp. 38-39 and 48).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from ..foundations.atomic_io import replace_atomic, unique_temporary_path
from ..foundations.dependency_graph import (
    deterministic_cycles,
    reverse_reachable_count,
    reverse_reachable_nodes,
)
from .documents import DocumentNodeKind, SourceRef, SpecificationCategory, VisionStatus
from .gate_models import (
    DependencyEdge,
    DependencyGraph,
    Gap,
    GapReport,
    GapSeverity,
    GapType,
    SemanticGapAdmission,
    SemanticGapFinding,
    SoftLockDecision,
    UnifiedSpecification,
    VersionChangeKind,
    VersionMetadata,
)

_UNRESOLVED_VISION = frozenset({VisionStatus.PENDING, VisionStatus.REVIEW_REQUIRED})


class SpecificationGate:
    """Perform only deterministic Gate 1 checks and retain model-needed flags."""

    def validate(
        self,
        specification: UnifiedSpecification,
        *,
        required_categories: set[SpecificationCategory],
        semantic_findings: Sequence[SemanticGapFinding] = (),
    ) -> tuple[DependencyGraph, GapReport]:
        requirement_ids = {requirement.id for requirement in specification.requirements}
        present_categories = {document.category for document in specification.documents}
        gaps: list[Gap] = []
        for category in sorted(required_categories - present_categories, key=str):
            gaps.append(
                Gap(
                    type=GapType.ABSENCE,
                    locations=[f"category:{category.value}"],
                    description=(
                        f'Required specification category "{category.value}" has no document.'
                    ),
                    categories_touched=[category],
                    suggested_fix="Add a source document to the specification manifest.",
                    severity=GapSeverity.CRITICAL,
                    source="manifest-category-presence",
                )
            )
        for tree in specification.documents:
            for node in tree.nodes:
                if node.kind is DocumentNodeKind.IMAGE and node.vision_status in _UNRESOLVED_VISION:
                    gaps.append(
                        Gap(
                            type=GapType.VERIFIABILITY,
                            locations=[f"{tree.document_id}:{node.source.location}"],
                            description=(
                                f'Image node "{node.node_id}" of document "{tree.document_id}" has '
                                f'vision status "{node.vision_status.value}"; its content is not '
                                "part of the machine-verified specification."
                            ),
                            categories_touched=[tree.category],
                            suggested_fix=(
                                "Review the image and resolve its structure, or record a "
                                "designer decision about it."
                            ),
                            severity=GapSeverity.IMPORTANT,
                            source="vision-resolution-presence",
                        )
                    )
        edges: list[DependencyEdge] = []
        for requirement in specification.requirements:
            if not requirement.text.strip():
                gaps.append(
                    Gap(
                        type=GapType.ABSENCE,
                        locations=[requirement.id],
                        description="Requirement text is empty.",
                        categories_touched=[requirement.category],
                        suggested_fix="Provide a concrete requirement statement.",
                        severity=GapSeverity.CRITICAL,
                        source="required-field-presence",
                    )
                )
            if not requirement.acceptance_checks:
                gaps.append(
                    Gap(
                        type=GapType.VERIFIABILITY,
                        locations=[requirement.id],
                        description="Requirement has no declared acceptance check.",
                        categories_touched=[requirement.category],
                        suggested_fix="Link a test, assertion, report, or other acceptance method.",
                        severity=GapSeverity.IMPORTANT,
                        source="acceptance-check-presence",
                    )
                )
            for dependency in requirement.dependencies:
                edges.append(DependencyEdge(source_id=requirement.id, target_id=dependency))
                if dependency not in requirement_ids:
                    gaps.append(
                        Gap(
                            type=GapType.TRACEABILITY,
                            locations=[requirement.id, dependency],
                            description=(
                                "Requirement dependency references a missing requirement node."
                            ),
                            categories_touched=[requirement.category],
                            suggested_fix=(
                                "Define the referenced requirement or correct the reference."
                            ),
                            severity=GapSeverity.CRITICAL,
                            source="dependency-graph-traversal",
                        )
                    )
        graph = DependencyGraph(nodes=sorted(requirement_ids), edges=edges)
        graph_edges = [(edge.source_id, edge.target_id) for edge in graph.edges]
        gaps = [
            gap.model_copy(
                update={
                    "blast_radius": reverse_reachable_count(
                        graph.nodes,
                        graph_edges,
                        gap.locations[-1],
                    )
                }
            )
            if gap.source == "dependency-graph-traversal"
            and len(gap.locations) == 2
            and gap.locations[-1] not in requirement_ids
            else gap
            for gap in gaps
        ]
        cycles = deterministic_cycles(graph.nodes, graph_edges)
        for cycle in cycles:
            gaps.append(
                Gap(
                    type=GapType.TRACEABILITY,
                    locations=cycle,
                    description="Requirement dependency graph contains a circular dependency.",
                    categories_touched=[],
                    suggested_fix="Break the circular dependency and retain a directed trace.",
                    severity=GapSeverity.IMPORTANT,
                    source="dependency-graph-traversal",
                    blast_radius=len(cycle),
                )
            )
        semantic_gaps, semantic_admissions = self.admit_semantic_findings(
            specification,
            graph,
            semantic_findings,
        )
        return graph, GapReport(
            document_version=specification.version,
            gaps=[*gaps, *semantic_gaps],
            semantic_admissions=semantic_admissions,
        )

    def admit_semantic_findings(
        self,
        specification: UnifiedSpecification,
        graph: DependencyGraph,
        findings: Sequence[SemanticGapFinding],
    ) -> tuple[list[Gap], list[SemanticGapAdmission]]:
        """Mechanically bind host-proposed semantic findings to frozen requirement evidence.

        This method verifies references, ordering, and receipt identity. It does not
        decide whether prose is truly ambiguous or inconsistent; that remains the
        explicitly marked analysis-provider claim in the admitted gap.
        """

        requirements = {requirement.id: requirement for requirement in specification.requirements}
        graph_edges = [(edge.source_id, edge.target_id) for edge in graph.edges]
        admitted: list[Gap] = []
        admissions: list[SemanticGapAdmission] = []
        seen_ids: set[str] = set()
        for finding in sorted(findings, key=lambda item: item.finding_id):
            if finding.finding_id in seen_ids:
                admissions.append(
                    SemanticGapAdmission(
                        finding_id=finding.finding_id,
                        accepted=False,
                        reason=(
                            f'Semantic finding ID "{finding.finding_id}" repeats an earlier '
                            "finding; finding IDs must be unique per Gate 1 evaluation."
                        ),
                    )
                )
                continue
            seen_ids.add(finding.finding_id)
            missing_requirements = sorted(set(finding.requirement_ids) - set(requirements))
            if missing_requirements:
                admissions.append(
                    SemanticGapAdmission(
                        finding_id=finding.finding_id,
                        accepted=False,
                        reason=(
                            "Semantic finding cites unknown requirements: "
                            f"{', '.join(missing_requirements)}."
                        ),
                    )
                )
                continue
            allowed_sources = Counter(
                _source_ref_key(source)
                for requirement_id in finding.requirement_ids
                for source in requirements[requirement_id].source_refs
            )
            cited_sources = Counter(_source_ref_key(source) for source in finding.source_refs)
            if any(count > allowed_sources[key] for key, count in cited_sources.items()):
                admissions.append(
                    SemanticGapAdmission(
                        finding_id=finding.finding_id,
                        accepted=False,
                        reason=(
                            "Semantic finding cites a source reference not bound to its "
                            "declared requirements."
                        ),
                    )
                )
                continue
            categories = sorted(
                {
                    requirements[requirement_id].category
                    for requirement_id in finding.requirement_ids
                },
                key=lambda category: category.value,
            )
            locations = [
                *finding.requirement_ids,
                *[f"{source.document_id}:{source.location}" for source in finding.source_refs],
            ]
            affected_requirements = set(finding.requirement_ids)
            for requirement_id in finding.requirement_ids:
                affected_requirements.update(
                    reverse_reachable_nodes(graph.nodes, graph_edges, requirement_id)
                )
            blast_radius = len(affected_requirements)
            admitted.append(
                Gap(
                    type=finding.type,
                    locations=locations,
                    description=finding.description,
                    categories_touched=categories,
                    suggested_fix=finding.suggested_fix,
                    severity=finding.severity,
                    source=(
                        "semantic-analysis:"
                        f"{finding.analysis_provider}:{finding.analysis_receipt_digest}"
                    ),
                    blast_radius=blast_radius,
                    model_analysis_required=True,
                )
            )
            admissions.append(
                SemanticGapAdmission(
                    finding_id=finding.finding_id,
                    accepted=True,
                    reason="Finding references only frozen requirement and source evidence.",
                )
            )
        return admitted, admissions

    def soft_lock(
        self,
        specification: UnifiedSpecification,
        report: GapReport,
        metadata: VersionMetadata,
        *,
        user_approved: bool,
        proceed_with_gaps: bool = False,
    ) -> SoftLockDecision:
        if report.document_version != specification.version:
            raise ValueError(
                f'Gap report document_version "{report.document_version}" does not match '
                f'the specification version "{specification.version}".'
            )
        if metadata.version != specification.version:
            raise ValueError(
                f'Version metadata version "{metadata.version}" does not match '
                f'the specification version "{specification.version}".'
            )
        if not user_approved:
            return SoftLockDecision(
                accepted=False, warnings=["Designer approval is required to soft-lock."]
            )
        warnings = [f"{gap.severity.value}: {gap.description}" for gap in report.gaps]
        if report.gaps and not proceed_with_gaps:
            return SoftLockDecision(
                accepted=False,
                warnings=[
                    *warnings,
                    "Soft-lock requires explicit proceed_with_gaps when gaps remain.",
                ],
            )
        return SoftLockDecision(
            accepted=True,
            warnings=warnings,
            metadata=metadata.model_copy(
                update={"soft_locked": True, "user_override_with_gaps": bool(report.gaps)}
            ),
        )


def _source_ref_key(source: SourceRef) -> tuple[str, str, str, str, str]:
    """Return the complete immutable source identity used for Gate 1 admission."""

    return (
        source.document_id,
        source.relative_path,
        source.source_hash,
        source.format.value,
        source.location,
    )


def classify_version_change(
    previous: UnifiedSpecification | None,
    current: UnifiedSpecification,
) -> tuple[VersionChangeKind, list[str]]:
    """Preview structural requirement-ID changes before a graph-aware Git lock check.

    ``SpecificationVersionService`` additionally compares dependency edges against
    the prior persisted lock snapshot. This Gate 1 helper has no prior graph
    parameter, so it deliberately classifies only requirement-level structure.
    """

    if previous is None:
        return VersionChangeKind.MAJOR, ["Initial soft-locked specification baseline."]
    previous_requirements = {item.id: item for item in previous.requirements}
    current_requirements = {item.id: item for item in current.requirements}
    removed = sorted(set(previous_requirements) - set(current_requirements))
    if removed:
        return VersionChangeKind.MAJOR, [f"Requirements removed: {', '.join(removed)}."]
    for requirement_id in sorted(set(previous_requirements) & set(current_requirements)):
        prior = previous_requirements[requirement_id]
        present = current_requirements[requirement_id]
        changed_fields = [
            field_name
            for field_name in ("text", "category", "fields")
            if getattr(prior, field_name) != getattr(present, field_name)
        ]
        if changed_fields:
            return VersionChangeKind.MAJOR, [
                f"Existing requirement changed: {requirement_id} ({', '.join(changed_fields)})."
            ]
    added = sorted(set(current_requirements) - set(previous_requirements))
    if added:
        return VersionChangeKind.MINOR, [f"New requirements added: {', '.join(added)}."]
    return VersionChangeKind.PATCH, [
        "Constraint, clarification, or feasibility-level changes only."
    ]


class Gate1ArtifactStore:
    """Persist Gate 1 outputs under the approved `specifications/` root."""

    def __init__(self, specification_root: Path) -> None:
        self._root = specification_root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def persist(
        self,
        specification: UnifiedSpecification,
        dependency_graph: DependencyGraph,
        gap_report: GapReport,
        metadata: VersionMetadata,
        plans: list[dict[str, Any]] = (),
    ) -> None:
        payloads: dict[str, dict[str, Any]] = {
            "unified-specification.yaml": specification.model_dump(mode="json"),
            "dependency-graph.yaml": dependency_graph.model_dump(mode="json"),
            "gap-report.yaml": {
                **gap_report.model_dump(mode="json"),
                "summary": gap_report.summary(),
            },
            "version-metadata.yaml": metadata.model_dump(mode="json"),
            **{f"plans/plan-{index}.yaml": plan for index, plan in enumerate(plans, start=1)},
        }
        staged: list[tuple[Path, Path]] = []
        try:
            for relative, payload in payloads.items():
                staged.append((self._stage(relative, payload), self._root / relative))
        except BaseException:
            for temporary, _target in staged:
                temporary.unlink(missing_ok=True)
            raise
        for temporary, target in staged:
            replace_atomic(temporary, target)
        self._remove_stale_plans(len(plans))

    def _stage(self, relative: str, payload: dict[str, Any]) -> Path:
        target = self._root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = unique_temporary_path(target)
        temporary.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return temporary

    def _remove_stale_plans(self, retained_count: int) -> None:
        plan_root = self._root / "plans"
        if not plan_root.is_dir():
            return
        for path in plan_root.glob("plan-*.yaml"):
            number = path.stem.removeprefix("plan-")
            if number.isdigit() and int(number) > retained_count:
                path.unlink(missing_ok=True)
