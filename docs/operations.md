# Running nailong: install, health, backup, upgrade and rollback

This is for whoever keeps a nailong setup running for other people or for scheduled work. Every
command here was run against the code in this repository; what could not be run is listed at the end.

## What to keep

Everything the agent remembers about a project lives in plain files under `<project>/.nailong/`:

| Path | What it is | In a backup |
|---|---|---|
| `config.toml`, `rules/` | project settings and rules | yes |
| `wiki/` (with `.history/`, `.journal.jsonl`) | pages, every revision, who wrote what | yes |
| `discoveries/journal.jsonl` | published, accepted and rejected findings | yes |
| `knowledge/trees.json` | the knowledge-base index (rebuildable with `/kb refresh`) | yes |
| `tasks/` | team-plan journals, so a plan can be resumed | yes |
| `events/` | typed event logs, one per run | yes |
| `claims/` | who is editing which file right now | yes (only live claims matter) |
| `checkpoints/`, `workspace/` | undo snapshots, downloads, rendered pages, telemetry and audit stores | no (large, regenerable; keep `workspace/` if you rely on its audit and telemetry records) |
| `state-version.json` | the version of the layout above (currently 2) | written on restore |

Your user settings, keys and session history are outside the project (`~/.config/nailong`,
the credential store) and are not touched by these commands.

## Install

```bash
uv tool install --editable apps/nailong_cli    # from a checkout; `nailong` is then on PATH
nailong --version
```

Upgrade with `git pull` (an editable install follows the code) or reinstall. After any upgrade,
run `nailong --health` and, if it says the state is older, `nailong --migrate`.

## Health

```bash
nailong --health --project /path/to/project            # text
nailong --health --project /path/to/project --output json
```

Exit code 0 means ok or only items needing attention; 1 means something is broken (an event log
whose chain does not verify, or unreadable memory). Run it from cron or Task Scheduler and alert on a
non-zero exit. In a session, `/health` prints the same report and `/events check` verifies the logs.

What it reports: event logs (chain intact, spans never ended), unfinished plans, stale wiki pages and
discoveries, conflicting discoveries, changed knowledge sources, how much of the wiki and the
discoveries is backed by evidence that still holds, claims held, and the size of the event and
workspace stores. A process that died leaves event spans open; the next start closes them as
"interrupted" and says so.

Recorder delivery: if you embed the SDK's `RecorderMirror`, `mirror.health()` returns `ok`,
`behind` (records waiting) or `degraded` (a ledger is no longer mirrored), the counts, whether
delivery is durable, and the last error. Give the mirror an `outbox=` path so a crash or an outage
loses nothing, and call `mirror.recover()` at start-up.

## Backup

```bash
nailong --backup /backups/project-2026-11-06.zip --project /path/to/project
nailong --verify-backup /backups/project-2026-11-06.zip
```

A backup is one zip with a manifest of every file's SHA-256 and the state version. It refuses to
overwrite an existing file. `--verify-backup` checks every file against the manifest; run it after
copying a backup anywhere. Take a backup before every upgrade and on a schedule (the state is small:
text and JSON). Back up while no session is writing if you need an exact point in time; files are
written atomically, so a backup taken during a session is consistent per file, and the journals
tolerate a last line cut off by a copy.

## Restore and rollback

```bash
nailong --restore /backups/project-2026-11-06.zip --project /path/to/project
```

Restore verifies the backup first (a damaged one is refused and nothing changes), refuses a backup
from a newer program, saves what it is about to replace to `<state>-rollback-<time>.zip` next to
the project, then restores. To undo a restore, restore that rollback zip. Do not restore while a
session is running in the project.

## Migrate

```bash
nailong --migrate --project /path/to/project
```

A session on state older than the program says so and still runs. `--migrate` takes a backup
(`...-before-migration-<time>.zip`), upgrades step by step (version 1 to 2 gives the knowledge index a
revision and gives existing wiki pages a revision 1 in their history), and writes
`state-version.json`. If a step fails, the state is put back exactly from that backup and the error
names the version and the cause. State written by a newer program is never changed: upgrade nailong
or restore an older backup.

## Running unattended

`nailong --run-schedules` from cron or Task Scheduler runs what is due. Where installing a timer is
not wanted, `nailong --daemon` stays running instead: it checks the schedule every `--daemon-interval`
seconds (60 by default, 10 at least) and runs due prompts exactly as `--run-schedules` does.
`--daemon-status` prints its status file (running or not, checks made, the last lines it produced) and
`--daemon-stop` asks it to finish its current check and exit. Only one daemon runs at a time; a status
file left by a dead process is ignored. It does not resume unfinished `/plan` runs by itself, because
those need a person to answer approvals.

Set `require_sandbox = true` in your user settings on any machine where commands must never run
unisolated: every command tool is then refused whenever no sandbox is active, whatever the mode or
the allow rules.

## Packaging and CI

CI (`.github/workflows/ci.yml`) lints and type-checks the SDK and the CLI, runs the SDK tests and the
CLI tests with coverage on Linux and Windows under Python 3.12 and 3.13, builds both wheels and installs
them into a clean environment to run `nailong --version` and `nailong --health`, and runs the Docker
sandbox tests (and, best effort, the bubblewrap ones). `release.yml` publishes the SDK and then the CLI
to PyPI on a `v*` tag through PyPI trusted publishing; it needs a trusted publisher configured on PyPI
for each project, and the project names `nailong-agent-sdk` and `nailong-cli` to be yours there. Nothing
has been published.

## Deploying to a team

- Install per user with `uv tool install`; give each user their own keys through the credential
  store or environment, never in the project.
- Commit `.nailong/config.toml`, `rules/`, `agents/` and `skills/`; do not commit `events/`,
  `workspace/`, `checkpoints/`, `knowledge/` or `claims/` (the repository's `.gitignore` already
  lists them). Commit or back up `wiki/` and `discoveries/` deliberately, since they are project
  knowledge.
- Hooks, MCP servers, `ssh_hosts`, `hook_env`, `offline` and the OTLP endpoint can only come from
  each user's own settings, not from a project.
- `nailong --serve` for a shared service: set `NAILONG_SERVER_TOKEN` (24+ characters), keep it on a
  loopback or private address, and choose modes with care (`default` plus `approvals` makes every
  tool ask the connected client).
- Run `nailong --health` on a schedule per project and `/cleanup` for old audit and telemetry runs.

## Incident notes

| Symptom | First step |
|---|---|
| "unfinished plan(s)" at start | `/plan list`, then `/plan resume` or `/plan abandon ID` |
| "closed N unfinished event span(s)" | a run was killed; nothing to do, `/events check` shows it was accounted for |
| a page marked stale | `/stale`; re-read the cited file, update the page with `wiki_write` (pass `sources` again) |
| `Another writer holds the wiki lock` | a writer is mid-write; a dead writer's lock is taken over at once, a live one's after 60 s without progress |
| `--health` broken: "does not match its hash" | an event log was edited or damaged; restore the logs from a backup, keep the damaged file as evidence |
| a task escalated | `/plan show ID` for the reason; fix the cause; `/plan resume ID` |

## Not verified

- The three tests in `tests/live` passed once on 2026-11-06 against `openai/gpt-4o-mini` through OpenRouter
  (a file read by tool, a PDF read with a page citation, and a complete event log), and the CLI fetched and
  summarised an arXiv paper for under a cent. That is one model on one service; other providers, streaming
  paths and long runs are untested.
- Docker, bubblewrap and Seatbelt sandboxes were not exercised on the machine used (Docker Desktop
  was started but its engine did not come up; Linux isolation was not run). CI runs the Docker tests.
- Restore, migration and backup were tested on Windows with synthetic state, not on a large or
  years-old project; there is no tested upgrade path from state version 0 (before wiki revisions
  existed, version 1 is the oldest supported).
- Startup takes about two seconds (see `docs/benchmarks-cli.md`).
