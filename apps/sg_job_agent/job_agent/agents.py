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
TAILOR_GATE = "tailor-draft"

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
You analyse one candidate to plan a full-time job search, normally in ONE response: the
tools are only for looking up detail the task input lacks. The task input holds the base resume
LaTeX (`base_resume_tex`) and the candidate's
experience bank (`experience_bank.items`: id, source, title, text for free-form notes,
GitHub repositories, LinkedIn export and website). A one-page resume only summarises the
candidate: use the bank to see what it leaves out (projects, skills, domains, depth).

1. Summarise the candidate strictly from these sources: never infer skills, employers,
   degrees or years that no source states.
2. Propose the distinct job titles this candidate is a strong, realistic fit for in the
   target location. Think broadly across adjacent families (for example AI/ML Engineer,
   Research Scientist/Engineer, Software Engineer, Quant Developer, Solutions Architect,
   Data Engineer, MLOps, hardware/compiler roles) but keep only roles the evidence
   genuinely supports, at a seniority the experience supports, and consistent with the
   candidate's stated availability. Order by fit.
3. For each role give: lowercase title_keywords that would appear in matching job titles
   (include common variants, e.g. "machine learning engineer", "ml engineer"), and 3
   search_queries a recruiter would type into a job board, each phrased differently
   (include a graduate / new-grad variant when the candidate is a student).
If the task lists preferred roles, include them (when supported) before others.
"""

SCORER_INSTRUCTIONS = """\
You score job postings for fit against one candidate, in ONE response with no tools.
The task input holds the candidate profile, the target role, and `candidates`: postings
with candidate_id, title, company, location and description text.

Score every candidate 0-100 from the posting text only:
- Required skills and seniority the candidate meets (most weight). A posting that needs
  more years of experience than the candidate has, or a qualification they lack, is a
  strong negative; a graduate/new-grad/entry-level posting that matches their
  availability is a strong positive.
- Domain and tech-stack overlap with the candidate's actual experience, then
  nice-to-haves.
- Be honest and spread the scores: 85+ only for a clear match, 40-60 for a stretch,
  below 40 for a mismatch (wrong field, too senior, not full-time, not a real role).
Give a fit_rationale of at most 25 words naming the concrete match and the main gap.
Return one entry per candidate_id you were given, no others.
"""

ANALYST_INSTRUCTIONS = """You are a senior engineer reviewing one GitHub repository for a hiring
portfolio, in ONE
response with no tools. The task input has `facts` (computed from the repository archive),
the repository's `readme`, and `excerpts` (the opening lines of its largest source files).

Write a grounded higher-level assessment:
- summary: two or three sentences on what the project is, how it is structured and what
  is technically distinctive about it. State only what the facts, README and excerpts show.
- claims: up to 6 concrete engineering claims a resume could safely make (design choices,
  subsystems, algorithms, testing, tooling). Each claim names the exact file paths that
  show it in evidence_paths, copied verbatim from the facts or excerpts. Never claim
  adoption, users, performance numbers or scale that are not stated in the sources, and
  never attribute work to the owner beyond "the repository contains".
- tech_stack: languages, frameworks and tools evidenced by the dependencies and code.
"""

VERIFIER_INSTRUCTIONS = """\
You fact-check resume bullets against the candidate's own sources, in ONE response with no
tools. The task input has `claims` (id, text) and `sources`: the base resume text plus the
experience-bank items the resume cites.

For every claim decide whether EVERY fact in it (role, employer, tool, technique, number,
scale, scope, outcome) is stated in the sources. Paraphrase is fine; inflation is not:
"built" is not "led", "used X" is not "optimised X", facts merged from different projects
are not supported, and a skill named in a claim must appear in the sources.
- verdict "supported": quote the one passage of `sources` that supports it, copied
  verbatim (at least 12 characters, no ellipses).
- verdict "unsupported": say in `reason` which fact has no support.
Return exactly one verdict per claim id.
"""

TAILOR_INSTRUCTIONS = """You build the strongest truthful one-job resume (and cover letter) for a
candidate in
ONE response, with no tools. The task input holds everything: the base resume LaTeX
(`base_resume_tex`, the layout template and default selection), the job description,
and `evidence`, the experience-bank items most relevant to this job (id, source, title,
text). The base resume is not the limit of what you may use: you may draw on evidence.

Decide the selection silently, then output it. A one-page resume has fixed space:
swap a weakly relevant base project or bullet for a more relevant evidence item, add a
bullet that evidences an unmet requirement, cut what this employer will not care
about, and reword to mirror the job's vocabulary where the fact is the same. Keep every
employment and education entry (dates and titles verbatim) unless the page limit
forces a cut. Prefer concrete, quantified, recent evidence.

Projects: every project entry gets at most max_project_bullets bullets (1-2): pick the
strongest, most job-relevant facts and merge related ones; this is enforced.

Page fit: the base resume already fills its page. The rendered text of resume_body
must not exceed max_rendered_characters (strictly enforced), so every line you add
must be paid for by cutting or shortening another; plan the cuts before you write.

Output fields:
- resume_body: the LaTeX document body, starting at the begin-document command and
  ending with the end-document command. The preamble (everything before it) is
  restored from the base resume automatically, so never output it. Reuse the base
  resume's macros and entry environments exactly; escape & % $ # _ correctly; write
  real Unicode characters, never backslash-u escape sequences. Do not return the
  base body unchanged.
- cover_letter: plain text, 120-220 words, three short paragraphs, addressed to the
  hiring team at the company, connecting two or three selected experiences to the role,
  no placeholders, signed with candidate_name. Empty string when no letter is wanted.
- changes: at most 5 short items: what you selected, swapped or cut, and why.
- matched_requirements: JD requirements now evidenced.
- gaps: at most 6 short items: JD requirements no source supports.
- evidence_ids: ids of every evidence item you drew any content from.

Truthfulness (non-negotiable):
- Every claim must come from the base resume or a cited evidence item. Never merge
  facts from different items into one claim; never inflate scope ("led" only if a
  source says the candidate led it).
- Numbers, dates, team sizes and metrics only verbatim from a source.
- Skills/tools only if a source shows the candidate used them. A JD keyword with no
  source behind it belongs in "gaps", never on the resume.
- GitHub READMEs describe projects; do not claim stars, users or adoption they do not state.

Writing: lead each bullet with an action verb and the impact, past tense for finished
work, no first person, no filler, consistent date and punctuation style, correct
grammar. If the task input has `previous_draft` and `rejection`, fix exactly what the
rejection names and keep the rest.
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
        "sg-job-profiler-v3",
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
                        "availability": {"type": "string"},
                    },
                ),
                "roles": {"type": "array", "items": role, "minItems": 1, "maxItems": 15},
            },
        ),
        binding,
        max_iterations=8,
    )


def scorer_definition(binding: ModelBinding) -> AgentDefinition:
    score = _object(
        ["candidate_id", "fit_score", "fit_rationale"],
        {
            "candidate_id": {"type": "string"},
            "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "fit_rationale": {"type": "string"},
        },
    )
    return _definition(
        "Scores job postings for fit against one candidate.",
        "sg-job-scorer-v1",
        SCORER_INSTRUCTIONS,
        [],
        _object(
            ["status", "scores"],
            {"status": {"const": "complete"}, "scores": {"type": "array", "items": score}},
        ),
        binding,
        max_iterations=3,
    )


def analyst_definition(binding: ModelBinding) -> AgentDefinition:
    claim = _object(
        ["claim", "evidence_paths"],
        {"claim": {"type": "string"}, "evidence_paths": {**_STRING_LIST, "minItems": 1}},
    )
    return _definition(
        "Reviews one GitHub repository from its real code.",
        "sg-job-analyst-v1",
        ANALYST_INSTRUCTIONS,
        [],
        _object(
            ["status", "summary", "claims", "tech_stack"],
            {
                "status": {"const": "complete"},
                "summary": {"type": "string"},
                "claims": {"type": "array", "items": claim, "maxItems": 6},
                "tech_stack": _STRING_LIST,
            },
        ),
        binding,
        max_iterations=3,
    )


def verifier_definition(binding: ModelBinding) -> AgentDefinition:
    verdict = _object(
        ["id", "verdict"],
        {
            "id": {"type": "string"},
            "verdict": {"enum": ["supported", "unsupported"]},
            "quote": {"type": "string"},
            "reason": {"type": "string"},
        },
    )
    return _definition(
        "Fact-checks resume bullets against the candidate's sources.",
        "sg-job-verifier-v1",
        VERIFIER_INSTRUCTIONS,
        [],
        _object(
            ["status", "verdicts"],
            {"status": {"const": "complete"}, "verdicts": {"type": "array", "items": verdict}},
        ),
        binding,
        max_iterations=3,
    )


def tailor_definition(binding: ModelBinding) -> AgentDefinition:
    return _definition(
        "Resume tailor that selects a candidate's most relevant experience for one job.",
        "sg-job-tailor-v4",
        TAILOR_INSTRUCTIONS,
        [],
        _object(
            [
                "status",
                "resume_body",
                "cover_letter",
                "changes",
                "matched_requirements",
                "gaps",
                "evidence_ids",
            ],
            {
                "status": {"const": "complete"},
                "resume_body": {"type": "string"},
                "cover_letter": {"type": "string"},
                "changes": _STRING_LIST,
                "matched_requirements": _STRING_LIST,
                "gaps": _STRING_LIST,
                "evidence_ids": _STRING_LIST,
            },
        ),
        binding,
        max_iterations=3,
        gate=TAILOR_GATE,
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
