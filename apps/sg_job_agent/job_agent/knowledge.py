"""The candidate's experience bank: cited evidence items plus BM25 retrieval.

Everything the tailoring agents may claim comes from here or from the base
resume. Each item keeps its origin (document section, GitHub repo, LinkedIn
export row, web page) so the agents cite item ids and the integrity check can
verify that claimed numbers and dates exist in what was cited.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SOURCES = ("resume", "document", "github", "linkedin", "website")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.]*[a-z0-9+#]|[a-z0-9]")
_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or our that the this to "
    "was we were will with you your".split()
)
MAX_ITEM_CHARS = 6_000


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass
class EvidenceItem:
    source: str
    title: str
    text: str
    url: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        digest = hashlib.sha1(f"{self.source}|{self.title}|{self.text}".encode()).hexdigest()
        return f"{self.source[:2]}-{digest[:8]}"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "id": self.id}


def chunk_text(text: str, *, title: str, max_chars: int = 2_500) -> list[tuple[str, str]]:
    """Split free-form notes on markdown headings, then pack paragraphs to max_chars.

    Returns (title, text) pairs; a heading becomes the title of its chunks.
    """

    sections: list[tuple[str, list[str]]] = [(title, [])]
    for line in text.splitlines():
        heading = re.match(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$", line)
        if heading:
            sections.append((heading.group(1).strip(), []))
        else:
            sections[-1][1].append(line)
    chunks: list[tuple[str, str]] = []
    for section_title, lines in sections:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]
        current = ""
        for paragraph in paragraphs:
            if current and len(current) + len(paragraph) + 2 > max_chars:
                chunks.append((section_title, current))
                current = ""
            current = f"{current}\n\n{paragraph}" if current else paragraph[: max_chars * 2]
        if current:
            chunks.append((section_title, current))
    return chunks


class KnowledgeBase:
    """Items persisted as JSON lines under ``<workspace>/knowledge/items.jsonl``."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "items.jsonl"
        self._items: dict[str, EvidenceItem] = {}
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    data = json.loads(line)
                    data.pop("id", None)
                    item = EvidenceItem(**data)
                    self._items[item.id] = item
        self._index: _Bm25 | None = None

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, item_id: object) -> bool:
        return item_id in self._items

    def replace_source(self, source: str, items: list[EvidenceItem]) -> int:
        """Swap every item of one source for a fresh ingest of that source."""

        if source not in SOURCES:
            raise ValueError(f"Unknown evidence source {source!r}")
        self._items = {k: v for k, v in self._items.items() if v.source != source}
        for item in items:
            item.text = item.text[:MAX_ITEM_CHARS]
            self._items[item.id] = item
        self._index = None
        return len(items)

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(item.to_dict(), ensure_ascii=False) for item in self._items.values()]
        self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def get(self, item_id: str) -> EvidenceItem:
        if item_id not in self._items:
            raise KeyError(f"Unknown evidence id {item_id!r}")
        return self._items[item_id]

    def items(self, source: str | None = None) -> list[EvidenceItem]:
        return [i for i in self._items.values() if source is None or i.source == source]

    def counts(self) -> dict[str, int]:
        return dict(Counter(item.source for item in self._items.values()))

    def catalog(self) -> list[dict[str, str]]:
        """Compact table of contents for prompts: id, source, title."""

        return [
            {"id": item.id, "source": item.source, "title": item.title[:90]}
            for item in self._items.values()
        ]

    def search(
        self, query: str, *, limit: int = 8, sources: list[str] | None = None
    ) -> list[tuple[EvidenceItem, float]]:
        if self._index is None:
            self._index = _Bm25(list(self._items.values()))
        hits = self._index.search(query)
        if sources:
            hits = [(item, score) for item, score in hits if item.source in sources]
        return hits[:limit]

    def text_for(self, item_ids: list[str]) -> str:
        return "\n\n".join(self.get(item_id).text for item_id in item_ids)


class _Bm25:
    def __init__(self, items: list[EvidenceItem], k1: float = 1.4, b: float = 0.75) -> None:
        self.items = items
        self.k1, self.b = k1, b
        self.docs = [Counter(tokens(f"{item.title}\n{item.text}")) for item in items]
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.average = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0
        frequency: Counter[str] = Counter()
        for doc in self.docs:
            frequency.update(doc.keys())
        total = len(self.docs)
        self.idf = {
            term: math.log(1 + (total - count + 0.5) / (count + 0.5))
            for term, count in frequency.items()
        }

    def search(self, query: str) -> list[tuple[EvidenceItem, float]]:
        terms = set(tokens(query))
        scored = []
        for item, doc, length in zip(self.items, self.docs, self.lengths, strict=True):
            score = 0.0
            for term in terms:
                tf = doc.get(term, 0)
                if tf:
                    norm = tf * (self.k1 + 1)
                    norm /= tf + self.k1 * (1 - self.b + self.b * length / self.average)
                    score += self.idf[term] * norm
            if score > 0:
                scored.append((item, round(score, 3)))
        scored.sort(key=lambda pair: (-pair[1], pair[0].id))
        return scored
