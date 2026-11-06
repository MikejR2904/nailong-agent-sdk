"""A local knowledge base: files you add are split into cited passages the agent can search."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nailong_agent_sdk import (
    DeterministicLexicalRetrievalIndex,
    DocumentFormat,
    DocumentNode,
    DocumentNodeKind,
    DocumentTree,
    GroundedRetrievalService,
    RetrievalQuery,
    SourceRef,
)
from nailong_agent_sdk.starter import FunctionTool, tool

from .documents import DOCUMENT_SUFFIXES, DocumentReader, _unit
from .storage import atomic_write, exclusive

CATEGORY = "knowledge"
TEXT_SUFFIXES = {".md", ".txt", ".rst", ".tex", ".json", ".yaml", ".yml", ".xml", ".toml"}
CHUNK_CHARS = 1_000
MAX_FILES = 400
MAX_FILE_BYTES = 8 * 1024 * 1024
LOCK_WAIT_SECONDS = 30.0
LOCK_STALE_SECONDS = 120.0
SKIPPED = {".git", "node_modules", ".venv", "venv", "__pycache__", ".nailong"}
FORMATS = {
    ".md": DocumentFormat.MD,
    ".txt": DocumentFormat.TXT,
    ".rst": DocumentFormat.TXT,
    ".tex": DocumentFormat.TEX,
    ".json": DocumentFormat.JSON,
    ".yaml": DocumentFormat.YAML,
    ".yml": DocumentFormat.YAML,
    ".xml": DocumentFormat.XML,
    ".toml": DocumentFormat.TXT,
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".pptx": DocumentFormat.TXT,
    ".xlsx": DocumentFormat.XLSX,
    ".xlsm": DocumentFormat.XLSX,
    ".csv": DocumentFormat.CSV,
    ".tsv": DocumentFormat.CSV,
}


@dataclass(frozen=True)
class Passage:
    path: str
    location: str
    text: str
    source_hash: str
    status: str = "current"


def _passages(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    buffer: list[str] = []
    size = 0
    first = 1
    for number, line in enumerate(lines, start=1):
        if buffer and (
            size + len(line) > CHUNK_CHARS or (not line.strip() and size > CHUNK_CHARS // 2)
        ):
            out.append((f"lines {first}-{number - 1}", "\n".join(buffer).strip()))
            buffer, size, first = [], 0, number
        buffer.append(line)
        size += len(line) + 1
    if buffer and "".join(buffer).strip():
        out.append((f"lines {first}-{len(lines)}", "\n".join(buffer).strip()))
    return [item for item in out if item[1]]


class KnowledgeBase:
    def __init__(self, directory: Path, roots: dict[str, Path], workspace: Path) -> None:
        self._directory = directory
        self._project = roots["project"].resolve()
        self._reader = DocumentReader(roots, workspace)
        self._trees: list[DocumentTree] | None = None
        self._stamp: tuple[int, int] | None = None
        self._revision = 0

    @property
    def _file(self) -> Path:
        return self._directory / "trees.json"

    def _disk_stamp(self) -> tuple[int, int] | None:
        try:
            info = self._file.stat()
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size

    def _load(self) -> None:
        self._stamp = self._disk_stamp()
        if self._stamp is None:
            self._trees, self._revision = [], 0
            return
        data = json.loads(self._file.read_text(encoding="utf-8"))
        items = data["trees"] if isinstance(data, dict) else data
        self._revision = int(data.get("revision", 0)) if isinstance(data, dict) else 0
        self._trees = [DocumentTree.model_validate(item) for item in items]

    def trees(self) -> list[DocumentTree]:
        if self._trees is None or self._disk_stamp() != self._stamp:
            self._load()
        assert self._trees is not None
        return self._trees

    @property
    def revision(self) -> int:
        self.trees()
        return self._revision

    def _change(self, apply: Callable[[dict[str, DocumentTree]], Any]) -> Any:
        """Read the file again under the lock, change it, and replace it whole, revision + 1."""
        self._directory.mkdir(parents=True, exist_ok=True)
        with exclusive(
            self._directory / ".lock",
            wait=LOCK_WAIT_SECONDS,
            stale=LOCK_STALE_SECONDS,
            what="knowledge base",
        ):
            self._load()
            trees = {tree.relative_path: tree for tree in self._trees or []}
            result = apply(trees)
            atomic_write(
                self._file,
                json.dumps(
                    {
                        "revision": self._revision + 1,
                        "trees": [tree.model_dump(mode="json") for tree in trees.values()],
                    }
                ),
            )
            self._load()
        return result

    def _files(self, target: Path) -> list[Path]:
        if target.is_file():
            return [target] if target.suffix.lower() in FORMATS else []
        found = []
        for path in sorted(target.rglob("*")):
            if any(part in SKIPPED for part in path.relative_to(target).parts):
                continue
            if path.is_file() and path.suffix.lower() in FORMATS:
                found.append(path)
        return found

    def add(self, name: str) -> dict[str, Any]:
        target = (self._project / name).resolve() if not Path(name).is_absolute() else Path(name)
        if not target.exists() or not target.resolve().is_relative_to(self._project):
            raise ValueError(f'"{name}" is not a file or folder inside the project.')
        files = self._files(target)
        if not files:
            raise ValueError(f'"{name}" has no files the knowledge base reads: {sorted(FORMATS)}.')
        if len(files) > MAX_FILES:
            raise ValueError(f'"{name}" has {len(files)} files; the limit is {MAX_FILES} at once.')
        built: dict[str, DocumentTree] = {}
        skipped: list[str] = []
        for path in files:
            relative = path.resolve().relative_to(self._project).as_posix()
            try:
                built[relative] = self._tree(path, relative)
            except (ValueError, OSError) as error:
                skipped.append(f"{relative}: {error}")

        def merge(trees: dict[str, DocumentTree]) -> int:
            trees.update(built)
            return len(trees)

        total = self._change(merge)
        return {"added": len(built), "skipped": skipped, "files": total}

    def _tree(self, path: Path, relative: str) -> DocumentTree:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"over {MAX_FILE_BYTES // 1_000_000} MB")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        suffix = path.suffix.lower()
        pieces: list[tuple[str, str]] = []
        if suffix in DOCUMENT_SUFFIXES:
            kind, units, _ = self._reader.units(path)
            unit = _unit(kind)
            for number, read in enumerate(units, start=1):
                text = read().strip()
                for index, (_, chunk) in enumerate(_passages(text)):
                    pieces.append(
                        (f"{unit} {number}" + (f" (part {index + 1})" if index else ""), chunk)
                    )
        else:
            pieces = _passages(path.read_text(encoding="utf-8", errors="replace"))
        if not pieces:
            raise ValueError("no text found")
        fmt = FORMATS[suffix]
        nodes = []
        for number, (location, text) in enumerate(pieces, start=1):
            source = SourceRef(
                document_id=relative,
                relative_path=relative,
                source_hash=digest,
                format=fmt,
                location=location,
            )
            nodes.append(
                DocumentNode(
                    node_id=f"{relative}#{number}",
                    kind=DocumentNodeKind.TEXT,
                    source=source,
                    content=text,
                )
            )
        return DocumentTree(
            document_id=relative,
            category=CATEGORY,
            title=path.name,
            format=fmt,
            relative_path=relative,
            source_hash=digest,
            nodes=nodes,
        )

    def remove(self, name: str) -> int:
        prefix = name.strip("/").replace("\\", "/")

        def drop(trees: dict[str, DocumentTree]) -> int:
            doomed = [key for key in trees if key == prefix or key.startswith(prefix + "/")]
            if not doomed:
                raise ValueError(f'Nothing in the knowledge base is named "{name}".')
            for key in doomed:
                del trees[key]
            return len(doomed)

        return self._change(drop)

    def listing(self) -> list[str]:
        lines = []
        for tree in self.trees():
            current = self._project / tree.relative_path
            if not current.is_file():
                state = "missing"
            elif hashlib.sha256(current.read_bytes()).hexdigest() != tree.source_hash:
                state = "changed since added (/kb refresh)"
            else:
                state = "current"
            lines.append(f"{tree.relative_path}: {len(tree.nodes)} passage(s), {state}")
        return lines

    def refresh(self) -> dict[str, Any]:
        names = [tree.relative_path for tree in self.trees()]
        dropped = [name for name in names if not (self._project / name).is_file()]
        for name in dropped:
            self.remove(name)
        updated = 0
        for name in names:
            if name not in dropped:
                updated += self.add(name)["added"]
        return {"updated": updated, "dropped": dropped}

    def search(self, query: str, limit: int = 6) -> list[Passage]:
        trees = self.trees()
        if not trees:
            raise ValueError("The knowledge base is empty; add files with /kb add PATH.")
        snapshot = hashlib.sha256("".join(t.source_hash for t in trees).encode()).hexdigest()[:16]
        service = GroundedRetrievalService(DeterministicLexicalRetrievalIndex())
        service.index_snapshot(snapshot, trees)
        result = service.retrieve(
            RetrievalQuery(
                snapshot_id=snapshot,
                query_text=query,
                allowed_categories=[CATEGORY],
                limit=max(1, min(limit, 20)),
            )
        )
        resolved = service.resolve(result, trees)
        found = [
            Passage(
                node.source.relative_path,
                node.source.location,
                str(node.content),
                node.source.source_hash[:12],
                self._source_status(node.source.relative_path, node.source.source_hash),
            )
            for node in resolved.nodes
        ]
        return sorted(found, key=lambda item: item.status != "current")

    def _source_status(self, relative: str, recorded: str) -> str:
        current = self._project / relative
        if not current.is_file():
            return "missing"
        return (
            "current" if hashlib.sha256(current.read_bytes()).hexdigest() == recorded else "changed"
        )

    def source_fingerprint(self, relative: str) -> str | None:
        for tree in self.trees():
            if tree.relative_path == relative:
                return tree.source_hash
        return None

    def tools(self) -> list[FunctionTool]:
        def knowledge_search(query: str, limit: int = 6) -> list[dict[str, str]]:
            """Search the passages added to the knowledge base; each hit says which file and where.

            Answer from these and cite the path and location; if nothing relevant comes back,
            say so. A hit whose status is changed or missing comes from a file that is not the one
            that was indexed: do not rely on it until the user runs /kb refresh.
            """
            return [
                {
                    "path": item.path,
                    "location": item.location,
                    "source_hash": item.source_hash,
                    "status": item.status,
                    "text": item.text,
                }
                for item in self.search(query, limit)
            ]

        return [tool(knowledge_search, read_only=True)]
