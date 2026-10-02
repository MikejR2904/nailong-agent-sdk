"""End-to-end job-hunt pipeline composed from nailong-agent-sdk BaseAgent runs.

    resume.tex --profiler--> roles --scout x role--> ledger --tailor+polish x job--> packets
               --applier--> applications

Every stage persists to the workspace (profile.json, jobs.json, jobs/<id>/...), so
each CLI subcommand can be run on its own and re-runs resume rather than repeat.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nailong_agent_sdk import (
    AgentDefinition,
    AgentModel,
    AgentResult,
    AgentRunStatus,
    AgentRuntimeServices,
    AgentWatchdogPolicy,
    ArtifactStore,
    ContextProjectionPolicy,
    CoreToolDispatcher,
    CoreToolServices,
    ModelBinding,
    OpenAICompatibleAgentModel,
    OpenAICompatibleEndpoint,
    ScopedAgentTask,
    TaskScope,
    VerificationDecision,
    VerificationGateRegistry,
)
from nailong_agent_sdk.agent.verification import VerificationContext
from nailong_agent_sdk.tools.core import WebSearchClient

from .agents import (
    RESUME_INTEGRITY_GATE,
    polisher_definition,
    profiler_definition,
    scout_definition,
    tailor_definition,
)
from .config import AgentConfig
from .latex import find_latex_engine
from .sources import JobSourceClient
from .store import JobLedger, JobStatus
from .tools import JobToolbox, JobToolExecutor, TailoringContext, run_resume_check

ModelFactory = Callable[[AgentDefinition], AgentModel]
Logger = Callable[[str], None]

# Resumes and JDs are long; the SDK defaults (1 KB previews, 12k tokens) are sized
# for small tool results.
CONTEXT_POLICY = ContextProjectionPolicy(
    context_token_budget=80_000,
    episode_token_budget=24_000,
    tool_result_preview_chars=16_000,
)


def _slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:limit] or "x"


def openai_compatible_factory(config: AgentConfig) -> ModelFactory:
    endpoint = OpenAICompatibleEndpoint(
        base_url=config.model.base_url,
        api_key=config.model.api_key(),
        timeout_seconds=config.model.timeout_seconds,
    )
    model = OpenAICompatibleAgentModel(
        endpoint, provider=config.model.provider, model=config.model.model
    )
    return lambda _definition: model


class JobAgentPipeline:
    def __init__(
        self,
        config: AgentConfig,
        workspace: Path,
        *,
        model_factory: ModelFactory | None = None,
        sources: JobSourceClient | None = None,
        search_client: WebSearchClient | None = None,
        log: Logger = print,
    ) -> None:
        if not config.model.model and model_factory is None:
            raise ValueError("Set model.model in your config (the exact model identifier).")
        self.config = config
        self.root = workspace.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = AgentRuntimeServices.open(self.root)
        self.artifacts = ArtifactStore(self.root)
        self.ledger = JobLedger(self.root / "jobs.json")
        self.sources = sources or JobSourceClient()
        self.search_client = search_client
        self._model_factory = model_factory
        self.log = log
        self.binding = ModelBinding(
            provider=config.model.provider,
            model=config.model.model or "scripted",
            parameters={
                "temperature": config.model.temperature,
                "max_tokens": config.model.max_tokens,
            },
        )

    # --- paths -----------------------------------------------------------------

    @property
    def base_resume_path(self) -> Path:
        return self.root / "base_resume.tex"

    @property
    def profile_path(self) -> Path:
        return self.root / "profile.json"

    def job_dir(self, job_id: str) -> str:
        return f"jobs/{job_id}"

    def import_resume(self, tex_path: Path) -> None:
        shutil.copyfile(tex_path, self.base_resume_path)
        self.log(f"Imported base resume from {tex_path}")

    def load_profile(self) -> dict[str, Any]:
        if not self.profile_path.is_file():
            raise FileNotFoundError("No profile yet: run the `profile` step first.")
        return json.loads(self.profile_path.read_text(encoding="utf-8"))

    # --- SDK plumbing ----------------------------------------------------------

    def _model(self, definition: AgentDefinition) -> AgentModel:
        if self._model_factory is None:
            self._model_factory = openai_compatible_factory(self.config)
        return self._model_factory(definition)

    def _core(self, declared_outputs: tuple[str, ...] = ()) -> CoreToolDispatcher:
        return CoreToolDispatcher(
            CoreToolServices(
                root=self.root,
                artifacts=self.artifacts,
                declared_output_paths=declared_outputs,
                result_journal=self.runtime.result_journal,
                search_client=self.search_client,
            )
        )

    def _toolbox(self, core: CoreToolDispatcher, **overrides: Any) -> JobToolbox:
        search = self.config.search
        boards = {
            "greenhouse": search.sources.greenhouse_boards,
            "lever": search.sources.lever_companies,
            "ashby": search.sources.ashby_boards,
        }
        return JobToolbox(
            workspace=self.root,
            ledger=self.ledger,
            sources=self.sources,
            location=search.location,
            boards={ats: names for ats, names in boards.items() if names},
            mycareersfuture=search.sources.mycareersfuture,
            exclude_companies=search.exclude_companies,
            exclude_title_keywords=search.exclude_title_keywords,
            **overrides,
        )

    def _executor(
        self, core: CoreToolDispatcher, toolbox: JobToolbox, web_budget: int | None = None
    ) -> JobToolExecutor:
        executor = JobToolExecutor(core, toolbox.handlers(), web_budget=web_budget)
        toolbox.web_call = executor.web
        return executor

    async def _run(
        self,
        definition: AgentDefinition,
        *,
        stage: str,
        key: str,
        instructions: str,
        task_input: dict[str, Any],
        criteria: list[str],
        executor: JobToolExecutor,
        gates: VerificationGateRegistry | None = None,
    ) -> AgentResult:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        task = ScopedAgentTask(
            id=f"{stage}-{_slug(key)}-{stamp}",
            input=task_input,
            scope=TaskScope(label=f"sg-job-agent-{stage}", boundaries={"stage": stage}),
            locked_interface=None,
            instructions=instructions,
            acceptance_criteria=criteria,
        )
        agent = self.runtime.create_agent(
            definition,
            self._model(definition),
            tool_executor=executor,
            verification_gates=gates,
            context_projection_policy=CONTEXT_POLICY,
            watchdog_policy=AgentWatchdogPolicy(
                model_turn_timeout_seconds=self.config.model.timeout_seconds + 30,
                run_deadline_seconds=3_600,
            ),
        )
        result = await agent.run(task)
        if result.status is not AgentRunStatus.COMPLETED:
            self.log(f"  [{stage}] {key}: {result.status.value} - {result.reason}")
        return result

    # --- stage 1: profile --------------------------------------------------------

    async def profile(self) -> dict[str, Any]:
        if not self.base_resume_path.is_file():
            raise FileNotFoundError("Import a base resume first (--resume path/to/resume.tex).")
        core = self._core()
        result = await self._run(
            profiler_definition(self.binding),
            stage="profile",
            key="resume",
            instructions=(
                f"Read the resume at {self.base_resume_path.name}, summarise the candidate, "
                f"and propose up to {self.config.search.max_roles} target full-time roles in "
                f"{self.config.search.location}."
            ),
            task_input={
                "resume_path": self.base_resume_path.name,
                "location": self.config.search.location,
                "preferred_roles": self.config.search.roles,
                "max_roles": self.config.search.max_roles,
            },
            criteria=["Every role and skill is supported by the resume text."],
            executor=self._executor(core, self._toolbox(core)),
        )
        if result.status is not AgentRunStatus.COMPLETED:
            raise RuntimeError(f"Profiling failed: {result.reason}")
        profile = result.output
        profile["roles"] = profile["roles"][: self.config.search.max_roles]
        self.profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        self.log("Target roles:")
        for role in profile["roles"]:
            self.log(f"  - {role['title']} ({role['seniority']}): {role['fit_rationale']}")
        return profile

    # --- stage 2: discover -------------------------------------------------------

    async def discover(self, role_titles: list[str] | None = None) -> list[str]:
        profile = self.load_profile()
        roles = profile["roles"]
        if role_titles:
            wanted = {title.lower() for title in role_titles}
            roles = [role for role in roles if role["title"].lower() in wanted]
        search = self.config.search
        saved: list[str] = []
        for role in roles:
            self.log(f"Scouting: {role['title']}")
            core = self._core()
            toolbox = self._toolbox(core, role_category=role["title"])
            web_enabled = search.sources.web_search and search.max_web_calls_per_role > 0
            result = await self._run(
                scout_definition(self.binding, web_search=web_enabled),
                stage="scout",
                key=role["title"],
                instructions=(
                    f"Find full-time {role['title']} openings in {search.location} and save "
                    f"up to {search.max_jobs_per_role} that fit the candidate "
                    f"(fit_score >= {search.min_fit_score})."
                ),
                task_input={
                    "role": role,
                    "candidate": profile["candidate"],
                    "location": search.location,
                    "min_fit_score": search.min_fit_score,
                    "max_jobs": search.max_jobs_per_role,
                    "web_search_enabled": web_enabled,
                    "web_search_sites": search.sources.web_search_sites if web_enabled else [],
                    "web_budget": search.max_web_calls_per_role if web_enabled else 0,
                },
                criteria=["Only real postings seen in tool results are saved."],
                executor=self._executor(core, toolbox, web_budget=search.max_web_calls_per_role),
            )
            # The ledger, not the model's summary, is authoritative for what was saved.
            saved += [job_id for job_id in toolbox.saved_job_ids if job_id not in saved]
            self.log(f"  saved {len(toolbox.saved_job_ids)} posting(s) ({result.status.value})")
        return saved

    # --- stage 3: tailor + polish ------------------------------------------------

    async def _job_description(self, job: dict[str, Any], toolbox: JobToolbox) -> str:
        if len(job.get("description", "")) >= 400:
            return job["description"]
        description = await toolbox.fetch_description(job["url"])
        self.ledger.update(job["id"], description=description)
        return description

    def _integrity_gates(self, resume_path: str, tailoring: TailoringContext):
        def gate(context: VerificationContext) -> VerificationDecision:
            output = context.output
            if output.get("resume_path") != resume_path:
                return VerificationDecision(False, f"resume_path must be {resume_path}.")
            check = run_resume_check(self.root / resume_path, tailoring)
            if not check.passed:
                return VerificationDecision(False, "; ".join(check.errors))
            return VerificationDecision(True)

        gates = VerificationGateRegistry()
        gates.register_callable(RESUME_INTEGRITY_GATE, gate)
        return gates

    async def tailor(self, job_id: str) -> dict[str, Any]:
        job = self.ledger.get(job_id)
        directory = self.job_dir(job_id)
        resume_path = f"{directory}/resume.tex"
        letter_path = (
            f"{directory}/cover_letter.txt" if self.config.tailoring.write_cover_letter else ""
        )
        declared = tuple(path for path in (resume_path, letter_path) if path)
        core = self._core(declared)
        toolbox = self._toolbox(core)
        executor = self._executor(core, toolbox, web_budget=3)
        try:
            description = await self._job_description(job, toolbox)
        except ValueError as error:
            self.ledger.update(job_id, status=JobStatus.TAILOR_FAILED, error=str(error))
            raise RuntimeError(f"Could not fetch the job description: {error}") from error
        jd_path = f"{directory}/job_description.md"
        (self.root / directory).mkdir(parents=True, exist_ok=True)
        (self.root / jd_path).write_text(
            f"# {job['title']} - {job['company']}\n\nURL: {job['url']}\n"
            f"Location: {job.get('location', '')}\n\n{description}\n",
            encoding="utf-8",
        )
        tailoring = TailoringContext(
            base_resume_tex=self.base_resume_path.read_text(encoding="utf-8"),
            job_description=description,
            latex_engine=find_latex_engine(self.config.tailoring.latex_engine),
            max_pages=self.config.tailoring.max_pages,
        )
        toolbox.tailoring = tailoring
        gates = self._integrity_gates(resume_path, tailoring)
        task_input = {
            "base_resume_path": self.base_resume_path.name,
            "job_description_path": jd_path,
            "resume_path": resume_path,
            "cover_letter_path": letter_path,
            "job": {k: job.get(k) for k in ("title", "company", "location", "url")},
            "candidate_name": self.config.candidate.full_name,
            "max_pages": tailoring.max_pages,
        }
        instructions = (
            f"Tailor the base resume for {job['title']} at {job['company']}. Write it to "
            f"{resume_path}"
            + (f" and a cover letter to {letter_path}" if letter_path else "")
            + "."
        )
        result = None
        for attempt in range(2):
            result = await self._run(
                tailor_definition(self.binding),
                stage="tailor",
                key=job_id,
                instructions=instructions,
                task_input=task_input,
                criteria=["check_tailored_resume passes on the final resume."],
                executor=executor,
                gates=gates,
            )
            if result.status is AgentRunStatus.COMPLETED:
                break
            instructions += (
                f"\nA previous attempt was rejected: {result.reason}. If {resume_path} already "
                "exists, read it and fix it rather than starting over."
            )
        if result is None or result.status is not AgentRunStatus.COMPLETED:
            reason = result.reason if result else "not run"
            self.ledger.update(job_id, status=JobStatus.TAILOR_FAILED, error=reason)
            raise RuntimeError(f"Tailoring failed for {job_id}: {reason}")
        summary = result.output

        if self.config.tailoring.polish_pass:
            snapshot = (self.root / resume_path).read_text(encoding="utf-8")
            polish = await self._run(
                polisher_definition(self.binding),
                stage="polish",
                key=job_id,
                instructions=f"Proofread and clean up {resume_path}"
                + (f" and {letter_path}" if letter_path else "")
                + ".",
                task_input=task_input,
                criteria=["check_tailored_resume passes on the final resume."],
                executor=executor,
                gates=gates,
            )
            if polish.status is AgentRunStatus.COMPLETED:
                summary["polish_fixes"] = polish.output.get("fixes", [])
            else:
                # A failed polish must not leave a broken resume behind.
                self.artifacts.write_text(resume_path, snapshot)
                summary["polish_fixes"] = [f"polish pass discarded: {polish.reason}"]

        check = run_resume_check(self.root / resume_path, tailoring)
        packet: dict[str, Any] = {
            "resume_tex": resume_path,
            "cover_letter": letter_path
            if letter_path and (self.root / letter_path).is_file()
            else "",
            "resume_pdf": "",
            "warnings": check.warnings,
            **{key: summary.get(key, []) for key in ("changes", "matched_requirements", "gaps")},
            "polish_fixes": summary.get("polish_fixes", []),
        }
        if check.pdf_path:
            pretty = (
                f"{directory}/{_slug(self.config.candidate.full_name)}_Resume_"
                f"{_slug(job['company'], 30)}.pdf"
            )
            shutil.copyfile(check.pdf_path, self.root / pretty)
            packet["resume_pdf"] = pretty
        self.ledger.update(job_id, status=JobStatus.TAILORED, packet=packet, error=None)
        self.log(f"  tailored {job['title']} @ {job['company']} -> {resume_path}")
        return packet

    def shortlist(self, limit: int | None = None) -> list[dict[str, Any]]:
        jobs = self.ledger.ranked(JobStatus.DISCOVERED, min_fit=self.config.search.min_fit_score)
        return jobs[:limit] if limit else jobs

    async def tailor_top(self, limit: int) -> list[str]:
        done = []
        for job in self.shortlist(limit):
            try:
                await self.tailor(job["id"])
                done.append(job["id"])
            except RuntimeError as error:
                self.log(f"  ! {error}")
        return done

    async def aclose(self) -> None:
        await self.sources.aclose()
