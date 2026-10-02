from __future__ import annotations

import json

import httpx
import pytest

from job_agent.config import AgentConfig
from job_agent.sources import JobSourceClient

BASE_RESUME = r"""\documentclass[11pt]{article}
\usepackage[margin=0.7in]{geometry}
\begin{document}
\begin{center}{\Large Alex Tan} \\ alex@example.com\end{center}
\section*{Summary}
Machine learning engineer with 4 years of experience building production ML systems.
\section*{Experience}
\textbf{Acme AI Pte Ltd} \hfill Singapore, 2021--2025
\begin{itemize}
  \item Built a PyTorch recommendation model that lifted click-through rate by 12\%.
  \item Reduced inference latency by 35\% by porting hot paths to C++.
  \item Led a team of 3 engineers shipping an LLM retrieval service on AWS.
\end{itemize}
\section*{Education}
\textbf{National University of Singapore} \hfill B.Comp. Computer Science, 2021
\section*{Skills}
Python, C++, PyTorch, SQL, AWS, Docker
\end{document}
"""

GREENHOUSE_JD = (
    "<p>We are hiring a Machine Learning Engineer in Singapore.</p>"
    "<ul><li>3+ years building ML systems in Python and PyTorch</li>"
    "<li>Experience with AWS and Docker</li><li>Kubernetes is a plus</li></ul>"
    + "<p>Join a fast-growing team working on ranking and retrieval.</p>"
    * 6
)


def mock_job_api(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.startswith("https://api.mycareersfuture.gov.sg/v2/search"):
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "uuid": "0123456789abcdef0123456789abcdef",
                        "title": "Machine Learning Engineer",
                        "postedCompany": {"name": "Lion Analytics Pte Ltd"},
                        "salary": {"minimum": 7000, "maximum": 10000},
                        "description": "<p>Build ML systems with Python and PyTorch.</p>" * 20,
                        "metadata": {
                            "jobDetailsUrl": "https://www.mycareersfuture.gov.sg/job/it/"
                            "machine-learning-engineer-0123456789abcdef0123456789abcdef",
                            "newPostingDate": "2026-09-30",
                        },
                    },
                    {
                        "uuid": "fedcba9876543210fedcba9876543210",
                        "title": "Machine Learning Intern",
                        "postedCompany": {"name": "Tiny Startup"},
                        "metadata": {},
                    },
                ]
            },
        )
    if url.startswith("https://boards-api.greenhouse.io/v1/boards/acme/jobs/4001"):
        return httpx.Response(200, json={"id": 4001, "content": GREENHOUSE_JD})
    if url.startswith("https://boards-api.greenhouse.io/v1/boards/acme/jobs"):
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 4001,
                        "title": "Senior Machine Learning Engineer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/4001",
                        "location": {"name": "Singapore"},
                        "content": GREENHOUSE_JD,
                    },
                    {
                        "id": 4002,
                        "title": "Machine Learning Engineer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/4002",
                        "location": {"name": "London, UK"},
                        "content": "<p>London role</p>",
                    },
                ]
            },
        )
    if url.startswith("https://api.lever.co/v0/postings/quantco"):
        return httpx.Response(
            200,
            json=[
                {
                    "id": "0a1b2c3d-1111-2222-3333-444455556666",
                    "text": "Quant Developer",
                    "hostedUrl": "https://jobs.lever.co/quantco/0a1b2c3d-1111-2222-3333-444455556666",
                    "applyUrl": "https://jobs.lever.co/quantco/0a1b2c3d-1111-2222-3333-444455556666/apply",
                    "categories": {"location": "Singapore", "commitment": "Full-time"},
                    "descriptionPlain": "Low-latency trading systems in C++.",
                    "lists": [{"text": "Requirements", "content": "<li>C++</li><li>Python</li>"}],
                }
            ],
        )
    if "missing-board" in url:
        return httpx.Response(404, json={"error": "not found"})
    return httpx.Response(404)


@pytest.fixture
def sources() -> JobSourceClient:
    return JobSourceClient(httpx.AsyncClient(transport=httpx.MockTransport(mock_job_api)))


@pytest.fixture
def config() -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "candidate": {
                "full_name": "Alex Tan",
                "email": "alex@example.com",
                "linkedin_url": "https://linkedin.com/in/alextan",
                "notice_period": "1 month",
                "answers": {"sponsorship": "No"},
            },
            "search": {
                "max_roles": 2,
                "sources": {"web_search": False, "greenhouse_boards": ["acme"]},
            },
            "tailoring": {"latex_engine": "none"},
        }
    )


@pytest.fixture
def resume_file(tmp_path):
    path = tmp_path / "resume.tex"
    path.write_text(BASE_RESUME, encoding="utf-8")
    return path


def dumps(value) -> str:
    return json.dumps(value)
