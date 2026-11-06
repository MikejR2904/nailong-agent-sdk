"""One way to name project memory, and one place that says whether a piece of it still holds.

Every item the CLI keeps has a reference: `file:src/auth.py`, `kb:docs/guide.pdf`,
`wiki:systems/auth`, `discovery:d-0003`. A reference has a fingerprint that changes when the thing
it names changes (a file's bytes, a knowledge-base source, a wiki page's text, a discovery's claim,
status and evidence). A wiki page records the fingerprints of what it cites, so when a discovery is
superseded or a file is edited, every page that rests on it reads as stale without anyone searching
for it.

Each kind of memory also has a scope, which decides who may write it:

- `private`: one agent's working notes. Only that agent writes or reads them.
- `shared`: findings published for others (discoveries). Any agent may publish; a person decides.
- `authoritative`: accepted knowledge (wiki pages, accepted discoveries). The main agent or the
  user writes it; a subagent publishes a discovery instead.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from .discoveries import DiscoveryStore
    from .knowledge import KnowledgeBase
    from .wiki import Wiki

KINDS = ("file", "kb", "wiki", "discovery")
SCOPES = {
    "private": "one agent's working notes",
    "shared": "findings published for others to build on",
    "authoritative": "accepted project knowledge",
}
KIND_SCOPE = {"file": None, "kb": "shared", "wiki": "authoritative", "discovery": "shared"}
_REF = re.compile(r"^(file|kb|wiki|discovery):(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class Ref:
    kind: str
    name: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.name}"


def parse_ref(text: str) -> Ref:
    """A bare path means a file, so references written before this existed still work."""
    found = _REF.match(text.strip())
    if found is None:
        return Ref("file", text.strip().replace("\\", "/"))
    kind, name = found.group(1).lower(), found.group(2)
    return Ref(kind, name.strip().lower() if kind in {"wiki", "discovery"} else name.strip())


def actor(label: str) -> str:
    """The identity recorded for whoever acted: `user`, `agent:main` or `agent:sub-scout`."""
    return label if label == "user" or label.startswith("agent:") else f"agent:{label}"


def may_write_authoritative(label: str) -> bool:
    return actor(label) in {"user", "agent:main"}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class MemoryIndex:
    def __init__(
        self,
        project: Path,
        wiki: Wiki | None = None,
        discoveries: DiscoveryStore | None = None,
        knowledge: KnowledgeBase | None = None,
    ) -> None:
        self._project = project.resolve()
        self._wiki = wiki
        self._discoveries = discoveries
        self._knowledge = knowledge

    def fingerprint(self, ref: Ref | str) -> str | None:
        """What the thing is now, or None when it does not exist."""
        item = parse_ref(ref) if isinstance(ref, str) else ref
        if item.kind == "file":
            target = (self._project / item.name).resolve()
            if not target.is_relative_to(self._project) or not target.is_file():
                return None
            return hashlib.sha256(target.read_bytes()).hexdigest()
        if item.kind == "kb":
            return self._knowledge.source_fingerprint(item.name) if self._knowledge else None
        if item.kind == "wiki":
            if self._wiki is None or item.name not in self._wiki.names():
                return None
            return self._wiki.content_digest(item.name)
        if item.kind == "discovery":
            if self._discoveries is None:
                return None
            try:
                found = self._discoveries.get(item.name)
            except ValueError:
                return None
            return digest(f"{found['status']}|{found['claim']}|{found['stale']}")
        raise ValueError(f"Unknown memory kind {item.kind!r}; the kinds are {list(KINDS)}.")

    def status(self, ref: Ref | str, recorded: str | None = None) -> str:
        """current, changed (differs from what was recorded) or missing."""
        now = self.fingerprint(ref)
        if now is None:
            return "missing"
        return "changed" if recorded is not None and now != recorded else "current"

    def describe(self, ref: Ref | str) -> dict[str, Any]:
        item = parse_ref(ref) if isinstance(ref, str) else ref
        info: dict[str, Any] = {
            "ref": str(item),
            "kind": item.kind,
            "scope": KIND_SCOPE[item.kind],
            "exists": self.fingerprint(item) is not None,
            "cited_by": self.dependents(item),
        }
        if item.kind == "discovery" and info["exists"] and self._discoveries is not None:
            found = self._discoveries.get(item.name)
            info.update(status=found["status"], stale=found["stale"], owner=found["owner"])
        if item.kind == "wiki" and info["exists"] and self._wiki is not None:
            page = self._wiki.read(item.name)
            info.update(revision=page["revision"], stale=page["stale"])
        return info

    def dependents(self, ref: Ref | str) -> list[str]:
        """Wiki pages whose latest revision cites this reference."""
        item = parse_ref(ref) if isinstance(ref, str) else ref
        return self._wiki.citing(str(item)) if self._wiki is not None else []

    def stale_report(self) -> dict[str, list[str]]:
        """Everything that no longer holds, by store."""
        report: dict[str, list[str]] = {}
        if self._wiki is not None:
            for page, reasons in self._wiki.stale_pages().items():
                report.setdefault("wiki", []).append(f"{page}: {', '.join(reasons)}")
        if self._discoveries is not None:
            for row in self._discoveries.listing("all"):
                if row["stale"] and row["status"] in {"proposed", "accepted"}:
                    report.setdefault("discovery", []).append(f"{row['id']} {row['topic']}")
        if self._knowledge is not None:
            for line in self._knowledge.listing():
                if not line.endswith("current"):
                    report.setdefault("kb", []).append(line)
        return report


def memory_status_tool(index: MemoryIndex):
    from nailong_agent_sdk.starter import tool

    def memory_status(ref: str) -> dict[str, Any]:
        """Say whether a piece of project memory still holds and which wiki pages rest on it.

        ref is file:PATH, kb:PATH, wiki:PAGE or discovery:ID (a bare path means a file). Use it
        before relying on an old page or discovery, and after you change something others cite.
        """
        return index.describe(ref)

    return tool(memory_status, read_only=True)
