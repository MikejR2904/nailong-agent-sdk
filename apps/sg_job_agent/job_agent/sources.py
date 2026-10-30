"""Job posting model and structured job-source clients.

Structured sources are public, unauthenticated endpoints: LinkedIn's guest job
listings, MyCareersFuture (Singapore government job portal) and the public
job-board APIs of Greenhouse, Lever, and Ashby. eFinancialCareers, JobStreet and
the rest of the web are reached through the SDK's web_search/web_fetch tools.
Every client degrades to "no results plus a warning" rather than failing a run.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) sg-job-agent/0.1 (personal job search)"


@dataclass
class JobPosting:
    title: str
    company: str
    url: str
    location: str = ""
    source: str = "web"
    ats: str = "other"
    ats_ref: dict[str, str] = field(default_factory=dict)
    description: str = ""
    apply_url: str = ""
    posted_at: str = ""
    salary: str = ""

    @property
    def id(self) -> str:
        return job_id_for(self.url)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "id": self.id}


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.parse_qs(parsed.query)
    # Keep only identity-bearing parameters; drop tracking noise.
    kept = {key: query[key] for key in ("gh_jid", "currentJobId", "jk") if key in query}
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    if host.endswith("linkedin.com") and "/jobs/view/" in path:
        # /jobs/view/some-title-at-company-1234567890 -> numeric id is the identity.
        match = re.search(r"(\d{6,})$", path)
        if match:
            path = f"/jobs/view/{match.group(1)}"
        host = "linkedin.com"
    return urllib.parse.urlunsplit(
        ("https", host, path, urllib.parse.urlencode(kept, doseq=True), "")
    )


def job_id_for(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:12]


_ATS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "greenhouse",
        re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?P<board>[\w-]+)/jobs/(?P<job>\d+)"),
    ),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/(?P<board>[\w.-]+)/(?P<job>[0-9a-f-]{36})")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/(?P<board>[\w.%-]+)/(?P<job>[0-9a-f-]{36})")),
    ("linkedin", re.compile(r"linkedin\.com/jobs/view/(?:[\w-]*?-)?(?P<job>\d{6,})")),
    (
        "mycareersfuture",
        re.compile(r"mycareersfuture\.gov\.sg/job/(?:[\w-]+/)*[\w-]*?(?P<job>[0-9a-f]{32})"),
    ),
    ("efinancialcareers", re.compile(r"efinancialcareers\.[\w.]+/.*?(?P<job>\d{6,})")),
    ("jobstreet", re.compile(r"jobstreet\.com(?:\.sg)?/.*?job/(?P<job>\d+)")),
]


def detect_ats(url: str) -> tuple[str, dict[str, str]]:
    for name, pattern in _ATS_PATTERNS:
        match = pattern.search(url)
        if match:
            return name, {key: value for key, value in match.groupdict().items() if value}
    return "other", {}


def html_to_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</div>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_linkedin_cards(page: str) -> list[JobPosting]:
    postings = []
    for card in re.split(r'(?=<div class="base-card)', page)[1:]:
        urn = re.search(r"jobPosting:(\d{6,})", card)
        title = re.search(r'(?s)base-search-card__title">(.*?)</h3>', card)
        company = re.search(r'(?s)base-search-card__subtitle">(.*?)</h4>', card)
        place = re.search(r'(?s)job-search-card__location">(.*?)</span>', card)
        posted = re.search(r'<time[^>]*datetime="([^"]+)"', card)
        if not (urn and title and company):
            continue
        postings.append(
            JobPosting(
                title=html_to_text(title.group(1)),
                company=html_to_text(company.group(1)),
                url=f"https://www.linkedin.com/jobs/view/{urn.group(1)}",
                location=html_to_text(place.group(1)) if place else "",
                source="linkedin",
                ats="linkedin",
                ats_ref={"job": urn.group(1)},
                posted_at=posted.group(1) if posted else "",
            )
        )
    return postings


def is_in_location(location: str, wanted: str) -> bool:
    haystack = location.lower()
    wanted = wanted.lower()
    if wanted == "singapore":
        return "singapore" in haystack or re.search(r"\bsg\b", haystack) is not None
    return wanted in haystack


def title_matches(title: str, keywords: list[str]) -> bool:
    lowered = title.lower()
    cleaned = (keyword.lower().strip() for keyword in keywords)
    return any(keyword and keyword in lowered for keyword in cleaned)


class JobSourceClient:
    """Async HTTP access to the structured job sources."""

    MCF_API = "https://api.mycareersfuture.gov.sg/v2"

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=httpx.Timeout(25.0),
            follow_redirects=True,
        )
        self.warnings: list[str] = []
        self._board_cache: dict[tuple[str, str], list[JobPosting]] = {}
        self._linkedin_slots = asyncio.Semaphore(2)
        self._linkedin_descriptions: dict[str, asyncio.Future[str]] = {}

    LINKEDIN_GUEST = "https://www.linkedin.com/jobs-guest/jobs/api"
    LINKEDIN_PAGE = 25

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get_json(self, url: str, **kwargs: Any) -> Any:
        response = await self._http.get(url, **kwargs)
        response.raise_for_status()
        return response.json()

    # --- LinkedIn (public guest endpoints, no login) ------------------------------

    async def _linkedin_get(self, url: str, **kwargs: Any) -> str:
        last: Exception | None = None
        for attempt in range(4):
            async with self._linkedin_slots:
                response = await self._http.get(
                    url, headers={"Accept": "text/html", "User-Agent": BROWSER_USER_AGENT}, **kwargs
                )
                await asyncio.sleep(0.4)
            if response.status_code in (429, 999) or response.status_code >= 500:
                last = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.text
        raise last or httpx.HTTPError("LinkedIn request failed")

    async def search_linkedin(self, query: str, location: str, limit: int = 20) -> list[JobPosting]:
        postings: list[JobPosting] = []
        start = 0
        try:
            while len(postings) < limit and start < 100:
                page = await self._linkedin_get(
                    f"{self.LINKEDIN_GUEST}/seeMoreJobPostings/search",
                    params={"keywords": query, "location": location, "f_JT": "F", "start": start},
                )
                cards = parse_linkedin_cards(page)
                if not cards:
                    break
                postings += cards
                start += self.LINKEDIN_PAGE
        except (httpx.HTTPError, ValueError) as error:
            self.warnings.append(f"LinkedIn search failed for {query!r}: {error}")
        return postings[:limit]

    async def fetch_linkedin_description(self, job_id: str) -> str:
        task = self._linkedin_descriptions.get(job_id)
        if task is None:
            task = asyncio.ensure_future(self._fetch_linkedin_description(job_id))
            self._linkedin_descriptions[job_id] = task
        return await task

    async def _fetch_linkedin_description(self, job_id: str) -> str:
        page = await self._linkedin_get(f"{self.LINKEDIN_GUEST}/jobPosting/{job_id}")
        match = re.search(
            r'(?is)<div class="[^"]*description__text[^"]*">(.*?)</div>\s*</section>', page
        ) or re.search(r'(?is)class="[^"]*show-more-less-html__markup[^"]*">(.*?)</div>', page)
        if not match:
            raise ValueError(f"no description block in LinkedIn posting {job_id}")
        return html_to_text(match.group(1))

    # --- MyCareersFuture -------------------------------------------------------

    async def search_mycareersfuture(self, query: str, limit: int = 20) -> list[JobPosting]:
        try:
            response = await self._http.post(
                f"{self.MCF_API}/search",
                params={"limit": limit, "page": 0},
                json={
                    "search": query,
                    "sortBy": ["new_posting_date"],
                    "employmentTypes": ["Full Time", "Permanent"],
                },
            )
            if response.status_code >= 400:
                # Older public form of the same API.
                response = await self._http.get(
                    f"{self.MCF_API}/jobs", params={"search": query, "limit": limit, "page": 0}
                )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            self.warnings.append(f"MyCareersFuture search failed for {query!r}: {error}")
            return []
        return [self._mcf_posting(item) for item in payload.get("results", [])[:limit]]

    @staticmethod
    def _mcf_posting(item: dict[str, Any]) -> JobPosting:
        metadata = item.get("metadata") or {}
        uuid = item.get("uuid", "")
        url = metadata.get("jobDetailsUrl") or f"https://www.mycareersfuture.gov.sg/job/{uuid}"
        salary = item.get("salary") or {}
        salary_text = ""
        if salary.get("minimum") or salary.get("maximum"):
            salary_text = f"SGD {salary.get('minimum', '?')}-{salary.get('maximum', '?')}/month"
        return JobPosting(
            title=item.get("title", "").strip(),
            company=((item.get("postedCompany") or {}).get("name") or "").strip(),
            url=url,
            location="Singapore",
            source="mycareersfuture",
            ats="mycareersfuture",
            ats_ref={"job": uuid},
            description=html_to_text(item.get("description", "")),
            apply_url=url,
            posted_at=str(metadata.get("newPostingDate") or metadata.get("createdAt") or ""),
            salary=salary_text,
        )

    # --- Public ATS boards -------------------------------------------------------

    async def board_postings(self, ats: str, board: str) -> list[JobPosting]:
        key = (ats, board)
        if key in self._board_cache:
            return self._board_cache[key]
        try:
            if ats == "greenhouse":
                payload = await self._get_json(
                    f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
                    params={"content": "true"},
                )
                postings = [self._greenhouse_posting(board, job) for job in payload.get("jobs", [])]
            elif ats == "lever":
                payload = await self._get_json(
                    f"https://api.lever.co/v0/postings/{board}", params={"mode": "json"}
                )
                postings = [self._lever_posting(board, job) for job in payload]
            elif ats == "ashby":
                payload = await self._get_json(
                    f"https://api.ashbyhq.com/posting-api/job-board/{board}"
                )
                postings = [self._ashby_posting(board, job) for job in payload.get("jobs", [])]
            else:
                raise ValueError(f"Unsupported ATS {ats!r}")
        except (httpx.HTTPError, ValueError) as error:
            self.warnings.append(f"{ats} board {board!r} unavailable: {error}")
            postings = []
        self._board_cache[key] = postings
        return postings

    @staticmethod
    def _greenhouse_posting(board: str, job: dict[str, Any]) -> JobPosting:
        url = (
            job.get("absolute_url") or f"https://boards.greenhouse.io/{board}/jobs/{job.get('id')}"
        )
        return JobPosting(
            title=job.get("title", "").strip(),
            company=board,
            url=url,
            location=(job.get("location") or {}).get("name", ""),
            source="greenhouse",
            ats="greenhouse",
            ats_ref={"board": board, "job": str(job.get("id", ""))},
            description=html_to_text(job.get("content", "")),
            apply_url=url,
            posted_at=job.get("updated_at", ""),
        )

    @staticmethod
    def _lever_posting(board: str, job: dict[str, Any]) -> JobPosting:
        categories = job.get("categories") or {}
        sections = [job.get("descriptionPlain", "")]
        for block in job.get("lists", []):
            sections.append(f"{block.get('text', '')}\n{html_to_text(block.get('content', ''))}")
        sections.append(job.get("additionalPlain", ""))
        return JobPosting(
            title=job.get("text", "").strip(),
            company=board,
            url=job.get("hostedUrl", ""),
            location=categories.get("location", "")
            or ", ".join(categories.get("allLocations", []) or []),
            source="lever",
            ats="lever",
            ats_ref={"board": board, "job": job.get("id", "")},
            description="\n\n".join(part for part in sections if part).strip(),
            apply_url=job.get("applyUrl", ""),
        )

    @staticmethod
    def _ashby_posting(board: str, job: dict[str, Any]) -> JobPosting:
        locations = [job.get("location", "")] + [
            (item or {}).get("location", "") for item in job.get("secondaryLocations", []) or []
        ]
        return JobPosting(
            title=job.get("title", "").strip(),
            company=board,
            url=job.get("jobUrl", ""),
            location=", ".join(location for location in locations if location),
            source="ashby",
            ats="ashby",
            ats_ref={"board": board, "job": job.get("id", "")},
            description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml", "")),
            apply_url=job.get("applyUrl", "") or job.get("jobUrl", ""),
            posted_at=job.get("publishedAt", ""),
        )

    async def search_boards(
        self,
        boards: dict[str, list[str]],
        keywords: list[str],
        location: str,
    ) -> list[JobPosting]:
        matches = []
        for ats, names in boards.items():
            for board in names:
                for posting in await self.board_postings(ats, board):
                    if is_in_location(posting.location, location) and title_matches(
                        posting.title, keywords
                    ):
                        matches.append(posting)
        return matches

    # --- Single posting details -------------------------------------------------

    async def fetch_structured_description(self, url: str) -> str | None:
        """Return the JD text via an ATS API when the URL belongs to one, else None."""

        ats, ref = detect_ats(url)
        try:
            if ats == "greenhouse":
                payload = await self._get_json(
                    f"https://boards-api.greenhouse.io/v1/boards/{ref['board']}/jobs/{ref['job']}"
                )
                return html_to_text(payload.get("content", ""))
            if ats == "lever":
                payload = await self._get_json(
                    f"https://api.lever.co/v0/postings/{ref['board']}/{ref['job']}"
                )
                return self._lever_posting(ref["board"], payload).description
            if ats == "ashby":
                for posting in await self.board_postings("ashby", ref["board"]):
                    if posting.ats_ref.get("job") == ref["job"]:
                        return posting.description
                return None
            if ats == "linkedin":
                return await self.fetch_linkedin_description(ref["job"])
            if ats == "mycareersfuture":
                payload = await self._get_json(f"{self.MCF_API}/jobs/{ref['job']}")
                return html_to_text(payload.get("description", ""))
        except (httpx.HTTPError, ValueError, KeyError) as error:
            self.warnings.append(f"Structured fetch failed for {url}: {error}")
        return None
