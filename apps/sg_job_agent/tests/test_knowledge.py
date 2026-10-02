"""Experience bank: ingestion from every source, retrieval, and evidence-cited tailoring."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile

import httpx
from conftest import BASE_RESUME
from nailong_agent_sdk import ScriptedModel

from job_agent.ingest import linkedin_items
from job_agent.knowledge import EvidenceItem, KnowledgeBase, chunk_text
from job_agent.pipeline import JobAgentPipeline
from job_agent.sources import JobPosting
from job_agent.store import JobStatus

NOTES = """# Everything I have done

## Ray tracing renderer (2022)
Wrote a CUDA path tracer for a graphics course; 3.2x faster than the CPU baseline.

## Robotics club
Captain of the NUS robotics team; we placed 2nd in a national competition in Mar 2024.
"""

README = """# fastkv
![build](https://img.shields.io/badge/build-passing-green)
A log-structured key-value store in Rust with a lock-free memtable.
Benchmarks: 1.8M ops/s on 8 cores.
```bash
cargo run --release
```
See [docs](https://example.com/docs).
"""


def github_api(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/users/alextan/repos":
        return httpx.Response(
            200,
            json=[
                {
                    "full_name": "alextan/fastkv",
                    "description": "LSM key-value store",
                    "language": "Rust",
                    "topics": ["database", "storage"],
                    "stargazers_count": 12,
                    "fork": False,
                    "html_url": "https://github.com/alextan/fastkv",
                    "created_at": "2025-01-02T00:00:00Z",
                    "pushed_at": "2026-05-01T00:00:00Z",
                },
                {"full_name": "alextan/forked-lib", "fork": True},
            ],
        )
    if path == "/repos/alextan/fastkv/readme":
        assert request.headers["accept"] == "application/vnd.github.raw"
        return httpx.Response(200, text=README)
    if path == "/repos/alextan/fastkv/languages":
        return httpx.Response(200, json={"Rust": 9000, "Shell": 100})
    if path == "/repos/course-org/pairs":
        return httpx.Response(
            200,
            json={
                "full_name": "course-org/pairs",
                "description": "Pairs trading backtester",
                "fork": False,
                "html_url": "https://github.com/course-org/pairs",
            },
        )
    if path.startswith("/repos/course-org/pairs/"):
        return httpx.Response(404)
    if request.url.host == "alex.example":
        pages = {
            "/": "<html><title>Alex Tan</title><body><nav>menu</nav><p>Hi! I write about "
            "systems.</p>"
            + "<p>Filler text about my interests.</p>" * 8
            + '<a href="/posts/kernels">kernels</a><a href="https://other.site/x">x</a></body>',
            "/posts/kernels": "<html><title>Fusing attention kernels</title><body><p>"
            + "I fused softmax and matmul in Triton, cutting latency by 41%. " * 5
            + "</p></body></html>",
        }
        body = pages.get(path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})
    return httpx.Response(404)


def linkedin_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "Positions.csv",
            "Company Name,Title,Description,Location,Started On,Finished On\n"
            'Acme AI Pte Ltd,ML Engineer,"Built ranking models; shipped A/B tested '
            'recommender",Singapore,Jan 2021,Dec 2025\n',
        )
        archive.writestr("Skills.csv", "Name\nPyTorch\nRust\nKubernetes\n")
        archive.writestr(
            "Profile.csv",
            "First Name,Last Name,Headline,Summary\nAlex,Tan,ML engineer,Systems + ML\n",
        )
    return buffer.getvalue()


def make_pipeline(tmp_path, config, sources, resume_file, model_factory=None):
    notes = tmp_path / "notes.md"
    notes.write_text(NOTES)
    export = tmp_path / "linkedin.zip"
    export.write_bytes(linkedin_zip())
    config.knowledge.documents = [str(notes)]
    config.knowledge.github_username = "alextan"
    config.knowledge.github_extra_repos = ["course-org/pairs"]
    config.knowledge.linkedin_export = str(export)
    config.knowledge.website_urls = ["https://alex.example/"]
    pipeline = JobAgentPipeline(
        config,
        tmp_path / "ws",
        model_factory=model_factory or (lambda _d: None),
        sources=sources,
        log=lambda _: None,
    )
    pipeline.import_resume(resume_file)
    return pipeline


def ingest(pipeline):
    http = httpx.AsyncClient(transport=httpx.MockTransport(github_api))
    return asyncio.run(pipeline.ingest(http=http))


def test_ingest_builds_bank_from_every_source(tmp_path, config, sources, resume_file):
    pipeline = make_pipeline(tmp_path, config, sources, resume_file)
    counts = ingest(pipeline)
    assert counts == {"resume": 4, "document": 2, "github": 2, "linkedin": 3, "website": 2}

    kb = KnowledgeBase(pipeline.root / "knowledge")  # persisted and reloadable
    fastkv = next(i for i in kb.items("github") if "fastkv" in i.title)
    assert "Languages: Rust, Shell" in fastkv.text and "1.8M ops/s" in fastkv.text
    assert "img.shields.io" not in fastkv.text and "cargo run" not in fastkv.text
    assert not any("forked-lib" in i.title for i in kb.items("github"))
    assert any("course-org/pairs" in i.title for i in kb.items("github"))
    assert {i.url for i in kb.items("website")} == {
        "https://alex.example/",
        "https://alex.example/posts/kernels",
    }
    assert "menu" not in " ".join(i.text for i in kb.items("website"))

    top, _ = kb.search("lock-free key value store in Rust")[0]
    assert top.id == fastkv.id
    attention = kb.search("attention kernel latency", sources=["website"])[0][0]
    assert "41%" in attention.text

    # Re-ingesting replaces a source instead of duplicating it.
    assert ingest(pipeline) == counts


def test_chunking_and_linkedin_preamble():
    chunks = chunk_text("intro\n\n## A\nalpha\n\n## B\nbeta", title="notes")
    assert chunks == [("notes", "intro"), ("A", "alpha"), ("B", "beta")]
    assert EvidenceItem("github", "t", "x").id == EvidenceItem("github", "t", "x").id


def test_linkedin_folder_with_notes_preamble(tmp_path):
    (tmp_path / "Positions.csv").write_text(
        "Notes:\nSome export notes\n\nCompany Name,Title,Started On\nAcme,Engineer,Jan 2020\n"
    )
    (item,) = linkedin_items(tmp_path)
    assert item.title == "LinkedIn Position: Engineer" and "Company Name: Acme" in item.text


def call(index, name, **arguments):
    return {"type": "tool-call", "call": {"id": f"c{index}", "name": name, "arguments": arguments}}


def final(**output):
    return {"type": "final", "output": {"status": "complete", **output}}


NEW_PROJECT = r"""\section*{Projects}
\textbf{fastkv: LSM Key-Value Store} \hfill github.com/alextan/fastkv
\begin{itemize}
  \item Built a log-structured key-value store in Rust with a lock-free memtable,
  reaching 1.8M ops/s on 8 cores.
\end{itemize}
\section*{Skills}"""


def selected_resume() -> str:
    return BASE_RESUME.replace(r"\section*{Skills}", NEW_PROJECT)


def tailor_run(job_id: str, evidence: list[str]) -> list[dict]:
    resume = f"jobs/{job_id}/resume.tex"
    return [
        call(1, "read_file", path=f"jobs/{job_id}/job_description.md"),
        call(2, "search_experience", query="Rust storage engine key-value"),
        call(3, "read_experience", id=evidence[0] if evidence else "gi-missing"),
        call(4, "write_draft", path=resume, content=selected_resume()),
        call(5, "check_tailored_resume", path=resume, evidence_ids=evidence),
        final(
            resume_path=resume,
            changes=["Added fastkv from GitHub for the storage-engine requirement"],
            matched_requirements=["Rust", "storage engines"],
            gaps=[],
            evidence_ids=evidence,
        ),
    ]


def test_tailor_adds_project_from_bank_only_when_cited(tmp_path, config, sources, resume_file):
    runs: list[list[dict]] = []
    seen_calls: list[str] = []

    def factory(definition):
        model = ScriptedModel(runs.pop(0))
        original = model.next_turn

        async def recording(context):
            if context.observations:
                seen_calls.append(context.observations[-1].message[:200])
            return await original(context)

        model.next_turn = recording
        return model

    config.tailoring.polish_pass = False
    pipeline = make_pipeline(tmp_path, config, sources, resume_file, factory)
    ingest(pipeline)
    fastkv = next(i for i in pipeline.knowledge.items("github") if "fastkv" in i.title)
    job_id, _ = pipeline.ledger.add(
        JobPosting(
            title="Storage Engineer",
            company="DataCo",
            url="https://example.com/jobs/1",
            location="Singapore",
            description="We build storage engines in Rust. " * 20,
        ),
        fit_score=80,
    )

    # Uncited: "1.8M" and "8" are in no allowed source, so the gate rejects both attempts.
    runs[:] = [tailor_run(job_id, []), tailor_run(job_id, [])]
    try:
        asyncio.run(pipeline.tailor(job_id))
        raise AssertionError("uncited tailoring should fail")
    except RuntimeError as error:
        assert "1.8m" in str(error).lower()
    assert pipeline.ledger.get(job_id)["status"] == JobStatus.TAILOR_FAILED.value

    # Cited: the same resume passes because the evidence contains the figures.
    runs[:] = [tailor_run(job_id, [fastkv.id])]
    packet = asyncio.run(pipeline.tailor(job_id))
    assert packet["evidence_ids"] == [fastkv.id]
    assert "fastkv" in (pipeline.root / packet["resume_tex"]).read_text()
    assert pipeline.ledger.get(job_id)["status"] == JobStatus.TAILORED.value


def test_unknown_evidence_id_is_an_error(tmp_path, config, sources, resume_file):
    pipeline = make_pipeline(tmp_path, config, sources, resume_file)
    ingest(pipeline)
    from job_agent.tools import run_resume_check

    path = pipeline.root / "resume.tex"
    path.write_text(BASE_RESUME)
    check = run_resume_check(path, pipeline.tailoring_context("JD"), ["gi-deadbeef"])
    assert not check.passed and "gi-deadbeef" in check.errors[-1]
    assert json.dumps(check.to_dict())


def test_github_listing_failure_keeps_extra_repos():
    from job_agent.ingest import GitHubIngester

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/users/"):
            return httpx.Response(403, text="forbidden")
        return github_api(request)

    ingester = GitHubIngester(httpx.AsyncClient(transport=httpx.MockTransport(api)))
    items = asyncio.run(ingester.items("alextan", extra_repos=["course-org/pairs"]))
    assert [item.title for item in items] == ["GitHub: course-org/pairs"]
    assert "listing repos of alextan" in ingester.warnings[0]
