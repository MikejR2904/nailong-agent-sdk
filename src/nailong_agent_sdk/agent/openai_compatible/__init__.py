# Copyright (c) 2026 David Michael Indraputra

"""Optional host-injected adapters for OpenAI-compatible chat, embeddings, and vision.

The adapters have no ambient credential lookup and no default model. A host must supply a
base URL, API key, and exact model identifier for each adapter. They return untrusted data
that is validated against the SDK's existing typed contracts before a caller can use it.
"""

from __future__ import annotations

from .chat import OpenAICompatibleAgentModel
from .embeddings import OpenAICompatibleEmbeddingProvider
from .transport import (
    HttpxJsonTransport,
    HttpxStreamingJsonTransport,
    JsonHttpTransport,
    OpenAICompatibleEndpoint,
    StreamingJsonHttpTransport,
    UrlLibJsonTransport,
)
from .vision import ImageBytesLoader, OpenAICompatibleVisionAdapter, SourceVerifiedImageLoader

__all__ = [
    "HttpxJsonTransport",
    "HttpxStreamingJsonTransport",
    "ImageBytesLoader",
    "JsonHttpTransport",
    "OpenAICompatibleAgentModel",
    "OpenAICompatibleEmbeddingProvider",
    "OpenAICompatibleEndpoint",
    "OpenAICompatibleVisionAdapter",
    "SourceVerifiedImageLoader",
    "StreamingJsonHttpTransport",
    "UrlLibJsonTransport",
]
