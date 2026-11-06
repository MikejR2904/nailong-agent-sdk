# Audit 3: limits and production readiness (SDK and CLI)

Date: 2026-11-06. Scope: `src/nailong_agent_sdk` (236 files, about 63,600 lines) and
`apps/nailong_cli` (86 files, about 16,800 lines). Compared against Claude Code, Codex CLI and
OpenCode. The comparison with those three rests on their public documentation and behaviour as
known at the time of writing; nothing here was measured against their current releases.

## Verdict

**Not production-grade as a general product. Production-suitable for a supervised, single-user,
single-machine workload, which is what it has been tested for.**

Production-grade here means four things: it holds up under unsupervised or multi-user use, it has
evidence beyond its own test suite, a user can install and update it without the author, and a
failure leaves you able to diagnose it. The system is strong on the first and last, weak on the
second and third.

| Question | Answer | Evidence |
|---|---|---|
| Is the code correct and well covered? | Mostly yes | CLI suite: about 750 tests pass on Windows (one load-sensitive failure, item 7); CLI line coverage about 90% with a floor of 85%; SDK suite is large (3,400+ collected) |
| Is it safe by default? | Partly | Approvals, denied paths, SSRF-safe fetch and `require_sandbox` exist. The sandbox is not on by default on Windows, and shell commands run unsandboxed unless the user opts in |
| Is it proven against real models? | Barely | Live runs used one small model (`openai/gpt-4o-mini` through OpenRouter). Two model adapters exist. No tokenizer, so context budgets are estimated |
| Can someone else install and update it? | No | Not on PyPI, no auto-update, no signed releases, no changelog, no `SECURITY.md`, no `CONTRIBUTING.md` |
| Does it scale? | To a point | All state is files on one machine. Checkpoints cap at 5,000 files or 100 MB |
| Is it maintained beyond one person? | No | One author, no external users to report regressions |

## Limitations

Ordered by how likely each is to hurt a real user.

### A. Safety and isolation

1. **Commands run unsandboxed by default on Windows.** `require_sandbox` forces the question, but
   the default is to ask the person. Claude Code and Codex ship OS-level sandboxes on by default
   (Seatbelt and Landlock/bubblewrap for Codex). OpenCode has none and relies on permissions, so
   it is no worse than OpenCode here, but behind the other two.
2. **The approval gate is the main control.** A model that talks the person into approving, or a
   person who approves everything, defeats it. The refusal reasons added this week help the model
   stop retrying; they do not make an approved command safe.
3. **Prompt injection is mitigated, not solved.** Fetched pages and files are labelled as data and
   the denied-path list blocks obvious secret locations, but nothing detects an injected
   instruction. Same position as the three competitors; none of them solves it either.
4. **User-only settings guard the dangerous switches** (hooks, MCP, ssh hosts, offline, OTLP,
   `require_sandbox`). A project file cannot set them. This is stronger than OpenCode, comparable
   to Claude Code.

### B. Correctness and robustness

5. **`/rewind` cannot undo command effects in large projects.** Found and fixed in this audit: the
   tree snapshot silently gave up past 5,000 files or 100 MB, and `/rewind` then said "Nothing to
   rewind". It now records the gap and says so. The limit itself remains; undoing a command in a
   large repository is still not possible. Claude Code and Codex lean on git for this. Using
   `git ls-files` to enumerate would raise the practical ceiling and is not done yet.
6. **126 broad `except Exception` handlers in the SDK** (10 in the CLI). Each can hide a real
   failure behind a generic path. The SDK also has 45 files on the type-check debt list, so
   mypy gives no assurance there.
7. **Concurrency tests are load-sensitive.** `test_processes_writing_different_wiki_pages_at_once_lose_none`
   failed once in a full-suite run on Windows and passed alone twice. Either the test's timing
   assumption or the lock's behaviour under load is wrong; this has not been resolved.
8. **Low coverage in `mcp_serve`, `plugins` and `worktree`.** These are the least-exercised
   modules and each starts child processes or touches git.
9. **State is files, not a database.** Locks, atomic writes and dead-holder takeover cover one
   machine. Two machines sharing a project through a network drive, or a synced folder such as
   OneDrive, are not supported and not detected.

### C. Model and provider coverage

10. **Two model adapters.** Claude Code is tied to Anthropic models; Codex to OpenAI; OpenCode
    supports dozens of providers. Here the breadth is narrower than OpenCode and wider than the
    other two only through the OpenAI-compatible route.
11. **One small model was exercised live.** Behaviours such as refusal retry loops and
    unanswerable-question loops were discovered only because a live run happened. A larger model
    and a weaker one will differ. No continuous live testing exists.
12. **No tokenizer.** Budgets and autocompact use estimates, so a limit can be hit earlier or later
    than shown.
13. **No provider-side caching or batching strategy** beyond what the adapter passes through.

### D. Distribution and operations

14. **Not published to PyPI.** Install is from a clone (`uv tool install --editable`). A wheel
    builds and the release workflow exists; it has not been run for a real release.
15. **No update path** (no self-update, no version check, no changelog, no migration notice for
    users). State versioning and `migrate` exist for data, not for the program.
16. **No `SECURITY.md`, `CONTRIBUTING.md` or `CHANGELOG.md`.** Only `LICENSE`.
17. **Start-up is 1.25 s**, against sub-second for the compiled competitors (Codex is Rust,
    OpenCode runs on Bun). Acceptable, noticeable.
18. **Windows is the development platform; Linux is verified in CI, macOS is not.** No macOS runner
    and no sandbox verification there.
19. **Install size is large** (the user decided not to reduce it).

### E. Product features the competitors have

20. No IDE integration beyond ACP; Claude Code and OpenCode have mature editor extensions.
21. No hosted or cloud task runner; Codex and Claude Code both offer one.
22. No built-in image or PDF understanding path comparable to Claude Code's.
23. No marketplace; plugins are local.
24. No telemetry on real-world failure, by design, so field problems reach the author only when
    reported.

## Comparison

| Area | Claude Code | Codex CLI | OpenCode | This system |
|---|---|---|---|---|
| Default OS sandbox | Yes | Yes | No | No on Windows; opt-in |
| Models | Anthropic | OpenAI | Many | Two adapters |
| Undo of file edits | Yes | Yes (git) | Yes (git) | Yes, snapshot with a size cap |
| Undo of command effects | Limited | Git based | Git based | Snapshot, capped, now reports the cap |
| Governed memory with provenance | Instruction files | Instruction files | Instruction files | Pins, wiki with revisions, discoveries, KB: the strongest part |
| Typed audit log, hash-chained, trace ids | No | Partial | No | Yes |
| Durable resumable plans | Partial | Partial | No | Yes |
| MCP | Yes | Yes | Yes | Yes (client and server) |
| Published, updated, supported | Yes | Yes | Yes | No |
| Field-tested by many users | Yes | Yes | Yes | No |

Where this system is ahead: auditability, memory governance, plan durability, and the user-only
settings boundary. Where it is behind: everything that depends on scale of use, which is
sandbox defaults, provider breadth, packaging and support.

## What would make it production-grade

In order of value:

1. Publish a release, add `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, and a version check.
2. Run live tests against at least three models of different strength on a schedule, and keep the
   transcripts as regression fixtures.
3. Make a sandbox the default where one exists, and say plainly on Windows that none is active.
4. Enumerate files through git in the checkpoint store, then re-measure the large-repository case.
5. Resolve the load-sensitive concurrency test by finding out whether the fault is in the test or
   the lock.
6. Burn down the SDK type debt and the broad exception handlers, starting with modules that touch
   the file system or subprocesses.
7. Add a macOS CI job.
8. Get a few outside users to run it for real work for a month and fix what they hit.

Items 1 to 4 are a few days of work. Item 8 is the one that actually decides the question, and
no amount of in-repo work substitutes for it.

## Fixed during this audit

- `/rewind` no longer claims "nothing changed" when a command ran in a project too large to
  snapshot; it says so (`checkpoints.py`, `workflow_commands.py`, with a test).
- CLI type check is clean when mypy targets Linux as well as Windows (platform checks use
  `sys.platform`; the process-group flag is read with `getattr`).
- Three tests no longer assume a `python` executable or Windows-only `shutil` behaviour, so they
  pass on Linux.
