"""An optional meaning-based index for the knowledge base, kept beside the word-based one.

Word search stays the default and the baseline: it is exact, free and needs no service. When
`embedding_model` is set, `/kb embed` sends the passages to an OpenAI-compatible `/embeddings`
endpoint (you run it on purpose; it says how much it sends) and stores one vector per passage
next to a hash of the passage text, so a vector made from older text is never used. A search then
ranks by word match and by meaning separately and merges the two rankings; every hit is still
checked against the source file's hash and marked changed or missing like any other.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from .storage import atomic_write, exclusive

BATCH = 64
TIMEOUT_SECONDS = 60.0
FUSION_K = 60
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class EmbeddingError(ValueError):
    pass


class Embedder(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HttpEmbedder:
    def __init__(self, base_url: str, api_key: str | None, model: str) -> None:
        self._url = base_url.rstrip("/") + "/embeddings"
        self._key = api_key
        self.model = model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            vectors += self._batch(list(texts[start : start + BATCH]))
        return vectors

    def _batch(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        body = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        request = urllib.request.Request(self._url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise EmbeddingError(
                f"{self._url} answered HTTP {error.code} {error.reason}."
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise EmbeddingError(
                f"Could not reach {self._url}: {type(error).__name__}: {error}"
            ) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise EmbeddingError(f"{self._url} sent more than {MAX_RESPONSE_BYTES} bytes.")
        try:
            data = json.loads(raw)["data"]
            ordered = sorted(data, key=lambda item: item["index"])
            vectors = [[float(x) for x in item["embedding"]] for item in ordered]
        except (ValueError, KeyError, TypeError) as error:
            raise EmbeddingError(
                f"{self._url} did not send embeddings in the OpenAI shape ({error})."
            ) from None
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"{self._url} returned {len(vectors)} vectors for {len(texts)} texts."
            )
        return vectors


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def fuse(*rankings: Sequence[str], k: int = FUSION_K) -> list[str]:
    """Reciprocal-rank fusion: an item near the top of either list ends up near the top."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position + 1)
    return sorted(scores, key=lambda item: (-scores[item], item))


class VectorStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"model": "", "vectors": {}}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise EmbeddingError(
                f"{self._path} is not valid JSON ({error.msg}); delete it and run /kb embed."
            ) from None

    def update(self, model: str, additions: dict[str, dict[str, Any]], keep: set[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive(
            self._path.with_suffix(".lock"), wait=30.0, stale=120.0, what="embedding index"
        ):
            data = self.load()
            vectors = data["vectors"] if data.get("model") == model else {}
            vectors = {key: value for key, value in vectors.items() if key in keep}
            vectors.update(additions)
            atomic_write(self._path, json.dumps({"model": model, "vectors": vectors}))
