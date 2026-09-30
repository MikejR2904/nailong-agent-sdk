# Copyright (c) 2026 David Michael Indraputra

"""Specification manifest, document, and source-locator contracts.

A document tree is an ordered structure with document/source locations. It is
not a lossy summary or a generic RAG index (systems-design framework, pp. 22-30).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from ..foundations.contracts import StrictModel


class SpecificationCategory(StrEnum):
    FUNCTIONAL = "functional"
    ARCHITECTURAL = "architectural"
    INTERFACE = "interface"
    PPA = "ppa"
    PDK = "pdk"
    VERIFICATION = "verification"
    SAFETY_SECURITY = "safety-security"
    ASSUMPTIONS = "assumptions"


class DocumentFormat(StrEnum):
    TXT = "txt"
    MD = "md"
    TEX = "tex"
    DOCX = "docx"
    PDF = "pdf"
    CSV = "csv"
    XLSX = "xlsx"
    YAML = "yaml"
    JSON = "json"
    XML = "xml"
    SYSTEMRDL = "systemrdl"
    SDC = "sdc"
    UPF = "upf"
    DOT = "dot"
    PLANTUML = "plantuml"
    WAVEDROM = "wavedrom"
    MERMAID = "mermaid"
    TIKZ = "tikz"
    SVG = "svg"
    DRAWIO = "drawio"
    VSDX = "vsdx"
    PNG = "png"
    JPEG = "jpeg"


class SourceRef(StrictModel):
    document_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    format: DocumentFormat
    location: str = Field(min_length=1)


class DocumentNodeKind(StrEnum):
    TEXT = "text-block"
    TABLE = "table-block"
    IMAGE = "image-node"
    STRUCTURED = "structured-block"
    DIAGRAM = "diagram-source"


class VisionStatus(StrEnum):
    NOT_REQUIRED = "not-required"
    PENDING = "pending"
    REVIEW_REQUIRED = "review-required"
    ACCEPTED = "accepted"


class DocumentNode(StrictModel):
    node_id: str = Field(min_length=1)
    kind: DocumentNodeKind
    source: SourceRef
    content: Any
    surrounding_text: list[str] = Field(default_factory=list)
    vision_status: VisionStatus = VisionStatus.NOT_REQUIRED
    resolved_structure: dict[str, Any] | None = None


class DocumentTree(StrictModel):
    schema_version: str = "document-tree-v1"
    document_id: str = Field(min_length=1)
    category: SpecificationCategory
    title: str = Field(min_length=1)
    format: DocumentFormat
    relative_path: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    nodes: list[DocumentNode]


class SpecificationDocument(StrictModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    format: DocumentFormat
    path: str = Field(min_length=1)
    category: SpecificationCategory
    dependencies: list[str] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def path_is_relative(cls, value: str) -> str:
        if Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError(
                "specification document paths must be relative and may not escape the root"
            )
        return value


class SpecificationManifest(StrictModel):
    schema_version: str = "specification-manifest-v1"
    documents: list[SpecificationDocument] = Field(min_length=1)

    @model_validator(mode="after")
    def document_ids_are_unique(self) -> SpecificationManifest:
        values = [document.id for document in self.documents]
        if len(values) != len(set(values)):
            raise ValueError("specification document IDs must be unique")
        return self
