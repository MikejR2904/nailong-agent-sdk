# Copyright (c) 2026 David Michael Indraputra

"""Synchronous OpenAI-compatible embedding provider for grounded-retrieval indexes."""

from __future__ import annotations

from collections.abc import Mapping

from ...foundations.errors import AgentSdkError
from .transport import JsonHttpTransport, OpenAICompatibleEndpoint, UrlLibJsonTransport


class OpenAICompatibleEmbeddingProvider:
    """Use a host-selected OpenAI-compatible embedding endpoint synchronously.

    ``QdrantRetrievalIndex`` is synchronous, so this adapter intentionally exposes the same
    synchronous ``EmbeddingProvider`` shape. It returns only finite numeric vector elements.
    """

    def __init__(
        self,
        endpoint: OpenAICompatibleEndpoint,
        *,
        model: str,
        transport: JsonHttpTransport | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("OpenAI-compatible embedding model identifier must be non-empty.")
        self._endpoint = endpoint
        self._model = model
        self._transport = transport or UrlLibJsonTransport()

    def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("Embedding input must be non-empty.")
        response = self._transport.post_json(
            self._endpoint.url_for("/embeddings"),
            headers=self._endpoint.headers,
            payload={"model": self._model, "input": text},
            timeout_seconds=self._endpoint.timeout_seconds,
        )
        data = response.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], Mapping):
            raise AgentSdkError(
                "OPENAI_COMPATIBLE_EMBEDDING_INVALID",
                "Embedding response must contain exactly one data item.",
            )
        raw_embedding = data[0].get("embedding")
        if not isinstance(raw_embedding, list) or not raw_embedding:
            raise AgentSdkError(
                "OPENAI_COMPATIBLE_EMBEDDING_INVALID",
                "Embedding response lacks a non-empty embedding vector.",
            )
        vector: list[float] = []
        for value in raw_embedding:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise AgentSdkError(
                    "OPENAI_COMPATIBLE_EMBEDDING_INVALID",
                    "Embedding vector elements must be numeric.",
                )
            numeric = float(value)
            if numeric != numeric or numeric in {float("inf"), float("-inf")}:
                raise AgentSdkError(
                    "OPENAI_COMPATIBLE_EMBEDDING_INVALID",
                    "Embedding vector elements must be finite.",
                )
            vector.append(numeric)
        return vector
