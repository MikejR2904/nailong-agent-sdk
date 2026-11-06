# Response to the 6 November 2026 codebase audit

The audit (an outside review of branch `nailong-cli` at `4e752ec`, source and test structure only)
made claims about the code. Each one was checked against the repository before anything was
changed. This page says what held, what was fixed, and what is still open. Nothing here claims the
project is production-ready; the audit's own verdict (not ready for unattended production) stands.

## Claims checked and what was done

| Audit finding | Checked | Result |
|---|---|---|
| Wiki write, log and index are separate, non-transactional steps | True | Fixed. Every write is a numbered revision under `.history/`, written before the page; the page, journal, log and index are replaced atomically, and an interrupted write is repaired from the history by the next write or by `/wiki repair`. |
| Two agents can overwrite the same wiki page | True | Fixed. An update must name the revision it read (`base_revision`); a stale one is refused naming the current revision. A cross-process lock serialises writers; a lock left by a dead writer is taken over after 60 s. Six threads racing on one base revision: exactly one wins (tested). |
| Wiki log is not an evidence ledger | True | Improved. `.journal.jsonl` records revision, author, reason, content hash and the files cited; every previous revision is kept. It is still a local file, not signed. |
| Stale knowledge is detected but nothing is invalidated | True | Fixed for the wiki. A page names the files it was written from (`sources`); when one changes or goes, the page reads as `stale` (`wiki_read`, `/wiki`). A knowledge-base search already dropped passages whose source changed. |
| No shared, governed discovery memory; the wiki can become an informal source of truth | True | Added `/discoveries` and `discovery_*` tools (see below). |
| Hook commands inherit the whole environment | True | Fixed. Hooks get a short allowlist (`PATH`, `HOME`, temp folders and similar) plus `NAILONG_*`; anything else must be listed in `hook_env` (user settings only; a project cannot widen it). |
| Hook failure policy is implicit | True | Fixed in part. A `pre_tool` or `prompt_submit` hook blocks on failure by default; `on_failure = "warn"` makes it advisory. A timeout is reported as a timeout. Post-tool hooks still cannot undo what ran. |
| Async recorder mirror can lose queued records | True | Fixed. See below. |
| `flush()` could return while a record was still being sent | Found while checking | Fixed: it waited for an empty queue, not for the in-flight item. It now waits for every queued item to finish. |
| CLI team mode runs one task at a time | True (`max_parallel=1` in `/plan`) | Not changed. The guide already says team runs go one task at a time; parallel team execution needs safe concurrent artifact editing first. |
| CLI `TodoList` is in-memory | True | Not changed. It is a display checklist, not task state. |
| Generated runtime state is tracked in git | True: 130 files under `apps/nailong_cli/.nailong/` | Untracked and ignored (`.gitignore`). The files stay on disk. |
| `HeuristicEntailmentScreen` can miss contradictions; "verified" is one flag | Not examined | Open. Separate assurance levels for summaries are a good idea and not done. |
| Retrieval is lexical and re-indexed on every search | True | Open, by design for now (reproducible baseline). |
| CI, live providers, Docker sandbox not qualified | Cannot be checked from here | Open. |

## What was added

**Wiki revisions (`apps/nailong_cli/nailong_cli/wiki.py`).** `wiki_read` returns the revision, the
cited sources and a `stale` flag. `wiki_write` takes `base_revision` (required to update) and
`sources` (up to 10 project files, hashed at write time; credential-style paths refused; left out
on an update it keeps the earlier list and its staleness). `/wiki history PAGE` lists revisions with
author and reason; `/wiki repair` rebuilds from the history; `/wiki` marks stale pages.

**Discoveries (`discoveries.py`, `/discoveries`).** An agent publishes a claim on a topic with
evidence: project files, optionally their exact words (checked against the file) and a location.
The file's hash is recorded. A subagent can publish and read; it cannot decide. Two different claims
on one topic are shown as a conflict on both. A person approves `discovery_accept`, and it must name
every conflicting discovery it replaces, so no claim wins silently. A discovery whose evidence
changed or vanished is stale and cannot be accepted until it is published again from the new text.
Accepted, non-stale discoveries are listed in the agent's instructions (bounded to 2,000
characters, read when the agent is built). Planning mode can read them but not publish or decide.
The store is an append-only journal under `.nailong/discoveries/`, guarded by the same cross-process
lock; a journal line cut off by a crash is ignored, damage elsewhere is named.

**Recorder outbox (`RecorderMirror(outbox=Path)`).** Each record is written and flushed to a local
outbox before it is queued or sent, and acknowledged after the recorder accepted it. After a crash
or a recorder outage, `recover()` resends what was never acknowledged (the recorder already treats
an identical record as a duplicate), `unacknowledged()` says how many are waiting, and `backfill`
acknowledges the run it re-sent. A truncated last line is ignored; damage in the middle raises
`RECORDER_OUTBOX_CORRUPT`. The CLI does not turn this on by itself: the mirror is built by whoever
embeds the SDK, so pass `outbox=` there.

**Hooks.** `hook_env` (user settings) and `on_failure = "block" | "warn"` per hook; see the guide.

## Phases 1 and 2 of the audit's roadmap (built after the first pass)

| Roadmap item | State |
|---|---|
| Unified memory identity and scope | Built in the CLI: `file:`, `kb:`, `wiki:`, `discovery:` references, one fingerprint per kind, `private` / `shared` / `authoritative` scopes, and a rule that only the main agent or the user writes authoritative pages. The SDK's `SharedSubstrateSnapshot` models are still separate. |
| Shared discovery publication; evidence-based acceptance; conflict detection | Built (`/discoveries`). Conflicts compare claim text on one topic; contradictions in meaning on different topics are not detected. |
| Versioning and stale-data invalidation | Built: wiki revisions; pages cite files, knowledge-base sources, discoveries and other pages, and go stale through the chain; knowledge-base hits from changed files are marked. `/stale` reports it. |
| Atomic wiki and knowledge updates | Built: both write under a cross-process lock, replace files atomically and keep a revision; the knowledge base re-reads before each change (six threads and four separate processes adding at once keep every file). |
| Durable task state | Built for `/plan` team runs (`.nailong/tasks/`); not for single-agent turns, which are sessions. |
| Durable recorder outbox | Built, opt-in (`RecorderMirror(outbox=...)`); a process killed with records queued loses none (tested with a real kill). |
| Idempotent task execution | Built for team tasks: a committed result is replayed under the same key, never run again; a changed task or upstream result gets a new key and reruns. Side effects of a task cut off mid-way cannot be undone; the rerun is told to check them. |
| Recovery after abrupt termination | Built for plans (resume, a dead process's lock taken over; tested by killing a real process), the wiki, discoveries, knowledge base and recorder outbox. Not built for anything else the SDK runs. |
| Safe parallel artifact editing | Built as per-file claims (`team_parallel` up to 4; background subagents too). It stops two tasks writing one file; it does not merge edits or prevent a task reading a file mid-change. |
| Bounded retries and escalation | Built for team tasks (`task_attempts`); an exhausted task is escalated to you and the plan stays resumable. |

## Phases 3 and 4 (built after that)

| Roadmap item | State |
|---|---|
| Unified typed lifecycle events | Built in the CLI (`events.py`, `.nailong/events/`): 15 fixed event types with required payload fields, session/turn/tool/plan/task spans, point events for memory and plan retries, and a hash chain per log. The SDK's own orchestrator and hooks do not emit into it. |
| Cross-agent parent-child trace propagation | Built inside the CLI: a tool call nests under its turn or its plan task, tasks under their plan, memory events under the tool call that caused them; W3C trace and span ids throughout. It does not propagate a `traceparent` to the model service. |
| Missing-event detection | Built: `check` finds a start with no end, an end with no start, a missing parent, a sequence gap, a broken chain and an edited line; a dead run's open spans are closed as interrupted and said so. It cannot know about an event that was never emitted. |
| Recorder delivery health | Built in the SDK (`RecorderMirror.health()`: ok, behind, degraded; counts; durable or not). The CLI does not run a recorder, so `/health` does not show one. |
| Provenance coverage metrics | Built (`/health`): share of wiki pages with sources, share of citations still current, stale pages, accepted-but-stale discoveries, share of evidence with a verified quote. |
| Operational health dashboards | A text and JSON report, not a graphical dashboard: `/health`, `--health` (exit code for schedulers). |
| Multi-process concurrency tests | Built for the wiki, discoveries, knowledge base, plan lock and claims (real child processes). |
| Crash-injection tests | Built with real killed processes for the plan journal, the recorder outbox and a wiki write cut between its history and its page. Found and fixed: a killed writer's lock blocked the next writer for 60 s; on Windows a waiter could make a lock release fail. |
| Live-provider integration tests | Written (`tests/live`, three tests, opt-in, budget-capped) and **not run**: they cost money and need your key. |
| Security and sandbox evaluation | Threat model plus 36 tests (`docs/threat-model.md`). Found and fixed: credential-file names missing from the denied list, no unpacked-size limit on Office files. The Docker, bubblewrap and Seatbelt sandboxes were **not** exercised. |
| Performance and cost benchmarks | Run (`docs/benchmarks-cli.md`). Found and fixed: a wiki write cost 225 ms at 300 pages because it rebuilt the index and log; it is now flat near 45 ms. Open: two-second start-up, dominated by importing the SDK and `mcp`. |
| Deployment, backup, migration and rollback procedures | Built and tested: `--backup`, `--verify-backup`, `--restore` (saves a rollback), `--migrate` (backs up first, restores on failure), a state version file, and `docs/operations.md`. |

Still not done from the roadmap: running the live-provider tests; exercising the Docker, bubblewrap
and Seatbelt sandboxes; multi-process tests of the SDK's own shared state; a recorder wired into the
CLI; a graphical dashboard.

## Open, in the audit's priority order

1. Shared memory beyond the CLI: the SDK's `SharedSubstrateSnapshot`/`ExploratoryDiscovery` models
   and the CLI's discoveries are separate; routing a discovery to affected tasks, and promoting an
   accepted discovery into a wiki page automatically, are not built.
2. Recovery and idempotency for workflows other than `/plan` team runs (single-agent turns,
   the SDK orchestrator), and cross-process tests of the SDK's own shared state.
3. Making the SDK's orchestrator and hooks emit into the typed event log, and propagating a
   `traceparent` to the model service.
4. Assurance levels for summary verification instead of one boolean.
5. Running the live-provider tests, evaluating the container and OS sandboxes, and load tests with
   many users (none was run for this response).
6. Merging concurrent edits (claims only prevent them), and a durable task state behind the
   checklist.
7. A semantic retrieval backend alongside the deterministic one.
