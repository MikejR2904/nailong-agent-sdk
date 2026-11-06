"""Reading PDF, Word, Excel, PowerPoint and CSV files, with where each part came from."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import posixpath
import re
import urllib.parse
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from nailong_agent_sdk.starter import FunctionTool, tool
from nailong_agent_sdk.tools.policy import sensitive_pattern_for

MAX_BYTES = 80 * 1024 * 1024
DEFAULT_CHARS = 12_000
MAX_CHARS = 40_000
MAX_ROWS = 200
MAX_HITS = 15
MAX_UNPACKED_BYTES = 250 * 1024 * 1024
MAX_ZIP_ENTRIES = 5_000
MAX_ZIP_RATIO = 200
FETCH_SECONDS = 90.0
FETCH_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
}
SNIPPET_CHARS = 160
DOCUMENT_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".csv", ".tsv"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
VISION_INSTRUCTIONS = (
    "You are looking at one page or image. Answer the question about it from what is visible: "
    "read text exactly, describe diagrams, axes, boxes and arrows, and give table cells as rows. "
    "Say so when something is unreadable; do not guess."
)


class DocumentReader:
    def __init__(self, roots: Mapping[str, Path], workspace: Path) -> None:
        self._roots = {name: path.resolve() for name, path in roots.items()}
        self._workspace = workspace

    def fetch(self, url: str) -> dict[str, Any]:
        from nailong_agent_sdk.tools.core.helpers import _http_get_public

        def byte_limit_for(content_type: str) -> int:
            if content_type in FETCH_TYPES or content_type == "application/octet-stream":
                return MAX_BYTES + 1
            raise ValueError(
                f"{url} is {content_type!r}, not a PDF, Word, Excel, PowerPoint or CSV file; "
                "use web_fetch for web pages."
            )

        fetched = _http_get_public(
            url, "fetch_document", byte_limit_for=byte_limit_for, deadline_seconds=FETCH_SECONDS
        )
        if fetched.deadline_reached:
            raise ValueError(
                f"The download from {fetched.url} did not finish in {FETCH_SECONDS:g}s."
            )
        if len(fetched.body) > MAX_BYTES:
            raise ValueError(f"{fetched.url} is over the {MAX_BYTES // 1_000_000} MB limit.")
        suffix = FETCH_TYPES.get(fetched.content_type)
        if suffix is None:
            suffix = ".pdf" if fetched.body.startswith(b"%PDF") else None
            urlpath = urllib.parse.urlparse(fetched.url).path.lower()
            for known in DOCUMENT_SUFFIXES:
                if suffix is None and urlpath.endswith(known):
                    suffix = known
        if suffix is None:
            raise ValueError(
                f"{fetched.url} sent application/octet-stream that is not a PDF and has no "
                "document extension, so its type is unknown."
            )
        if suffix == ".pdf" and not fetched.body.startswith(b"%PDF"):
            raise ValueError(f"{fetched.url} claims to be a PDF but does not start with %PDF.")
        digest = hashlib.sha256(fetched.body).hexdigest()[:12]
        host = re.sub(r"[^A-Za-z0-9.-]", "-", urllib.parse.urlparse(fetched.url).hostname or "web")
        target = self._workspace / "downloads" / f"{host}-{digest}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fetched.body)
        root = next(iter(self._roots.values()))
        shown = target.relative_to(root).as_posix() if target.is_relative_to(root) else str(target)
        return {
            "path": shown,
            "url": fetched.url,
            "bytes": len(fetched.body),
            "sha256": hashlib.sha256(fetched.body).hexdigest()[:12],
            "note": "Untrusted download; pass path to read_document or search_document.",
        }

    def _resolve(self, name: str) -> Path:
        if name.lower().startswith(("http://", "https://")):
            raise ValueError(
                f"{name} is a URL. Call fetch_document with it first (needs web = true or --web), "
                "then pass the path it returns."
            )
        candidate = Path(name)
        for root in self._roots.values():
            target = candidate if candidate.is_absolute() else root / candidate
            target = target.resolve()
            if target.is_relative_to(root) and target.is_file():
                if sensitive_pattern_for(target) is not None:
                    raise ValueError(f'"{name}" is a denied credential path.')
                return target
        raise ValueError(
            f'There is no file "{name}" in the project or the added directories '
            f"({', '.join(self._roots)})."
        )

    def _bytes(self, path: Path) -> bytes:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError(
                f"{path.name} is {path.stat().st_size // 1_000_000} MB; the limit is "
                f"{MAX_BYTES // 1_000_000} MB."
            )
        return path.read_bytes()

    def units(self, path: Path) -> tuple[str, list[Callable[[], str]], str]:
        suffix = path.suffix.lower()
        data = self._bytes(path)
        digest = hashlib.sha256(data).hexdigest()[:12]
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            return "pdf", [_page_text(page) for page in reader.pages], digest
        if suffix in {".docx", ".pptx", ".xlsx", ".xlsm"}:
            _check_package(data, path.name)
        if suffix == ".docx":
            return "docx", _docx_chunks(data), digest
        if suffix == ".pptx":
            return "pptx", _pptx_slides(data), digest
        if suffix in {".xlsx", ".xlsm"}:
            return "xlsx", _xlsx_sheets(data), digest
        if suffix in {".csv", ".tsv"}:
            return suffix[1:], _csv_chunks(data, "\t" if suffix == ".tsv" else ","), digest
        raise ValueError(
            f"{path.name} is not a document type read_document handles (pdf, docx, pptx, xlsx, "
            "csv, tsv); use read_file for text, or interpret_page for an image."
        )

    def read(self, name: str, start: int = 1, max_chars: int = DEFAULT_CHARS) -> dict[str, Any]:
        path = self._resolve(name)
        kind, units, digest = self.units(path)
        total = len(units)
        if not 1 <= start <= max(total, 1):
            raise ValueError(
                f"{path.name} has {total} {_unit(kind)}(s); start must be 1 to {total}."
            )
        limit = min(max(max_chars, 500), MAX_CHARS)
        parts: list[str] = []
        used = 0
        last = start - 1
        empty: list[int] = []
        for number in range(start, total + 1):
            text = units[number - 1]().strip()
            if not text:
                empty.append(number)
            block = (
                f"[{_unit(kind)} {number}]\n{text}"
                if text
                else f"[{_unit(kind)} {number}] (no text)"
            )
            if parts and used + len(block) > limit:
                break
            if len(block) > limit:
                block = block[:limit] + "\n[cut]"
            parts.append(block)
            used += len(block)
            last = number
        result: dict[str, Any] = {
            "path": name,
            "format": kind,
            "sha256": digest,
            "total": total,
            "unit": _unit(kind),
            "from": start,
            "to": last,
            "next": last + 1 if last < total else None,
            "text": "\n\n".join(parts),
        }
        if empty and kind == "pdf":
            result["no_text_layer"] = empty
            result["note"] = (
                "These pages have no text layer (scans or drawings); look at one with "
                "interpret_page."
            )
        return result

    def search(self, name: str, query: str, limit: int = MAX_HITS) -> dict[str, Any]:
        words = [word.lower() for word in re.findall(r"\w+", query) if len(word) > 1]
        if not words:
            raise ValueError("query needs at least one word of two or more letters.")
        path = self._resolve(name)
        kind, units, digest = self.units(path)
        hits: list[dict[str, Any]] = []
        for number, unit in enumerate(units, start=1):
            text = unit()
            lowered = text.lower()
            score = sum(lowered.count(word) for word in words)
            if score == 0:
                continue
            position = min(lowered.find(word) for word in words if word in lowered)
            start = max(0, position - SNIPPET_CHARS // 2)
            snippet = " ".join(text[start : start + SNIPPET_CHARS].split())
            hits.append({_unit(kind): number, "matches": score, "snippet": snippet})
        hits.sort(key=lambda item: -item["matches"])
        return {
            "path": name,
            "sha256": digest,
            "total": len(units),
            "hits": hits[: min(limit, MAX_HITS)],
        }

    def render(self, name: str, page: int) -> Path:
        path = self._resolve(name)
        if path.suffix.lower() in IMAGE_SUFFIXES:
            return path
        if path.suffix.lower() != ".pdf":
            raise ValueError(
                f"{path.name} is not a PDF or an image, so there is no page to look at."
            )
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(self._bytes(path))
        try:
            if not 1 <= page <= len(document):
                raise ValueError(
                    f"{path.name} has {len(document)} page(s); page must be 1 to {len(document)}."
                )
            image = document[page - 1].render(scale=2.0).to_pil()
        finally:
            document.close()
        folder = self._workspace / "document-images"
        folder.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
        target = folder / f"{path.stem}-{digest}-p{page}.png"
        image.save(target)
        return target


def _check_package(data: bytes, name: str) -> None:
    """Refuse an Office file that is a zip bomb or not a zip at all, before any parser sees it."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ValueError(f"{name} is not a valid Office package ({error})") from None
    entries = archive.infolist()
    unpacked = sum(entry.file_size for entry in entries)
    if len(entries) > MAX_ZIP_ENTRIES:
        raise ValueError(f"{name} holds {len(entries)} parts; the limit is {MAX_ZIP_ENTRIES}.")
    if unpacked > MAX_UNPACKED_BYTES:
        raise ValueError(
            f"{name} would unpack to {unpacked // (1024 * 1024)} MB; the limit is "
            f"{MAX_UNPACKED_BYTES // (1024 * 1024)} MB."
        )
    packed = max(1, sum(entry.compress_size for entry in entries))
    if unpacked // packed > MAX_ZIP_RATIO and unpacked > 10 * 1024 * 1024:
        raise ValueError(
            f"{name} unpacks to {unpacked // packed} times its size, which looks like a zip bomb "
            f"(the limit is {MAX_ZIP_RATIO} times)."
        )


def _constant(text: str) -> Callable[[], str]:
    return lambda: text


def _page_text(page: Any) -> Callable[[], str]:
    return lambda: page.extract_text() or ""


def _bound(function: Callable[[str], str], argument: str) -> Callable[[], str]:
    return lambda: function(argument)


def _unit(kind: str) -> str:
    return {"pdf": "page", "xlsx": "sheet", "pptx": "slide"}.get(kind, "part")


def _docx_chunks(data: bytes) -> list[Callable[[], str]]:
    from docx import Document

    document = Document(io.BytesIO(data))
    blocks: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            style = paragraph.style.name if paragraph.style is not None else ""
            blocks.append(f"# {text}" if style.startswith("Heading") else text)
    for number, table in enumerate(document.tables, start=1):
        rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
        blocks.append(f"[table {number}]\n" + "\n".join(rows))
    return _group(blocks)


_DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_RELATIONSHIPS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MAX_SLIDE_XML_BYTES = 8 * 1024 * 1024


def _slide_text(xml: bytes) -> str:
    root = ElementTree.fromstring(xml)
    lines: list[str] = []
    for table in root.iter(f"{_DRAWING}tbl"):
        for row in table.iter(f"{_DRAWING}tr"):
            cells = [
                "".join(node.text or "" for node in cell.iter(f"{_DRAWING}t")).strip()
                for cell in row.iter(f"{_DRAWING}tc")
            ]
            lines.append(" | ".join(cells))
    in_table = {id(node) for table in root.iter(f"{_DRAWING}tbl") for node in table.iter()}
    for paragraph in root.iter(f"{_DRAWING}p"):
        if id(paragraph) in in_table:
            continue
        text = "".join(node.text or "" for node in paragraph.iter(f"{_DRAWING}t")).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _pptx_slides(data: bytes) -> list[Callable[[], str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ValueError(f"the file is not a PowerPoint package ({error})") from None

    def number(name: str) -> int:
        match = re.search(r"(\d+)\.xml$", name)
        return int(match.group(1)) if match else 0

    names = sorted(
        (n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=number
    )
    if not names:
        raise ValueError("the file has no slides (no ppt/slides/slideN.xml)")

    def read(name: str) -> bytes:
        info = archive.getinfo(name)
        if info.file_size > MAX_SLIDE_XML_BYTES:
            raise ValueError(
                f"{name} is {info.file_size} bytes, over the {MAX_SLIDE_XML_BYTES} limit"
            )
        return archive.read(name)

    def notes_for(name: str) -> str:
        rels = posixpath.join("ppt/slides/_rels", posixpath.basename(name) + ".rels")
        if rels not in archive.namelist():
            return ""
        for rel in ElementTree.fromstring(read(rels)).iter(f"{_RELATIONSHIPS}Relationship"):
            if str(rel.get("Type", "")).endswith("/notesSlide"):
                target = posixpath.normpath(posixpath.join("ppt/slides", str(rel.get("Target"))))
                if target in archive.namelist():
                    return _slide_text(read(target)).strip()
        return ""

    def slide(name: str) -> str:
        body = _slide_text(read(name)).strip()
        notes = notes_for(name)
        return body + (f"\n[notes]\n{notes}" if notes else "")

    return [_bound(slide, name) for name in names]


def _group(blocks: list[str]) -> list[Callable[[], str]]:
    groups: list[str] = []
    current: list[str] = []
    size = 0
    for block in blocks:
        if current and size + len(block) > DEFAULT_CHARS:
            groups.append("\n\n".join(current))
            current, size = [], 0
        current.append(block)
        size += len(block)
    if current or not groups:
        groups.append("\n\n".join(current))
    return [_constant(text) for text in groups]


def _xlsx_sheets(data: bytes) -> list[Callable[[], str]]:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheets: list[Callable[[], str]] = []
    for sheet in workbook.worksheets:
        rows = []
        for index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            if index > MAX_ROWS:
                rows.append(f"[more rows not shown: the sheet is cut at {MAX_ROWS}]")
                break
            rows.append("\t".join("" if cell is None else str(cell) for cell in row))
        text = f"{sheet.title}\n" + "\n".join(rows)
        sheets.append(_constant(text))
    workbook.close()
    return sheets


def _csv_chunks(data: bytes, delimiter: str) -> list[Callable[[], str]]:
    text = data.decode("utf-8-sig", errors="replace")
    rows = ["\t".join(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    chunks = [
        "\n".join(rows[start : start + MAX_ROWS]) for start in range(0, max(len(rows), 1), MAX_ROWS)
    ]
    return [_constant(text) for text in chunks]


def document_tools(
    reader: DocumentReader,
    vision: Callable[[Path, str], Any] | None,
    fetchable: bool = False,
) -> list[FunctionTool]:
    def read_document(path: str, start: int = 1, max_chars: int = DEFAULT_CHARS) -> dict[str, Any]:
        """Read a PDF, Word, Excel or CSV file from the project, a page or sheet at a time.

        Returns the text with its page numbers, how many there are, and next, the number to pass as
        start for more. Pages with no text layer are listed so you can look at them.
        """
        return reader.read(path, start, max_chars)

    def search_document(path: str, query: str, limit: int = MAX_HITS) -> dict[str, Any]:
        """Find which pages or sheets of a document mention the words, with a snippet of each."""
        return reader.search(path, query, limit)

    tools = [tool(read_document, read_only=True), tool(search_document, read_only=True)]
    if fetchable:

        async def fetch_document(url: str) -> dict[str, Any]:
            """Download a PDF, Word, Excel, PowerPoint or CSV file from a public web address into
            the workspace and return its path for read_document, search_document and
            interpret_page. Use it for a document URL such as an arXiv PDF link."""
            return await asyncio.to_thread(reader.fetch, url)

        tools.append(tool(fetch_document, needs_approval=True))
    if vision is not None:

        async def interpret_page(path: str, question: str, page: int = 1) -> str:
            """Look at one PDF page or an image file and answer a question about what it shows.

            Use it for diagrams, charts, scanned pages and tables the text layer loses.
            """
            image = await asyncio.to_thread(reader.render, path, page)
            return str(await vision(image, question))

        tools.append(tool(interpret_page, read_only=True))
    return tools
