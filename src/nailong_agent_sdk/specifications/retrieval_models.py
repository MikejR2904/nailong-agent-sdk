# Copyright (c) 2026 David Michael Indraputra

"""Retrieval document, query, candidate, and result contracts.

A vector store ranks source references; it never becomes working-memory authority.
Every retrieved candidate is resolved against locally held ``DocumentTree`` data
before a context selector may expose content to a model.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from ..foundations.contracts import StrictModel
from ..foundations.hashing import canonical_hash
from ..foundations.identifiers import require_unique
from .documents import DocumentNode, SourceRef, SpecificationCategory


class RetrievalStatus(StrEnum):
    RETRIEVED = "retrieved"
    CACHE_HIT = "cache-hit"
    UNAVAILABLE = "unavailable"


class RetrievalFailureMode(StrEnum):
    FALLBACK_LEXICAL = "fallback-lexical"
    REJECT = "reject"


class RetrievalDocument(StrictModel):
    """Indexable text plus immutable source provenance from one frozen snapshot."""

    snapshot_id: str = Field(min_length=1)
    category: SpecificationCategory
    document_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    source: SourceRef
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def node_belongs_to_source_document(self) -> RetrievalDocument:
        if self.document_id != self.source.document_id:
            raise ValueError("retrieval document_id must equal source.document_id")
        return self

    @property
    def stable_id(self) -> str:
        payload = {
            "snapshot_id": self.snapshot_id,
            "category": self.category,
            "document_id": self.document_id,
            "node_id": self.node_id,
            "source_hash": self.source.source_hash,
            "location": self.source.location,
        }
        return canonical_hash(payload)


class RetrievalCandidate(StrictModel):
    """A bounded rank/reference returned by an untrusted retrieval backend."""

    snapshot_id: str = Field(min_length=1)
    category: SpecificationCategory
    document_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    source: SourceRef
    score: float
    backend_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def candidate_belongs_to_source_document(self) -> RetrievalCandidate:
        if self.document_id != self.source.document_id:
            raise ValueError("retrieval candidate document_id must equal source.document_id")
        return self


class RetrievalQuery(StrictModel):
    """A bounded candidate request. Query text is never persisted in telemetry/cache keys."""

    snapshot_id: str = Field(min_length=1)
    query_text: str = Field(min_length=1)
    allowed_categories: list[SpecificationCategory] = Field(min_length=1)
    limit: int = Field(default=8, ge=1, le=100)
    policy_id: str = Field(default="retrieval-policy-v1", min_length=1)

    @model_validator(mode="after")
    def allowed_categories_are_unique(self) -> RetrievalQuery:
        require_unique(self.allowed_categories, "retrieval allowed_categories")
        return self

    @property
    def query_digest(self) -> str:
        return canonical_hash(
            {
                "snapshot_id": self.snapshot_id,
                "query_text": self.query_text,
                "allowed_categories": sorted(self.allowed_categories),
                "limit": self.limit,
                "policy_id": self.policy_id,
            }
        )


class RetrievalResult(StrictModel):
    status: RetrievalStatus
    query_digest: str = Field(min_length=1)
    candidates: list[RetrievalCandidate] = Field(default_factory=list)
    backend: str = Field(min_length=1)
    failure_reason: str | None = None
    cache_warning: str | None = None


class ResolvedRetrieval(StrictModel):
    """Locally verified nodes admitted from retrieved source references."""

    result: RetrievalResult
    nodes: list[DocumentNode] = Field(default_factory=list)
    rejected_candidate_ids: list[str] = Field(default_factory=list)
