# Remaining gaps and the plan to close them

Date: 2026-11-06. Sources: `audit-3-production-readiness.md`, `cli-review-2026-11-06.md`, and the
CI run for commit `3ea284d`. Effort is for one person who knows the code: S is under a day, M is
one to three days, L is a week or more.

## CI state

All jobs passed on `3ea284d` except **SDK tests on Windows with Python 3.13**. The same job passed
on Python 3.12, and both Ubuntu versions passed. I cannot read the job log without GitHub
credentials, so the failing test is not identified yet. A clean-checkout run of the whole SDK suite
on Windows 3.13 passed locally earlier, which points to a timing-sensitive test rather than a
Windows 3.13 incompatibility. Step 0 below finds out.

## Gaps

### Correctness and tests

| # | Gap | Evidence |
|---|---|---|
| G1 | SDK tests fail on Windows 3.13 in CI | run 42236723179, job 127956150198 |
| G2 | Two multiprocess wiki tests fail now and then under full-suite load, and pass alone | seen twice on Windows |
| G3 | Low coverage in `mcp_serve`, `plugins`, `worktree` | coverage report |
| G4 | 126 broad `except Exception` in the SDK (10 in the CLI) | grep |
| G5 | 45 SDK files are excluded from the type check | `pyproject.toml` |
| G6 | Real terminal behaviour (prompt menu, Ctrl+C, Esc, resize) is only tested with scripted input | no TTY test |
| G7 | macOS has no CI job; Seatbelt and the macOS and Linux clipboard paths never ran | CI matrix |

### Safety and isolation

| # | Gap | Evidence |
|---|---|---|
| G8 | No sandbox on Windows; commands need `--allow-unsandboxed` even when a rule allows them | review |
| G9 | Undoing command effects stops at 5,000 files or 100 MB (now reported, not removed) | `checkpoints.py` |

### Compatibility with Claude Code, Codex and OpenCode

| # | Gap |
|---|---|
| G10 | Hooks cannot rewrite a call's input, cannot answer `allow` to skip the approval prompt, and lack `PreCompact` and `SubagentStop` |
| G11 | No `--input-format stream-json`; the init event lists no tools |
| G12 | `edit` and `all` subagents run one at a time |
| G13 | `nailong -r` needs an id; no picker at launch |
| G14 | No `/rename`, themes or session sharing |
| G15 | Hooks and MCP shared in a project need each person to approve once (by design); no org-wide managed settings file |

### Models and providers

| # | Gap |
|---|---|
| G16 | Two model adapters; live runs only on one small model |
| G17 | No tokenizer, so context budgets are estimates |
| G18 | No provider-side prompt-caching strategy beyond pass-through |

### Distribution and operations

| # | Gap |
|---|---|
| G19 | Not on PyPI; no update path; the release workflow has never run for a real release |
| G20 | No `CHANGELOG.md`, `SECURITY.md` or `CONTRIBUTING.md` |
| G21 | Start-up takes about 1.25 s |
| G22 | State is files on one machine; a synced or shared folder is not detected |
| G23 | CI actions (`checkout@v4`, `setup-python@v5`) run on a deprecated Node version |
| G24 | One author, no outside users |

## Plan

Ordered so that each step makes the next one safer. Each step says how it is checked.

**Step 0: identify the CI failure (G1). Effort S.**
Ask for the failing test name from the job log (or a token with read access to the repository's
Actions logs). Meanwhile the timing-sensitive SDK directories are being looped on Python 3.13
locally. Then fix the test or the code it exposes. Also make CI print failures in the summary
(`-rfEs` is already set, so the log has them) and upload the pytest output as an artifact so the
next failure can be read without a login. Check: the job is green three runs in a row.

**Step 1: remove flakiness (G2, G1 if related). Effort M.**
Find out whether the wiki tests fail because of the test (six processes starting at once, a fixed
timeout) or the lock (waiting longer than its limit under load). Capture the worker's stderr in
the assertion message, run the test in a loop under artificial CPU load, and fix the cause. Check:
200 looped runs with no failure.

**Step 2: ship it (G19, G20, G23). Effort S to M.**
Add `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`. Bump the CI actions. Run the release workflow
on a tag to a test index first, then to PyPI once you decide to publish. Add a startup version
check that is off by default and honours `--offline`. Check: a clean machine installs the wheel
and `nailong --version` works.

**Step 3: make undo reliable in big projects (G9). Effort M.**
Enumerate files with `git ls-files --cached --others --exclude-standard` when the project is a git
repository, which skips ignored build output and raises the practical ceiling a lot. Raise the
limits only where they were protecting against ignored folders. Keep the explicit notice for the
cases that still exceed it. Check: the 5,200-file probe now restores a command-created file.

**Step 4: safer defaults where a sandbox exists (G8). Effort M.**
On Windows, research and add a Job-Object plus restricted-token runner, or make WSL or Docker the
documented default and say so at start-up. Where a rule explicitly allows a command and the
person passed `--permission-mode bypass`, keep the flag requirement; where only a rule allows it,
offer to remember the unsandboxed choice per project once. Check: the sandbox tests run on a
Windows CI job.

**Step 5: hook parity (G10, G11). Effort M.**
Add `updatedInput` rewriting (the tool call's arguments replaced before approval), the `allow`
decision as "skip the prompt but still apply deny rules", `PreCompact` and `SubagentStop`. Add
`--input-format stream-json` so another program can feed messages, and list tools in the init
event. Check: tests that replay the documented Claude Code hook and stream examples.

**Step 6: test what scripted input cannot (G6, G7). Effort M.**
Add a pseudo-terminal test layer (`pywinpty` on Windows, `pty` on POSIX) for the prompt, Ctrl+C
and Esc. Add a `macos-latest` CI job for the CLI tests and the Seatbelt path. Check: the jobs run
in CI.

**Step 7: widen model coverage (G16, G17, G18). Effort L.**
Add a scheduled workflow with a small live-test set against three models of different strength,
keeping transcripts as fixtures. Add provider-specific token counting where the provider offers
it. Add cache-control markers for Anthropic. Check: the nightly job is green for a week and its
cost is bounded by a budget flag.

**Step 8: pay down the code debt (G3, G4, G5). Effort L, in small pieces.**
Take ten modules at a time: narrow each broad `except Exception` to the errors that can happen
and add the test that provokes them, bring the module off the type-check debt list, and raise
coverage on `mcp_serve`, `plugins` and `worktree` to the CLI floor. Check: the debt list and the
count of broad handlers fall every week, and coverage has a floor in CI.

**Step 9: smaller features (G12 to G15). Effort S each.**
`-r` with no id opens the picker; `/rename`; parallel `edit` subagents only for disjoint file
claims (the claims store already exists); an optional managed settings file for organisations.
Check: a test for each.

**Step 10: users (G22, G24). Ongoing.**
Detect a synced or network folder for `.nailong` and warn. Find three people to use it for real
work for a month and fix what they hit. This is the step that decides whether it is production
grade; nothing above replaces it.

## Order and dependencies

Steps 0 and 1 first, because a red CI hides everything else. Steps 2 and 3 are independent and
cheap, so do them next. Step 4 and 5 can run in parallel with each other. Step 6 should land
before Step 7 so live runs have a tested terminal. Step 8 runs alongside everything after Step 1.
