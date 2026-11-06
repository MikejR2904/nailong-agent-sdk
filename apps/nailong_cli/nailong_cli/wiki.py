"""A project wiki the agent keeps: linked Markdown pages with revisions, an index and a journal.

Every write is a numbered revision kept under `.history/`, so nothing is overwritten for good, an
update must name the revision it was based on (a second writer cannot silently replace the first),
and a crash between the steps of a write is repaired from the history the next time the wiki is
written. A page can name the project files it was written from; when one of them changes, the page
reads as stale.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import FunctionTool, tool
from nailong_agent_sdk.tools.policy import sensitive_pattern_for

from .memory_refs import actor, digest, may_write_authoritative, parse_ref
from .storage import append_durably, exclusive
from .storage import atomic_write as _atomic_write

MAX_PAGE_CHARS = 20_000
MAX_PAGES = 500
MAX_SOURCES = 10
SNIPPET_CHARS = 160
MAX_HITS = 10
LOCK_WAIT_SECONDS = 10.0
LOCK_STALE_SECONDS = 60.0
RESERVED = {"index", "log"}
_PAGE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}(?:/[a-z0-9][a-z0-9_-]{0,63}){0,2}$")
_INDEX_LINE = re.compile(r"^- \[\[([^\]]+)\]\]: ")
_LINK = re.compile(r"\[\[([^\]\n]+)\]\]")
_REVISION = re.compile(r"^(\d{4,})\.md$")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Wiki:
    def __init__(self, directory: Path, project: Path | None = None) -> None:
        self._directory = directory
        self._project = project.resolve() if project is not None else None
        self._index: Any = None
        self.events: Any = None

    def attach(self, index: Any) -> None:
        """Let pages cite discoveries, other pages and knowledge-base sources, not only files."""
        self._index = index

    def content_digest(self, page: str) -> str | None:
        path = self._directory / f"{self._name(page)}.md"
        return digest(path.read_text(encoding="utf-8")) if path.is_file() else None

    def citing(self, ref: str) -> list[str]:
        wanted = str(parse_ref(ref))
        return [
            name
            for name in self.names()
            if any(str(parse_ref(key)) == wanted for key in self._recorded_sources(name))
        ]

    def _name(self, page: str) -> str:
        name = page.strip().lower().removesuffix(".md")
        if not _PAGE.match(name):
            raise ValueError(
                f'Wiki page name "{page}" must be lowercase letters, digits, "-" or "_", with at '
                'most two "/" folders, for example "systems/auth".'
            )
        return name

    def _path(self, page: str) -> Path:
        return self._directory / f"{self._name(page)}.md"

    def _history_dir(self, name: str) -> Path:
        return self._directory / ".history" / name

    def _journal_path(self) -> Path:
        return self._directory / ".journal.jsonl"

    def names(self) -> list[str]:
        if not self._directory.is_dir():
            return []
        found = []
        for folder, subfolders, files in os.walk(self._directory):
            subfolders[:] = [item for item in subfolders if not item.startswith(".")]
            relative = Path(folder).relative_to(self._directory)
            for item in files:
                if item.endswith(".md") and not item.startswith("."):
                    found.append((relative / item).with_suffix("").as_posix())
        return sorted(name for name in found if name not in RESERVED)

    def revision(self, page: str) -> int:
        name = self._name(page)
        numbers = self._revisions(name)
        if numbers:
            return numbers[-1]
        return 1 if (self._directory / f"{name}.md").is_file() else 0

    def _revisions(self, name: str) -> list[int]:
        folder = self._history_dir(name)
        if not folder.is_dir():
            return []
        return sorted(
            int(match.group(1))
            for item in folder.iterdir()
            if (match := _REVISION.match(item.name))
        )

    def read(self, page: str) -> dict[str, Any]:
        name = self._name(page)
        path = self._directory / f"{name}.md"
        if not path.is_file():
            raise ValueError(f'There is no wiki page "{page}"; pages: {self.names()}.')
        text = path.read_text(encoding="utf-8")
        sources = self.source_status(name)
        return {
            "page": name,
            "text": text,
            "revision": self.revision(name),
            "links_to": self._links(text),
            "linked_from": self.backlinks(name),
            "sources": sources,
            "stale": any(item["status"] != "current" for item in sources),
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

    def journal(self, page: str | None = None) -> list[dict[str, Any]]:
        path = self._journal_path()
        if not path.is_file():
            return []
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return [item for item in entries if page is None or item.get("page") == page]

    def _recorded_sources(self, name: str) -> dict[str, str]:
        for entry in reversed(self.journal(name)):
            if entry.get("action") != "recovered":
                return dict(entry.get("sources") or {})
        return {}

    def source_status(self, page: str) -> list[dict[str, str]]:
        name = self._name(page)
        statuses = []
        for relative, recorded in sorted(self._recorded_sources(name).items()):
            ref = parse_ref(relative)
            if ref.kind != "file":
                status = self._index.status(ref, recorded) if self._index is not None else "missing"
                statuses.append({"path": relative, "status": status})
                continue
            target = self._source_path(relative, check=False)
            if target is None or not target.is_file():
                status = "missing"
            else:
                status = "current" if _digest(target.read_bytes()) == recorded else "changed"
            statuses.append({"path": relative, "status": status})
        return statuses

    def stale_pages(self) -> dict[str, list[str]]:
        stale = {}
        for name in self.names():
            changed = [
                f"{item['path']} ({item['status']})"
                for item in self.source_status(name)
                if item["status"] != "current"
            ]
            if changed:
                stale[name] = changed
        return stale

    def _source_path(self, relative: str, *, check: bool) -> Path | None:
        if self._project is None:
            if check:
                raise ValueError("This wiki is not attached to a project, so it cannot cite files.")
            return None
        target = (self._project / relative).resolve()
        if not target.is_relative_to(self._project):
            if check:
                raise ValueError(f'Source "{relative}" is outside the project.')
            return None
        if check and sensitive_pattern_for(target) is not None:
            raise ValueError(f'Source "{relative}" is a credential-style path and is not cited.')
        return target

    def write(
        self,
        page: str,
        text: str,
        why: str,
        *,
        base_revision: int | None = None,
        sources: list[str] | None = None,
        author: str = "main",
    ) -> dict[str, Any]:
        name = self._name(page)
        if name in RESERVED:
            raise ValueError(
                f'"{name}" is kept by the wiki itself (index, log); pick another page.'
            )
        if not may_write_authoritative(author):
            raise ValueError(
                f"{actor(author)} may not write wiki pages: they are authoritative project "
                "knowledge. Publish a discovery instead; the main agent or the user can then "
                "record it."
            )
        body = text.strip()
        if not body:
            raise ValueError("A wiki page cannot be empty.")
        if len(body) > MAX_PAGE_CHARS:
            raise ValueError(
                f"The page would be {len(body)} characters; the limit is {MAX_PAGE_CHARS}. Split "
                "it into linked pages."
            )
        cited = self._recorded_sources(name) if sources is None else self._cite(sources)
        self._directory.mkdir(parents=True, exist_ok=True)
        with self._locked():
            self._repair_if_interrupted()
            path = self._directory / f"{name}.md"
            existed = path.is_file()
            current = self.revision(name)
            if existed and not base_revision:
                raise ValueError(
                    f'Wiki page "{name}" already exists at revision {current}. Read it with '
                    f"wiki_read, then write again with base_revision={current} so another "
                    "writer's changes are not lost."
                )
            if existed and base_revision != current:
                raise ValueError(
                    f'Wiki page "{name}" is at revision {current}, not {base_revision}: someone '
                    "changed it after you read it. Read it again and merge your change."
                )
            if not existed and base_revision:
                raise ValueError(f'Wiki page "{name}" does not exist, so it has no revision.')
            if not existed and len(self.names()) >= MAX_PAGES:
                raise ValueError(
                    f"The wiki already has {MAX_PAGES} pages; merge some before adding."
                )
            self._write_steps(name, path, existed, current, body, why, cited, author)
        known = set(self.names()) | RESERVED
        broken = [link for link in self._links(body) if link not in known]
        if self.events is not None:
            self.events.emit(
                "wiki.written", {"page": name, "revision": current + 1, "author": author}
            )
        return {
            "page": name,
            "created": not existed,
            "revision": current + 1,
            "broken_links": broken,
        }

    def _cite(self, sources: list[str]) -> dict[str, str]:
        if len(sources) > MAX_SOURCES:
            raise ValueError(f"A page can cite at most {MAX_SOURCES} files; got {len(sources)}.")
        cited = {}
        for relative in sources:
            ref = parse_ref(relative)
            if ref.kind != "file":
                if self._index is None:
                    raise ValueError(
                        f'Source "{relative}" is not a file, and this wiki is not attached to '
                        "the project's memory."
                    )
                now = self._index.fingerprint(ref)
                if now is None:
                    raise ValueError(f'Source "{relative}" does not exist.')
                cited[str(ref)] = now
                continue
            target = self._source_path(relative, check=True)
            assert target is not None
            if not target.is_file():
                raise ValueError(f'Source "{relative}" is not a file in the project.')
            cited[Path(relative).as_posix()] = _digest(target.read_bytes())
        return cited

    def _write_steps(
        self,
        name: str,
        path: Path,
        existed: bool,
        current: int,
        body: str,
        why: str,
        cited: dict[str, str],
        author: str,
    ) -> None:
        marker = self._directory / ".writing"
        marker.write_text(name, encoding="utf-8")
        history = self._history_dir(name)
        if existed and not self._revisions(name):
            _atomic_write(history / f"{1:04d}.md", path.read_text(encoding="utf-8"))
        revision = current + 1
        text = body + "\n"
        _atomic_write(history / f"{revision:04d}.md", text)
        _atomic_write(path, text)
        entry = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "page": name,
            "revision": revision,
            "action": "updated" if existed else "created",
            "author": author,
            "why": " ".join(why.split())[:200] or "no reason given",
            "sha256": _digest(text.encode("utf-8")),
            "sources": cited,
            "scope": "authoritative",
            "actor": actor(author),
        }
        self._journal_append(entry)
        self._update_index(name, text)
        self._append_log(entry)
        marker.unlink(missing_ok=True)

    def _journal_append(self, entry: dict[str, Any]) -> None:
        append_durably(self._journal_path(), json.dumps(entry, separators=(",", ":")))

    def _locked(self):
        return exclusive(
            self._directory / ".lock",
            wait=LOCK_WAIT_SECONDS,
            stale=LOCK_STALE_SECONDS,
            what="wiki",
        )

    def _repair_if_interrupted(self) -> None:
        if (self._directory / ".writing").exists():
            self.repair()

    def repair(self) -> list[str]:
        """Bring pages, journal, index and log back in line with the history after a crash."""
        fixed: list[str] = []
        history_root = self._directory / ".history"
        folders = (
            sorted(item for item in history_root.rglob("*") if item.is_dir())
            if history_root.is_dir()
            else []
        )
        for folder in folders:
            name = folder.relative_to(history_root).as_posix()
            numbers = [
                int(m.group(1)) for item in folder.iterdir() if (m := _REVISION.match(item.name))
            ]
            if not numbers or not _PAGE.match(name):
                continue
            latest = max(numbers)
            text = (folder / f"{latest:04d}.md").read_text(encoding="utf-8")
            page = self._directory / f"{name}.md"
            if not page.is_file() or page.read_text(encoding="utf-8") != text:
                _atomic_write(page, text)
                fixed.append(f"{name}: page restored to revision {latest}")
            recorded = {e.get("revision") for e in self.journal(name)}
            if latest not in recorded:
                self._journal_append(
                    {
                        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "page": name,
                        "revision": latest,
                        "action": "recovered",
                        "author": "wiki",
                        "why": "journal entry rebuilt after an interrupted write",
                        "sha256": _digest(text.encode("utf-8")),
                        "sources": {},
                    }
                )
                fixed.append(f"{name}: journal entry added for revision {latest}")
        if self._directory.is_dir():
            self._rebuild_index()
            self._render_log()
        (self._directory / ".writing").unlink(missing_ok=True)
        return fixed

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

    @staticmethod
    def _log_line(entry: dict[str, Any]) -> str:
        return (
            f"- {entry['at']} {entry['action']} [[{entry['page']}]] r{entry['revision']} "
            f"by {entry.get('author', 'main')}: {entry['why']}"
        )

    def _append_log(self, entry: dict[str, Any]) -> None:
        path = self._directory / "log.md"
        if not path.is_file():
            self._render_log()
            return
        append_durably(path, self._log_line(entry))

    @staticmethod
    def _title(name: str, text: str) -> str:
        first = next((line.strip("# ").strip() for line in text.splitlines() if line.strip()), name)
        return f"- [[{name}]]: {first[:100]}"

    def _update_index(self, name: str, text: str) -> None:
        path = self._directory / "index.md"
        if not path.is_file():
            self._rebuild_index()
            return
        entries = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            found = _INDEX_LINE.match(line)
            if found:
                entries[found.group(1)] = line
        entries[name] = self._title(name, text)
        body = "\n".join(["# Wiki index", "", *(entries[key] for key in sorted(entries))])
        _atomic_write(path, body + "\n")

    def _render_log(self) -> None:
        lines = ["# Change log", ""]
        lines.extend(self._log_line(entry) for entry in self.journal())
        _atomic_write(self._directory / "log.md", "\n".join(lines) + "\n")

    def _rebuild_index(self) -> None:
        lines = ["# Wiki index", ""]
        for name in self.names():
            text = (self._directory / f"{name}.md").read_text(encoding="utf-8")
            first = next(
                (line.strip("# ").strip() for line in text.splitlines() if line.strip()), name
            )
            lines.append(f"- [[{name}]]: {first[:100]}")
        _atomic_write(self._directory / "index.md", "\n".join(lines) + "\n")

    def tools(self, author: str = "main") -> list[FunctionTool]:
        def wiki_read(page: str) -> dict[str, Any]:
            """Read a wiki page: its text, revision, links, and whether the files it was written
            from have changed since (stale)."""
            return self.read(page)

        def wiki_search(query: str) -> list[dict[str, Any]]:
            """Find wiki pages that mention the words; check here before researching something."""
            return self.search(query)

        def wiki_write(
            page: str,
            text: str,
            why: str,
            base_revision: int = 0,
            sources: list[str] | None = None,
        ) -> dict[str, Any]:
            """Create or replace a wiki page and say why.

            Updating a page that exists needs base_revision: the revision number wiki_read gave
            you, so a change made by someone else in between is not lost. List in sources the
            project files the page was written from (up to 10); the page reads as stale when one
            of them changes; leave sources out on an update to keep the earlier list (and its
            staleness) or pass the list again to confirm you re-read them. Write short pages that
            link to each other with [[page-name]]; the result lists links to pages that do not
            exist yet. Keep claims tied to a path or page number.
            """
            return self.write(
                page, text, why, base_revision=base_revision, sources=sources, author=author
            )

        return [
            tool(wiki_read, read_only=True),
            tool(wiki_search, read_only=True),
            tool(wiki_write),
        ]
