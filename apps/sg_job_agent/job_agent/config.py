"""Typed user configuration for the Singapore job agent.

Everything personal (contact details, screening answers) and every external
choice (model endpoint, job sources, apply mode) lives in one YAML file the user
owns. The agent never invents these values.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateConfig(_Strict):
    full_name: str
    email: str
    phone: str = ""
    location: str = "Singapore"
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    current_company: str = ""
    # Free-text facts the agent may state but never infer, e.g. "Singapore Citizen".
    work_authorization: str = ""
    notice_period: str = ""
    expected_salary_sgd: str = ""
    # Screening-question answers keyed by a case-insensitive label substring,
    # e.g. {"sponsorship": "No", "notice period": "1 month"}.
    answers: dict[str, str] = Field(default_factory=dict)


class SourcesConfig(_Strict):
    mycareersfuture: bool = True
    web_search: bool = True
    # Sites the scout targets with `site:` web searches.
    web_search_sites: list[str] = Field(
        default_factory=lambda: [
            "linkedin.com/jobs/view",
            "efinancialcareers.sg",
            "sg.jobstreet.com",
            "boards.greenhouse.io",
            "jobs.lever.co",
            "jobs.ashbyhq.com",
        ]
    )
    # Public ATS job boards to scan directly (board token / company slug).
    greenhouse_boards: list[str] = Field(default_factory=list)
    lever_companies: list[str] = Field(default_factory=list)
    ashby_boards: list[str] = Field(default_factory=list)


class SearchConfig(_Strict):
    location: str = "Singapore"
    # Leave empty to let the profiler infer roles from the resume.
    roles: list[str] = Field(default_factory=list)
    max_roles: int = Field(default=6, ge=1, le=15)
    max_jobs_per_role: int = Field(default=15, ge=1, le=100)
    max_web_calls_per_role: int = Field(default=8, ge=0, le=40)
    min_fit_score: int = Field(default=60, ge=0, le=100)
    exclude_companies: list[str] = Field(default_factory=list)
    exclude_title_keywords: list[str] = Field(
        default_factory=lambda: ["intern", "internship", "part-time", "part time", "contract"]
    )
    sources: SourcesConfig = Field(default_factory=SourcesConfig)


class ModelConfig(_Strict):
    # "anthropic" uses the native Claude adapter (official anthropic SDK). Any other
    # provider name uses the SDK's OpenAI-compatible adapter and needs base_url.
    provider: str = "anthropic"
    model: str = "claude-opus-5-5"
    base_url: str = ""
    api_key_env: str = "ANTHROPIC_API_KEY"
    # Claude: low | medium | high | xhigh | max. Ignored by OpenAI-compatible models.
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    # OpenAI-compatible models only; current Claude models reject sampling parameters.
    temperature: float | None = None
    max_tokens: int = 64_000
    timeout_seconds: float = 600.0

    def api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise RuntimeError(
                f"Set the {self.api_key_env} environment variable to your model API key."
            )
        return key


class KnowledgeConfig(_Strict):
    """Where the experience bank comes from. Every source is optional."""

    # Free-form "everything I have done" notes: .md .txt .tex .pdf .docx
    documents: list[str] = Field(default_factory=list)
    github_username: str = ""
    # owner/repo for repos outside your account (e.g. org or course repos).
    github_extra_repos: list[str] = Field(default_factory=list)
    github_include_forks: bool = False
    github_token_env: str = "GITHUB_TOKEN"
    # LinkedIn data export ZIP/folder (Settings > Data privacy > Get a copy of
    # your data) or the profile's "Save to PDF". Profiles are never scraped.
    linkedin_export: str = ""
    website_urls: list[str] = Field(default_factory=list)
    max_website_pages: int = Field(default=15, ge=1, le=100)


class TailoringConfig(_Strict):
    max_pages: int = Field(default=2, ge=1, le=5)
    # auto = first available of tectonic, latexmk, pdflatex.
    latex_engine: Literal["auto", "tectonic", "latexmk", "pdflatex", "none"] = "auto"
    polish_pass: bool = True
    write_cover_letter: bool = True


class ApplyConfig(_Strict):
    # manual: open the posting and wait for you; review: browser pre-fills the
    # form and you press Submit; auto: submits supported ATS forms itself.
    mode: Literal["manual", "review", "auto"] = "review"
    max_applications_per_run: int = Field(default=10, ge=1, le=100)
    headless: bool = False
    # Optional path to a Chrome/Chromium binary (e.g. your own Chrome) for Playwright.
    browser_executable: str = ""


class AgentConfig(_Strict):
    candidate: CandidateConfig
    search: SearchConfig = Field(default_factory=SearchConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    tailoring: TailoringConfig = Field(default_factory=TailoringConfig)
    apply: ApplyConfig = Field(default_factory=ApplyConfig)

    @classmethod
    def load(cls, path: Path) -> AgentConfig:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)
