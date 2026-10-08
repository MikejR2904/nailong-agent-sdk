# Copyright (c) 2026 David Michael Indraputra

"""Gate 1 requirement, gap, and version-metadata contracts.

The gate validates absence, traceability, and verifiability without pretending
that model-only ambiguity and semantic analysis are deterministic (systems-design
framework, updated PDF, pp. 38-39 and 48).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from ..foundations.contracts import StrictModel
from ..foundations.identifiers import require_unique
from .documents import DocumentTree, SourceRef, SpecificationCategory


class GapType(StrEnum):
    ABSENCE = "absence"
    TRACEABILITY = "traceability"
    INCONSISTENCY = "inconsistency"
    AMBIGUITY = "ambiguity"
    VERIFIABILITY = "verifiability"
    UNSTATED_ASSUMPTION = "unstated_assumption"


class GapSeverity(StrEnum):
    CRITICAL = "CRITICAL"
    IMPORTANT = "IMPORTANT"
    OPTIONAL = "OPTIONAL"


class RequirementEntry(StrictModel):
    id: str = Field(min_length=1)
    category: SpecificationCategory
    text: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    acceptance_checks: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)


class UnifiedSpecification(StrictModel):
    schema_version: str = "unified-specification-v1"
    version: str = Field(min_length=1)
    documents: list[DocumentTree]
    requirements: list[RequirementEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def requirement_ids_are_unique(self) -> UnifiedSpecification:
        ids = [requirement.id for requirement in self.requirements]
        require_unique(ids, "unified specification requirement IDs")
        return self


class DependencyEdge(StrictModel):
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)


class DependencyGraph(StrictModel):
    schema_version: str = "specification-dependency-graph-v1"
    nodes: list[str] = Field(default_factory=list)
    edges: list[DependencyEdge] = Field(default_factory=list)


class Gap(StrictModel):
    type: GapType
    locations: list[str] = Field(min_length=1)
    description: str = Field(min_length=1)
    categories_touched: list[SpecificationCategory] = Field(default_factory=list)
    suggested_fix: str = Field(min_length=1)
    severity: GapSeverity
    source: str = Field(min_length=1)
    blast_radius: int | None = Field(default=None, ge=0)
    model_analysis_required: bool = False


class SemanticGapFinding(StrictModel):
    """An untrusted semantic finding eligible for deterministic Gate 1 admission.

    The SDK does not infer semantic ambiguity from prose. A host-selected analysis
    component may propose a finding, but the gate admits it only when every cited
    requirement and source reference belongs to the frozen specification.
    """

    schema_version: str = "semantic-gap-finding-v1"
    finding_id: str = Field(min_length=1, max_length=128)
    type: GapType
    requirement_ids: list[str] = Field(min_length=1, max_length=32)
    source_refs: list[SourceRef] = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=4_000)
    suggested_fix: str = Field(min_length=1, max_length=2_000)
    severity: GapSeverity = GapSeverity.IMPORTANT
    analysis_provider: str = Field(min_length=1, max_length=128)
    analysis_model: str | None = Field(default=None, min_length=1, max_length=256)
    analysis_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def semantic_findings_are_bounded_and_noncritical(self) -> SemanticGapFinding:
        semantic_types = {
            GapType.AMBIGUITY,
            GapType.INCONSISTENCY,
            GapType.UNSTATED_ASSUMPTION,
        }
        if self.type not in semantic_types:
            raise ValueError("Semantic findings may use only semantic GapType values.")
        require_unique(self.requirement_ids, "Semantic finding requirement_ids")
        if self.type is GapType.INCONSISTENCY and len(self.requirement_ids) < 2:
            raise ValueError("Inconsistency findings must cite at least two requirements.")
        if self.severity is GapSeverity.CRITICAL:
            raise ValueError("Semantic findings cannot assign CRITICAL severity.")
        return self


class SemanticGapAdmission(StrictModel):
    """Deterministic evidence of whether an untrusted semantic finding was admitted."""

    finding_id: str = Field(min_length=1)
    accepted: bool
    reason: str = Field(min_length=1)


class GapReport(StrictModel):
    schema_version: str = "gap-report-v1"
    document_version: str = Field(min_length=1)
    gaps: list[Gap] = Field(default_factory=list)
    semantic_admissions: list[SemanticGapAdmission] = Field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "total_gaps": len(self.gaps),
            "critical": sum(item.severity is GapSeverity.CRITICAL for item in self.gaps),
            "important": sum(item.severity is GapSeverity.IMPORTANT for item in self.gaps),
            "optional": sum(item.severity is GapSeverity.OPTIONAL for item in self.gaps),
            "semantic_findings_accepted": sum(item.accepted for item in self.semantic_admissions),
            "semantic_findings_rejected": sum(
                not item.accepted for item in self.semantic_admissions
            ),
        }


class VersionChangeKind(StrEnum):
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


class VersionMetadata(StrictModel):
    schema_version: str = "specification-version-v1"
    version: str = Field(min_length=1)
    change_kind: VersionChangeKind
    rationale: list[str] = Field(default_factory=list)
    unified_specification_hash: str = Field(min_length=1)
    soft_locked: bool = False
    user_override_with_gaps: bool = False


class SoftLockDecision(StrictModel):
    accepted: bool
    warnings: list[str] = Field(default_factory=list)
    metadata: VersionMetadata | None = None
