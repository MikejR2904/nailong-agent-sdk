# Second audit and comparison with OpenCode, Claude Code, Codex and Manus

Written 2026-11-06 on branch `nailong-cli` (HEAD `4e752ec` plus 190 uncommitted changes). It follows
the first audit and its response (`audit-response-2026-11-06.md`). What I ran is listed in section 1;
everything else in sections 2 to 4 comes from reading the code or from the competitor sources named
in section 5, and is marked when it is only that.

## 1. What was measured

| Fact | Value | How |
|---|---|---|
| SDK source | 236 files, 63,640 lines | `find`, `cat | wc -l` |
| CLI source | 84 files, 16,260 lines | same |
| Tests | SDK about 59,100 lines; CLI about 10,100 lines; CLI suite 727 passed, 3 skipped (live tests, opt-in) | pytest run today |
| Lint | `ruff check` and `ruff format --check` clean for the SDK and the CLI | run today |
| SDK suite | passed in the last full run (excluding `tests/trust_bench`, whose optional packages were removed from the root environment, and the live-model benchmark tests) | background run today |
| Type checker | none configured (no mypy or pyright in `pyproject.toml` or CI) | grep |
| Coverage | not measured (`coverage` is not installed) | import check |
| Start-up | about 2 s for `--version`; the SDK's `starter` import is 1.35 s, `mcp` 0.6 s of it | `-X importtime`, `docs/benchmarks-cli.md` |
| Largest modules | `agent/base_agent/agent.py` 2,810 lines; `memory/episode_store.py` 2,267; `state/graph.py` 1,694; CLI `cli.py` 942 | `wc -l` |

## 2. Findings

Severity is my judgement of the risk to someone relying on this software.

### High

**H1. The CLI is not in CI.** `.github/workflows/ci.yml` lints `src tests` and runs the root `tests/`
only. `apps/nailong_cli` (16,000 lines, 727 tests, every feature added in the last month) is neither
linted nor tested by CI, and its dependencies are never installed there. A regression in the CLI would
reach the branch unseen. Fix: add a job that installs `apps/nailong_cli` and runs its suite on Linux
and Windows.

**H2. Weeks of work exist only as uncommitted changes.** 190 files differ from `4e752ec`: pins,
events, plan journal, wiki revisions, discoveries, backup and migration, the audit fixes, tests and
docs. Nothing was reviewed, pushed or tagged. One disk failure loses all of it. Fix: commit in
reviewable pieces and push.

**H3. Commands run unsandboxed by default, and on Windows only Docker can sandbox them.** The
`sandbox` setting is `auto`, `none` or `docker`; `auto` uses bubblewrap or Seatbelt where present and
nothing on Windows. The CLI states "NOT SANDBOXED" and gates commands behind approvals, which is
honest but is the weakest default among the tools compared (section 4). None of the container or OS
sandboxes was exercised in this audit (the Docker engine was not running; no Linux run).

**H4. Nothing here has been shown to work against a real model service.** All 727 CLI tests use
scripted models. `tests/live` exists, is opt-in and has never been run; the SDK's live benchmark
tests were already failing because the Fireworks account is suspended (HTTP 412). Tool-call parsing,
streaming, the prompted-tools fallback, retries and cost accounting are therefore verified against
what the code expects of a provider, not against a provider. This is the largest unknown.

### Medium

**M1. Start-up is slow.** About 2 s before any work, mostly importing the SDK and the `mcp` package.
Short commands such as `--health` or `--version` pay it. Fix: import `mcp` and the document libraries
lazily; add a light entry shim for flags that need no agent.

**M2. The feature-gap document is out of date.** `feature-gap-analysis.md` lists 49 rows as missing;
at least these are built: shell completion, `/editor`, configurable key bindings, GitLab CI, deleting
sessions, instruction files in subdirectories, a local untracked instruction file, and hook events
beyond the six it names. Checked by searching the code. Not yet re-verified: the rest. A stale
comparison doc misleads more than none.

**M3. Maintainability of the largest files and the lack of static typing.** Four SDK modules exceed
1,500 lines; the SDK has 126 `except Exception` sites (most are probably boundary handlers, but that
is not reviewable at a glance) and no type checker enforces the annotations that exist.

**M4. Unit-level coverage is unknown and uneven.** 23 of 84 CLI modules are not imported by name by
any test (among them `turns`, `commands`, `compaction`, `checkpoints`, `worktree`, `mcp_serve`,
`telemetry_export`, `cost`, `modes`). Most are exercised through end-to-end runs of the whole CLI,
but without a coverage tool nobody can say how much.

**M5. Timing-dependent tests.** `test_typing_during_a_turn_...` failed once in a full run and passed
three times alone. On CI's mix of Windows and Linux runners such tests will flake.

**M6. Several overlapping records, no single view.** A turn leaves traces in the SDK's telemetry and
audit stores, the CLI event log, the session store, the plan journal, the wiki journal and the
discoveries journal. Each is sound; there is no one place to ask "what happened in this run". The
typed event log is the start of that, and the SDK's orchestrator does not emit into it.

**M7. Residual security items** (full list in `threat-model.md`): the external-editor launcher uses
`shell=True` with `$VISUAL`/`$EDITOR` (the user's own variable, low risk, but quoting is the only
protection); document parsing of untrusted files has size limits but no process isolation or time
limit; hooks, MCP servers and custom tools are trusted code.

**M8. Scale limits of file-based state.** The knowledge base rebuilds its search index on every call;
discovery publish re-reads its journal (19 ms at 200 entries, linear after); wiki search is linear;
file locks and dead-holder detection are single-machine (not safe on a network share).

**M9. Distribution.** Not published to PyPI; the `/ci` templates assume `pip install nailong-cli`,
which does not work today. Installation is `uv tool install --editable` from a checkout.

### Low

- State format versioning exists since today (version 2) with one migration; there is no
  forward-compatibility test across releases because there is only one release.
- Autocompact is on by default, so a long session spends extra model calls on summaries without a
  prompt; it is documented, not announced in the session.
- `fetch_document` and `web_fetch` share SSRF checks through a private SDK helper
  (`_http_get_public`) imported across packages.

### Things the audit looked for and did not find

No `eval`, `exec`, `pickle` or `yaml.load` in the SDK or CLI. No `TODO`, `FIXME` or `HACK` markers.
No secrets in the repository (`.env` is ignored). Project settings cannot enable hooks, MCP servers,
remote shells, a wider hook environment or the OTLP endpoint. The state locks, plan journal, wiki and
recorder outbox survived real process kills in tests.

## 3. Where nailong stands on its own terms

| Strength | Evidence |
|---|---|
| Tamper-evident records: hash-chained audit, telemetry and event logs; signed checkpoints; an independent recorder with a durable outbox | SDK observability, `events.py`, `test_events*.py`, `test_recorder_outbox.py` |
| Governed memory: pinned rules, wiki with revisions and staleness, discoveries with evidence and conflict handling, a knowledge base with source hashes | `pins.py`, `wiki.py`, `discoveries.py`, `memory_refs.py` |
| Work that survives a crash: durable team plans with resume, retries and per-file claims | `taskstate.py`, `claims.py`, kill tests |
| Operability: `--health`, backup, verify, restore with rollback, versioned migration | `health.py`, `state_ops.py` |
| Models without native tool calls (prompted tools), offline mode, many OpenAI-compatible hosts | `factory.py`, `config.py` |
| Document work built in: PDF, Word, Excel, PowerPoint reading, page-level vision, citations | `documents.py`, `knowledge.py` |

## 4. Comparison

Competitor entries come from the sources in section 5 and from the project's earlier comparison
documents; they are a snapshot, change weekly, and were not each verified by running the products.
"Yes" means the capability exists as described by the vendor or a third party; "partial" is my reading.

| Area | nailong (this repo) | Claude Code | Codex CLI | OpenCode | Manus |
|---|---|---|---|---|---|
| What it is | Python SDK plus a terminal CLI, pre-1.0, one author | Anthropic's terminal, IDE, desktop, web and Slack agent | OpenAI's Rust CLI, IDE, desktop and cloud agent | Open-source terminal agent, 75+ providers | Hosted general agent (now part of Meta) with cloud sandboxes |
| Models | Anthropic, OpenAI-compatible hosts, local servers, prompted tools for models without tool calling | Claude models (and cloud-provider routes) | OpenAI models, configurable providers | Very broad catalogue | Managed by the service |
| Default execution safety | Approvals; **no sandbox by default**, Docker optional, OS sandboxes untested here | Permission rules; optional OS sandbox | OS sandbox with approval policies by default | Permission config; no default OS sandbox that I could confirm | Everything runs in a cloud VM, not on your machine (browser operator and desktop app can reach your machine) |
| Permissions | modes, allow/ask/deny rules, remote approvals, per-agent allow lists | fine-grained rules, an automatic classifier mode | approval policy plus sandbox modes | per-tool config | task-level confirmation |
| Extensibility | skills (folders), 11 hook events, MCP, Markdown subagents, Python custom tools, local plugins | skills, many hook events, MCP, subagents, plugins and a marketplace | skills, MCP, plugins, hooks | plugins (npm), custom tools, MCP, agents | open-standard skills, connectors, project skills |
| Memory | pins, instruction files, wiki, discoveries, knowledge base (lexical search) | `CLAUDE.md` plus automatic memory | `AGENTS.md`, memories | `AGENTS.md` | projects, knowledge, skills |
| Audit and observability | strongest here: hash chains, signed checkpoints, independent recorder, OTLP export | OpenTelemetry and transcripts | OpenTelemetry and rollouts | session sharing, logs | task replay |
| Multi-agent | planner, team with retries and resume, background subagents | subagents, agent teams | multi-agent orchestration | parallel agents | parallel sub-agents ("Wide Research") |
| Long-horizon / background | `/plan` resume, schedules via OS cron, no resident worker | background and cloud sessions, `/loop`, scheduled routines | `/goal` (experimental), cloud tasks | server mode | cloud tasks that run while you are away |
| Surfaces | CLI, HTTP service with OpenAPI, ACP (unverified with an editor) | terminal, IDE, desktop, web, Slack, GitHub Action | terminal, IDE, desktop app, cloud, GitHub | terminal, desktop, IDE, server | web, desktop, mobile, browser extension |
| Browser / computer use | none (web fetch and search only) | browser integration and computer use | limited | none that I could confirm | browser operator, "My Computer" desktop access |
| Documents | PDF, Word, Excel, PowerPoint, CSV reading and a cited knowledge base built in | PDFs and images natively; Office files via skills | via skills or tools | via tools | strong (files, slides, sheets in the sandbox) |
| Distribution and polish | checkout install; start-up about 2 s; no auto-update | installer, auto-update, frequent releases (2.1.310 on 2026-10-23 per a third-party tracker) | npm, Homebrew, native; weekly releases (0.161.0 on 2026-10-07 per a tracker) | npm, Homebrew, AUR; 1.18.x in 2026-08 per a package mirror | hosted |
| Tested against real services | no (scripted models only) | yes, at scale | yes, at scale | yes, at scale | yes, at scale |

### Reading the table

**Behind, clearly.** Default isolation of commands; real-provider testing; surfaces (no IDE
extension verified, no desktop, no cloud, no mobile); browser and computer use; distribution and
update; startup time; ecosystem (plugins, marketplace, community, documentation by others); and the
sheer number of users who have found the bugs.

**Level or partial.** Skills, MCP, hooks, subagents and permissions all exist with fewer events,
fewer integrations and less polish than Claude Code or Codex. Memory and documents are broader in
function here than in the terminal tools, but retrieval is lexical only.

**Ahead, in specific narrow ways.** Evidence integrity (an independent recorder with signed
checkpoints is not something the others advertise), governed shared memory with provenance and
conflict handling, crash-safe plans with resume, an operations toolkit (health, backup, migration),
and use with models that cannot call tools. These matter for audit-heavy or regulated work and for
research on agent coordination; they do not make everyday coding faster.

**Manus is a different kind of product.** It is a hosted agent in a cloud computer. Comparing
sandbox safety, always-on tasks and browser operation to a local terminal tool is comparing different
deployment models; the useful lesson is its isolation-by-default and its persistence while you are
away, both of which this project lacks.

## 5. Sources for the competitor snapshot

- Claude Code releases: [Claude Code version tracker](https://www.havoptic.com/tools/claude-code), [the official release notes](https://docs.anthropic.com/en/release-notes/claude-code)
- Codex CLI: [Codex release tracker](https://www.havoptic.com/tools/openai-codex), [Codex timeline](https://www.scriptbyai.com/codex-timeline/), [/goal coverage](https://www.noqta.tn/en/news/openai-codex-cli-goal-autonomous-agentic-coding-2026)
- OpenCode: [package mirror](https://community.chocolatey.org/packages/opencode/1.0.27) (the vendor's own release notes were not retrieved)
- Manus: [Manus product blog](https://manus.im/blog/product), [Manus in 2026](https://codersera.com/blog/manus-ai-2026-status-meta-block-desktop-app/), [capabilities overview](https://fast.io/resources/manus-ai-agent/)

Several of these are third-party trackers and reviews; version numbers and dates conflicted between
them in places. Treat the competitor columns as a dated snapshot and verify against each vendor's own
documentation before relying on a single cell.

## 6. Follow-up, same day

Each recommendation below was acted on after the audit; this is what happened, not a re-grade.

| # | Recommendation | Outcome |
|---|---|---|
| 1 | Commit and push | Done: four commits on `nailong-cli`, pushed. The tree has been clean since. |
| 2 | CLI in CI | Done in `ci.yml`: lint, types, tests with coverage on two systems and two Python versions, wheel build and clean install. **Not yet run on GitHub**: the workflow was checked locally piece by piece (wheels built and installed in a clean environment; `mypy` and `ruff` clean), but a first push will show what differs on the runners. |
| 3 | Live model | Three live tests passed against `openai/gpt-4o-mini` via OpenRouter, and the exact arXiv task that failed earlier now completes (fetch, four reads, a six-sentence answer, 7 steps, $0.008). One model, one service. The summary did not cite page numbers although asked to. |
| 4 | Sandbox | Added `require_sandbox`. Did not change the default: `auto` already uses bubblewrap or Seatbelt where present, and auto-selecting Docker would add a daemon probe to every start and depend on an image being present. The Docker daemon would not start on this machine, so the container tests ran in CI configuration only; a best-effort bubblewrap job was added. |
| 5 | Start-up | 2.0 s to 1.25 s by loading `mcp` only when an MCP server is used. The rest is the SDK's pydantic model modules. |
| 6 | Gap document, types, coverage | Gap document refreshed (16 rows). The CLI is `mypy` clean (it had 106 errors, fixed, none of them a runtime bug: the `mcp` keyword complaints were false alarms). The SDK is `mypy` clean apart from 45 files with 191 errors listed in `pyproject.toml` as debt. CLI coverage is **89%** (10,124 statements); CI fails below 85%. The claim in section 2 that 23 modules are "not tested" was too strong: those modules are exercised through end-to-end runs; the lowest are `mcp_serve` 40%, `plugins` 46% and `worktree` 53%. |
| 7 | Publish | Both wheels build and run from a clean environment; a release workflow is written. **Not published**: that needs your PyPI account and project names. |
| 8 | Features | Added a resident scheduler (`--daemon`) and optional meaning-based knowledge search (`/kb embed`). **Not done**: browser or computer use (needs a browser engine this repository does not bundle), an IDE extension tested in a real editor, and the other missing rows listed in `feature-gap-analysis.md`. |

## 7. Recommended order (as first written)

1. Commit and push the work (H2). Same day.
2. Add the CLI to CI: install, lint, test on Linux and Windows (H1). Small, and it turns every other
   claim in this document into something that stays true.
3. Run the live-provider tests against one real model with a spending cap, and fix what breaks (H4).
4. Make sandboxing the default where it exists and run the Docker, bubblewrap and Seatbelt tests in CI
   (H3).
5. Cut start-up: lazy imports, a light entry for flags that need no agent (M1).
6. Re-verify and rewrite `feature-gap-analysis.md` (M2) and add a type checker and coverage to CI
   (M3, M4).
7. Publish the package so install and the CI templates work (M9).
8. Only then add features: browser or computer use, an IDE extension tested with a real editor, a
   resident background worker, a semantic retrieval option.
