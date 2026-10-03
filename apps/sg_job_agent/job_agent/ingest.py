"""Stage 0: build the experience bank from the candidate's own material.

Sources (all optional, configured under ``knowledge:``):
  documents  free-form notes: .md .txt .tex .pdf .docx ("dump everything here")
  github     every public repo of a user (README, languages, topics) + extra repos
  linkedin   LinkedIn's official data export (ZIP or folder of CSVs) or a
             "Save to PDF" profile. LinkedIn forbids scraping, so profiles are
             never fetched from linkedin.com.
  website    a bounded same-site crawl of a personal site or blog

No model is involved: ingestion is deterministic and every item keeps its origin.
"""

from __future__ import annotations

import csv
import io
import re
import urllib.parse
import zipfile
from collections.abc import Iterable
from pathlib import Path

import httpx

from .knowledge import EvidenceItem, chunk_text
from .latex import latex_to_text
from .sources import USER_AGENT, html_to_text

GITHUB_API = "https://api.github.com"
MAX_README_CHARS = 5_000

# --- resume and documents -------------------------------------------------------


def resume_items(tex: str) -> list[EvidenceItem]:
    """One item per \\section of the base resume."""

    parts = re.split(r"\\section\*?\{([^}]*)\}", tex)
    items = []
    for title, body in zip(parts[1::2], parts[2::2], strict=True):
        text = latex_to_text("\\begin{document}" + body + "\\end{document}")
        if text:
            items.append(EvidenceItem(source="resume", title=f"Resume: {title}", text=text))
    return items


def _docx_text(path: Path) -> str:
    from docx import Document

    lines = []
    for paragraph in Document(str(path)).paragraphs:
        style = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
        prefix = "## " if style.startswith("heading") or style == "title" else ""
        lines.append(prefix + paragraph.text)
    return "\n".join(lines)


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    return "\n\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def document_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".tex":
        return latex_to_text(path.read_text(encoding="utf-8"))
    if suffix == ".pdf":
        return _pdf_text(path)
    if suffix == ".docx":
        return _docx_text(path)
    raise ValueError(f"Unsupported document type: {path.name} (use .md .txt .tex .pdf .docx)")


def document_items(paths: Iterable[Path]) -> list[EvidenceItem]:
    items = []
    for path in paths:
        for title, text in chunk_text(document_text(path), title=path.stem):
            items.append(
                EvidenceItem(source="document", title=title, text=text, meta={"file": path.name})
            )
    return items


# --- LinkedIn data export -------------------------------------------------------

_LINKEDIN_FILES = {
    "Profile.csv": ("Profile", ["Headline", "Summary", "Industry", "Geo Location", "Websites"]),
    "Positions.csv": (
        "Position",
        ["Title", "Company Name", "Location", "Started On", "Finished On", "Description"],
    ),
    "Education.csv": (
        "Education",
        ["School Name", "Degree Name", "Start Date", "End Date", "Notes", "Activities"],
    ),
    "Projects.csv": ("Project", ["Title", "Started On", "Finished On", "Url", "Description"]),
    "Honors.csv": ("Honor", ["Title", "Issued On", "Description"]),
    "Certifications.csv": ("Certification", ["Name", "Authority", "Started On", "Url"]),
    "Publications.csv": ("Publication", ["Name", "Publisher", "Published On", "Description"]),
    "Volunteering.csv": (
        "Volunteering",
        ["Role", "Company Name", "Started On", "Finished On", "Description"],
    ),
}
_LIST_FILES = {"Skills.csv": ("Skills", "Name"), "Courses.csv": ("Courses", "Name")}


def _csv_rows(text: str, expected: str) -> list[dict[str, str]]:
    lines = text.lstrip("\ufeff").splitlines()
    # Some export files start with a "Notes:" preamble before the header row.
    start = next((i for i, line in enumerate(lines) if expected in line), 0)
    return list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))


def _export_files(export: Path) -> dict[str, str]:
    if export.is_dir():
        return {p.name: p.read_text(encoding="utf-8") for p in export.rglob("*.csv")}
    with zipfile.ZipFile(export) as archive:
        return {
            Path(name).name: archive.read(name).decode("utf-8", errors="replace")
            for name in archive.namelist()
            if name.lower().endswith(".csv")
        }


def linkedin_items(export: Path) -> list[EvidenceItem]:
    if export.suffix.lower() == ".pdf":
        return [
            EvidenceItem(source="linkedin", title=title, text=text, meta={"file": export.name})
            for title, text in chunk_text(_pdf_text(export), title="LinkedIn profile")
        ]
    files = _export_files(export)
    items: list[EvidenceItem] = []
    for name, (kind, columns) in _LINKEDIN_FILES.items():
        if name not in files:
            continue
        for row in _csv_rows(files[name], columns[0]):
            fields = [(column, (row.get(column) or "").strip()) for column in columns]
            text = "\n".join(f"{column}: {value}" for column, value in fields if value)
            if not text:
                continue
            label = next((value for _, value in fields if value), kind)
            items.append(
                EvidenceItem(source="linkedin", title=f"LinkedIn {kind}: {label}", text=text)
            )
    for name, (kind, column) in _LIST_FILES.items():
        if name in files:
            values = [row.get(column, "").strip() for row in _csv_rows(files[name], column)]
            values = [value for value in values if value]
            if values:
                items.append(
                    EvidenceItem(
                        source="linkedin", title=f"LinkedIn {kind}", text=", ".join(values)
                    )
                )
    return items


# --- GitHub -----------------------------------------------------------------------


def _clean_readme(markdown: str) -> str:
    text = re.sub(r"```.*?```", " ", markdown, flags=re.S)  # code blocks are not evidence
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # images and badges
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # keep link text only
    text = html_to_text(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_README_CHARS]


class GitHubIngester:
    def __init__(self, http: httpx.AsyncClient, token: str = "") -> None:
        self._http = http
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self.warnings: list[str] = []

    async def _get(self, path: str, **kwargs) -> httpx.Response:
        response = await self._http.get(f"{GITHUB_API}{path}", headers=self._headers, **kwargs)
        if response.status_code == 403 and "rate limit" in response.text.lower():
            raise RuntimeError(
                "GitHub API rate limit reached; set a token (knowledge.github_token_env)."
            )
        response.raise_for_status()
        return response

    async def user_repos(self, username: str, *, include_forks: bool) -> list[dict]:
        repos: list[dict] = []
        for page in range(1, 6):
            batch = (
                await self._get(
                    f"/users/{username}/repos",
                    params={"per_page": 100, "page": page, "type": "owner", "sort": "pushed"},
                )
            ).json()
            repos += batch
            if len(batch) < 100:
                break
        return [repo for repo in repos if include_forks or not repo.get("fork")]

    async def archive(self, full_name: str, *, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async with self._http.stream(
            "GET", f"{GITHUB_API}/repos/{full_name}/tarball", headers=self._headers
        ) as response:
            if response.status_code == 403 and "rate limit" in (await response.aread()).decode(
                errors="replace"
            ).lower():
                raise RuntimeError("GitHub API rate limit reached; set a token.")
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"{full_name} archive exceeds {max_bytes // 1_000_000} MB")
                chunks.append(chunk)
        return b"".join(chunks)

    async def repo(self, full_name: str) -> dict:
        return (await self._get(f"/repos/{full_name}")).json()

    async def repo_item(self, repo: dict) -> EvidenceItem:
        full_name = repo["full_name"]
        readme = ""
        try:
            raw = await self._http.get(
                f"{GITHUB_API}/repos/{full_name}/readme",
                headers={**self._headers, "Accept": "application/vnd.github.raw"},
            )
            if raw.status_code == 200:
                readme = _clean_readme(raw.text)
        except httpx.HTTPError as error:
            self.warnings.append(f"README for {full_name}: {error}")
        languages: list[str] = []
        try:
            languages = list((await self._get(f"/repos/{full_name}/languages")).json())
        except (httpx.HTTPError, RuntimeError) as error:
            self.warnings.append(f"languages for {full_name}: {error}")
        header = [
            f"Repository: {full_name}",
            f"Description: {repo.get('description') or ''}",
            f"Languages: {', '.join(languages) or repo.get('language') or ''}",
            f"Topics: {', '.join(repo.get('topics') or [])}",
            f"Stars: {repo.get('stargazers_count', 0)}",
            f"Created: {(repo.get('created_at') or '')[:10]}",
            f"Last pushed: {(repo.get('pushed_at') or '')[:10]}",
        ]
        if repo.get("fork"):
            header.append("Fork of another repository")
        text = "\n".join(line for line in header if not line.endswith(": ")) + "\n\n" + readme
        return EvidenceItem(
            source="github",
            title=f"GitHub: {full_name}",
            text=text.strip(),
            url=repo.get("html_url") or f"https://github.com/{full_name}",
            meta={
                "languages": languages,
                "stars": repo.get("stargazers_count", 0),
                "repo": full_name,
                "pushed_at": repo.get("pushed_at") or "",
                "fork": bool(repo.get("fork")),
            },
        )

    async def items(
        self,
        username: str,
        *,
        extra_repos: Iterable[str] = (),
        include_forks: bool = False,
        max_repos: int = 60,
    ) -> list[EvidenceItem]:
        repos: list[dict] = []
        if username:
            try:
                repos = await self.user_repos(username, include_forks=include_forks)
            except (httpx.HTTPError, RuntimeError) as error:
                # Keep going: explicitly listed repos may still be reachable.
                self.warnings.append(f"listing repos of {username}: {error}")
        seen = {repo["full_name"].lower() for repo in repos}
        for full_name in extra_repos:
            if full_name.lower() in seen:
                continue
            try:
                repos.append(await self.repo(full_name))
            except (httpx.HTTPError, RuntimeError) as error:
                self.warnings.append(f"extra repo {full_name}: {error}")
        return [await self.repo_item(repo) for repo in repos[:max_repos]]


# --- personal website / blog ---------------------------------------------------


async def website_items(
    http: httpx.AsyncClient, start_urls: Iterable[str], *, max_pages: int = 15
) -> tuple[list[EvidenceItem], list[str]]:
    """Breadth-first crawl limited to each start URL's host."""

    items: list[EvidenceItem] = []
    warnings: list[str] = []
    for start in start_urls:
        host = urllib.parse.urlsplit(start).netloc
        queue, seen, fetched = [start], {start}, 0
        while queue and fetched < max_pages:
            url = queue.pop(0)
            try:
                response = await http.get(url, headers={"User-Agent": USER_AGENT})
            except httpx.HTTPError as error:
                warnings.append(f"{url}: {error}")
                continue
            fetched += 1
            if response.status_code != 200 or "html" not in response.headers.get(
                "content-type", ""
            ):
                continue
            page = response.text
            title_match = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
            title = html_to_text(title_match.group(1)) if title_match else url
            text = html_to_text(re.sub(r"(?is)<(nav|header|footer)\b.*?</\1>", " ", page))
            if len(text) > 200:
                for chunk_title, chunk in chunk_text(text, title=title):
                    items.append(
                        EvidenceItem(source="website", title=chunk_title, text=chunk, url=url)
                    )
            for href in re.findall(r'href="([^"#]+)"', page):
                link = urllib.parse.urljoin(url, href).split("#")[0]
                parsed = urllib.parse.urlsplit(link)
                if (
                    parsed.netloc == host
                    and parsed.scheme in {"http", "https"}
                    and link not in seen
                    and not re.search(r"\.(png|jpe?g|gif|svg|pdf|zip|css|js|xml)$", parsed.path)
                ):
                    seen.add(link)
                    queue.append(link)
    return items, warnings
