"""End-to-end pipeline runs with the SDK's ScriptedModel and mocked job APIs."""

from __future__ import annotations

import asyncio
import json

import pytest
from conftest import BASE_RESUME
from nailong_agent_sdk import ScriptedModel

from job_agent.apply import Applier
from job_agent.pipeline import JobAgentPipeline
from job_agent.store import JobStatus

PROFILE = {
    "status": "complete",
    "candidate": {
        "headline": "ML engineer, 4 years",
        "years_experience": 4,
        "core_skills": ["Python", "PyTorch", "C++", "AWS"],
    },
    "roles": [
        {
            "title": "Machine Learning Engineer",
            "seniority": "mid-senior",
            "fit_rationale": "4 years shipping PyTorch models in production.",
            "title_keywords": ["machine learning engineer", "ml engineer"],
            "search_queries": ["machine learning engineer"],
        }
    ],
}


def call(index: int, name: str, **arguments) -> dict:
    return {"type": "tool-call", "call": {"id": f"c{index}", "name": name, "arguments": arguments}}


def final(**output) -> dict:
    return {"type": "final", "output": {"status": "complete", **output}}


class Models:
    """Model factory handing out one scripted model per stage run, in order."""

    def __init__(self, **scripts: list[list[dict]]) -> None:
        self.scripts = {stage: list(runs) for stage, runs in scripts.items()}
        self.used: list[str] = []

    def __call__(self, definition):
        stage = definition.instructions.version.split("-")[2]  # sg-job-<stage>-v1
        self.used.append(stage)
        return ScriptedModel(self.scripts[stage].pop(0))


def make_pipeline(tmp_path, config, sources, resume_file, models) -> JobAgentPipeline:
    pipeline = JobAgentPipeline(
        config, tmp_path / "ws", model_factory=models, sources=sources, log=lambda _: None
    )
    pipeline.import_resume(resume_file)
    return pipeline


def tailor_script(resume_tex: str) -> list[dict]:
    return [
        call(1, "read_file", path="base_resume.tex"),
        call(2, "read_file", path="jobs/{job}/job_description.md"),
        call(3, "write_draft", path="jobs/{job}/resume.tex", content=resume_tex),
        call(
            4,
            "write_draft",
            path="jobs/{job}/cover_letter.txt",
            content="Dear Hiring Team,\n\nI build production ML systems.\n\nAlex Tan",
        ),
        call(5, "check_tailored_resume", path="jobs/{job}/resume.tex"),
        final(
            resume_path="jobs/{job}/resume.tex",
            cover_letter_path="jobs/{job}/cover_letter.txt",
            changes=["Led with PyTorch recommendation work"],
            matched_requirements=["Python", "PyTorch", "AWS"],
            gaps=["Kubernetes"],
        ),
    ]


def bind(script: list[dict], job_id: str) -> list[dict]:
    return json.loads(json.dumps(script).replace("{job}", job_id))


def test_profile_discover_tailor_and_manual_apply(tmp_path, config, sources, resume_file):
    tailored = BASE_RESUME.replace(
        "Machine learning engineer with", "Production-focused machine learning engineer with"
    )
    models = Models(
        profiler=[[call(1, "read_file", path="base_resume.tex"), final(**PROFILE)]],
        scout=[],
        tailor=[],
        polisher=[[final(resume_path="jobs/{job}/resume.tex", fixes=[])]],
    )
    pipeline = make_pipeline(tmp_path, config, sources, resume_file, models)

    from job_agent.sources import job_id_for

    mcf_id = job_id_for(
        "https://www.mycareersfuture.gov.sg/job/it/"
        "machine-learning-engineer-0123456789abcdef0123456789abcdef"
    )
    gh_id = job_id_for("https://boards.greenhouse.io/acme/jobs/4001")
    common = {
        "url": "x",
        "title": "x",
        "company": "x",
        "location": "Singapore",
        "fit_rationale": "Strong PyTorch match",
    }
    models.scripts["scout"].append(
        [
            call(
                1,
                "search_job_sources",
                query="machine learning engineer",
                title_keywords=["machine learning engineer"],
            ),
            call(2, "save_job_posting", **common, candidate_id=mcf_id, fit_score=72),
            call(3, "save_job_posting", **common, candidate_id=gh_id, fit_score=88),
            final(saved_job_ids=[mcf_id, gh_id]),
        ]
    )

    async def scenario():
        profile = await pipeline.profile()
        assert profile["roles"][0]["title"] == "Machine Learning Engineer"
        saved = await pipeline.discover()
        assert set(saved) == {mcf_id, gh_id}

        ranked = pipeline.shortlist()
        assert [job["id"] for job in ranked] == [gh_id, mcf_id]  # by fit score
        assert ranked[0]["role_category"] == "Machine Learning Engineer"
        assert ranked[0]["ats"] == "greenhouse"

        models.scripts["tailor"].append(bind(tailor_script(tailored), gh_id))
        models.scripts["polisher"][0] = bind(models.scripts["polisher"][0], gh_id)
        packet = await pipeline.tailor(gh_id)
        return packet

    packet = asyncio.run(scenario())
    job = pipeline.ledger.get(gh_id)
    assert job["status"] == JobStatus.TAILORED.value
    assert (pipeline.root / packet["resume_tex"]).read_text() == tailored
    assert packet["cover_letter"].endswith("cover_letter.txt")
    assert packet["gaps"] == ["Kubernetes"]
    assert "Kubernetes" in (pipeline.root / f"jobs/{gh_id}/job_description.md").read_text()
    assert models.used == ["profiler", "scout", "tailor", "polisher"]

    # No PDF without a LaTeX engine, so applying is a confirmed manual hand-off.
    opened = []

    async def yes(_question):
        return True

    applier = Applier(
        config, pipeline.root, pipeline.ledger, confirm=yes, open_url=opened.append, log=print
    )
    outcome = asyncio.run(applier.apply(gh_id))
    assert outcome.status is JobStatus.APPLIED
    assert opened == ["https://boards.greenhouse.io/acme/jobs/4001"]
    assert asyncio.run(applier.apply(gh_id)).detail == "already applied"


def test_fabricating_tailor_is_rejected_and_ledger_marked(tmp_path, config, sources, resume_file):
    gh_url = "https://boards.greenhouse.io/acme/jobs/4001"
    fabricated = BASE_RESUME.replace("12\\%", "45\\%")
    models = Models(tailor=[], polisher=[])
    pipeline = make_pipeline(tmp_path, config, sources, resume_file, models)
    from job_agent.sources import JobPosting

    job_id, _ = pipeline.ledger.add(
        JobPosting(
            title="Senior Machine Learning Engineer",
            company="acme",
            url=gh_url,
            location="Singapore",
            ats="greenhouse",
            ats_ref={"board": "acme", "job": "4001"},
        ),
        fit_score=88,
    )
    # Both attempts fabricate; the deterministic gate must reject both.
    models.scripts["tailor"] = [bind(tailor_script(fabricated), job_id)] * 2
    with pytest.raises(RuntimeError, match="45%"):
        asyncio.run(pipeline.tailor(job_id))
    record = pipeline.ledger.get(job_id)
    assert record["status"] == JobStatus.TAILOR_FAILED.value
    assert models.used == ["tailor", "tailor"]
    # The JD came from the Greenhouse API since the ledger had no description.
    assert "Kubernetes is a plus" in record["description"]


def test_scout_cannot_save_non_singapore_or_unknown_candidates(tmp_path, config, sources):
    from job_agent.tools import JobToolbox

    pipeline = JobAgentPipeline(
        config, tmp_path / "ws", model_factory=Models(), sources=sources, log=lambda _: None
    )
    toolbox: JobToolbox = pipeline._toolbox(pipeline._core())

    async def scenario():
        found = await toolbox.search_job_sources(
            {"query": "machine learning", "title_keywords": ["machine learning"]}
        )
        titles = {item["title"] for item in found["results"]}
        # Intern excluded by title filter; London Greenhouse role excluded by location.
        assert titles == {"Machine Learning Engineer", "Senior Machine Learning Engineer"}
        with pytest.raises(ValueError, match="not in Singapore"):
            await toolbox.save_job_posting(
                {
                    "url": "https://boards.greenhouse.io/acme/jobs/4002",
                    "title": "Machine Learning Engineer",
                    "company": "acme",
                    "location": "London, UK",
                    "fit_score": 90,
                    "fit_rationale": "x",
                }
            )
        with pytest.raises(ValueError, match="Unknown candidate_id"):
            await toolbox.save_job_posting(
                {
                    "candidate_id": "deadbeef0000",
                    "url": "u",
                    "title": "t",
                    "company": "c",
                    "location": "Singapore",
                    "fit_score": 90,
                    "fit_rationale": "x",
                }
            )

    asyncio.run(scenario())


def test_lever_board_and_missing_board_warning(sources):
    async def scenario():
        found = await sources.search_boards(
            {"lever": ["quantco"], "greenhouse": ["missing-board"]}, ["quant"], "Singapore"
        )
        return found

    found = asyncio.run(scenario())
    assert [posting.title for posting in found] == ["Quant Developer"]
    assert "C++" in found[0].description and found[0].apply_url.endswith("/apply")
    assert any("missing-board" in warning for warning in sources.warnings)


def test_real_openai_compatible_adapter_round_trip(tmp_path, config, sources, resume_file):
    """Drive the profiler through OpenAICompatibleAgentModel with a fake HTTP transport."""

    from nailong_agent_sdk import OpenAICompatibleAgentModel, OpenAICompatibleEndpoint

    sent: list[dict] = []
    replies = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": json.dumps({"path": "base_resume.tex"}),
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"type": "final", "output": PROFILE}),
                    }
                }
            ]
        },
    ]

    class FakeTransport:
        def post_json(self, url, *, headers, payload, timeout_seconds):
            sent.append(payload)
            return replies[len(sent) - 1]

    config.model.model = "accounts/fireworks/models/test-model"
    adapter = OpenAICompatibleAgentModel(
        OpenAICompatibleEndpoint(base_url="https://api.fireworks.ai/inference/v1", api_key="k"),
        provider=config.model.provider,
        model=config.model.model,
        transport=FakeTransport(),
    )
    pipeline = JobAgentPipeline(
        config, tmp_path / "ws", model_factory=lambda _d: adapter, sources=sources, log=print
    )
    pipeline.import_resume(resume_file)
    profile = asyncio.run(pipeline.profile())
    assert profile["roles"][0]["title"] == "Machine Learning Engineer"
    assert [tool["function"]["name"] for tool in sent[0]["tools"]] == ["read_file"]
    assert sent[0]["temperature"] == config.model.temperature
    # The resume content reached the model as the tool result on the second call.
    assert "Acme AI Pte Ltd" in json.dumps(sent[1]["messages"])
