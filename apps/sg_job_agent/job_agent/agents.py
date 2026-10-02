"""SDK AgentDefinitions for the four model-driven stages.

profiler  - reads the base resume, summarises the candidate, proposes target roles
scout     - one run per role: finds real Singapore openings and saves them with a fit score
tailor    - one run per job: writes a tailored resume (+ cover letter) and self-checks it
polisher  - one run per job: independent proofread/clean-up pass on the tailored files

Each definition declares only the tools that stage needs; the SDK enforces that.
"""

from __future__ import annotations

from typing import Any

from nailong_agent_sdk import (
    AgentDefinition,
    ModelBinding,
    TerminationPolicy,
    VersionedInstructions,
)

from .tools import tool_definitions

RESUME_INTEGRITY_GATE = "resume-integrity"

_STRING_LIST = {"type": "array", "items": {"type": "string"}}


def _object(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


def _definition(
    identity: str,
    version: str,
    instructions: str,
    tools: list[str],
    output_schema: dict[str, Any],
    binding: ModelBinding,
    max_iterations: int,
    gate: str | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        identity=identity,
        instructions=VersionedInstructions(version=version, text=instructions),
        input_schema={"type": "object"},
        tools=tool_definitions(*tools),
        model_binding=binding,
        output_schema=output_schema,
        termination_policy=TerminationPolicy(max_iterations=max_iterations, status_field="status"),
        verification_gate_id=gate,
    )


PROFILER_INSTRUCTIONS = """\
You analyse one candidate's LaTeX resume to plan a full-time job search.

1. Read the resume file named in the task with read_file (page through it with
   offset/limit until you have read every line).
2. Summarise the candidate strictly from the resume: never infer skills, employers,
   degrees, or years that the resume does not state.
3. Propose the distinct job titles this candidate is a strong, realistic fit for in
   the target location. Think broadly across adjacent families (for example AI/ML
   Engineer, Research Scientist/Engineer, Software Engineer, Quant Developer,
   Solutions Architect, Data Engineer, MLOps) but keep only roles the resume
   genuinely supports, at a seniority the experience supports. Order by fit.
4. For each role give: lowercase title_keywords that would appear in matching job
   titles (include common variants, e.g. "machine learning engineer", "ml engineer"),
   and 2-4 search_queries a recruiter would type into a job board.
If the task lists preferred roles, include them (when supported) before others.
"""

SCOUT_INSTRUCTIONS = """\
You find real, currently open, full-time job postings for ONE target role in the
target location, and save the good ones to the ledger.

Workflow:
1. Call search_job_sources with the role's queries and title_keywords. It covers
   MyCareersFuture and configured company boards and does not use your web budget.
2. If web search is enabled, use web_search with site-restricted queries for the
   listed sites, e.g. `site:linkedin.com/jobs/view "Quant Developer" Singapore` or
   `site:efinancialcareers.sg Quant Developer`. Make exactly ONE web_search or
   web_fetch call per turn; the backend blocks bursts. A rate-limit error is not
   "no results": try again on a later turn or move on. Stop web calls when your
   web budget is spent (a blocked result tells you).
3. For promising results whose snippet is too thin to judge, call
   get_job_description.
4. Score fit 0-100 against the candidate profile: required skills and seniority
   met (most weight), domain match, nice-to-haves. Be honest; a stretch role is 40-60.
5. save_job_posting for every posting that is (a) a real job page you saw in a tool
   result, (b) located in the target location, (c) full-time, (d) fit_score >= the
   task's min_fit_score. Prefer specific posting URLs over search/listing pages.
   Pass candidate_id for search_job_sources results.
Never invent a URL, company, or posting. Saving fewer real jobs is better than
saving any fabricated one. Stop after max_jobs saved postings.
Finish with saved_job_ids exactly as returned by save_job_posting.
"""

TAILOR_INSTRUCTIONS = """\
You tailor one candidate's LaTeX resume to one job description, then self-check it.

Read the base resume and the job description files named in the task with
read_file (read every line). Then write the tailored resume to the declared
resume path with write_draft.

Tailoring rules (truthfulness is non-negotiable):
- Use ONLY facts in the base resume. Never add employers, titles, dates, degrees,
  projects, skills, tools, certifications, or metrics that the base resume lacks.
- Keep every number (metrics, dates, team sizes, percentages) verbatim.
- You MAY: reorder sections and bullets so the most relevant evidence comes first;
  rewrite bullets to lead with impact and mirror the JD's vocabulary where the
  underlying fact is the same; tighten or drop less relevant bullets; rewrite the
  summary/objective for this role; reorder the skills list to put matching skills first.
- Keep the base resume's LaTeX preamble, packages, macros, and visual style.
  Escape special characters (& % $ # _) correctly.
- Respect the page limit in the task.

Polish while writing: consistent tense (past for previous roles), strong action
verbs, no first person, no filler, consistent date and punctuation style, ATS-
friendly plain section headings.

Then call check_tailored_resume on the resume path. Fix every error with
edit_draft or write_draft and check again until it passes. Treat warnings about
unsupported JD terms seriously: remove such a term unless the base resume shows
the same thing under another name.

If a cover letter path is declared, write a concise plain-text cover letter there
(under 300 words, addressed to the hiring team at the company, three short
paragraphs, only facts from the base resume, no placeholders, signed with the
candidate's name from the task input).

Finish with the summary fields: the most important changes, the JD requirements
your resume demonstrates, and genuine gaps (requirements the resume cannot show).
"""

POLISHER_INSTRUCTIONS = """\
You are a meticulous resume editor doing a final clean-up pass on a tailored
LaTeX resume (and cover letter, if declared). You did not write them.

Read the tailored resume, the base resume, and the job description with read_file.
Fix only real problems, with minimal edit_draft edits:
- typos, grammar, inconsistent tense, inconsistent date/punctuation formats
- awkward or duplicated phrasing, filler, buzzword stuffing
- LaTeX issues: unescaped special characters, broken macros, overfull lines
- any claim, skill, or number not supported by the base resume: remove it
Do not restructure or re-tailor; do not add content. Then call
check_tailored_resume on the resume path and make sure it passes before finishing.
"""


def profiler_definition(binding: ModelBinding) -> AgentDefinition:
    role = _object(
        ["title", "seniority", "fit_rationale", "title_keywords", "search_queries"],
        {
            "title": {"type": "string", "minLength": 2},
            "seniority": {"type": "string"},
            "fit_rationale": {"type": "string"},
            "title_keywords": {**_STRING_LIST, "minItems": 1},
            "search_queries": {**_STRING_LIST, "minItems": 1},
        },
    )
    return _definition(
        "Resume profiler that plans a candidate's full-time job search.",
        "sg-job-profiler-v1",
        PROFILER_INSTRUCTIONS,
        ["read_file"],
        _object(
            ["status", "candidate", "roles"],
            {
                "status": {"const": "complete"},
                "candidate": _object(
                    ["headline", "years_experience", "core_skills"],
                    {
                        "headline": {"type": "string"},
                        "years_experience": {"type": "number", "minimum": 0},
                        "core_skills": _STRING_LIST,
                        "domains": _STRING_LIST,
                        "education": _STRING_LIST,
                        "highlights": _STRING_LIST,
                    },
                ),
                "roles": {"type": "array", "items": role, "minItems": 1, "maxItems": 15},
            },
        ),
        binding,
        max_iterations=12,
    )


def scout_definition(binding: ModelBinding, *, web_search: bool) -> AgentDefinition:
    tools = ["search_job_sources", "get_job_description", "save_job_posting"]
    if web_search:
        tools += ["web_search", "web_fetch"]
    return _definition(
        "Job scout that finds and saves real full-time openings for one target role.",
        "sg-job-scout-v1",
        SCOUT_INSTRUCTIONS,
        tools,
        _object(
            ["status", "saved_job_ids"],
            {
                "status": {"const": "complete"},
                "saved_job_ids": _STRING_LIST,
                "queries_used": _STRING_LIST,
                "notes": {"type": "string"},
            },
        ),
        binding,
        max_iterations=40,
    )


def tailor_definition(binding: ModelBinding) -> AgentDefinition:
    return _definition(
        "Resume tailor that adapts a LaTeX resume to one job description truthfully.",
        "sg-job-tailor-v1",
        TAILOR_INSTRUCTIONS,
        ["read_file", "write_draft", "edit_draft", "check_tailored_resume"],
        _object(
            ["status", "resume_path", "changes", "matched_requirements", "gaps"],
            {
                "status": {"const": "complete"},
                "resume_path": {"type": "string"},
                "cover_letter_path": {"type": "string"},
                "changes": _STRING_LIST,
                "matched_requirements": _STRING_LIST,
                "gaps": _STRING_LIST,
            },
        ),
        binding,
        max_iterations=24,
        gate=RESUME_INTEGRITY_GATE,
    )


def polisher_definition(binding: ModelBinding) -> AgentDefinition:
    return _definition(
        "Resume editor doing a final proofreading and clean-up pass.",
        "sg-job-polisher-v1",
        POLISHER_INSTRUCTIONS,
        ["read_file", "edit_draft", "check_tailored_resume"],
        _object(
            ["status", "resume_path", "fixes"],
            {
                "status": {"const": "complete"},
                "resume_path": {"type": "string"},
                "fixes": _STRING_LIST,
            },
        ),
        binding,
        max_iterations=16,
        gate=RESUME_INTEGRITY_GATE,
    )
