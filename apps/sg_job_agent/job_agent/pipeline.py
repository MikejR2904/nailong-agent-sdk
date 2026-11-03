"""End-to-end job-hunt pipeline composed from nailong-agent-sdk BaseAgent runs.

    resume.tex --profiler--> roles --scout x role--> ledger --tailor+polish x job--> packets
               --applier--> applications

Every stage persists to the workspace (profile.json, jobs.json, jobs/<id>/...), so
each CLI subcommand can be run on its own and re-runs resume rather than repeat.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
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
    TAILOR_GATE,
    analyst_definition,
    polisher_definition,
    profiler_definition,
    scorer_definition,
    tailor_definition,
    verifier_definition,
)
from .claims import claims_to_verify, unsupported_claims
from .codebase import analyze_archive, key_excerpts, verified_claims
from .config import AgentConfig
from .ingest import (
    GitHubIngester,
    document_items,
    linkedin_items,
    resume_items,
    website_items,
)
from .knowledge import EvidenceItem, KnowledgeBase
from .latex import find_latex_engine, latex_to_text
from .overleaf import OverleafEntry, write_launcher
from .sources import JobSourceClient
from .store import JobLedger, JobStatus
from .tools import (
    JobToolbox,
    JobToolExecutor,
    TailoringContext,
    assemble_resume,
    run_resume_check,
)
from .visual import render_preview, vision_review

MAX_ARCHIVE_BYTES = 40_000_000
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


def model_factory_for(config: AgentConfig) -> ModelFactory:
    if not config.model.model:
        raise ValueError("Set model.model in your config (the exact model identifier).")
    if config.model.provider == "anthropic":
        return claude_factory(config)
    return openai_compatible_factory(config)


def claude_factory(config: AgentConfig) -> ModelFactory:
    import anthropic

    from .claude_model import ClaudeAgentModel

    client = anthropic.AsyncAnthropic(
        api_key=config.model.api_key(),
        # Explicit, so an ambient ANTHROPIC_BASE_URL never reroutes the agent.
        base_url=config.model.base_url or "https://api.anthropic.com",
        timeout=config.model.timeout_seconds,
        max_retries=3,
    )
    return lambda _definition: ClaudeAgentModel(
        client,
        model=config.model.model,
        effort=config.model.effort,
        max_tokens=config.model.max_tokens,
    )


def openai_compatible_factory(config: AgentConfig) -> ModelFactory:
    if not config.model.base_url:
        raise ValueError("model.base_url is required for OpenAI-compatible providers.")
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
        self.config = config
        self.root = workspace.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = AgentRuntimeServices.open(self.root)
        self.artifacts = ArtifactStore(self.root)
        self.ledger = JobLedger(self.root / "jobs.json")
        self.sources = sources or JobSourceClient()
        self.search_client = search_client
        self._model_factory = model_factory
        self._knowledge: KnowledgeBase | None = None
        self.log = log
        parameters: dict[str, Any] = {}
        if config.model.provider != "anthropic":
            parameters["max_tokens"] = min(config.model.max_tokens, 32_000)
            if config.model.temperature is not None:
                parameters["temperature"] = config.model.temperature
            if config.model.reasoning_effort is not None:
                parameters["reasoning_effort"] = config.model.reasoning_effort
        self.binding = ModelBinding(
            provider=config.model.provider,
            model=config.model.model or "scripted",
            parameters=parameters,
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

    @property
    def knowledge(self) -> KnowledgeBase:
        if self._knowledge is None:
            self._knowledge = KnowledgeBase(self.root / "knowledge")
        return self._knowledge

    # --- stage 0: ingest -------------------------------------------------------

    async def ingest(self, *, http: httpx.AsyncClient | None = None) -> dict[str, int]:
        """Build the experience bank from every configured source (no model calls)."""

        settings = self.config.knowledge
        kb = self.knowledge
        client = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True)
        warnings: list[str] = []
        try:
            if self.base_resume_path.is_file():
                tex = self.base_resume_path.read_text(encoding="utf-8")
                kb.replace_source("resume", resume_items(tex))
            if settings.documents:
                paths = [Path(path).expanduser() for path in settings.documents]
                kb.replace_source("document", document_items(paths))
            if settings.github_username or settings.github_extra_repos:
                github = GitHubIngester(client, os.environ.get(settings.github_token_env, ""))
                try:
                    items = await github.items(
                        settings.github_username,
                        extra_repos=settings.github_extra_repos,
                        include_forks=settings.github_include_forks,
                    )
                    kb.replace_source("github", items)
                except (httpx.HTTPError, RuntimeError) as error:
                    warnings.append(f"GitHub ingest failed: {error}")
                warnings += github.warnings
            if settings.linkedin_export:
                export = Path(settings.linkedin_export).expanduser()
                kb.replace_source("linkedin", linkedin_items(export))
            if settings.website_urls:
                items, site_warnings = await website_items(
                    client, settings.website_urls, max_pages=settings.max_website_pages
                )
                kb.replace_source("website", items)
                warnings += site_warnings
        finally:
            if http is None:
                await client.aclose()
        kb.save()
        counts = kb.counts()
        self.log("Experience bank: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        for warning in warnings:
            self.log(f"  warning: {warning}")
        return counts

    async def analyze_code(self) -> int:
        """Read the real code of the candidate's GitHub repos and review it (cached)."""

        settings = self.config.knowledge
        repos = [
            item
            for item in self.knowledge.items("github")
            if item.meta.get("repo") and not item.meta.get("fork")
        ]
        if not settings.code_analysis or not repos:
            return 0
        repos.sort(key=lambda item: item.meta.get("pushed_at", ""), reverse=True)
        repos = repos[: settings.max_analyzed_repos]
        gate = asyncio.Semaphore(3)
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True) as client:
            github = GitHubIngester(client, os.environ.get(settings.github_token_env, ""))

            async def one(item: EvidenceItem) -> EvidenceItem | None:
                async with gate:
                    return await self._analyze_repo(github, item)

            results = await asyncio.gather(*(one(item) for item in repos))
        items = [item for item in results if item is not None]
        self.knowledge.replace_source("code", items)
        self.knowledge.save()
        self.log(f"Code analysis: {len(items)} of {len(repos)} repositories")
        return len(items)

    async def _analyze_repo(
        self, github: GitHubIngester, readme_item: EvidenceItem
    ) -> EvidenceItem | None:
        full_name = readme_item.meta["repo"]
        pushed_at = readme_item.meta.get("pushed_at", "")
        cache = self.root / "knowledge" / "code" / (full_name.replace("/", "__") + ".json")
        if cache.is_file():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("pushed_at") == pushed_at:
                return EvidenceItem(**cached["item"])
        try:
            data = await github.archive(full_name, max_bytes=MAX_ARCHIVE_BYTES)
        except (httpx.HTTPError, RuntimeError, ValueError) as error:
            self.log(f"  warning: code analysis of {full_name} skipped: {error}")
            return None
        facts, sources = await asyncio.to_thread(analyze_archive, data, full_name)
        core = self._core()
        review = await self._run(
            analyst_definition(self.binding),
            stage="analyze",
            key=full_name,
            instructions=f"Review the repository {full_name} from its real code.",
            task_input={
                "facts": facts.to_text(),
                "readme": readme_item.text[:3000],
                "excerpts": key_excerpts(facts, sources),
            },
            criteria=["Every claim cites files that exist in the repository."],
            executor=self._executor(core, self._toolbox(core)),
        )
        text = facts.to_text()
        if review.status is AgentRunStatus.COMPLETED:
            claims = verified_claims(review.output["claims"], facts)
            text += "\n\nReview of the code: " + review.output["summary"]
            text += "".join(
                f"\n- {claim['claim']} (files: {', '.join(claim['evidence_paths'])})"
                for claim in claims
            )
            text += "\nTech stack: " + ", ".join(review.output["tech_stack"])
        else:
            self.log(f"  warning: review of {full_name} failed: {review.reason}")
        item = EvidenceItem(
            source="code",
            title=f"Code analysis: {full_name}",
            text=text,
            url=readme_item.url,
            meta={"repo": full_name, "pushed_at": pushed_at},
        )
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps({"pushed_at": pushed_at, "item": item.to_dict()}, ensure_ascii=False),
            encoding="utf-8",
        )
        return item

    def load_profile(self) -> dict[str, Any]:
        if not self.profile_path.is_file():
            raise FileNotFoundError("No profile yet: run the `profile` step first.")
        return json.loads(self.profile_path.read_text(encoding="utf-8"))

    # --- SDK plumbing ----------------------------------------------------------

    def _model(self, definition: AgentDefinition) -> AgentModel:
        if self._model_factory is None:
            self._model_factory = model_factory_for(self.config)
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
            linkedin=search.sources.linkedin,
            exclude_companies=search.exclude_companies,
            exclude_title_keywords=search.exclude_title_keywords,
            knowledge=self.knowledge,
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
        bank = [
            {
                "id": item.id,
                "source": item.source,
                "title": item.title[:90],
                "text": item.text[:500],
            }
            for item in self.knowledge.items()
            if item.source != "resume"
        ][:40]
        result = await self._run(
            profiler_definition(self.binding),
            stage="profile",
            key="resume",
            instructions=(
                "Summarise the candidate and propose up to "
                f"{self.config.search.max_roles} target full-time roles in "
                f"{self.config.search.location}."
            ),
            task_input={
                "base_resume_tex": self.base_resume_path.read_text(encoding="utf-8"),
                "location": self.config.search.location,
                "preferred_roles": self.config.search.roles,
                "max_roles": self.config.search.max_roles,
                "experience_bank": {"counts": self.knowledge.counts(), "items": bank},
            },
            criteria=["Every role and skill is supported by the resume or the experience bank."],
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
        network = asyncio.Semaphore(3)
        batches = await asyncio.gather(
            *(self._scout_role(role, profile, network) for role in roles)
        )
        saved: list[str] = []
        for batch in batches:
            saved += [job_id for job_id in batch if job_id not in saved]
        return saved

    async def _scout_role(
        self, role: dict[str, Any], profile: dict[str, Any], network: asyncio.Semaphore
    ) -> list[str]:
        search = self.config.search
        core = self._core()
        toolbox = self._toolbox(core, role_category=role["title"])
        pool: dict[str, dict[str, Any]] = {}
        for query in role["search_queries"][:3]:
            async with network:
                found = await toolbox.search_job_sources(
                    {"query": query, "title_keywords": role["title_keywords"], "max_results": 15}
                )
            for warning in found["warnings"]:
                self.log(f"  [{role['title']}] {warning}")
            for result in found["results"]:
                if not result["already_in_ledger"]:
                    pool.setdefault(result["candidate_id"], result)
        candidates = list(pool.values())[: search.max_jobs_per_role * 3]

        async def describe(result: dict[str, Any]) -> dict[str, Any]:
            posting = toolbox.candidate(result["candidate_id"])
            text = posting.description
            if len(text) < 400:
                try:
                    async with network:
                        text = (await toolbox.get_job_description({"url": posting.url}))[
                            "description"
                        ]
                except (ValueError, KeyError):
                    pass
            return {
                "candidate_id": result["candidate_id"],
                "title": result["title"],
                "company": result["company"],
                "location": result["location"],
                "description": text[:1800],
            }

        described = await asyncio.gather(*(describe(result) for result in candidates))
        if not described:
            self.log(f"  [{role['title']}] no new postings found")
            return []
        scored = await self._run(
            scorer_definition(self.binding),
            stage="score",
            key=role["title"],
            instructions=f"Score these {len(described)} postings for the {role['title']} role.",
            task_input={
                "candidate": profile["candidate"],
                "role": role,
                "location": search.location,
                "candidates": described,
            },
            criteria=["Every candidate_id is scored once."],
            executor=self._executor(core, toolbox),
        )
        if scored.status is not AgentRunStatus.COMPLETED:
            self.log(f"  [{role['title']}] scoring failed: {scored.reason}")
            return []
        by_id = {item["candidate_id"]: item for item in described}
        ranked = sorted(scored.output["scores"], key=lambda entry: -entry["fit_score"])
        saved: list[str] = []
        for entry in ranked:
            if len(saved) >= search.max_jobs_per_role or entry["fit_score"] < search.min_fit_score:
                break
            info = by_id.get(entry["candidate_id"])
            if info is None:
                continue
            posting = toolbox.candidate(entry["candidate_id"])
            try:
                await toolbox.save_job_posting(
                    {
                        "candidate_id": entry["candidate_id"],
                        "url": posting.url,
                        "title": posting.title,
                        "company": posting.company,
                        "location": posting.location,
                        "fit_score": entry["fit_score"],
                        "fit_rationale": entry["fit_rationale"][:600],
                        "description": info["description"],
                    }
                )
                saved.append(entry["candidate_id"])
            except ValueError as error:
                self.log(f"  [{role['title']}] skipped {posting.title}: {error}")
        self.log(f"  [{role['title']}] scored {len(described)}, saved {len(saved)}")
        return saved

    # --- stage 3: tailor + polish ------------------------------------------------

    async def _job_description(self, job: dict[str, Any], toolbox: JobToolbox) -> str:
        if len(job.get("description", "")) >= 400:
            return job["description"]
        description = await toolbox.fetch_description(job["url"])
        self.ledger.update(job["id"], description=description)
        return description

    def _gates(
        self,
        resume_path: str,
        letter_path: str,
        tailoring: TailoringContext,
        evidence_ids: list[str],
    ):
        """``evidence_ids`` is shared state: the tailor's citations, reused by the polisher."""

        def tailor_gate(context: VerificationContext) -> VerificationDecision:
            output = context.output
            evidence_ids[:] = output.get("evidence_ids", [])
            tex = assemble_resume(tailoring.base_resume_tex, output.get("resume_body", ""))
            if " ".join(tex.split()) == " ".join(tailoring.base_resume_tex.split()):
                return VerificationDecision(
                    False,
                    "resume_body is the unchanged base resume: select, reorder or reword "
                    "content for this job.",
                )
            budget = int(len(latex_to_text(tailoring.base_resume_tex)) * 1.03)
            size = len(latex_to_text(tex))
            if tailoring.latex_engine is not None and size > budget:
                return VerificationDecision(
                    False,
                    f"resume_body is too long for {tailoring.max_pages} page(s): {size} "
                    f"rendered characters against a budget of {budget}. Cut at least "
                    f"{size - budget} characters (drop the least relevant bullets).",
                )
            letter = output.get("cover_letter", "").strip()
            if letter_path:
                words = len(letter.split())
                if not 80 <= words <= 260:
                    return VerificationDecision(
                        False, f"cover_letter is {words} words; write between 120 and 220."
                    )
            (self.root / resume_path).write_text(tex, encoding="utf-8")
            check = run_resume_check(self.root / resume_path, tailoring, evidence_ids)
            if not check.passed:
                return VerificationDecision(False, "; ".join(check.errors))
            if letter_path:
                (self.root / letter_path).write_text(letter + chr(10), encoding="utf-8")
            return VerificationDecision(True)

        def polish_gate(context: VerificationContext) -> VerificationDecision:
            output = context.output
            if output.get("resume_path") != resume_path:
                return VerificationDecision(False, f"resume_path must be {resume_path}.")
            check = run_resume_check(self.root / resume_path, tailoring, evidence_ids)
            if not check.passed:
                return VerificationDecision(False, "; ".join(check.errors))
            return VerificationDecision(True)

        gates = VerificationGateRegistry()
        gates.register_callable(TAILOR_GATE, tailor_gate)
        gates.register_callable(RESUME_INTEGRITY_GATE, polish_gate)
        return gates

    def _evidence_for(self, job: dict[str, Any], description: str, limit: int = 14) -> list[dict]:
        query = f"{job['title']} {job['company']} {description[:4000]}"
        return [
            {
                "id": item.id,
                "source": item.source,
                "title": item.title,
                "text": item.text[: 1800 if item.source == "code" else 900],
            }
            for item, _score in self.knowledge.search(query, limit=limit)
            if item.source != "resume"
        ]

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
        tailoring = self.tailoring_context(description)
        toolbox.tailoring = tailoring
        evidence_ids: list[str] = []
        gates = self._gates(resume_path, letter_path, tailoring, evidence_ids)
        task_input: dict[str, Any] = {
            "base_resume_tex": tailoring.base_resume_tex,
            "job_description": description[:8000],
            "evidence": self._evidence_for(job, description),
            "job": {k: job.get(k) for k in ("title", "company", "location", "url")},
            "candidate_name": self.config.candidate.full_name,
            "max_pages": tailoring.max_pages,
            "max_project_bullets": tailoring.max_project_bullets,
            "max_rendered_characters": int(len(latex_to_text(tailoring.base_resume_tex)) * 1.03),
            "cover_letter_wanted": bool(letter_path),
        }
        instructions = f"Tailor the resume for {job['title']} at {job['company']}."
        result = None
        problems: list[str] = []
        for _attempt in range(2):
            result = await self._run(
                tailor_definition(self.binding),
                stage="tailor",
                key=job_id,
                instructions=instructions,
                task_input=task_input,
                criteria=["The assembled resume passes the integrity checks."],
                executor=executor,
                gates=gates,
            )
            if result.status is AgentRunStatus.COMPLETED:
                problems = await self._unsupported(
                    job_id, resume_path, tailoring, evidence_ids, executor
                )
                if not problems:
                    break
                rejection = "Unsupported claims, remove or rewrite them: " + " | ".join(problems)
            else:
                rejection = result.reason
            task_input = {
                **task_input,
                "rejection": rejection,
                "previous_draft": (self.root / resume_path).read_text(encoding="utf-8")
                if (self.root / resume_path).is_file()
                else "",
            }
        if result is None or result.status is not AgentRunStatus.COMPLETED:
            reason = result.reason if result else "not run"
            self.ledger.update(job_id, status=JobStatus.TAILOR_FAILED, error=reason)
            raise RuntimeError(f"Tailoring failed for {job_id}: {reason}")
        if problems:
            reason = "Unsupported claims remain: " + " | ".join(problems)
            self.ledger.update(job_id, status=JobStatus.TAILOR_FAILED, error=reason)
            raise RuntimeError(f"Tailoring failed for {job_id}: {reason}")
        summary = result.output

        if self.config.tailoring.polish_pass:
            task_input = {**task_input, "evidence_ids": list(evidence_ids)}
            snapshot = (self.root / resume_path).read_text(encoding="utf-8")
            polish = await self._run(
                polisher_definition(self.binding),
                stage="polish",
                key=job_id,
                instructions=f"Proofread and clean up {resume_path}"
                + (f" and {letter_path}" if letter_path else "")
                + ".",
                task_input={
                    **task_input,
                    "resume_path": resume_path,
                    "base_resume_path": self.base_resume_path.name,
                    "job_description_path": jd_path,
                },
                criteria=["check_tailored_resume passes on the final resume."],
                executor=executor,
                gates=gates,
            )
            if polish.status is AgentRunStatus.COMPLETED:
                summary["polish_fixes"] = polish.output.get("fixes", [])
            else:
                self.artifacts.write_text(resume_path, snapshot)
                summary["polish_fixes"] = [f"polish pass discarded: {polish.reason}"]

        return self.record_tailored(job_id, summary, tailoring)

    async def _unsupported(
        self,
        job_id: str,
        resume_path: str,
        tailoring: TailoringContext,
        evidence_ids: list[str],
        executor: JobToolExecutor,
    ) -> list[str]:
        tex = (self.root / resume_path).read_text(encoding="utf-8")
        claims = claims_to_verify(tailoring.base_resume_tex, tex)
        if not claims:
            return []
        cited = [item_id for item_id in evidence_ids if item_id in self.knowledge]
        sources = (
            latex_to_text(tailoring.base_resume_tex) + chr(10) * 2 + self.knowledge.text_for(cited)
        )[:40_000]
        verdict = await self._run(
            verifier_definition(self.binding),
            stage="verify",
            key=job_id,
            instructions=f"Fact-check these {len(claims)} resume bullets against the sources.",
            task_input={"claims": claims, "sources": sources},
            criteria=["Every claim has one verdict."],
            executor=executor,
        )
        if verdict.status is not AgentRunStatus.COMPLETED:
            return [f"the verifier could not run: {verdict.reason}"]
        return unsupported_claims(claims, verdict.output["verdicts"], sources)

    def tailoring_context(self, description: str) -> TailoringContext:
        return TailoringContext(
            base_resume_tex=self.base_resume_path.read_text(encoding="utf-8"),
            job_description=description,
            latex_engine=find_latex_engine(self.config.tailoring.latex_engine),
            max_pages=self.config.tailoring.max_pages,
            max_project_bullets=self.config.tailoring.max_project_bullets,
            knowledge=self.knowledge,
        )

    def record_tailored(
        self, job_id: str, summary: dict[str, Any], tailoring: TailoringContext
    ) -> dict[str, Any]:
        """Final check, PDF copy, Overleaf launcher, and ledger update for a tailored job."""

        job = self.ledger.get(job_id)
        directory = self.job_dir(job_id)
        resume_path = f"{directory}/resume.tex"
        letter_path = f"{directory}/cover_letter.txt"
        evidence_ids = summary.get("evidence_ids", [])
        check = run_resume_check(self.root / resume_path, tailoring, evidence_ids)
        if not check.passed:
            self.ledger.update(
                job_id, status=JobStatus.TAILOR_FAILED, error="; ".join(check.errors)
            )
            raise RuntimeError(f"Tailored resume for {job_id} failed checks: {check.errors}")
        packet: dict[str, Any] = {
            "resume_tex": resume_path,
            "cover_letter": letter_path if (self.root / letter_path).is_file() else "",
            "resume_pdf": "",
            "page_count": check.page_count,
            "warnings": check.warnings,
            **{key: summary.get(key, []) for key in ("changes", "matched_requirements", "gaps")},
            "polish_fixes": summary.get("polish_fixes", []),
            "evidence_ids": evidence_ids,
        }
        if check.pdf_path:
            pretty = (
                f"{directory}/{_slug(self.config.candidate.full_name)}_Resume_"
                f"{_slug(job['company'], 24)}_{_slug(job['title'], 28)}.pdf"
            )
            shutil.copyfile(check.pdf_path, self.root / pretty)
            packet["resume_pdf"] = pretty
        if check.pdf_path:
            preview = render_preview(
                Path(check.pdf_path), self.root / directory / "resume_preview.png"
            )
            packet["preview_png"] = f"{directory}/resume_preview.png" if preview else ""
            if (
                preview
                and self.config.tailoring.vision_review
                and (self.config.model.provider != "anthropic")
            ):
                try:
                    packet["visual_review"] = vision_review(
                        preview,
                        base_url=self.config.model.base_url,
                        api_key=self.config.model.api_key(),
                        model=self.config.model.model,
                    )
                except (httpx.HTTPError, KeyError, ValueError) as error:
                    packet["visual_review"] = [f"vision review unavailable: {error}"]
        launcher = self.root / directory / "open_in_overleaf.html"
        write_launcher(
            launcher,
            [self._overleaf_entry(job, packet, relative_to=directory)],
            heading=f"{job['title']} - {job['company']}",
        )
        packet["overleaf_launcher"] = f"{directory}/open_in_overleaf.html"
        self.ledger.update(job_id, status=JobStatus.TAILORED, packet=packet, error=None)
        self.write_overleaf_index()
        self.log(f"  tailored {job['title']} @ {job['company']} -> {resume_path}")
        return packet

    def _overleaf_entry(
        self, job: dict[str, Any], packet: dict[str, Any], *, relative_to: str = ""
    ) -> OverleafEntry:
        prefix = f"{relative_to}/" if relative_to else ""
        pdf = packet.get("resume_pdf", "")
        return OverleafEntry(
            title=job["title"],
            subtitle=f"{job['company']} · fit {job.get('fit_score', '?')}",
            tex=(self.root / packet["resume_tex"]).read_text(encoding="utf-8"),
            file_name="resume.tex",
            pdf_href=pdf.removeprefix(prefix) if pdf else "",
            posting_url=job["url"],
        )

    def write_overleaf_index(self) -> Path:
        """One page with an "Open in Overleaf" button for every tailored resume."""

        jobs = self.ledger.ranked(JobStatus.TAILORED, JobStatus.APPLIED, JobStatus.NEEDS_MANUAL)
        entries = [self._overleaf_entry(job, job["packet"]) for job in jobs if job.get("packet")]
        return write_launcher(
            self.root / "overleaf.html", entries, heading="Tailored resumes - Open in Overleaf"
        )

    def shortlist(self, limit: int | None = None) -> list[dict[str, Any]]:
        jobs = self.ledger.ranked(JobStatus.DISCOVERED, min_fit=self.config.search.min_fit_score)
        return jobs[:limit] if limit else jobs

    async def tailor_top(self, limit: int) -> list[str]:
        return await self.tailor_many([job["id"] for job in self.shortlist(limit)])

    async def tailor_many(self, job_ids: list[str]) -> list[str]:
        gate = asyncio.Semaphore(3)

        async def one(job_id: str) -> str | None:
            async with gate:
                try:
                    await self.tailor(job_id)
                except RuntimeError as error:
                    self.log(f"  ! {error}")
                    return None
            return job_id

        done = await asyncio.gather(*(one(job_id) for job_id in job_ids))
        return [job_id for job_id in done if job_id]

    async def aclose(self) -> None:
        await self.sources.aclose()
