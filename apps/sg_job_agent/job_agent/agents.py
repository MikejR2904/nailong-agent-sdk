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
You analyse one candidate to plan a full-time job search.

1. Read the base resume named in the task with read_file (page through it with
   offset/limit until you have read every line).
2. The task input lists the candidate's experience bank (free-form notes, GitHub
   repositories, LinkedIn export, personal website). Use search_experience and
   read_experience to learn what the resume leaves out: projects, skills,
   domains, and how deep each one goes. Read at least the items that look most
   substantial; a one-page resume is only a summary of the candidate.
3. Summarise the candidate strictly from these sources: never infer skills,
   employers, degrees, or years that no source states.
4. Propose the distinct job titles this candidate is a strong, realistic fit for in
   the target location. Think broadly across adjacent families (for example AI/ML
   Engineer, Research Scientist/Engineer, Software Engineer, Quant Developer,
   Solutions Architect, Data Engineer, MLOps) but keep only roles the evidence
   genuinely supports, at a seniority the experience supports, and consistent with
   the candidate's stated availability. Order by fit.
5. For each role give: lowercase title_keywords that would appear in matching job
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
You build the strongest truthful one-job resume for a candidate by SELECTING the
most relevant experience from everything they have done, then writing it well.
The base resume is the layout template and a default selection, not the limit
of what you may use.

1. Read the job description and the base resume (read_file, every line).
2. List the JD's concrete requirements. For each, search_experience (try several
   phrasings) and read_experience the promising items. The experience bank holds
   the candidate's own notes, GitHub repositories, LinkedIn export and website.
3. Decide the selection. A one-page resume has fixed space, so compete for it:
   swap a weakly relevant base-resume project or bullet for a more relevant one
   from the bank, add a bullet that evidences an unmet requirement, and cut
   what this employer will not care about. Prefer concrete, quantified,
   recent evidence. Keep every employment and education entry that is on the
   base resume (dates and titles verbatim) unless the page limit forces a cut.
4. Write the tailored resume to the declared resume path with write_draft.
   Reuse the base resume's preamble, macros and entry environments exactly so
   new entries look identical to existing ones; escape & % $ # _ correctly.
5. Call check_tailored_resume with the resume path and evidence_ids: every bank
   item you drew any content from. Fix every error with edit_draft and check
   again until it passes and the page limit holds.

Truthfulness (non-negotiable):
- Every claim must come from the base resume or a cited bank item. Never merge
  facts from different items into one claim; never inflate scope ("led" only if
  a source says the candidate led it).
- Numbers, dates, team sizes and metrics only verbatim from a source.
- Skills/tools only if a source shows the candidate used them. A JD keyword
  with no source behind it belongs in "gaps", never on the resume.
- GitHub READMEs describe projects; do not claim stars, users or adoption the
  README does not state.

Writing: lead each bullet with an action verb and the impact, mirror the JD's
vocabulary where the underlying fact is the same, past tense for finished work,
no first person, no filler, consistent date and punctuation style.

If a cover letter path is declared, write a plain-text cover letter there (under
300 words, three short paragraphs, addressed to the hiring team at the company,
only sourced facts, no placeholders, signed with the candidate's name from the
task input) that connects two or three selected experiences to the role.

Finish with: changes (what you selected, swapped, cut, and why), the JD
requirements now evidenced, gaps (requirements no source supports), and
evidence_ids (the same list you passed to the final passing check).
"""

POLISHER_INSTRUCTIONS = """\
You are a meticulous resume editor doing a final clean-up pass on a tailored
LaTeX resume (and cover letter, if declared). You did not write them.

Read the tailored resume, the job description, and the evidence items whose ids
are in the task input (read_experience). Fix only real problems, with minimal
edit_draft edits:
- typos, grammar, inconsistent tense, inconsistent date/punctuation formats
- awkward or duplicated phrasing, filler, buzzword stuffing, a bullet that wraps
  onto a near-empty last line (tighten it)
- LaTeX issues: unescaped special characters, broken macros, text past the margin
- any claim, skill, or number that neither the base resume nor a cited item
  supports: remove it
Do not re-select content. Then call check_tailored_resume with the resume path and
the same evidence_ids, and make sure it passes before finishing.
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
        "sg-job-profiler-v2",
        PROFILER_INSTRUCTIONS,
        ["read_file", "search_experience", "read_experience"],
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
        max_iterations=30,
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
        "Resume tailor that selects a candidate's most relevant experience for one job.",
        "sg-job-tailor-v2",
        TAILOR_INSTRUCTIONS,
        [
            "read_file",
            "search_experience",
            "read_experience",
            "write_draft",
            "edit_draft",
            "check_tailored_resume",
        ],
        _object(
            ["status", "resume_path", "changes", "matched_requirements", "gaps", "evidence_ids"],
            {
                "status": {"const": "complete"},
                "resume_path": {"type": "string"},
                "cover_letter_path": {"type": "string"},
                "changes": _STRING_LIST,
                "matched_requirements": _STRING_LIST,
                "gaps": _STRING_LIST,
                "evidence_ids": _STRING_LIST,
            },
        ),
        binding,
        max_iterations=40,
        gate=RESUME_INTEGRITY_GATE,
    )


def polisher_definition(binding: ModelBinding) -> AgentDefinition:
    return _definition(
        "Resume editor doing a final proofreading and clean-up pass.",
        "sg-job-polisher-v2",
        POLISHER_INSTRUCTIONS,
        ["read_file", "read_experience", "edit_draft", "check_tailored_resume"],
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
