"""Job-hunting tools and the SDK ToolExecutor that serves them.

``JobToolExecutor`` implements the SDK's ``ToolExecutor`` protocol. It routes
the SDK's governed core tools (read_file, write_draft, web_search, web_fetch, ...)
to ``CoreToolDispatcher`` and the job-specific tools below to ``JobToolbox``.
Web calls are serialized, spaced out, and budgeted because public search
backends block bursts of automated traffic.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nailong_agent_sdk import CoreToolDispatcher, core_tool_definitions
from nailong_agent_sdk.foundations.contracts import (
    EpisodeKind,
    ToolConcurrency,
    ToolDefinition,
    ToolExecutionResult,
)
from nailong_agent_sdk.tools.tools import ToolInvocationContext

from .latex import (
    check_tailored_resume,
    compile_latex,
    overfull_lines,
    pdf_page_count,
)
from .sources import JobPosting, JobSourceClient, detect_ats, is_in_location
from .store import JobLedger

WEB_TOOLS = frozenset({"web_search", "web_fetch"})
MAX_JD_CHARS = 15_000

Handler = Callable[[dict[str, Any]], Awaitable[Any]]
WebCall = Callable[[str, dict[str, Any]], Awaitable[ToolExecutionResult]]


def _object(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _tool(name: str, description: str, schema: dict[str, Any], *, write: bool) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema=schema,
        episode_kind=EpisodeKind.ACTION if write else EpisodeKind.EXPLORATORY,
        concurrency=ToolConcurrency.SERIAL if write else ToolConcurrency.PARALLEL_SAFE,
    )


JOB_TOOLS: dict[str, ToolDefinition] = {
    tool.name: tool
    for tool in [
        _tool(
            "search_job_sources",
            "Search structured job sources (MyCareersFuture, the Singapore government job "
            "portal, plus configured Greenhouse/Lever/Ashby company boards) for full-time "
            "openings in the target location. Returns candidates with a candidate_id; "
            "this does not use the rate-limited web_search budget.",
            _object(
                ["query", "title_keywords"],
                {
                    "query": {"type": "string", "description": "Free-text job search query."},
                    "title_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Lowercase title fragments that identify this role.",
                    },
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 40},
                },
            ),
            write=False,
        ),
        _tool(
            "get_job_description",
            "Fetch the full job description for a posting URL (uses ATS APIs when "
            "possible, otherwise a bounded web fetch). Counts toward the web budget "
            "only when it falls back to a web fetch.",
            _object(["url"], {"url": {"type": "string"}}),
            write=False,
        ),
        _tool(
            "save_job_posting",
            "Save one real, currently open, full-time posting located in the target "
            "location to the job ledger with your fit assessment. Pass candidate_id for "
            "results from search_job_sources; otherwise give the exact posting URL you "
            "saw in a tool result. Never save a URL you did not see in a tool result.",
            _object(
                ["url", "title", "company", "location", "fit_score", "fit_rationale"],
                {
                    "candidate_id": {"type": "string"},
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "location": {"type": "string"},
                    "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "fit_rationale": {"type": "string", "maxLength": 600},
                    "description": {
                        "type": "string",
                        "description": "JD text if you already fetched it.",
                    },
                },
            ),
            write=True,
        ),
        _tool(
            "check_tailored_resume",
            "Validate a tailored LaTeX resume against the base resume and job description: "
            "flags fabricated numbers, placeholders, broken LaTeX, JD skills the base "
            "resume never mentions, then compiles it and reports the page count. Call it "
            "after every edit and fix all errors before finishing.",
            _object(["path"], {"path": {"type": "string"}}),
            write=False,
        ),
    ]
}


def tool_definitions(*names: str) -> list[ToolDefinition]:
    core = {tool.name: tool for tool in core_tool_definitions()}
    return [JOB_TOOLS[name] if name in JOB_TOOLS else core[name] for name in names]


@dataclass
class TailoringContext:
    base_resume_tex: str
    job_description: str
    latex_engine: str | None
    max_pages: int


@dataclass
class JobToolbox:
    """Job-specific tool handlers bound to one agent invocation."""

    workspace: Path
    ledger: JobLedger
    sources: JobSourceClient
    # Budgeted, serialized web access; bound to JobToolExecutor.web by the pipeline.
    web_call: WebCall | None = None
    location: str = "Singapore"
    boards: dict[str, list[str]] = field(default_factory=dict)
    mycareersfuture: bool = True
    role_category: str = ""
    exclude_companies: list[str] = field(default_factory=list)
    exclude_title_keywords: list[str] = field(default_factory=list)
    tailoring: TailoringContext | None = None
    saved_job_ids: list[str] = field(default_factory=list)
    _candidates: dict[str, JobPosting] = field(default_factory=dict)

    def handlers(self) -> dict[str, Handler]:
        return {
            "search_job_sources": self.search_job_sources,
            "get_job_description": self.get_job_description,
            "save_job_posting": self.save_job_posting,
            "check_tailored_resume": self.check_tailored_resume,
        }

    def _excluded(self, title: str, company: str) -> str | None:
        lowered_title = title.lower()
        for keyword in self.exclude_title_keywords:
            # Whole words only: "intern" must not reject "International" or "Internal".
            if re.search(rf"\b{re.escape(keyword.lower())}\b", lowered_title):
                return f'title contains excluded keyword "{keyword}"'
        lowered_company = company.lower()
        for name in self.exclude_companies:
            if name.lower() and name.lower() in lowered_company:
                return f'company "{company}" is excluded'
        return None

    async def search_job_sources(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments["query"]
        keywords = arguments["title_keywords"]
        limit = int(arguments.get("max_results", 20))
        self.sources.warnings.clear()
        found: list[JobPosting] = []
        if self.mycareersfuture:
            found += await self.sources.search_mycareersfuture(query, limit=limit)
        if self.boards:
            found += await self.sources.search_boards(self.boards, keywords, self.location)
        results = []
        for posting in found:
            if not posting.title or self._excluded(posting.title, posting.company):
                continue
            self._candidates[posting.id] = posting
            existing = self.ledger.find_duplicate(posting)
            results.append(
                {
                    "candidate_id": posting.id,
                    "title": posting.title,
                    "company": posting.company,
                    "location": posting.location,
                    "url": posting.url,
                    "source": posting.source,
                    "salary": posting.salary,
                    "already_in_ledger": existing is not None,
                    "description_preview": posting.description[:700],
                }
            )
            if len(results) >= limit:
                break
        return {"results": results, "warnings": list(self.sources.warnings)}

    async def fetch_description(self, url: str) -> str:
        structured = await self.sources.fetch_structured_description(url)
        if structured:
            return structured[:MAX_JD_CHARS]
        if self.web_call is None:
            raise ValueError("No web access is configured for this agent.")
        result = await self.web_call("web_fetch", {"url": url, "max_chars": MAX_JD_CHARS})
        if result.status != "succeeded":
            raise ValueError(result.error or "web_fetch failed")
        return str(result.output.get("content", ""))

    async def get_job_description(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = arguments["url"]
        candidate = next((p for p in self._candidates.values() if p.url == url), None)
        if candidate and len(candidate.description) > 400:
            text = candidate.description[:MAX_JD_CHARS]
        else:
            text = await self.fetch_description(url)
        return {"url": url, "untrusted_content": True, "description": text}

    async def save_job_posting(self, arguments: dict[str, Any]) -> dict[str, Any]:
        candidate = self._candidates.get(arguments.get("candidate_id", ""))
        if arguments.get("candidate_id") and candidate is None:
            raise ValueError("Unknown candidate_id; use one returned by search_job_sources.")
        posting = candidate or JobPosting(
            title=arguments["title"],
            company=arguments["company"],
            url=arguments["url"],
            location=arguments["location"],
            source=detect_ats(arguments["url"])[0],
        )
        if candidate is None:
            posting.ats, posting.ats_ref = detect_ats(posting.url)
            posting.apply_url = posting.url
        if arguments.get("description") and len(arguments["description"]) > len(
            posting.description
        ):
            posting.description = arguments["description"][:MAX_JD_CHARS]
        location = posting.location or arguments["location"]
        if not is_in_location(location, self.location):
            raise ValueError(
                f'Location "{location}" is not in {self.location}; only save postings there.'
            )
        reason = self._excluded(posting.title, posting.company)
        if reason:
            raise ValueError(f"Not saved: {reason}.")
        job_id, created = self.ledger.add(
            posting,
            fit_score=int(arguments["fit_score"]),
            fit_rationale=arguments["fit_rationale"],
            role_category=self.role_category,
        )
        if job_id not in self.saved_job_ids:
            self.saved_job_ids.append(job_id)
        return {"job_id": job_id, "created": created, "duplicate_of_existing": not created}

    async def check_tailored_resume(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.tailoring is None:
            raise ValueError("check_tailored_resume is only available while tailoring.")
        path = (self.workspace / arguments["path"]).resolve()
        if not path.is_relative_to(self.workspace.resolve()):
            raise ValueError("Path escapes the workspace.")
        if not path.is_file():
            raise ValueError(f"{arguments['path']} does not exist yet; write it first.")
        return run_resume_check(path, self.tailoring).to_dict()


def run_resume_check(path: Path, tailoring: TailoringContext):
    check = check_tailored_resume(
        tailoring.base_resume_tex,
        path.read_text(encoding="utf-8"),
        job_description=tailoring.job_description,
    )
    if tailoring.latex_engine is None:
        check.warnings.append("No LaTeX engine installed; compilation was skipped.")
        return check
    ok, pdf, log_tail = compile_latex(path, engine=tailoring.latex_engine)
    check.compiled = ok
    check.compile_log_tail = "" if ok else log_tail
    if not ok:
        check.errors.append("LaTeX compilation failed; see compile_log_tail.")
        return check
    check.pdf_path = str(pdf)
    check.page_count = pdf_page_count(pdf)
    overfull = overfull_lines(path.with_suffix(".log"))
    if overfull:
        where = ", ".join(f"line {line} ({width:.0f}pt)" for line, width in overfull)
        check.errors.append(
            f"Text runs past the right margin at source {where}. Reword or shorten those lines."
        )
    if check.page_count > tailoring.max_pages:
        check.errors.append(
            f"Resume is {check.page_count} pages; the limit is {tailoring.max_pages}. "
            "Cut the least relevant content."
        )
    return check


class JobToolExecutor:
    """SDK ToolExecutor routing core and job tools, with a serialized web budget."""

    def __init__(
        self,
        core: CoreToolDispatcher,
        handlers: dict[str, Handler],
        *,
        web_budget: int | None = None,
        min_web_interval_seconds: float = 2.0,
    ) -> None:
        self._core = core
        self._handlers = handlers
        self._web_budget = web_budget
        self._web_calls = 0
        self._min_interval = min_web_interval_seconds
        self._web_lock = asyncio.Lock()
        self._last_web_call = 0.0

    @property
    def web_calls(self) -> int:
        return self._web_calls

    async def execute(
        self, tool: ToolDefinition, context: ToolInvocationContext
    ) -> ToolExecutionResult:
        arguments = context.call.arguments
        if tool.name in WEB_TOOLS:
            return await self.web(tool.name, arguments)
        handler = self._handlers.get(tool.name)
        if handler is not None:
            try:
                return ToolExecutionResult(status="succeeded", output=await handler(arguments))
            except Exception as error:  # surfaced to the model as a recoverable failure
                return ToolExecutionResult(status="failed", error=str(error))
        return await self._core.execute(tool.name, arguments)

    async def web(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        async with self._web_lock:
            if self._web_budget is not None and self._web_calls >= self._web_budget:
                return ToolExecutionResult(
                    status="blocked",
                    error=(
                        f"Web budget of {self._web_budget} calls is spent. Save what you "
                        "found and finish now."
                    ),
                )
            wait = self._min_interval - (time.monotonic() - self._last_web_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._web_calls += 1
            try:
                return await self._core.execute(name, arguments)
            finally:
                self._last_web_call = time.monotonic()
