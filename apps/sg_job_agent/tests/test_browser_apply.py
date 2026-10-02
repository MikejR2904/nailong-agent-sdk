"""Browser applier against a local Lever-style form (skipped without Playwright)."""

from __future__ import annotations

import asyncio
import functools
import http.server
import json
import os
import threading

import pytest

pytest.importorskip("playwright.async_api")

from job_agent.apply import Applier  # noqa: E402
from job_agent.sources import JobPosting  # noqa: E402
from job_agent.store import JobLedger, JobStatus  # noqa: E402

FORM = """<!doctype html><html><body>
<form id="f">
  <input type="file" name="resume" required>
  <input name="name" required><input name="email" required><input name="phone">
  <input name="org"><input name="urls[LinkedIn]">
  <textarea name="comments"></textarea>
  <div class="application-question">
    <label for="q1">Will you now or in future require visa sponsorship? *</label>
    <input id="q1" name="q1" required>
  </div>
  <div class="application-question">
    <label for="q2">What is your notice period?</label>
    <select id="q2" name="q2">
      <option></option><option>1 month</option><option>3 months</option>
    </select>
  </div>
  %EXTRA%
  <button type="submit" id="btn-submit">Submit application</button>
</form>
<script>
document.getElementById('f').addEventListener('submit', (event) => {
  event.preventDefault();
  const data = {};
  for (const [key, value] of new FormData(event.target)) {
    data[key] = typeof value === 'string' ? value : value.name;
  }
  document.body.innerHTML = '<h2>Thank you for applying!</h2><pre id="d"></pre>';
  document.getElementById('d').textContent = JSON.stringify(data);
});
</script></body></html>"""

UNKNOWN_QUESTION = """<div class="application-question">
    <label for="q3">Favourite programming paradigm? *</label><input id="q3" name="q3" required>
  </div>"""


@pytest.fixture
def form_server(tmp_path):
    def serve(extra: str = "") -> str:
        form_dir = tmp_path / "site" / "quantco" / "abc"
        (form_dir / "apply").mkdir(parents=True, exist_ok=True)
        (form_dir / "apply" / "index.html").write_text(FORM.replace("%EXTRA%", extra))
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(tmp_path / "site")
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}/quantco/abc"

    servers: list = []
    yield serve
    for server in servers:
        server.shutdown()


def _setup(tmp_path, config, posting_url):
    root = tmp_path / "ws"
    (root / "jobs" / "j").mkdir(parents=True)
    (root / "jobs" / "j" / "resume.pdf").write_bytes(b"%PDF-1.4 test")
    (root / "jobs" / "j" / "cover_letter.txt").write_text("Dear team, hello. Alex")
    ledger = JobLedger(root / "jobs.json")
    job_id, _ = ledger.add(
        JobPosting(
            title="Quant Developer",
            company="quantco",
            url=posting_url,
            location="Singapore",
            ats="lever",
        )
    )
    ledger.update(
        job_id,
        status=JobStatus.TAILORED,
        packet={
            "resume_pdf": "jobs/j/resume.pdf",
            "resume_tex": "",
            "cover_letter": "jobs/j/cover_letter.txt",
            "warnings": [],
            "gaps": [],
        },
    )
    config.apply.headless = True
    config.apply.browser_executable = os.environ.get("SG_JOB_AGENT_TEST_BROWSER", "")
    config.candidate.phone = "+65 9123 4567"
    config.candidate.current_company = "Acme AI"
    return root, ledger, job_id


def test_auto_mode_fills_and_submits(tmp_path, config, form_server):
    root, ledger, job_id = _setup(tmp_path, config, form_server())
    logs: list[str] = []
    applier = Applier(config, root, ledger, log=logs.append)
    outcome = asyncio.run(applier.apply(job_id, mode="auto"))
    assert outcome.status is JobStatus.APPLIED, logs
    assert ledger.get(job_id)["status"] == "applied"


def test_auto_mode_submitted_values(tmp_path, config, form_server):
    """Inspect exactly what reached the form by capturing the confirmation page."""

    root, ledger, job_id = _setup(tmp_path, config, form_server())
    captured: dict = {}
    applier = Applier(config, root, ledger, log=lambda _: None)
    original = applier._confirmed

    async def capture(page):
        ok = await original(page)
        if ok:
            captured.update(json.loads(await page.inner_text("#d")))
        return ok

    applier._confirmed = capture
    asyncio.run(applier.apply(job_id, mode="auto"))
    assert captured == {
        "resume": "resume.pdf",
        "name": "Alex Tan",
        "email": "alex@example.com",
        "phone": "+65 9123 4567",
        "org": "Acme AI",
        "urls[LinkedIn]": "https://linkedin.com/in/alextan",
        "comments": "Dear team, hello. Alex",
        "q1": "No",
        "q2": "1 month",
    }


def test_unanswerable_required_question_blocks_auto_submit(tmp_path, config, form_server):
    root, ledger, job_id = _setup(tmp_path, config, form_server(UNKNOWN_QUESTION))
    applier = Applier(config, root, ledger, log=lambda _: None)
    outcome = asyncio.run(applier.apply(job_id, mode="auto"))
    assert outcome.status is JobStatus.NEEDS_MANUAL
    assert "Favourite programming paradigm" in outcome.detail
