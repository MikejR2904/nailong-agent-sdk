"""Command-line entry point.

sg-job-agent ingest   --resume resume.tex     # build the experience bank
sg-job-agent profile                          # infer target roles
sg-job-agent discover [--role "AI Engineer"]   # find openings, save to ledger
sg-job-agent list     [--status discovered]   # inspect the ledger
sg-job-agent tailor   --top 5 | --job ID       # tailored resume + cover letter
sg-job-agent apply    --top 5 | --job ID [--mode review]
sg-job-agent run      --resume resume.tex --top 5   # everything, in order
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .apply import Applier, ask_yes_no
from .config import AgentConfig
from .pipeline import JobAgentPipeline
from .store import JobStatus


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sg-job-agent", description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser(
        "ingest", help="Build the experience bank (documents, GitHub, LinkedIn export, website)."
    )
    ingest.add_argument("--resume", type=Path, help="Base resume .tex (imported once).")

    profile = sub.add_parser("profile", help="Analyse the resume and propose target roles.")
    profile.add_argument("--resume", type=Path, help="Base resume .tex (imported once).")

    discover = sub.add_parser("discover", help="Find openings for the target roles.")
    discover.add_argument("--role", action="append", help="Limit to these role titles.")

    listing = sub.add_parser("list", help="Show ledger entries ranked by fit.")
    listing.add_argument("--status", choices=[s.value for s in JobStatus])

    sub.add_parser("overleaf", help="Write overleaf.html: open tailored resumes in Overleaf.")

    for name, text in (("tailor", "Tailor resumes."), ("apply", "Submit applications.")):
        command = sub.add_parser(name, help=text)
        group = command.add_mutually_exclusive_group(required=True)
        group.add_argument("--job", action="append", help="Job id(s) from `list`.")
        group.add_argument("--top", type=int, help="The N best-fitting eligible jobs.")
        if name == "apply":
            command.add_argument("--mode", choices=["manual", "review", "auto"])
            command.add_argument("--yes", action="store_true", help="Skip the batch prompt.")

    run = sub.add_parser("run", help="profile -> discover -> tailor -> apply.")
    run.add_argument("--resume", type=Path)
    run.add_argument("--top", type=int, default=5)
    run.add_argument("--mode", choices=["manual", "review", "auto"])
    run.add_argument("--skip-apply", action="store_true")
    run.add_argument("--yes", action="store_true")
    return parser


def _print_jobs(jobs: list[dict]) -> None:
    if not jobs:
        print("(no jobs)")
    for job in jobs:
        print(
            f"{job['id']}  fit={job.get('fit_score', '?'):>3}  {job['status']:<13} "
            f"{job['title'][:45]:<45}  {job['company'][:28]:<28}  {job['url']}"
        )


async def _apply(pipeline: JobAgentPipeline, args: argparse.Namespace, ids: list[str]) -> None:
    config = pipeline.config
    mode = args.mode or config.apply.mode
    ids = ids[: config.apply.max_applications_per_run]
    if not ids:
        print("Nothing to apply to: tailor some jobs first.")
        return
    _print_jobs([pipeline.ledger.get(job_id) for job_id in ids])
    if mode == "auto" and not args.yes:
        if not await ask_yes_no(f"Auto-submit up to {len(ids)} application(s) as listed?"):
            return
    applier = Applier(config, pipeline.root, pipeline.ledger)
    for job_id in ids:
        try:
            await applier.apply(job_id, mode)
        except Exception as error:  # one broken form must not stop the batch
            print(f"  ! {job_id}: {error}")


def _tailored_ids(pipeline: JobAgentPipeline, args: argparse.Namespace) -> list[str]:
    if getattr(args, "job", None):
        return args.job
    return [job["id"] for job in pipeline.ledger.ranked(JobStatus.TAILORED)][: args.top]


async def _main(args: argparse.Namespace) -> None:
    config = AgentConfig.load(args.config)
    pipeline = JobAgentPipeline(config, args.workspace)
    try:
        if getattr(args, "resume", None):
            pipeline.import_resume(args.resume)
        if args.command == "ingest":
            await pipeline.ingest()
        elif args.command == "profile":
            await pipeline.profile()
        elif args.command == "discover":
            await pipeline.discover(args.role)
            _print_jobs(pipeline.shortlist())
        elif args.command == "list":
            statuses = [JobStatus(args.status)] if args.status else []
            _print_jobs(pipeline.ledger.ranked(*statuses))
        elif args.command == "overleaf":
            print(f"Open {pipeline.write_overleaf_index()} in a browser logged in to Overleaf.")
        elif args.command == "tailor":
            if args.job:
                for job_id in args.job:
                    await pipeline.tailor(job_id)
            else:
                await pipeline.tailor_top(args.top)
        elif args.command == "apply":
            await _apply(pipeline, args, _tailored_ids(pipeline, args))
        elif args.command == "run":
            await pipeline.ingest()
            if not pipeline.profile_path.is_file() or args.resume:
                await pipeline.profile()
            await pipeline.discover()
            tailored = await pipeline.tailor_top(args.top)
            if not args.skip_apply:
                await _apply(pipeline, args, tailored)
    finally:
        await pipeline.aclose()


def main() -> None:
    asyncio.run(_main(_parser().parse_args()))


if __name__ == "__main__":
    main()
