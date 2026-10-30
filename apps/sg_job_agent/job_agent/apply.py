"""Submit applications: browser-assisted for Greenhouse/Lever, hand-off elsewhere.

Modes (config.apply.mode, overridable per run):
  manual  open the posting, show the packet, ask whether you submitted
  review  a real browser opens with the form pre-filled; you review and click Submit
  auto    the form is filled and submitted for you, but only when no required field is
          left unanswered and no CAPTCHA is present; otherwise it falls back to review

LinkedIn, MyCareersFuture (Singpass), eFinancialCareers and JobStreet need your own
login and forbid automated submission in their terms, so they are always hand-off.
Nothing is ever recorded as applied unless a submission was confirmed.
"""

from __future__ import annotations

import asyncio
import webbrowser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AgentConfig, CandidateConfig
from .store import JobLedger, JobStatus

BROWSER_ATS = frozenset({"greenhouse", "lever"})
CONFIRMATION_TEXT = (
    "thank you for applying",
    "application has been submitted",
    "application was submitted",
    "we've received your application",
    "we have received your application",
    "application received",
)
_SCAN_FIELDS_JS = """
() => {
  const out = [];
  const selector = 'input:not([type=hidden]):not([type=file]):not([type=submit])'
    + ':not([type=button]):not([type=checkbox]):not([type=radio]), textarea, select';
  document.querySelectorAll(selector).forEach((el, i) => {
    if (el.offsetParent === null) return;
    el.setAttribute('data-sgja', String(i));
    let label = '';
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) label = l.innerText;
    }
    if (!label) { const l = el.closest('label'); if (l) label = l.innerText; }
    if (!label) label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
    if (!label) {
      const box = el.closest('.application-question, .field, li');
      if (box) label = box.innerText.slice(0, 200);
    }
    label = label.trim();
    out.push({
      idx: String(i), tag: el.tagName.toLowerCase(), label: label, value: el.value || '',
      required: el.required || el.getAttribute('aria-required') === 'true' || /\\*$/.test(label),
    });
  });
  return out;
}
"""

Confirm = Callable[[str], Awaitable[bool]]
Pause = Callable[[str], Awaitable[None]]


async def ask_yes_no(question: str) -> bool:
    answer = await asyncio.to_thread(input, f"{question} [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


async def wait_for_enter(message: str) -> None:
    await asyncio.to_thread(input, f"{message} ")


def candidate_answers(candidate: CandidateConfig) -> dict[str, str]:
    """Label-substring -> answer. Explicit user answers win over derived ones."""

    derived = {
        "linkedin": candidate.linkedin_url,
        "github": candidate.github_url,
        "portfolio": candidate.portfolio_url,
        "website": candidate.portfolio_url,
        "notice period": candidate.notice_period,
        "expected salary": candidate.expected_salary_sgd,
        "salary expectation": candidate.expected_salary_sgd,
        "current company": candidate.current_company,
        "current employer": candidate.current_company,
        "location": candidate.location,
        "city": candidate.location,
    }
    answers = {key: value for key, value in derived.items() if value}
    answers.update({key.lower(): value for key, value in candidate.answers.items() if value})
    return answers


def match_answer(label: str, answers: dict[str, str]) -> str | None:
    """Answer for the most specific (longest) key contained in the label."""

    lowered = " ".join(label.lower().split())
    hits = [key for key in answers if key and key in lowered]
    return answers[max(hits, key=len)] if hits else None


def application_url(job: dict[str, Any]) -> str:
    url = job.get("apply_url") or job["url"]
    if job.get("ats") == "lever" and not url.rstrip("/").endswith("/apply"):
        return f"{url.rstrip('/')}/apply"
    return url


@dataclass
class Outcome:
    status: JobStatus
    detail: str


class Applier:
    def __init__(
        self,
        config: AgentConfig,
        workspace: Path,
        ledger: JobLedger,
        *,
        confirm: Confirm = ask_yes_no,
        pause: Pause = wait_for_enter,
        open_url: Callable[[str], Any] = webbrowser.open,
        log: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self.root = workspace.resolve()
        self.ledger = ledger
        self.confirm = confirm
        self.pause = pause
        self.open_url = open_url
        self.log = log

    def _file(self, relative: str) -> Path | None:
        if not relative:
            return None
        path = self.root / relative
        return path if path.is_file() else None

    async def apply(self, job_id: str, mode: str | None = None) -> Outcome:
        job = self.ledger.get(job_id)
        if job.get("status") == JobStatus.APPLIED.value:
            return Outcome(JobStatus.APPLIED, "already applied")
        packet = job.get("packet") or {}
        if not packet:
            raise ValueError(f"Job {job_id} has no tailored packet; run `tailor` first.")
        mode = mode or self.config.apply.mode
        self.log(f"\n=== {job['title']} @ {job['company']} ({job.get('ats')}) ===")
        for warning in packet.get("warnings", []):
            self.log(f"  warning: {warning}")
        if packet.get("gaps"):
            self.log(f"  gaps: {'; '.join(packet['gaps'])}")

        resume_pdf = self._file(packet.get("resume_pdf", ""))
        if mode != "manual" and job.get("ats") in BROWSER_ATS and resume_pdf is not None:
            try:
                outcome = await self._browser_apply(job, packet, resume_pdf, mode)
            except ImportError:
                self.log("  Playwright is not installed; falling back to manual hand-off.")
                outcome = await self._manual(job, packet)
        else:
            if mode != "manual" and resume_pdf is None:
                self.log("  No compiled PDF (install tectonic or TeX Live); hand-off instead.")
            outcome = await self._manual(job, packet)
        self.ledger.update(job_id, status=outcome.status, apply_detail=outcome.detail)
        self.log(f"  -> {outcome.status.value}: {outcome.detail}")
        return outcome

    async def _manual(self, job: dict[str, Any], packet: dict[str, Any]) -> Outcome:
        url = application_url(job)
        self.log(f"  Apply here: {url}")
        for key in ("resume_pdf", "resume_tex", "cover_letter"):
            if packet.get(key):
                self.log(f"  {key}: {self.root / packet[key]}")
        self.open_url(url)
        if await self.confirm(f"Did you submit the application to {job['company']}?"):
            return Outcome(JobStatus.APPLIED, "submitted manually (confirmed by you)")
        return Outcome(JobStatus.NEEDS_MANUAL, "not submitted yet")

    async def _browser_apply(
        self, job: dict[str, Any], packet: dict[str, Any], resume_pdf: Path, mode: str
    ) -> Outcome:
        from playwright.async_api import async_playwright  # optional dependency

        candidate = self.config.candidate
        cover_path = self._file(packet.get("cover_letter", ""))
        cover_text = cover_path.read_text(encoding="utf-8") if cover_path else ""
        headless = self.config.apply.headless and mode == "auto"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=headless,
                executable_path=self.config.apply.browser_executable or None,
            )
            try:
                page = await browser.new_page()
                await page.goto(application_url(job), wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)
                if job["ats"] == "greenhouse":
                    await self._fill_greenhouse(page, candidate, resume_pdf, cover_path)
                else:
                    await self._fill_lever(page, candidate, resume_pdf, cover_text)
                unanswered = await self._answer_questions(page, candidate_answers(candidate))
                captcha = await page.locator(
                    "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .h-captcha, .g-recaptcha"
                ).count()
                blockers = [f"unanswered required field: {label}" for label in unanswered]
                if captcha:
                    blockers.append("CAPTCHA present")
                if mode == "auto" and not blockers:
                    return await self._submit(page, job)
                for blocker in blockers:
                    self.log(f"  needs you: {blocker}")
                if headless:
                    return Outcome(JobStatus.NEEDS_MANUAL, "; ".join(blockers))
                await self.pause(
                    "  Review the pre-filled form in the browser, complete anything missing, "
                    "click Submit, then press Enter here."
                )
                if await self._confirmed(page) or await self.confirm(
                    f"Did the application to {job['company']} go through?"
                ):
                    return Outcome(JobStatus.APPLIED, "submitted in browser after your review")
                return Outcome(JobStatus.NEEDS_MANUAL, "; ".join(blockers) or "not submitted")
            finally:
                await browser.close()

    @staticmethod
    async def _fill(page: Any, selector: str, value: str) -> bool:
        if not value:
            return False
        locator = page.locator(selector).first
        if await locator.count() and await locator.is_visible():
            await locator.fill(value)
            return True
        return False

    @staticmethod
    async def _upload(page: Any, selector: str, path: Path | None) -> bool:
        if path is None:
            return False
        locator = page.locator(selector).first
        if await locator.count():
            await locator.set_input_files(str(path))
            return True
        return False

    async def _fill_greenhouse(
        self, page: Any, candidate: CandidateConfig, resume: Path, cover: Path | None
    ) -> None:
        first, _, last = candidate.full_name.partition(" ")
        await self._fill(page, "#first_name", first)
        await self._fill(page, "#last_name", last or first)
        await self._fill(page, "#email", candidate.email)
        await self._fill(page, "#phone", candidate.phone)
        await self._upload(
            page,
            "input[type=file][id*='resume' i], input[type=file][name*='resume' i], "
            "input[type=file]",
            resume,
        )
        await self._upload(
            page, "input[type=file][id*='cover' i], input[type=file][name*='cover' i]", cover
        )

    async def _fill_lever(
        self, page: Any, candidate: CandidateConfig, resume: Path, cover_text: str
    ) -> None:
        await self._upload(page, "input[type=file][name='resume']", resume)
        await page.wait_for_timeout(1500)  # Lever parses the resume and may prefill fields
        await self._fill(page, "input[name='name']", candidate.full_name)
        await self._fill(page, "input[name='email']", candidate.email)
        await self._fill(page, "input[name='phone']", candidate.phone)
        await self._fill(page, "input[name='org']", candidate.current_company)
        await self._fill(page, "input[name='location']", candidate.location)
        await self._fill(page, "input[name='urls[LinkedIn]']", candidate.linkedin_url)
        await self._fill(page, "input[name='urls[GitHub]']", candidate.github_url)
        await self._fill(page, "input[name='urls[Portfolio]']", candidate.portfolio_url)
        await self._fill(page, "textarea[name='comments']", cover_text)

    async def _answer_questions(self, page: Any, answers: dict[str, str]) -> list[str]:
        """Fill empty fields whose label matches a configured answer.

        Returns labels of required fields that are still empty.
        """

        for field in await page.evaluate(_SCAN_FIELDS_JS):
            if field["value"]:
                continue
            answer = match_answer(field["label"], answers)
            if answer is None:
                continue
            locator = page.locator(f"[data-sgja='{field['idx']}']")
            try:
                if field["tag"] == "select":
                    await locator.select_option(label=answer)
                else:
                    await locator.fill(answer)
            except Exception as error:  # an answer that does not fit this widget
                self.log(f"  could not fill {field['label'][:60]!r}: {error}")
        remaining = await page.evaluate(_SCAN_FIELDS_JS)
        return [
            f["label"][:80] or "(unlabelled)" for f in remaining if f["required"] and not f["value"]
        ]

    async def _confirmed(self, page: Any) -> bool:
        try:
            text = (await page.inner_text("body")).lower()
        except Exception:
            return False
        return any(phrase in text for phrase in CONFIRMATION_TEXT)

    async def _submit(self, page: Any, job: dict[str, Any]) -> Outcome:
        button = page.locator(
            "#submit_app, #btn-submit, button[type=submit], input[type=submit]"
        ).first
        await button.click()
        for _ in range(20):
            await page.wait_for_timeout(1000)
            if await self._confirmed(page):
                return Outcome(JobStatus.APPLIED, f"auto-submitted to {job['company']}")
        return Outcome(
            JobStatus.NEEDS_MANUAL,
            "clicked Submit but saw no confirmation; check your email or the posting",
        )
