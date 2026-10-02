from __future__ import annotations

import asyncio

from conftest import BASE_RESUME
from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult

from job_agent.apply import application_url, candidate_answers, match_answer
from job_agent.latex import check_tailored_resume, latex_to_text
from job_agent.sources import JobPosting, canonical_url, detect_ats, html_to_text
from job_agent.store import JobLedger, JobStatus
from job_agent.tools import JobToolExecutor, tool_definitions


def test_latex_to_text_keeps_content_and_drops_markup():
    text = latex_to_text(BASE_RESUME)
    assert "Acme AI Pte Ltd" in text
    assert "12%" in text and "\\textbf" not in text and "usepackage" not in text


def test_unchanged_resume_passes():
    assert check_tailored_resume(BASE_RESUME, BASE_RESUME).passed


def test_fabricated_metric_is_an_error():
    tailored = BASE_RESUME.replace("12\\%", "40\\%")
    check = check_tailored_resume(BASE_RESUME, tailored)
    assert not check.passed and check.new_numbers == ["40%"]


def test_reformatted_number_is_not_flagged():
    base = BASE_RESUME.replace("a team of 3", "10,000 users and a team of 3")
    tailored = base.replace("10,000", "10000")
    assert check_tailored_resume(base, tailored).passed


def test_skill_copied_from_jd_is_flagged_as_warning():
    tailored = BASE_RESUME.replace("SQL, AWS, Docker", "SQL, AWS, Docker, Kubernetes")
    check = check_tailored_resume(
        BASE_RESUME, tailored, job_description="Kubernetes and Docker experience"
    )
    assert check.passed and check.unsupported_terms == ["kubernetes"]


def test_placeholders_and_broken_documents_fail():
    assert not check_tailored_resume(BASE_RESUME, BASE_RESUME.replace("SQL", "TODO")).passed
    assert not check_tailored_resume(BASE_RESUME, BASE_RESUME.split("\\end{document}")[0]).passed


def test_detect_ats_and_canonical_urls():
    assert detect_ats("https://job-boards.greenhouse.io/acme/jobs/4001?gh_src=li") == (
        "greenhouse",
        {"board": "acme", "job": "4001"},
    )
    assert detect_ats("https://jobs.lever.co/quantco/0a1b2c3d-1111-2222-3333-444455556666")[0] == (
        "lever"
    )
    linkedin = "https://sg.linkedin.com/jobs/view/ml-engineer-at-foo-4012345678?trk=public"
    assert detect_ats(linkedin) == ("linkedin", {"job": "4012345678"})
    assert canonical_url(linkedin) == canonical_url(
        "https://www.linkedin.com/jobs/view/4012345678/"
    )


def test_html_to_text():
    assert html_to_text("<ul><li>C++</li><li>Python &amp; SQL</li></ul>") == "- C++\n- Python & SQL"


def test_ledger_dedupes_same_job_across_sites(tmp_path):
    ledger = JobLedger(tmp_path / "jobs.json")
    first = JobPosting(title="Quant Developer", company="QuantCo", url="https://a.example/1")
    again = JobPosting(
        title="Quant  Developer", company="quantco", url="https://b.example/2", description="JD"
    )
    job_id, created = ledger.add(first, fit_score=70)
    duplicate_id, created_again = ledger.add(again, fit_score=80)
    assert created and not created_again and duplicate_id == job_id
    record = JobLedger(tmp_path / "jobs.json").get(job_id)  # persisted
    assert record["fit_score"] == 80 and record["description"] == "JD"
    assert len(record["seen_urls"]) == 2
    ledger.update(job_id, status=JobStatus.APPLIED)
    assert ledger.get(job_id)["history"][-1]["to"] == "applied"


def test_screening_answers(config):
    answers = candidate_answers(config.candidate)
    assert match_answer("LinkedIn Profile *", answers) == "https://linkedin.com/in/alextan"
    assert match_answer("Will you now or in future require visa sponsorship?", answers) == "No"
    assert match_answer("What is your notice period?", answers) == "1 month"
    assert match_answer("Favourite colour", answers) is None


def test_lever_application_url_points_at_form():
    job = {"ats": "lever", "url": "https://jobs.lever.co/q/abc", "apply_url": ""}
    assert application_url(job) == "https://jobs.lever.co/q/abc/apply"


class _FakeCore:
    def __init__(self):
        self.calls = []

    async def execute(self, name, arguments):
        self.calls.append(name)
        return ToolExecutionResult(status="succeeded", output={"results": []})


def test_web_budget_is_enforced():
    core = _FakeCore()
    executor = JobToolExecutor(core, {}, web_budget=2, min_web_interval_seconds=0)
    (search,) = tool_definitions("web_search")

    async def call():
        from nailong_agent_sdk.tools.tools import ToolInvocationContext

        context = ToolInvocationContext(
            agent_identity="t",
            task=None,  # type: ignore[arg-type]
            iteration=1,
            call=ToolCall(id="c", name="web_search", arguments={"query": "x"}),
        )
        return await executor.execute(search, context)

    statuses = [asyncio.run(call()).status for _ in range(3)]
    assert statuses == ["succeeded", "succeeded", "blocked"] and len(core.calls) == 2


DATED_RESUME = r"""\begin{document}
\begin{twocolentry}{\textbf{May -- Dec 2025}}
  \textbf{Engineer} \\ \textit{Acme}
\end{twocolentry}
\vspace{0.05cm}
\begin{twocolentry}{\textbf{Nov 2024 -- Jan 2025}}
  \textbf{Developer} \\ \textit{Beta}
\end{twocolentry}
\end{document}"""


def test_dates_inside_environment_arguments_are_checked():
    assert "May - Dec 2025" in latex_to_text(DATED_RESUME)
    moved = DATED_RESUME.replace("May -- Dec 2025", "Jan -- Dec 2024")
    check = check_tailored_resume(DATED_RESUME, moved)
    assert not check.passed and "dec 2024" in check.errors[0]


def test_layout_spacing_changes_are_not_fabrication():
    tighter = DATED_RESUME.replace("\\vspace{0.05cm}", "\\vspace{0.02cm}")
    assert check_tailored_resume(DATED_RESUME, tighter).passed


def test_overleaf_form_round_trips_latex():
    import html as html_lib
    import re
    import urllib.parse

    from job_agent.overleaf import OverleafEntry, launcher_html

    tex = BASE_RESUME + '% quotes " and <tags> & 100\\% survive\n'
    page = launcher_html([OverleafEntry(title="T", subtitle="S", tex=tex)], heading="H")
    assert 'action="https://www.overleaf.com/docs" method="post"' in page
    encoded = re.search(r'name="encoded_snip" value="([^"]*)"', page).group(1)
    assert urllib.parse.unquote(html_lib.unescape(encoded)) == tex
    assert 'name="engine" value="pdflatex"' in page


def test_title_exclusions_match_whole_words(tmp_path):
    from job_agent.tools import JobToolbox

    toolbox = JobToolbox(
        workspace=tmp_path,
        ledger=JobLedger(tmp_path / "jobs.json"),
        sources=None,  # type: ignore[arg-type]
        exclude_title_keywords=["intern", "contract"],
    )
    assert toolbox._excluded("International Payments Engineer", "Acme") is None
    assert toolbox._excluded("Internal Tools Engineer", "Acme") is None
    assert toolbox._excluded("Software Engineer Intern", "Acme")
    assert toolbox._excluded("Engineer (Contract)", "Acme")


def test_overfull_lines_parsed_from_log(tmp_path):
    from job_agent.latex import overfull_lines

    log = tmp_path / "resume.log"
    log.write_text(
        "Overfull \\hbox (27.60052pt too wide) in paragraph at lines 169--170\n"
        "Overfull \\hbox (0.3pt too wide) in paragraph at lines 12--13\n"
    )
    assert overfull_lines(log) == [(169, 27.60052)]
    assert overfull_lines(tmp_path / "missing.log") == []
