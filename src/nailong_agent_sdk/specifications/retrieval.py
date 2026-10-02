# Copyright (c) 2026 David Michael Indraputra

"""Provenance-grounded candidate retrieval for frozen specification snapshots.

Redis can cache only bounded references and scores, never raw source text,
state, approvals, or execution evidence.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..observability.metrics import record_metric_value
from ..observability.telemetry_models import TelemetryActor, TelemetryAuthority, TelemetryContext
from ..observability.telemetry_store import TelemetryStore
from .documents import DocumentNode, DocumentTree, SpecificationCategory
from .preprocessing import keywords_from_task
from .retrieval_models import (
    ResolvedRetrieval,
    RetrievalCandidate,
    RetrievalDocument,
    RetrievalFailureMode,
    RetrievalQuery,
    RetrievalResult,
    RetrievalStatus,
)


class EmbeddingProvider(Protocol):
    """Host-owned embedding boundary; credentials and provider transport stay local."""

    def embed(self, text: str) -> list[float]: ...


class RetrievalIndex(Protocol):
    """Candidate-ranking backend, not an authoritative source store."""

    @property
    def backend_name(self) -> str: ...

    def upsert(self, documents: Iterable[RetrievalDocument]) -> None: ...

    def search(self, query: RetrievalQuery) -> list[RetrievalCandidate]: ...


class RetrievalCache(Protocol):
    """Optional cache for bounded retrieval responses only."""

    def get(self, key: str) -> RetrievalResult | None: ...

    def set(self, key: str, result: RetrievalResult, ttl_seconds: int) -> None: ...


class InMemoryRetrievalCache:
    """Deterministic test/development cache without wall-clock expiry semantics."""

    def __init__(self) -> None:
        self._values: dict[str, RetrievalResult] = {}

    def get(self, key: str) -> RetrievalResult | None:
        return self._values.get(key)

    def set(self, key: str, result: RetrievalResult, ttl_seconds: int) -> None:
        if ttl_seconds < 1:
            raise ValueError("retrieval cache ttl_seconds must be positive")
        self._values[key] = result


class RedisRetrievalCache:
    """Optional Redis cache; no instance is created until the host opts in."""

    def __init__(self, redis_url: str, *, namespace: str = "agent-sdk:retrieval:") -> None:
        if not redis_url.strip():
            raise ValueError("redis_url must be non-empty")
        if not namespace.endswith(":"):
            raise ValueError("redis retrieval cache namespace must end with ':'.")
        try:
            import redis
        except ImportError as error:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "Redis retrieval caching requires agent-design-agent-sdk[redis-cache]."
            ) from error
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._namespace = namespace

    def get(self, key: str) -> RetrievalResult | None:
        raw = self._client.get(self._namespace + key)
        return RetrievalResult.model_validate_json(raw) if raw is not None else None

    def set(self, key: str, result: RetrievalResult, ttl_seconds: int) -> None:
        if ttl_seconds < 1:
            raise ValueError("retrieval cache ttl_seconds must be positive")
        self._client.set(self._namespace + key, result.model_dump_json(), ex=ttl_seconds)


class DeterministicLexicalRetrievalIndex:
    """Source-preserving deterministic fallback for offline or test use."""

    backend_name = "deterministic-lexical-v1"

    def __init__(self) -> None:
        self._documents: dict[str, RetrievalDocument] = {}

    def upsert(self, documents: Iterable[RetrievalDocument]) -> None:
        for document in documents:
            self._documents[document.stable_id] = document

    def search(self, query: RetrievalQuery) -> list[RetrievalCandidate]:
        terms = keywords_from_task(query.query_text)
        allowed = set(query.allowed_categories)
        ranked: list[tuple[int, RetrievalDocument]] = []
        for document in self._documents.values():
            if document.snapshot_id != query.snapshot_id or document.category not in allowed:
                continue
            score = len(terms & keywords_from_task(document.text))
            if score:
                ranked.append((score, document))
        ranked.sort(key=lambda item: (-item[0], item[1].document_id, item[1].node_id))
        return [
            RetrievalCandidate(
                snapshot_id=document.snapshot_id,
                category=document.category,
                document_id=document.document_id,
                node_id=document.node_id,
                source=document.source,
                score=float(score),
                backend_id=document.stable_id,
            )
            for score, document in ranked[: query.limit]
        ]


class QdrantRetrievalIndex:
    """Optional Qdrant REST adapter with exact snapshot/category metadata filtering."""

    backend_name = "qdrant-rest-v1"

    def __init__(
        self,
        base_url: str,
        collection_name: str,
        embedding_provider: EmbeddingProvider,
        *,
        embedding_dimensions: int,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Qdrant base_url must use HTTP(S).")
        if not collection_name:
            raise ValueError("Qdrant collection_name must be non-empty.")
        if embedding_dimensions < 1:
            raise ValueError("Qdrant embedding_dimensions must be positive.")
        if timeout_seconds <= 0:
            raise ValueError("Qdrant timeout_seconds must be positive.")
        self._base_url = base_url.rstrip("/")
        self._collection_name = collection_name
        self._embedding_provider = embedding_provider
        self._embedding_dimensions = embedding_dimensions
        self._timeout_seconds = timeout_seconds
        self._collection_ready = False

    def upsert(self, documents: Iterable[RetrievalDocument]) -> None:
        values = list(documents)
        if not values:
            return
        self._ensure_collection()
        points = []
        for document in values:
            vector = self._embedding_provider.embed(document.text)
            self._assert_dimensions(vector)
            points.append(
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, document.stable_id)),
                    "vector": vector,
                    "payload": _document_payload(document),
                }
            )
        self._request(
            "PUT", f"/collections/{self._collection_name}/points?wait=true", {"points": points}
        )

    def search(self, query: RetrievalQuery) -> list[RetrievalCandidate]:
        self._ensure_collection()
        vector = self._embedding_provider.embed(query.query_text)
        self._assert_dimensions(vector)
        payload = self._request(
            "POST",
            f"/collections/{self._collection_name}/points/search",
            {
                "vector": vector,
                "limit": query.limit,
                "with_payload": True,
                "filter": {
                    "must": [
                        {"key": "snapshot_id", "match": {"value": query.snapshot_id}},
                        {
                            "key": "category",
                            "match": {"any": [item.value for item in query.allowed_categories]},
                        },
                    ]
                },
            },
        )
        points = payload.get("result", [])
        if not isinstance(points, list):
            raise RuntimeError("Qdrant response result must be a list.")
        candidates: list[RetrievalCandidate] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            candidate = _candidate_from_qdrant_payload(point)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        try:
            self._request("GET", f"/collections/{self._collection_name}")
        except RuntimeError as error:
            if "HTTP 404" not in str(error):
                raise
            self._request(
                "PUT",
                f"/collections/{self._collection_name}",
                {
                    "vectors": {
                        "size": self._embedding_dimensions,
                        "distance": "Cosine",
                    }
                },
            )
        for field_name in ("snapshot_id", "category"):
            self._request(
                "PUT",
                f"/collections/{self._collection_name}/index?wait=true",
                {"field_name": field_name, "field_schema": "keyword"},
            )
        self._collection_ready = True

    def _assert_dimensions(self, vector: list[float]) -> None:
        if len(vector) != self._embedding_dimensions:
            raise ValueError(
                "Embedding provider returned dimensions that do not match the Qdrant collection."
            )

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self._base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                parsed = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise RuntimeError(f"Qdrant HTTP {error.code} for {method} {path}.") from error
        except URLError as error:
            raise RuntimeError(
                f"Qdrant transport failure for {method} {path}: {error.reason}"
            ) from error
        if not isinstance(parsed, dict) or parsed.get("status") not in {"ok", "success"}:
            raise RuntimeError("Qdrant response did not report success.")
        return parsed


class RetrievalTelemetrySink:
    """Write digest-only retrieval outcomes into the local telemetry ledger."""

    def __init__(
        self,
        telemetry: TelemetryStore,
        context_factory: Callable[[RetrievalQuery], TelemetryContext],
    ) -> None:
        self._telemetry = telemetry
        self._context_factory = context_factory

    def record(self, query: RetrievalQuery, result: RetrievalResult) -> None:
        context = self._context_factory(query)
        event = self._telemetry.emit(
            "retrieval.completed",
            context,
            actor=TelemetryActor(
                kind="system",
                identifier="agent-sdk-retrieval",
                role="retrieval",
            ),
            authority=TelemetryAuthority.DETERMINISTIC,
            status=result.status.value,
            payload={
                "query_digest": result.query_digest,
                "snapshot_id": query.snapshot_id,
                "policy_id": query.policy_id,
                "backend": result.backend,
                "candidate_count": len(result.candidates),
                "failure_reason": result.failure_reason,
            },
        )
        record_metric_value(
            self._telemetry,
            context,
            "retrieval.candidate_count",
            float(len(result.candidates)),
            "count",
            source_event_id=event.event_id,
        )
        record_metric_value(
            self._telemetry,
            context,
            "retrieval.cache_hit_count",
            float(result.status is RetrievalStatus.CACHE_HIT),
            "count",
            source_event_id=event.event_id,
        )


class GroundedRetrievalService:
    """Retrieve ranked references, cache safely, and resolve only local source facts."""

    def __init__(
        self,
        index: RetrievalIndex,
        *,
        cache: RetrievalCache | None = None,
        cache_ttl_seconds: int = 300,
        telemetry_sink: RetrievalTelemetrySink | None = None,
    ) -> None:
        if cache_ttl_seconds < 1:
            raise ValueError("retrieval cache_ttl_seconds must be positive")
        self._index = index
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds
        self._telemetry_sink = telemetry_sink

    def index_snapshot(self, snapshot_id: str, trees: Iterable[DocumentTree]) -> int:
        documents = specification_retrieval_documents(snapshot_id, trees)
        self._index.upsert(documents)
        return len(documents)

    def retrieve(
        self,
        query: RetrievalQuery,
        *,
        failure_mode: RetrievalFailureMode = RetrievalFailureMode.FALLBACK_LEXICAL,
    ) -> RetrievalResult:
        cache_key = query.query_digest
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                result = _validated_result(query, cached).model_copy(
                    update={"status": RetrievalStatus.CACHE_HIT}
                )
                self._record(query, result)
                return result
        try:
            candidates = self._index.search(query)
            result = _validated_result(
                query,
                RetrievalResult(
                    status=RetrievalStatus.RETRIEVED,
                    query_digest=query.query_digest,
                    candidates=candidates,
                    backend=self._index.backend_name,
                ),
            )
        except Exception as error:
            if failure_mode is RetrievalFailureMode.REJECT:
                raise
            result = RetrievalResult(
                status=RetrievalStatus.UNAVAILABLE,
                query_digest=query.query_digest,
                backend=self._index.backend_name,
                failure_reason=f"{type(error).__name__}: {error}",
            )
        if self._cache is not None and result.status is RetrievalStatus.RETRIEVED:
            self._cache.set(cache_key, result, self._cache_ttl_seconds)
        self._record(query, result)
        return result

    def resolve(self, result: RetrievalResult, trees: Iterable[DocumentTree]) -> ResolvedRetrieval:
        local: dict[tuple[str, str, str], DocumentNode] = {}
        categories: dict[str, SpecificationCategory] = {}
        for tree in trees:
            categories[tree.document_id] = tree.category
            for node in tree.nodes:
                local[(tree.document_id, node.node_id, node.source.source_hash)] = node
        nodes: list[DocumentNode] = []
        rejected: list[str] = []
        for candidate in result.candidates:
            node = local.get(
                (candidate.document_id, candidate.node_id, candidate.source.source_hash)
            )
            if node is None or node.source != candidate.source:
                rejected.append(candidate.backend_id)
                continue
            if categories.get(candidate.document_id) is not candidate.category:
                rejected.append(candidate.backend_id)
                continue
            nodes.append(node)
        return ResolvedRetrieval(result=result, nodes=nodes, rejected_candidate_ids=rejected)

    def _record(self, query: RetrievalQuery, result: RetrievalResult) -> None:
        if self._telemetry_sink is not None:
            self._telemetry_sink.record(query, result)


def specification_retrieval_documents(
    snapshot_id: str,
    trees: Iterable[DocumentTree],
) -> list[RetrievalDocument]:
    """Produce deterministic, source-preserving index records from frozen trees."""

    if not snapshot_id:
        raise ValueError("snapshot_id must be non-empty")
    documents: list[RetrievalDocument] = []
    for tree in trees:
        for node in tree.nodes:
            text = str(node.content).strip()
            if text:
                documents.append(
                    RetrievalDocument(
                        snapshot_id=snapshot_id,
                        category=tree.category,
                        document_id=tree.document_id,
                        node_id=node.node_id,
                        source=node.source,
                        text=text,
                    )
                )
    return sorted(documents, key=lambda document: (document.document_id, document.node_id))


def _validated_result(query: RetrievalQuery, result: RetrievalResult) -> RetrievalResult:
    if result.query_digest != query.query_digest:
        raise ValueError("retrieval result query digest does not match request")
    allowed = set(query.allowed_categories)
    candidates = [
        candidate
        for candidate in result.candidates
        if candidate.snapshot_id == query.snapshot_id and candidate.category in allowed
    ]
    candidates.sort(
        key=lambda candidate: (-candidate.score, candidate.document_id, candidate.node_id)
    )
    return result.model_copy(update={"candidates": candidates[: query.limit]})


def _document_payload(document: RetrievalDocument) -> dict[str, Any]:
    return {
        "snapshot_id": document.snapshot_id,
        "category": document.category.value,
        "document_id": document.document_id,
        "node_id": document.node_id,
        "source": document.source.model_dump(mode="json"),
        "text": document.text,
        "stable_id": document.stable_id,
    }


def _candidate_from_qdrant_payload(point: Mapping[str, Any]) -> RetrievalCandidate | None:
    payload = point.get("payload")
    if not isinstance(payload, dict):
        return None
    try:
        return RetrievalCandidate(
            snapshot_id=payload["snapshot_id"],
            category=payload["category"],
            document_id=payload["document_id"],
            node_id=payload["node_id"],
            source=payload["source"],
            score=float(point["score"]),
            backend_id=str(payload["stable_id"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
