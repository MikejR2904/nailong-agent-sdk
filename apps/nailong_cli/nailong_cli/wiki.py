"""A project wiki the agent keeps: linked Markdown pages, an index and a log of changes."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import FunctionTool, tool

MAX_PAGE_CHARS = 20_000
MAX_PAGES = 500
SNIPPET_CHARS = 160
MAX_HITS = 10
RESERVED = {"index", "log"}
_PAGE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}(?:/[a-z0-9][a-z0-9_-]{0,63}){0,2}$")
_LINK = re.compile(r"\[\[([^\]\n]+)\]\]")


class Wiki:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def _path(self, page: str) -> Path:
        name = page.strip().lower().removesuffix(".md")
        if not _PAGE.match(name):
            raise ValueError(
                f'Wiki page name "{page}" must be lowercase letters, digits, "-" or "_", with at '
                'most two "/" folders, for example "systems/auth".'
            )
        return self._directory / f"{name}.md"

    def names(self) -> list[str]:
        if not self._directory.is_dir():
            return []
        found = (
            path.relative_to(self._directory).with_suffix("").as_posix()
            for path in self._directory.rglob("*.md")
        )
        return sorted(name for name in found if name not in RESERVED)

    def read(self, page: str) -> dict[str, Any]:
        path = self._path(page)
        if not path.is_file():
            raise ValueError(f'There is no wiki page "{page}"; pages: {self.names()}.')
        text = path.read_text(encoding="utf-8")
        return {
            "page": page,
            "text": text,
            "links_to": self._links(text),
            "linked_from": self.backlinks(page),
        }

    def backlinks(self, page: str) -> list[str]:
        target = page.strip().lower().removesuffix(".md")
        found = []
        for name in self.names():
            if name == target:
                continue
            text = (self._directory / f"{name}.md").read_text(encoding="utf-8")
            if target in self._links(text):
                found.append(name)
        return found

    @staticmethod
    def _links(text: str) -> list[str]:
        return sorted({link.strip().lower() for link in _LINK.findall(text)})

    def write(self, page: str, text: str, why: str) -> dict[str, Any]:
        name = page.strip().lower().removesuffix(".md")
        if name in RESERVED:
            raise ValueError(
                f'"{name}" is kept by the wiki itself (index, log); pick another page.'
            )
        path = self._path(page)
        body = text.strip()
        if not body:
            raise ValueError("A wiki page cannot be empty.")
        if len(body) > MAX_PAGE_CHARS:
            raise ValueError(
                f"The page would be {len(body)} characters; the limit is {MAX_PAGE_CHARS}. Split "
                "it into linked pages."
            )
        if not path.is_file() and len(self.names()) >= MAX_PAGES:
            raise ValueError(f"The wiki already has {MAX_PAGES} pages; merge some before adding.")
        existed = path.is_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body + "\n", encoding="utf-8")
        self._log(name, "updated" if existed else "created", why)
        self._rebuild_index()
        known = set(self.names()) | RESERVED
        broken = [link for link in self._links(body) if link not in known]
        return {"page": name, "created": not existed, "broken_links": broken}

    def search(self, query: str, limit: int = MAX_HITS) -> list[dict[str, Any]]:
        words = [word.lower() for word in re.findall(r"\w+", query) if len(word) > 1]
        if not words:
            raise ValueError("query needs at least one word of two or more letters.")
        hits = []
        for name in self.names():
            text = (self._directory / f"{name}.md").read_text(encoding="utf-8")
            lowered = text.lower()
            score = sum(lowered.count(word) for word in words) + 3 * sum(
                word in name for word in words
            )
            if score:
                at = min((lowered.find(word) for word in words if word in lowered), default=0)
                snippet = " ".join(text[max(0, at - 60) : at + SNIPPET_CHARS].split())
                hits.append({"page": name, "score": score, "snippet": snippet})
        hits.sort(key=lambda item: -item["score"])
        return hits[: min(limit, MAX_HITS)]

    def _log(self, name: str, action: str, why: str) -> None:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        entry = (
            f"- {stamp} {action} [[{name}]]: {' '.join(why.split())[:200] or 'no reason given'}\n"
        )
        self._directory.mkdir(parents=True, exist_ok=True)
        with (self._directory / "log.md").open("a", encoding="utf-8") as handle:
            if handle.tell() == 0:
                handle.write("# Change log\n\n")
            handle.write(entry)

    def _rebuild_index(self) -> None:
        lines = ["# Wiki index", ""]
        for name in self.names():
            if name in RESERVED:
                continue
            text = (self._directory / f"{name}.md").read_text(encoding="utf-8")
            first = next(
                (line.strip("# ").strip() for line in text.splitlines() if line.strip()), name
            )
            lines.append(f"- [[{name}]]: {first[:100]}")
        (self._directory / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def tools(self) -> list[FunctionTool]:
        def wiki_read(page: str) -> dict[str, Any]:
            """Read a wiki page, with the pages it links to and the pages that link to it."""
            return self.read(page)

        def wiki_search(query: str) -> list[dict[str, Any]]:
            """Find wiki pages that mention the words; check here before researching something."""
            return self.search(query)

        def wiki_write(page: str, text: str, why: str) -> dict[str, Any]:
            """Create or replace a wiki page and say why.

            Write what you learned as short pages that link to each other with [[page-name]]; the
            result lists links to pages that do not exist yet. The index and the change log are
            kept for you. Keep claims you got from a file or a page tied to its path or page number.
            """
            return self.write(page, text, why)

        return [
            tool(wiki_read, read_only=True),
            tool(wiki_search, read_only=True),
            tool(wiki_write),
        ]
