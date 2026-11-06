# Using the `nailong` CLI

A coding agent in your terminal. It reads your project, proposes changes, and asks before it writes
a file or runs a command. This guide is task-ordered; the reference for every flag is
`nailong --help`, and for the design see [interactive-cli.md](interactive-cli.md).

**Honest status.** The CLI is tested end to end with scripted models and, in short runs, against
one cheap hosted model. It has **not** been run against a real self-hosted model, and the
bubblewrap, Seatbelt, background-job, worktree and full-screen prompt paths have not been exercised
in a real terminal. Read [Known limits](#known-limits) before relying on it.

## Contents

1. [Install](#1-install)
2. [Configure a model](#2-configure-a-model)
3. [First run](#3-first-run)
4. [Talking to the agent](#4-talking-to-the-agent)
5. [Permission modes and approvals](#5-permission-modes-and-approvals)
6. [Sandboxing commands](#6-sandboxing-commands)
7. [Sessions: resume, fork, name, search, export](#7-sessions)
8. [Undo: `/rewind`](#8-undo-rewind)
9. [Slash commands](#9-slash-commands)
10. [Command-line flags](#10-command-line-flags)
11. [Settings file reference](#11-settings-file-reference)
12. [Extending it: instructions, commands, skills, hooks, MCP, subagents, plugins](#12-extending-it)
13. [The agent writing its own tools (`--forge`)](#13-forge)
14. [Scripts and CI (`-p`)](#14-scripts-and-ci)
15. [Self-hosted models and offline mode](#15-self-hosted-models-and-offline-mode)
16. [Cost, budget and effort](#16-cost-budget-and-effort)
17. [Troubleshooting](#17-troubleshooting)
18. [Known limits](#known-limits)

---

## 1. Install

The CLI is a separate package in `apps/nailong_cli` that depends on the SDK in this repository.
It needs Python 3.12 or newer.

To use `nailong` from any folder, like `claude` or `codex`, install it as a tool. It is editable,
so changes to this repository take effect without reinstalling:

```bash
uv tool install --editable apps/nailong_cli     # run from the repository root
nailong --help                                  # works in any directory
```

If `nailong` is not found afterwards, run `uv tool update-shell` and open a new terminal (the
launcher goes in `~/.local/bin`, or `uv tool dir --bin` shows where). Upgrade with
`uv tool upgrade nailong-cli` (not needed for an editable install), remove with
`uv tool uninstall nailong-cli`.

For working on the CLI itself, use a project environment instead; `uv run nailong` then works only
from inside `apps/nailong_cli`, because `uv run` uses the project it finds above the current folder
(a test workspace under the repository root finds the SDK's project, which has no `nailong`
command, hence "program not found"):

```bash
cd apps/nailong_cli
uv sync
uv run nailong --help       # run inside a terminal for the interactive prompt
```

Without `uv`:

```bash
pip install -e ../..       # the SDK
pip install -e .           # the CLI
nailong --help             # or: python -m nailong_cli --help
```

`prompt_toolkit` is a normal dependency, so the interactive prompt is on by default whenever
`nailong` runs in a real terminal (stdin and stdout are a TTY). When input is piped, or in `-p`
mode, you get plain text instead. If you installed before this was added, run `uv sync` again.

## 2. Configure a model

**The quick way:** just run `nailong` in a terminal. With no settings it says so and offers to set
itself up, like first launch of any desktop app:

1. a welcome box, then an arrow-key menu to pick the service (OpenRouter, Anthropic, OpenAI,
   Gemini, xAI, Ollama, vLLM, LM Studio, llama.cpp, or your own URL);
2. the model name, as the service lists it;
3. your API key, typed or pasted into a hidden field.

The key goes into the **operating system's credential store** (Windows Credential Manager, macOS
Keychain, Secret Service on Linux) under the name `nailong-cli`, never into a settings file, and is
used straight away, so the session starts without you touching environment variables. If the
store is unavailable the key is used for that session only and the CLI says so. Press Enter at the
key prompt to skip it and set the variable yourself.

An environment variable always wins over a saved key. `nailong --setup` redoes the setup;
`nailong --forget-key OPENROUTER_API_KEY` deletes a saved key.

**By hand.** Settings are TOML. Three layers, later wins: your user file
`~/.config/nailong/config.toml`, then the project file `.nailong/config.toml`, then flags.

The smallest working file, for a hosted OpenAI-compatible service:

```toml
provider = "openai-compatible"
base_url = "openrouter"              # or openai | gemini | xai | a full URL
model = "provider/model-name"
api_key_env = "OPENROUTER_API_KEY"   # the NAME of an environment variable
```

For Anthropic's own API:

```toml
provider = "anthropic"
model = "a-claude-model-id"
api_key_env = "ANTHROPIC_API_KEY"
```

**The key never goes in a file.** `api_key_env` names the environment variable that holds it:

```bash
export OPENROUTER_API_KEY=...        # PowerShell: $env:OPENROUTER_API_KEY = "..."
```

Check the setup with `nailong` then `/doctor`, or non-interactively:

```bash
nailong -p "Say hello in three words."
```

Pick a model that supports **tool calling**. Small models sometimes return malformed tool
arguments or empty replies; the CLI shows the model the problem and asks again (twice by default),
but a weak model can still fail. A cheap model that worked in testing: `google/gemini-2.5-flash-lite`
through OpenRouter.

## 3. First run

```bash
cd your-project
nailong                              # interactive
nailong "explain how this project is laid out"   # interactive, starting with a message
```

You see a boxed banner (version, model, mode, session, project, sandbox) and a prompt between two
rules. Below the input is a hint bar: the permission mode and what Shift+Tab does, then model,
session and spending. Type a request. While the agent works you see `✻ Thinking…`; the answer
streams, each tool call is a `●` line with its result under it (`⎿`), and anything that changes a
file or runs a command asks first. While it works you see an animated spinner with the elapsed
time. **Esc** stops the turn and returns you to the prompt; so does Ctrl+C. `/exit`, Ctrl+D, or
Ctrl+C twice at an empty prompt leaves.

**Seeing the model think.** When the model sends its reasoning, it streams live under a dim
`∴ Thinking` heading, indented, before the answer or the next tool call. `/reasoning off` hides it
(and `/reasoning on` brings it back); `show_reasoning = false` in settings or `--no-reasoning`
turns it off for a run. It is for your eyes only: reasoning is never saved in the session, sent
back to the model, or written to the audit log. What you see depends on the model and service:

| Source | How reasoning arrives |
|---|---|
| OpenRouter, vLLM (`--reasoning-parser`), Ollama, DeepSeek-style servers | `reasoning` / `reasoning_content` fields in the stream, or `<think>…</think>` in the text |
| Anthropic | thinking blocks, which need extended thinking switched on: `[parameters]` with `thinking = { type = "enabled", budget_tokens = 4000 }` (check your model's current requirements) |
| Models that do not reason | nothing to show |

Reasoning models spend output tokens on thinking. If a run stops with `finish_reason "length"`, raise `max_tokens` (or leave it unset) in your settings.

In `-p` mode reasoning is shown only with `--verbose`; `--output stream-json` always carries it as
`{"type": "reasoning", "delta": "..."}` events.

Answers are shown as formatted markdown (headings, lists, bold, code blocks) a block at a time as
they stream. Tool results are collapsed: a one-line headline (`read 40 lines`, `exit 0 · 0.2s`) and
at most three lines of preview. **Ctrl+O** at the prompt, or `/expand [N]`, prints the full output
of the last (or Nth) tool call.

```text
╭───────────────────────────────────────────────╮
│ ✻ nailong 0.1.0                               │
│   your-model  ·  default mode  ·  session ... │
╰───────────────────────────────────────────────╯
──────────────────────────────────────────────────
> add a --verbose flag to cli.py and a test for it
● read_file cli.py
  ⎿ succeeded: ...
● write_file cli.py
  (a diff)
  Approve write_file?
  ❯ 1. Yes
    2. Yes, for this session
    3. Yes, and always for this
    4. No
```

**Keys in the prompt**

| Key | Does |
|---|---|
| Enter | send the message |
| `\` then Enter, Alt+Enter, or Ctrl+J | new line (multi-line message) |
| `/` | opens a menu of commands with descriptions; keep typing to filter; Tab or arrows choose |
| `@` | completes file and directory names inside the project |
| Shift+Tab | cycle permission mode: default → accept-edits → auto → plan |
| Up / Down, Ctrl+R | history (kept between sessions), reverse search |
| Ctrl+O | print the full output of the last tool call |
| Ctrl+C | cancel the running turn (twice at an empty prompt to leave) |
| Ctrl+D | leave |

**While a turn runs:** Esc or Ctrl+C stops it. You can keep typing: the text appears on the spinner
line after a `▸`, and Enter sends it. A line that is not one of the live commands below is queued
and runs, in order, when the agent is done; half-typed text you did not send is put back at the
prompt. **In an approval menu:** ↑/↓ (or j/k, Tab) move, Enter confirms, 1-4 pick
directly, Esc means no. Without a real terminal (pipes, tests) approvals fall back to typed
`y/s/a/n` or `1-4`.

## 4. Talking to the agent

| You type | What happens |
|---|---|
| plain text | a turn: the agent reads, edits, runs commands as allowed |
| `@path/to/file` | the file's contents are attached to the message (a directory attaches a listing; `@pic.png` attaches an image) |
| `!command` | **you** run a shell command; the agent is not involved |
| `/something` | a slash command (section 9) |

Mentions refuse secrets (`.env`, keys) and paths outside the project. Add images with
`--image FILE` on the command line too. The model must support images.

Put lasting instructions in a file the agent reads every turn (section 12): `NAILONG.md`,
`AGENTS.md` or `CLAUDE.md` in the project. `/init` asks the agent to draft an `AGENTS.md` and asks
before saving it.

### Plan, then do

`/mode plan` (or `--permission-mode plan`) removes every tool that changes things. The agent
investigates and proposes a plan. Afterwards the CLI asks whether to carry it out accepting edits,
carry it out asking each time, or stay in plan mode.

### Long pastes

A paste of more than 10 lines or 1,000 characters shows in the prompt box as
`[Pasted text #1 +40 lines]`, so a pasted log does not fill the screen. The whole text is kept and is
what the model receives when you send the message; the label is replaced by the text at that moment.
Alt+E opens the label at the cursor back into its text so you can read or edit it before sending;
the footer says so while a label is in the box. `/paste` lists this session's pastes and `/paste N`
prints one in full. Short pastes go in as they are. Labels are per session: one recalled from the
history file after a restart is sent as the literal label, because the text it stood for is gone.
Piped or non-terminal input is not collapsed.

### Splitting a big request: `/plan`

`/plan Compare these two libraries and write a summary file` asks the model for a plan, shows it,
and, when it would be a team, asks what you want:

```text
Plan: Pricing and security are independent; the brief needs both.
  pricing [explorer]: Compare the two vendors' pricing.
  security [explorer]: Compare the two vendors' security.
  brief [worker] (after pricing, security): Write the one-page brief from the notes.
Suggested: a team - 2 roles are involved (explorer, worker); pricing and security can run at the same time.
  1. Run as a team (3 tasks: explorer, worker)
  2. Keep everything in one agent
  3. Cancel
```

- **When it asks.** Only for a team suggestion (two or more roles, or two independent tasks). A job
  the planner judges to be one task or one line of steps just runs, as it would without `/plan`.
- **Roles.** `explorer` (reads files, searches and fetches; cannot change anything or run commands)
  and `worker` (can edit and run, with your usual approvals) are built in. Each `[agents.NAME]`
  in your settings becomes a role too; one whose `tools` is not `read-only` counts as writable.
- **How a team runs.** In dependency order, each task with its own agent and the earlier tasks'
  results in its prompt; one at a time unless `team_parallel` (1 to 4) says otherwise. A failed task
  is tried again up to `task_attempts` times (default 2) with the failure in its prompt; if it still
  fails, the tasks that need it skip and you are told what needs you. Everything goes through your
  normal approvals, budget and `/rewind`.
- **Plans survive the process.** Every team run is journaled under `.nailong/tasks/`: the plan, each
  attempt, each committed result. If the process is killed, the next session says
  `1 unfinished plan(s)`; `/plan list` shows them, `/plan show ID` the tasks, `/plan resume [ID]`
  runs only what is left. A task finished in the earlier run is kept and not run again, as long as
  it and everything upstream of it is unchanged; a task that was cut off is run again with a note to
  check what it already changed. Only one process runs a plan at a time. `/plan abandon ID` drops
  one.
- **Side by side without collisions.** A task that edits a file first claims it; another task (or the
  main agent) that tries to edit a claimed file is refused with the holder's name. Claims end with
  the task, and a claim left by a dead process is ignored. Background subagents claim their files
  the same way.
- **One agent instead.** Option 2 runs the same steps as a single turn in your current session.
- **Not recorded in the session.** A team run's tasks are not saved as turns of the session you can
  `/resume`; the single-agent option is.

### How the agent keeps its place in a long task

Each step the model is shown the task, your earlier turns in this session, and the most recent tool
calls with their results. When that grows past `context_tokens`, the oldest results are replaced
by short stubs (the call and a 400-character summary). Three things keep it from going in circles:

- **Results are kept whole.** A tool result is shown up to `tool_result_chars` (12,000); the SDK's
  own default is 1,024, which is why an agent could appear to "forget" a file it had just read.
  Lower both settings for a small local model; raise them if your model has a large window.
- **Repeats are refused.** If the agent repeats an identical read, glob, grep or file write and
  nothing has changed since (no write, edit or command in between), the call is skipped, you see
  `↺ skipped a repeated read_file`, and the model is told which step already did it.
- **It knows where it is.** The instructions carry the working directory, platform, shell
  (Windows `cmd.exe` is named, so it stops writing bash), whether commands are sandboxed, and
  today's date; the task list it keeps with `todo_write` is returned in full on every update.

- **It remembers its last thought.** For models that send reasoning (DeepSeek-style
  `reasoning_content`, OpenRouter `reasoning`), the reasoning and spoken text of the previous step
  go back with its tool calls, in memory only, so it continues its plan instead of re-deriving it.
  Anthropic thinking blocks were already kept this way.
- **Web pages are readable.** `web_fetch` returns an HTML page as text (title, headings, lists,
  up to 40 links) and reads long pages in slices (`page`, `next_page`); it used to return the raw
  source, which for many sites is all stylesheet and script.
- **Searches go one at a time**, about a second apart, because search backends rate-limit bursts.
- **A working style is in the default instructions:** say why before a call, note only what is new
  after it, never repeat a call, stop when the evidence is enough, answer once with sources.

At the step limit you are asked `Keep going for N more?`; yes sends "Continue from where you
stopped" in the same session.

## 5. Permission modes and approvals

| Mode | Reading | File edits | Commands |
|---|---|---|---|
| `default` | free | ask | ask |
| `accept-edits` | free | go through | ask |
| `auto` | free | go through | read-only ones go through, others ask |
| `plan` | free | unavailable | unavailable |
| `bypass` | free | go through | go through (needs a sandbox, or `--allow-unsandboxed`) |

Set it with `--permission-mode`, `permission_mode` in settings, or `/mode NAME` inside. On a
terminal, Shift+Tab cycles `default → accept-edits → auto → plan`.

**Approval prompts** show the diff or the exact command, then an arrow-key menu. Answers: **yes once**, **for this
session** (never offered for commands when there is no sandbox), **always** (saved), or **no**.
A "no" goes back to the model as a failed tool call so it can try another way.

**Saved rules** live outside the project, in `~/.config/nailong/permissions.toml`, one set per
project path, so a repository cannot grant itself permissions. Manage them with `/permissions`
(`/permissions allow run_command:pytest*`, `/permissions remove 2`). Rules for commands are matched
**per part of a chained command**: allowing `pytest` does not allow `pytest; curl x`.

For one run only: `--allow "run_command:pytest*"` and `--disallow "run_command:rm *"`
(repeatable; a disallow wins).

**Protected files.** The agent's file tools cannot write `.nailong/` or `NAILONG.md`.

**Without a terminal** (`-p`, pipes, CI), nothing can ask, so an action is allowed only if a saved
rule or `--allow` covers it; otherwise it is refused and the model is told.

## 6. Sandboxing commands

`sandbox` in settings (default `auto`):

| Value | Meaning |
|---|---|
| `auto` | bubblewrap on Linux or Seatbelt on macOS if available; otherwise **no sandbox** |
| `docker` | each command runs in a container (`docker_image`, default `python:3.12-slim`) |
| `none` | no sandbox |

With no sandbox the banner says **commands are NOT sandboxed**; those commands are never allowed
for a whole session, and unattended runs refuse them unless you pass `--allow-unsandboxed`.
`bypass` mode requires a sandbox or that flag. On Windows use `sandbox = "docker"` (Docker must be
running). `/sandbox` shows what is active and what each command can reach. `--add-dir` directories
are made writable inside the sandbox too.

## 7. Sessions

Every conversation is a **session**, saved as a tamper-evident log under the project's
`.nailong/workspace`.

```bash
nailong -c                    # continue the latest session
nailong -r SESSION_ID         # resume a named one
nailong -c --fork-session     # branch the latest instead of continuing it
nailong --fork ID --at 3      # branch ID after its turn 3
nailong -n "login bug"        # name a new session
```

**Where they are.** Per project, on your disk, under the project's workspace:

| Path (relative to the project) | Holds |
|---|---|
| `.nailong/workspace/.agent-sessions/<id>.jsonl` | the session: one line per turn (prompt, answer, status, tool names), hash-chained |
| `.nailong/workspace/.agent-audit-logs/` | the tamper-evident audit log of every model and tool step |
| `.nailong/workspace/.agent-telemetry/telemetry.sqlite3` | telemetry events |
| `.nailong/workspace/.agent-project-state/` | per-session state records |
| `.nailong/checkpoints/` | the file snapshots `/rewind` uses |

Your prompt history is in `~/.local/state/nailong/history` and saved permission rules are in
`~/.config/nailong/permissions.toml`, outside the project. Because the sessions belong to the
project folder, you resume them by running `nailong` from the same folder (or `--project DIR`).
The files are plain JSON lines; copy the folder to move a session to another machine.

**What resuming restores.** The model is given the earlier prompts and answers (a bounded digest,
or your `/compact` summary), so it knows what was discussed. It is not given its past
reasoning, and the files on disk are whatever they are now; `/rewind` is how you go back.

Inside: `/sessions` lists them, `/resume` shows a picker, `/fork [turn]` branches here, `/new`
(or `/clear`) starts an empty one, `/verify` checks the hash chains of the session and of forged
tools, `/search TEXT` finds text across sessions, `/export [file]` writes the session as markdown
(default `.nailong/exports/ID.md`).

**Long sessions.** The model is sent a bounded digest of earlier turns, not the transcript.
`/compact [focus]` has the model write a summary that replaces older turns in what is sent;
Old turns are now summarised automatically once they no longer fit (set `autocompact = false` to stop that). `/context` shows what is sent.

**What the agent keeps, and how to keep the one sentence that matters.** Only some things are in
every request. The instruction files (`NAILONG.md`, `AGENTS.md`, `CLAUDE.md`, `.nailong/rules/`) and
your **pinned rules** are loaded in full every time and never summarised. The conversation is not:
only the latest turns are sent, and older ones survive only as a model-written summary. The wiki,
discoveries, notes and knowledge base are in the prompt only when the agent searches them (accepted
discoveries that still hold are the exception).

So a policy that must always apply belongs in a pin. `/remember Never push to main without a review.`
writes one line to `.nailong/pinned.md` (`--user` writes `~/.config/nailong/pinned.md` for every
project) and puts it at the top of the instructions, ahead of everything else, from the next request.
A pin is one rule of up to 500 characters; at most 40 per scope and 5,000 characters in all, so pins
can never be cut off. The files are plain Markdown bullets you can edit by hand. `/memory pins`
lists them with ids, `/forget ID` removes one, and `/memory` shows the whole picture with sizes. The
agent has a `memory_pin` tool for "remember that we use UTC", but every pin it proposes needs your
approval, and it is not offered in planning mode.

When older turns are summarised, sentences in your messages that read as standing rules ("always",
"never", "from now on", "our policy is", and the like) and are not already pinned are kept word for
word at the end of the summary (up to 20), and the CLI lists them so you can pin the ones that matter.
This is a pattern match on your wording, not a judgement of importance: a rule phrased differently can
still be paraphrased away, which is why the dependable route is to pin it.

## 8. Undo: `/rewind`

Before each turn the CLI snapshots the files the agent changes **and** what its commands change.

```text
> /rewind          # lists the turns that changed files
> /rewind 2        # shows what would be restored and deleted, asks, then restores
```

It restores the state before turn N. It covers the project and any `--add-dir` directories. It is
a convenience, not a substitute for git: it cannot undo effects outside those directories (a
database, a network call).

**Scratch and test files.** The default instructions tell the agent not to add test or scratch files
to the project to check its work unless you asked for tests, to check with a command instead, and to
delete any file it made only for checking. That is an instruction to a model, not a guarantee, so the
CLI also checks: at the end of every turn it lists the files that turn created and that still exist
(`created this turn and still there: test_calc.py  (/rewind removes them)`), including files made by
commands. Nothing is deleted for you. `/rewind N` removes them along with the rest of that turn.

**When a tool is refused.** A refused command, or a question asked in a run with nobody at the
terminal, now reaches the model with the reason and an instruction not to retry or find another way
round (a model had been seen trying eight spellings of the same refused command until it ran out of
steps). The refusal is also printed.

## 9. Slash commands

| Command | What it does |
|---|---|
| `/help` | list commands |
| `/mode [NAME]` | show or set the permission mode |
| `/permissions` | saved rules: `allow TOOL[:PATTERN]`, `remove N` |
| `/model [NAME]` | show or switch the model |
| `/status` `/doctor` | what is configured; check the setup |
| `/context` | what is sent each turn |
| `/compact [focus]` | summarise the session so far |
| `/cost` | spending this run (needs prices, section 16) |
| `/tokens` | where the next request's tokens go (instructions, tool definitions, history), the largest tools, and the last turn's usage |
| `/sessions` `/resume` `/fork [N]` `/new` `/clear` | sessions; `/sessions delete ID` removes a saved one (asks first; not the one you are in); `/sessions import FILE [name]` turns a Claude Code or Codex transcript (`.jsonl`) into a session you can `/resume` |
| `/models [text]` | asks the model service which models it offers (`GET /models`, with your key) and switches to the one you pick; `/model NAME` switches directly |
| `/web [on\|off]` `/web search [on\|off]` | shows or changes web access (page and document fetching; web search) for this session without restarting; refused while `offline` is set. A message with a link while web is off gets a one-line hint |
| `/search TEXT` `/export [file]` | find in sessions; write markdown |
| `/init` | draft `AGENTS.md` (asks before saving) |
| `/diff` `/review` | show your uncommitted changes; have the agent review them (read-only) |
| `/rewind [N]` `/redo` | undo changes (section 8); `/redo` puts back what the last `/rewind` undid, until the next turn |
| `/editor [text]` | write a long prompt in `$VISUAL` or `$EDITOR` and send it |
| `/stats [days]` | runs, event statuses and the busiest events over the last days (default 7) |
| `/connect` | choose a model service, save the settings and switch to it now |
| `/ci github\|github-review\|gitlab [write]` | a workflow that runs nailong from issue comments, reviews pull requests inline, or runs in a pipeline |
| `/plan REQUEST` | split a request into tasks; run them as a team or keep it in one agent |
| `/plan list\|show ID\|resume [ID]\|abandon ID` | unfinished team plans: see them, continue only what is left, or drop one |
| `/events [check\|tail [N]]` | the typed event log of this run: counts, open spans; `check` verifies every log in the project |
| `/health` | the same report as `--health`, inside a session |
| `/stale [REF]` | what in the wiki, discoveries and knowledge base no longer holds; one item with `file:`, `kb:`, `wiki:` or `discovery:` |
| `/suggest` | three useful next prompts |
| `/telemetry [now]` | the OpenTelemetry export: collector, what it sends, last result, send now |
| `/why` | what the agent is doing and the reasoning it gave for that step (works any time) |
| `/ps` | running agents and prompts waiting in the queue |
| `/pause` `/unpause` | freeze the running work where it is, and carry on from exactly there |
| `/pause JOB` `/unpause JOB` | the same for one background command (`/jobs` shows it as paused) |
| `/interrupt [text]` | stop what is running; with text, run it next |
| `/stop ID` | stop one running agent (`main`, or a task id during a `/plan` team run) |
| `/broadcast TEXT` | tell every running agent something now; it reads it at its next step |
| `/cleanup [days]` | remove old runs' audit and telemetry records (shows them, asks first) |
| `/expand [N]` | full output of the last (or Nth) tool call |
| `/reasoning [on\|off]` | show or hide the model's reasoning as it streams |
| `/tools` `/todos` `/plugins` | what the agent has; `/tools new NAME [user]` makes a tool file |
| `/memory [pins\|show]` | everything the agent carries: pinned rules, instruction files with sizes, the conversation digest and summary, and what is only looked up (notes, wiki, discoveries, knowledge base); `show` prints what is loaded |
| `/remember [--user] RULE` `/forget ID` | pin a short standing rule into every request (project, or all projects with `--user`), or remove one |
| `/paste [N]` | long pasted text kept behind a `[Pasted text #N +L lines]` label: list them or show one |
| `/trust-tools` | read and approve project tool files before they load |
| `/variant [NAME\|off]` | switch to a saved set of model parameters |
| `/skills` `/deploy ARGS` `$deploy ARGS` | list skills, make or install one (`/skills new NAME`, `/skills install DIR`), run one by name |
| `/agents` | subagents (files and settings) and the ones running in the background |
| `/mcp` | MCP servers and their tools; `/mcp add NAME COMMAND...`, `/mcp add NAME URL`, `/mcp preset browser`, `/mcp remove NAME` |
| `/schedule` | prompts that run on a timetable: `list`, `add WHEN -- PROMPT`, `remove ID`, `install` |
| `/sandbox` `/verify` `/forge` | sandbox details; integrity check; forged tools |
| `/add-dir PATH [name]` | give the agent another directory |
| `/jobs` `/kill ID` | background commands and persistent shells (`/kill shell:name`) |
| `/notes [title]` | the task notes the agent keeps |
| `/exit` (`/quit`) | leave |

Your own commands (section 12) appear in the list too.

## 10. Command-line flags

```text
nailong [PROMPT]                 interactive, starting with PROMPT
nailong -p PROMPT                run once, print the answer, exit
```

| Flag | Meaning |
|---|---|
| `--output text\|json\|stream-json` | result format for `-p` |
| `--project DIR` | project directory (default: current) |
| `--add-dir DIR` | another directory the agent may use (repeatable) |
| `--worktree [NAME]` | work in a new git worktree |
| `--provider`, `--model` | override those settings |
| `--effort low\|medium\|high` | reasoning effort, where the provider supports it |
| `--param KEY=JSON` | extra model parameter, e.g. `--param temperature=0.2` |
| `--permission-mode MODE` | see section 5 |
| `--allow RULE`, `--disallow RULE` | per-run permission rules |
| `--allow-unsandboxed` | permit commands with no sandbox |
| `--max-turns N` | most model steps in one turn (default 30) |
| `--max-budget-usd USD` | stop when spending reaches this (needs prices) |
| `--system-prompt-append TEXT` | extra instructions for this run |
| `--json-schema FILE` | make the final answer match a JSON Schema |
| `--image FILE` | attach an image (repeatable) |
| `--mcp-config FILE` | MCP servers in the usual `mcpServers` JSON |
| `--plugin-dir DIR` | load a plugin folder |
| `--web` / `--search` | let the agent fetch pages / search the web |
| `--offline` | refuse any setting that reaches beyond your network |
| `-c` / `-r ID` / `--fork ID --at N` / `--fork-session` / `-n NAME` | sessions |
| `--autocompact` | summarise old turns automatically (already the default) |
| `--daemon` `--daemon-status` `--daemon-stop` `--daemon-interval S` | stay running and run scheduled prompts when due (one daemon at a time; the interval is at least 10 s); say whether it runs; ask it to stop |
| `--health` | report the state of this project's agent files (event logs, plans, stale memory, evidence coverage) and exit 1 if any is broken; with `--output json` for a scheduler |
| `--backup FILE` `--verify-backup FILE` `--restore FILE` `--migrate` | save, check, restore and upgrade the agent state under `.nailong/`; see `docs/operations.md` |
| `--review [BASE]` | print a read-only review of uncommitted changes and exit |
| `--forge` | let the agent propose tools for your review |
| `--verbose` | show full tool arguments and results (and reasoning in `-p` mode) |
| `--no-reasoning` | do not show the model's reasoning |
| `--fallback-model NAME` | model to try when the main one fails (repeatable, in order) |
| `--otlp-endpoint URL` | send recorded runs to this OpenTelemetry collector (OTLP/HTTP) |
| `--mcp-serve` | offer this project's tools to another program over MCP (section 12) |
| `--run-schedules` | run the scheduled prompts that are due, then exit (for cron or Task Scheduler) |
| `--serve` `--port N` `--hostname H` | run the agent behind a small HTTP service (section 12) |
| `--attach URL` | talk to a `--serve` at that address (`-p PROMPT` for one message) |
| `--acp` | speak the Agent Client Protocol on stdio, for editors (section 12) |
| `--completion SHELL` | print a completion script for bash, zsh, fish or powershell |
| `--upgrade` | say how to upgrade nailong where it is installed |
| `--debug-file FILE` | write a JSON-lines debug log |
| `--no-color`, `--version` | |

## 11. Settings file reference

```toml
# required
provider = "openai-compatible"      # or "anthropic"
model = "name"

# connection
base_url = "openrouter"             # openai-compatible: shortcut or URL (required there)
api_key_env = "NAME_OF_VARIABLE"    # required for anthropic; optional for a self-hosted server
timeout_seconds = 120
max_tokens = 4096
allow_insecure_http = false         # true for plain HTTP beyond this machine
invalid_output_retries = 2          # re-asks after an unusable model reply
tool_mode = "auto"                  # auto | native | prompted: for models without tool calling
fallback_models = ["other-model"]   # tried in order when the main model fails (same service and key)

# behaviour
permission_mode = "default"
max_steps = 30                      # model steps per turn; at the limit you are asked whether to keep going
task_attempts = 2                   # tries per /plan team task before it is escalated to you (1 to 5)
team_parallel = 1                   # /plan tasks run at once (1 to 4); edits are claimed per file
context_tokens = 48000              # how much of the conversation the model is shown each step
tool_result_chars = 12000           # how much of each tool result it sees in full
sandbox = "auto"                    # auto | none | docker
docker_image = "python:3.12-slim"
web = false
web_search = false
autocompact = true
show_reasoning = true             # stream the model's reasoning when it sends any
notify = false                      # terminal bell when a turn ends
editing_mode = "emacs"              # or "vi"
instructions = "extra standing instructions"
max_budget_usd = 5.0

# user file only (a project file's copy is ignored, and /doctor says so)
offline = false
add_dirs = ["/abs/path/one"]
otlp_endpoint = "http://localhost:4318"   # send recorded runs to this OpenTelemetry collector
otlp_include = ["telemetry", "audit", "integrity"]   # what to send (all three by default)
model_timeout_seconds = 300    # one model call; 0 = no limit
tool_timeout_seconds = 600     # one tool call; 0 = no limit
run_deadline_seconds = 0       # a whole turn; 0 = no limit (the default)
ssh_hosts = []                 # hosts the agent may open remote shells on (user file only)
hook_env = []                  # extra environment variables passed to hook commands (user file only)
require_sandbox = false        # refuse every command while none is sandboxed, in every mode (user file only)
embedding_model = ""           # turns on meaning-based knowledge search; see /kb embed (user file only)
embedding_base_url = ""        # embeddings service; defaults to base_url for openai-compatible (user file only)
small_model = ""               # cheaper model for /compact and /suggest summaries
instruction_files = []         # extra instruction files or globs, e.g. ["CONTRIBUTING.md", "docs/*.md"]
format_on_edit = false         # run ruff/prettier/gofmt/rustfmt/shfmt after edits (user file only)
diagnostics_on_edit = false    # run ruff check after edits and tell the agent (user file only)
lsp_on_edit = false            # ask language servers found on PATH about edited files (user file only)
```

Tables: `[parameters]` (model parameters), `[prices.MODEL]` (section 16), `[agents.NAME]` (section
12), `[mcp.NAME]` and `[[hooks]]` (user file only).

## 12. Extending it

**Instruction files.** `NAILONG.md`, `AGENTS.md`, `CLAUDE.md` in the project (and
`~/.config/nailong/NAILONG.md` for yourself) are added to the agent's instructions. `/memory`
lists what was found.

**Custom commands.** A markdown file `.nailong/commands/ship.md` becomes `/ship`. Its text is sent
as the prompt; `$ARGUMENTS` is replaced by what you typed after the name.

**Skills.** A skill is a folder with a `SKILL.md`, the open Agent Skills layout that Claude Code
and Codex also read, so a skill written for either works here:

```
.nailong/skills/deploy/
  SKILL.md          ---, name: deploy, description: Deploy to an environment, ---, then the steps
  scripts/go.sh     files beside it, listed to the agent when the skill runs
```

The header takes `name` (equal to the folder), `description`, and optionally `allowed-tools`
(`read_file grep`, or Claude's `Read Bash(git status:*)`), `disable-model-invocation: true` and
`argument-hint`. A single `name.md` is still a skill, with its first line as the description.

Folders searched, later ones winning: `~/.codex/skills`, `~/.claude/skills`,
`~/.config/nailong/skills`, `<project>/.claude/skills`, `<project>/.nailong/skills`. Plugins can
bring skills too.

You run a skill by name: `/deploy staging` or `$deploy staging`. The text after the name replaces
`$ARGUMENTS` (or `$1`, `$2`) in the body. The agent also sees a one-line index of the skills and
loads one itself with `use_skill` when a task matches; `use_skill` with `file=` reads a file
bundled with it, and scripts are run by their full path through `run_command` (so the sandbox and
approvals apply as always). `disable-model-invocation: true` hides a skill from the agent so only
you can run it. `allowed-tools` restricts the agent to those tools for the length of that turn when *you* run
the skill with `/name`; anything else is refused with a message naming the skill. When the agent
loads a skill on its own, the list is not enforced, because it cannot know in advance what the work
will need. It never grants permission, which
still comes from the mode and your approvals.

`/skills` lists them with their source; `/skills new NAME` makes a template in the project;
`/skills install FOLDER` copies a skill folder in. A skill file is limited to 20,000 characters
and a bad one stops startup with its path and the reason.
**Your own tools.** Put a Python file in `~/.config/nailong/tools/` (yours, loaded at start) or
`.nailong/tools/` (this project). `/tools new shout` writes a template:

```python
from nailong_agent_sdk.starter import tool

@tool
def shout(text: str) -> str:
    """Return the text in capitals; the model reads this to decide when to call it."""
    return text.upper()
```

The arguments' names and type hints become the tool's schema, and the docstring is its
description. A file with no `@tool` but a `run(...)` function becomes a tool named after the file.
Several `@tool` functions in one file are all offered. Names are letters, digits and underscores,
may not take a built-in's name, and may not repeat across files.

A tool runs in the nailong process with your authority, so the loader is careful. Files in your
own folder load at start. A **project** file is code someone else may have put there: it is not
imported until you run `/trust-tools`, which shows the file and asks; the answer is remembered by
the file's hash, so any edit makes it wait for approval again. Every custom tool asks for approval
when the agent calls it (unless the author marked it `read_only=True`), and plan mode does not
offer them. A file that fails to import is named at startup with the error and the rest still
load. For something that needs its own process, an MCP server is the better home.

**Subagents.** Besides `[agents.NAME]` in your settings, a subagent can be a Markdown file in
`.nailong/agents/`, `~/.config/nailong/agents/` or the `.claude/agents` folders (project beats user):

```
---
name: code-scout
description: Finds where something is defined and reports file and line
tools: Read, Grep, Glob        # read-only (default) | edit | all, or Claude's tool names
model: a-cheaper-model         # optional: another model on the same service
temperature: 0.2               # optional
steps: 8                       # optional: most steps for one task
deny: run_command:rm *, write_file   # optional: tools this agent may never use
allow: write_file:notes/*             # optional, user-level files only: lets it run in the background
                                      # with these pre-approved actions (see below)
---
You search the code and answer with file:line references. You change nothing.
```

The main agent calls `agent_code_scout`. Read-only subagents may run side by side: the agent can
call several in one step. It can also start one with `agent_start` and keep working, then collect
the answer with `agent_result`; the run shows in `/ps` and can be stopped (`/stop agent-1`),
paused or told something (`/broadcast`). Read-only subagents run in the background freely. One that
writes or runs commands may too, but only if its user-level agent file (not a project file) has an
`allow:` list: a background run never asks anyone, so it may do exactly what that list pre-approves
and everything else is refused. `/agents` lists them with the file each came
from and shows background runs.

**Instruction files.** `NAILONG.md`, `AGENTS.md` or `CLAUDE.md` are read from the repository root
down to the project folder (outermost first), then `NAILONG.local.md` (yours, not for
version control) and every `.nailong/rules/*.md`. A line may pull in another file with
`@docs/style.md`: the file is included in place (up to three levels), only from inside the
project or your settings folder, and a missing, outside or self-including import is replaced by a
bracketed note saying so. Folders below the project that have their own instruction file are
listed to the agent, which reads that file before changing things there.

**MCP servers.** Besides `[mcp.NAME]` in your user settings and `--mcp-config`, `/mcp add` saves a
server in `~/.config/nailong/mcp.json` so it connects at every start (`/mcp remove` undoes it;
a running session keeps it until restart). `/mcp preset browser` adds the Playwright browser
server (`npx` fetches its package the first time it runs), which gives the agent a browser to
click and read pages with. Tools of a server can be limited in its table with
`allow_tools = ["read"]` or `deny_tools = ["delete"]` (the tool names without the `mcp__server__`
prefix); anything else is refused with a message naming the setting.

**Hooks.** Shell commands the CLI runs around the agent, set in your **user** file only (a hook is
a program, so a project cannot add one):

```toml
[[hooks]]
event = "pre_tool"       # pre_tool | post_tool | session_start | session_end | prompt_submit | turn_end
                         # | file_edited | tool_failed | assistant_message | permission_asked
                         # | compacted
tool = "run_command"     # glob, default *; for tool events
command = "scripts/check.sh"
```

`file_edited` runs after each successful write, edit, notebook edit or patch (with the tool input
in `NAILONG_TOOL_INPUT`); `permission_asked` runs just before you are asked to approve something
(`NAILONG_TOOL`, `NAILONG_SUBJECT`); `compacted` runs after a summary is saved; `tool_failed` runs after a tool call that did not
succeed (`NAILONG_TOOL_STATUS` says how); `assistant_message` runs after a completed turn with the
reply in `NAILONG_MESSAGE`. None of these can refuse anything.

Hook commands run with a short environment (`PATH`, `HOME`, the temp folders and similar) plus
`NAILONG_*`; anything else, such as an API key, reaches them only if you list its name in
`hook_env`. Add `on_failure = "warn"` to a hook to make it advisory: a failing one is reported and the
action goes ahead (the default, `"block"`, refuses it). A `pre_tool` hook that exits non-zero (or
times out) refuses the call (the model is told). A failing
`prompt_submit` hook refuses the message; its output is otherwise added to the prompt. Hooks get
`NAILONG_HOOK_EVENT`, `NAILONG_PROJECT`, and for tool events `NAILONG_TOOL`, `NAILONG_TOOL_INPUT`
(JSON) and, after a call, `NAILONG_TOOL_STATUS`.

**MCP servers.** In the user file:

```toml
[mcp.files]
command = "npx"
args = ["-y", "some-mcp-server"]
# or a remote one:  url = "https://host/mcp"
```

or `--mcp-config servers.json` with `{"mcpServers": {...}}`. Their tools appear as
`mcp__server__tool` and need approval like other tools. `/mcp` lists them.

**Subagents.** Specialists the main agent can call as tools:

```toml
[agents.reviewer]
description = "Reviews a change for bugs."
instructions = "You review code. Read the files you are given and report problems."
tools = "read-only"      # read-only (default) | edit | all
```

They run one at a time; `/agents` lists them.

**Plugins.** A folder with `plugin.toml` plus optional `commands/`, `skills/` directories. The
manifest may define `agents`, `hooks` and `mcp`. Install by copying the folder to
`~/.config/nailong/plugins/` or pass `--plugin-dir`. Names are prefixed (`myplugin:ship`).
Plugins you install run with your authority; read them first.

**More directories and background work.** `--add-dir`, `/add-dir`, and `add_dirs` give the agent
other directories. The agent can start a long-running command in the background
(`start_background`, with approval); watch with `/jobs`, stop with `/kill ID`.

### Questions, notebooks, fallbacks, telemetry and cleanup

These are SDK features that the CLI now exposes.

- **The agent can ask you a question.** When only you can decide something, it uses
  `ask_human_question`; you see `? the question` and type an answer. With no terminal (`-p`, pipes)
  it is told the question went unanswered and carries on.
- **Notebook cells.** `notebook_edit` replaces or appends to one cell of any `.ipynb` file in the
  project by index, without running it. It asks for approval like other edits, is covered by
  `accept-edits` mode and by `/rewind`, and is absent in plan mode.
- **Fallback models.** `fallback_models = ["b", "c"]` (or `--fallback-model b`) names models on the
  same service and key to try, in order, when the main model fails. `/status` lists them. The names
  must differ from the main model and from each other.
- **OpenTelemetry export.** Off by default. Set `otlp_endpoint` in your *user* settings (a project
  file cannot choose where your data goes) or pass `--otlp-endpoint URL`. Records go to
  `<url>/v1/traces`, `/v1/logs` and `/v1/metrics` over OTLP/HTTP in the background, as they are
  written (about a second after the last one) and again after each turn and when you leave; a
  collector that is slow or down never delays you, and a ledger in the workspace means each record
  is sent once. A run's spans appear when the run ends, because the profile that holds them is
  written then. What is sent is `otlp_include` (user settings only): `telemetry` (spans, log
  records and metrics), `audit` (every hash-chained audit entry, scope `nailong.audit`) and
  `integrity` (each signed run-head checkpoint and a verdict saying whether the checkpoints, the
  audit chain and the terminal link hold, scope `nailong.integrity`). A collector stores these
  records; it does not verify them, so each carries its hashes and sequence numbers. `/telemetry`
  names what is being sent. Credentials go in `OTEL_EXPORTER_OTLP_HEADERS`
  (`Authorization=Bearer%20token`); the standard endpoint variable is deliberately not read, so a
  collector set up for other tools does not receive your prompts. `/telemetry` shows the last
  result, `/telemetry now` sends immediately. `offline` mode refuses a collector that is not on
  your network.
- **Cleanup.** `/cleanup [days]` (default 30) lists finished runs older than that, keeping the three
  most recent, and asks before removing their audit and telemetry records. A tombstone note records
  that they existed. Sessions you can `/resume` are not touched.

### Pausing and killing

`/pause` (typed while the agent works) stops the work without restarting anything. The agent waits
at the start of its next step, so a model request already sent finishes first and its answer is
held; the command the agent is waiting for is frozen in its tracks (POSIX `SIGSTOP`; on Windows its
process tree is suspended) and carries on from the same instruction at `/unpause` (`/resume` is the saved-session command). Time spent paused
counts against no limit: not the run deadline, and not the command's own time limit. Background
commands keep running when you pause the agent; pause one with `/pause job-1`. A tool written as a
Python function cannot be frozen: it finishes, and the agent pauses after it. Commands in the
Docker sandbox are not frozen either. Esc, `/interrupt` and `/stop ID` end paused work too, and
`/kill job-1` kills a background command even if it is paused. The pause is lost if you quit.

### Shells that keep their state, and servers

`run_command` starts a fresh shell every time, so a directory change, a variable or a login does
not carry over. For work that needs state, the agent opens a persistent shell with `shell_open`
and runs commands in it with `shell_run`. The shell is one process that stays alive: `cd`, `export`,
a virtualenv and anything it started persist, and every call returns the exit code and the
directory it ended in. Up to five can be open; `/jobs` lists them, `/kill shell:name` closes one,
`/pause shell:name` freezes it, and live output, pauses and time limits work as for any command.
A command that runs past its limit closes the shell (a hung command cannot be interrupted through
a pipe) and the error says which state was lost; use `start_background` for long work. Two limits
of the design: a command that reads from standard input, or that exits the shell, ends the
session; local shells use `cmd.exe` on Windows and `/bin/sh` elsewhere, inside the sandbox when
one is active.

**Remote machines.** Remote shells are off until you list hosts in `ssh_hosts` in your *user*
settings (a project file cannot add any), for example `ssh_hosts = ["admin@server1"]`. The agent
then calls `shell_open` with `host`, and approval is asked as for any command. The connection is
one `ssh` process kept for the whole session, so the server sees one login, not one per command;
on macOS and Linux the connection is also shared and kept ten minutes after the shell closes
(`ControlMaster`), so reopening is cheap. It never asks for a password: it runs with
`BatchMode`, so it uses your keys and your ssh-agent, and fails with ssh's own message when they
do not work. The host must be one you listed, and a name that starts with a dash is refused.
Windows OpenSSH has no connection sharing, so there the single long-lived process is what keeps
the login. The remote side is assumed to be a POSIX shell.

**Notes.** For a long task the agent keeps notes (`notes_write`: goal, facts learned, what is done,
what is next) in files under `.nailong/workspace/notes`, and rereads them (`notes_read`) when the
task resumes, so the job survives compaction and a restart. `/notes` lists or shows them.

### Scheduled prompts

`/schedule add daily 09:00 -- summarise what changed in the repo since yesterday` saves a prompt
with a timetable: `every 30m`, `every 2h`, `every 1d`, `daily 09:00`, `weekdays 09:00`, or five
cron fields (`0 9 * * 1-5`). It runs when `nailong --run-schedules` runs, which checks every
saved schedule and runs the ones that are due, one after another, each as a one-shot `-p` run in
its own project. Schedule that command with your system: `/schedule install` prints the `crontab`
line or the `schtasks` command (it does not install anything itself). Because nobody is there to
approve things, a schedule runs in `plan` (read-only, the default) or `accept-edits`, never with
commands, and `mode=accept-edits` goes before the `--`. Each run's output is kept in
`.nailong/schedules/ID/` and the run is recorded in the audit trail like any other. Two runners
never overlap, and a schedule that was missed while the machine was off runs once when it is next
checked, not once per missed time.

### Time limits and long commands

A model call that takes more than `model_timeout_seconds` (300) or a tool call that takes more than
`tool_timeout_seconds` (600) ends the turn with an error that names the limit; `run_deadline_seconds`
caps a whole turn and is off by default, since agent tasks can legitimately run long. `0` switches a
limit off, and `/status` shows the three. A shell command also has its own 120-second limit and is
killed with its whole process tree when it expires.

A command's output is shown as it is printed (the first 40 lines; `/expand` has all of it after the
command ends), so a slow command is visibly working. A tool written as an ordinary Python function
runs in a thread, and a thread cannot be stopped: when such a tool times out or you press Esc, the
error says it is **still running in the background** and its effects may still happen, and no other
tool starts until it has finished.

### Permission rules

Besides the five modes, rules decide a tool for a pattern. In your settings (a project file may add
`ask` and `deny` rules but never `allow`; the CLI ignores its `allow` entries and `/doctor` says so):

```toml
[[rules]]
action = "ask"            # allow | ask | deny
tool = "run_command"      # a name, a comma list, or a wildcard such as mcp__db__*
pattern = "git push*"     # matched against the command, URL or path

[[rules]]
action = "deny"
tool = "read_file, write_file, edit_file, apply_patch"
pattern = "secrets/*"
```

The last rule that matches wins, so put broad rules first. `deny` refuses even a read and cannot
be overridden by any mode. `ask` always asks, even in `bypass` and `accept-edits`; with no
terminal it refuses. `allow` approves without the mode's own question (it also works for
`--allow tool:pattern`, which is now a rule too). A command is matched whole and part by part, so
`ask` on `rm *` catches `echo hi && rm -rf build`, while `allow` on `git *` does not let
`git status && curl x` through. For `apply_patch` the rules are tried against every file in the
patch and the strictest result wins. Credential files such as `.env` are already refused by the
file tools themselves, whatever the rules say.

### Patches, formatters and checks

`apply_patch` changes several files in one step from a patch the model writes
(`*** Begin Patch` ... `*** End Patch` with `*** Add File`, `*** Update File` (optionally
`*** Move to`) and `*** Delete File`; hunks start with `@@` and use space, `-` and `+` lines).
Every hunk must match the file's current text, line endings are kept, and either every file
changes or none does. It follows the same rules as the other edit tools: `accept-edits` lets it
through, otherwise it asks; `/rewind` undoes it; it cannot touch the settings folder.

After the agent writes a file you can have it formatted and checked. `format_on_edit = true` runs
whichever of `ruff format`, `prettier --write` (if `package.json` mentions it), `gofmt -w`,
`rustfmt` and `shfmt -w` is installed, by file extension. Add your own with
`[formatters.NAME]` (`command = ["prog", "$FILE"]`, `extensions = [".md"]`). `diagnostics_on_edit
= true` runs `ruff check` on edited Python, and `[diagnostics.NAME]` adds any linter or type
checker the same way; when one reports problems, its output reaches the agent as a message at its
next step. All of these settings are in the user file only, since each runs a program.

**Language servers.** `lsp_on_edit = true` starts whichever of `pyright-langserver`,
`typescript-language-server`, `gopls` and `rust-analyzer` is on your `PATH`, the first time a
file of its kind is edited, and keeps it for the session. After each edit the file is sent to the
server and its errors and warnings (up to 20, with line and column) reach the agent at its next
step, like the checks above. Add a server with `[lsp.NAME]` (`command = ["server", "--stdio"]`,
`extensions = [".rb"]`). The client sends the whole file and waits up to six seconds for the
server's answer; a server that fails to start or stops answering is named once and then left
alone. With a server active the agent also has `code_navigate` (`definition`, `references` or
`hover` at a 1-based line and character of a file in the project); results come back as
project-relative `path:line:column`. There is no rename. It was tested against a stand-in server
speaking the protocol, not against pyright or gopls themselves.

### Commands with arguments, options and live output

A file in `.nailong/commands/` may start with a header: `description:` (shown in the menu),
`agent: NAME` (hand the work to that subagent), `model: NAME` (use that model for this command's
turn only) and `subtask: true` (needs an `agent:`). In the text, `$ARGUMENTS` is everything typed,
`$1` to `$9` are the words (quotes group), and `` !`git diff --stat` `` is replaced by that
command's output. A command that only reads (`git status`, `ls`, `echo`...) runs at once; any
other asks you first, and with no terminal is refused. `@path` attaches a file as usual.

`small_model` names a cheaper model used for `/compact`, auto-compaction and `/suggest`.
`[variants.deep]` tables (for example `effort = "high"`, `temperature = 0.2`) are named sets of
model parameters; `/variant deep` switches and `/variant off` goes back. Starting a message with
`@name` hands it to the subagent of that name, unless a file of that name exists.

### Settings that read the environment or a file

In your **user** settings a value may contain `{env:NAME}` (empty if unset) and `{file:path}`
(relative to the settings file, or absolute or `~`; the trailing newline is dropped; a missing file
stops startup naming it). Project settings are never filled in, so a project file cannot ask for
your environment or your files. `instruction_files = ["CONTRIBUTING.md", "docs/*.md"]` adds more
instruction files from inside the project.

### Running it as a service: `--serve` and `--attach`

`nailong --serve` listens on `127.0.0.1:4096` (`--port`, `--hostname`). Every request needs the
token in `NAILONG_SERVER_TOKEN` (24 or more characters); if you set none, one is generated and
printed once, and a non-loopback address refuses to start without your own. `POST /run` takes
`{"prompt": "...", "mode": "plan" | "accept-edits", "session": "ID"}` and returns the answer as
JSON with the session id; add `?stream=1` for a server-sent-event stream of the same events as
`--output stream-json`. Only `plan` and `accept-edits` are allowed by default, because nobody at
the server can approve anything else. Runs go one at a time. `GET /health` needs no token. The other flags you give
`--serve` (model, provider, `--add-dir`...) apply to every run. `nailong --attach URL -p "..."`
sends one message and prints the stream; without `-p` it is a small prompt loop that keeps the
session. It refuses to send the token over plain http to another machine. `GET /openapi.json` describes
the service (OpenAPI 3.1) and `GET /` serves a small page for sending requests from a browser
(the token you type stays in that tab's session storage; the page writes model output with
`textContent` only, under a strict content-security policy).

To let the client answer permission questions, send `"mode": "default", "approvals": true` to
`POST /run?stream=1`. Each tool that needs approval produces an `approval_request` event
(`id`, `tool`, `subject`, `description`) and the run waits; answer it with
`POST /approve {"id": "...", "allow": true}` (same token). Only a single yes or no is offered:
there is no "for this session" or "always" over HTTP, a command is still refused when no sandbox is
active and `--allow-unsandboxed` was not given, and an approval nobody answers within 300 seconds
is refused with the reason. `bypass` and `auto` cannot be requested remotely.

`nailong --acp` speaks the Agent Client Protocol on stdio so an editor that can run an agent
(Zed, for example) can drive it: `initialize`, `session/new` and `session/prompt`, with the answer
and each tool call sent as `session/update` notifications. Runs are read-only unless you start it
with `--permission-mode accept-edits`, or with `--permission-mode default`, in which case the
editor is sent a `session/request_permission` (allow once or reject) for each tool that needs one. It was written from the protocol's shape and tested with
scripted JSON, not with a real editor. `session/cancel` stops the running prompt (the reply
says `stopReason: cancelled`); over `--serve`, `POST /cancel` does the same for the run in progress
(409 when none is running).

### Sharing it with a team, CI and other tools

`/ci github` prints, and `/ci github write` saves, `.github/workflows/nailong.yml`: it answers
`/nailong <request>` comments, but only from owners, members and collaborators, in read-only
(plan) mode, passing the comment to the shell through an environment variable and never into the
script text. It expects `.nailong/config.toml` to be committed and the model key to be a repository
secret. `/ci gitlab write` saves `.gitlab-ci-nailong.yml`, a manual pipeline job that takes the
request from a `NAILONG_PROMPT` variable. Both just run `nailong -p`, so the same works in any CI.

`nailong --completion bash|zsh|fish|powershell` prints a completion script for the flags.
`nailong --upgrade` says which command upgrades the copy you are running (it does not run it).

### Keys and themes

`[keybinds]` rebinds `cycle_mode` (default `s-tab`), `expand_output` (`c-o`), `expand_paste`
(`escape e`, Alt+E) and `newline` (`escape enter` and `c-j`) with prompt_toolkit key names, one or a list, for example
`cycle_mode = ["c-t", "s-tab"]`; an unknown key stops startup naming the action. `theme` may be a
file: `.nailong/themes/NAME.json` or `~/.config/nailong/themes/NAME.json` holding any of `rule`,
`hint`, `mode` and `menu` as prompt_toolkit style strings, filling the rest from `default`.

### Offering the tools to another program: `--mcp-serve`

`nailong --mcp-serve` runs an MCP server on stdio instead of a session, so another program (an
editor, another agent) can use this project's file tools. It needs no model or key and prints
nothing on stdout but the protocol; the notice of what it offers goes to stderr. There is nobody to
ask, so the permission mode decides what is offered: the default and `plan` offer reading only
(`read_file`, `glob`, `grep`); `--permission-mode accept-edits` adds `write_file`, `edit_file`
and `notebook_edit`; `auto` and `bypass` also add `run_command`, and they refuse to start where
commands are not sandboxed unless you pass `--allow-unsandboxed`. The settings folder stays
protected from the edit tools. Register it in another tool as
`{"command": "nailong", "args": ["--mcp-serve", "--project", "/path"]}`.

### Documents, a knowledge base, a wiki and built-in skills

**Reading files that are not plain text.** `read_document` reads a PDF, Word (`.docx`), Excel
(`.xlsx`), PowerPoint (`.pptx`, one slide at a time with its tables and speaker notes) or CSV/TSV file inside the project or an added directory: PDFs a page at a time with
`[page N]` markers, Word headings and tables, one sheet per chunk for Excel (200 rows each). Every
result carries the file's hash, the total size and a `next` position to continue from.
`search_document` ranks the pages or sheets that match a query, with snippets, so the agent reads
only what it needs. `interpret_page` renders one PDF page to a PNG in the workspace and asks the
session's own model about it (figures, tables, scans); with a model that takes no images it fails
saying so. Files over 80 MB and credential files are refused.

**Documents at a web address.** With web on (`/web on` in the session, `--web`, or `web = true`), `fetch_document` downloads a PDF,
Word, Excel, PowerPoint or CSV file from a public URL (an arXiv PDF link, for example) into
`.nailong/workspace/downloads/` and returns the path for `read_document`. It asks for approval like
`web_fetch`, refuses private and loopback addresses, follows redirects only to public hosts, checks
the file really is the type it claims, and caps it at 80 MB. Without web access `read_document` on
a URL says so and names the setting.

**`/kb`, a cited knowledge base.** `/kb add PATH` indexes a file or folder (text, Markdown, code,
PDF, Word, Excel, CSV) into passages of about 1,000 characters kept in `.nailong/knowledge`;
`/kb list` shows each source as current, changed since added, or missing; `/kb refresh` re-reads
changed sources; `/kb search TEXT` and the agent's `knowledge_search` return passages with file,
location and source hash. With `embedding_model` set in your user settings, `/kb embed` sends the
passages that have no current vector to an OpenAI-compatible `/embeddings` service (it prints how many
passages and characters it sent; nothing is sent until you run it), and `knowledge_search` then merges
word matches with meaning matches, so "vehicle" can find a passage that says "automobile". If the
service is down the search falls back to words and the result carries a notice. A hit from a file that has changed or gone since it was indexed is
marked `changed` or `missing` and listed last. Search is by words, not meaning. The index is written
under a lock, read again before each change and replaced whole with a revision number, so two
sessions adding files at once keep both.

**One name for every piece of memory.** `file:PATH`, `kb:PATH`, `wiki:PAGE` and `discovery:ID`
name what the project remembers. A wiki page can cite any of them in `sources`, not only files; the
page is recorded with the cited thing's fingerprint, so replacing a discovery, rewriting a page it
rests on or editing a cited file makes every page built on it read as stale. `/stale` lists what no
longer holds across the wiki, discoveries and knowledge base; `/stale REF` (and the agent's
`memory_status`) shows one item, its scope and what cites it. Scopes: *private* (one agent's
notes), *shared* (published discoveries) and *authoritative* (wiki pages, accepted discoveries).
Only the main agent or you write authoritative pages; a subagent publishes a discovery instead.

**`/discoveries`, findings with evidence.** Agents publish a claim about a topic with the
project files that support it (`discovery_publish`; a subagent can publish and read, not decide). It
becomes project knowledge only when you approve `discovery_accept`. Two different claims on one
topic show as a conflict, and accepting one must name every conflicting one it replaces. If a file
a discovery rests on changes, it reads as stale and cannot be accepted until it is published again.
Accepted, current discoveries are added to the agent's instructions. `/discoveries` lists the open
ones; `/discoveries accepted|rejected|all` or an id (`/discoveries d-0001`) shows more.

**`/wiki`, what the agent has learned.** Linked Markdown pages in `.nailong/wiki` (`/wiki` lists
them, `/wiki PAGE` shows one, `/wiki search TEXT` searches). The agent uses `wiki_read`,
`wiki_search` and `wiki_write`; a write needs a reason, rebuilds `index.md`, appends to `log.md`
and reports `[[links]]` that point nowhere. Every write is a numbered revision kept under
`.history/`; updating a page needs the `base_revision` that `wiki_read` returned, so a change made
in between is never overwritten. A page can list the project files it came from (`sources`) and
reads as stale when one changes. `/wiki history PAGE` shows revisions; `/wiki repair` restores the
pages, index and log from the history after a crash. Plan mode can read but not write.

**Built-in skills.** `/skills` lists eight skills that ship with the CLI: `paper-summary`,
`wiki-ingest`, `kb-answer`, `code-review`, `release-notes`, `analyze-spreadsheet`,
`incident-notes` and `server-health-check` (typed by you only). A skill of the same name in your
project or home folder replaces the built-in one. Design notes: `docs/documents-and-knowledge.md`.

## 13. Forge

`--forge` lets the agent **propose a new tool** (a Python function with tests). Nothing runs
unreviewed: a review screen shows the source, the diff against the active version, what the code
reaches for (network, processes), the agent's tests, and the isolation used (Docker by default,
no network, read-only, resource-capped). You approve, or reject with a reason that goes back to the
agent. `/forge list`, `/forge approve ID`, `/forge reject ID [reason]`,
`/forge rollback NAME VERSION`. Approved tools are versioned and hash-chained; `/verify` checks
them. Without Docker running, forged tools are not executed.

## 14. Scripts and CI

```bash
nailong -p "fix the failing test" --permission-mode accept-edits --allow "run_command:pytest*"
git diff | nailong -p "summarise this diff"          # piped input is added to the prompt
nailong -p "..." --output json                       # one JSON object
nailong -p "..." --output stream-json                # one JSON event per line
nailong -p "list the TODOs" --json-schema todos.json # answer shaped by a JSON Schema
nailong --review main                                # review changes since main
```

JSON output has `status`, `answer`, `steps`, `cost`, `session`, and `reason` on failure.

**Exit codes:** `0` completed · `1` failed · `2` blocked (an approval was refused) · `64` setup or
usage problem (the message names the setting) · `130` interrupted.

## 15. Self-hosted models and offline mode

```toml
provider = "openai-compatible"
model = "your-served-model-name"
base_url = "vllm"                    # or ollama | lmstudio | llamacpp | http://gpu-box:8000/v1
allow_insecure_http = true           # plain HTTP beyond this machine
timeout_seconds = 300
offline = true
```

No key is needed unless you set `api_key_env`. The server must support chat completions and tool
calling. `<think>…</think>` blocks in replies are removed. `offline = true` makes the CLI refuse to
start if the model host, `web`, `web_search` or an MCP URL would leave your network; it is a
configuration check, not a firewall. Full details: [local-models.md](local-models.md).

## 16. Cost, budget and effort

Prices are yours to supply (per million tokens), keyed by the model name in use:

```toml
[prices.your-model]
input_per_million = 3.0
cached_input_per_million = 0.3
output_per_million = 15.0
```

`/cost` then shows spending this run; the status line shows the total; `--max-budget-usd 2` stops
the next turn once spending reaches the limit. A model with no price is counted as unpriced and
`/cost` says which setting to add. `--effort` and `--param` pass provider parameters through.

## 17. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `nailong` prints `Missing setting(s): provider, model` and exits | no settings yet; in a terminal it offers setup, otherwise run `nailong --setup` or write the file (section 2) |
| It starts but shows no menus, spinner or colours, and says `not a terminal` | the shell did not give it a console. Git Bash (mintty) does this: run `winpty nailong`, or use PowerShell, cmd or Windows Terminal. Piped input also lands here by design |
| `The environment variable "X" named by api_key_env is not set ... no key is saved for it` | run `nailong --setup` to save a key, or export the variable in the shell that runs `nailong` |
| `provider anthropic needs api_key_env` | set `api_key_env` for Anthropic |
| `commands are NOT sandboxed` and a command is refused | use `sandbox = "docker"`, or pass `--allow-unsandboxed` knowingly |
| `unusable reply N times in a row` | the model cannot follow the tool format; use a larger model, lower `temperature`, or raise `invalid_output_retries` |
| `Provider response does not match the AgentTurn contract` | a model that ignores the reply format; same remedies |
| `offline is on but …` | the message names the setting to change |
| Hooks or MCP in `.nailong/config.toml` do nothing | by design: only your user file may set them |
| Ctrl+C does not stop a running command | the command may ignore it; use `/jobs` and `/kill` for background ones |

`/doctor` checks the key, settings files, git, and sandbox. `--verbose` shows full tool arguments;
`--debug-file run.jsonl` records the stream.

## Events, health and operations

Everything the CLI does is also written to a typed event log, one per run, under
`.nailong/events/`: `session`, `turn`, `tool`, `plan` and `task` spans (each a start and an end that
share a span id) and points such as `task.retry`, `wiki.written`, `discovery.published` and
`memory.compacted`. Each event carries its session, run, plan, task and agent ids, the id of the event
that caused it, and W3C trace and span ids, so a tool call can be followed up to its turn and a task
up to its plan. Each line holds the hash of the line before, so an edited or removed line shows. If a
run is killed, the next start closes its open spans as `interrupted`. Hooks get `NAILONG_TRACE_ID`,
`NAILONG_SPAN_ID` and `NAILONG_PARENT_EVENT_ID`.

`/health` and `nailong --health` summarise the project's state; `nailong --backup`, `--verify-backup`,
`--restore` and `--migrate` look after it. The procedures, what is backed up, rollback and what could
not be verified are in `docs/operations.md`; the threat model and its tests in `docs/threat-model.md`;
measured timings in `docs/benchmarks-cli.md`.

## Known limits

- Not run against a real self-hosted model (none was available).
- bubblewrap and Seatbelt are tested on the command lines they build, not executed.
- Background jobs, `--worktree` and the interactive prompt were exercised only through test harnesses (a captured terminal), not by a person at a real terminal.
- A shell command can still edit `.nailong/config.toml` unless a sandbox keeps it read-only; saved
  rules and hooks live outside the project, so such an edit cannot grant itself permission.
- `/rewind` cannot undo effects outside the project and added directories.
- Deliberately absent: login/OAuth and accounts, IDE integration, desktop/remote/cloud sessions, a
  plugin marketplace, `/cd`. See [cli-parity.md](cli-parity.md).

## Hooks written for Claude Code

A hook that uses a Claude Code event name or a `matcher` follows Claude Code's conventions:

```toml
[[hooks]]
event = "PreToolUse"
matcher = "Bash"
command = "python check_command.py"
```

- The event arrives as JSON on standard input: `session_id`, `hook_event_name`, `cwd`, `tool_name`
  (`Bash`, `Edit`, `Write`, `Read`, `Glob`, `Grep` for our tools of the same job), `tool_input`
  and, after a call, `tool_response`.
- Exit code 2 blocks the call and the standard error text goes to the model. Any other non-zero
  exit is only reported.
- A hook may print `{"decision": "block", "reason": "..."}`,
  `{"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "..."}}` or
  `{"continue": false, "stopReason": "..."}` to block. Rewriting a call's input and `allow`
  decisions are not supported.
- Hooks that use our own event names (`pre_tool` and so on) and no `matcher` keep the earlier
  behaviour: details in environment variables, any non-zero exit blocks. Set
  `protocol = "claude"` to switch one over.
- `/hooks` lists the hooks in force.

## Sharing hooks and MCP servers in a project

A project's `.nailong/config.toml` may define `[[hooks]]` and `[mcp.NAME]`, but they run programs,
so they are ignored until you approve them. Run `nailong --trust-project` (or `/trust-project`
inside a session): it prints every hook command and MCP server, and on a yes records a hash of
them in your user directory. They load the next time nailong starts there. If anyone changes the
file, they are ignored again until you approve the new version. All other project settings that
could widen access (`offline`, `require_sandbox`, `ssh_hosts`, and so on) stay user-only.

## Output for programs

`--output json` prints one summary object. `--output stream-json` prints this tool's own events.
`--output claude-stream-json` prints the event shapes Claude Code's stream uses, so programs
written for it can read the stream: `system` (init), `assistant`, `user` (tool results) and
`result` with `subtype`, `is_error`, `duration_ms`, `num_turns`, `total_cost_usd` and `usage`.
`nailong -p` with no prompt reads the prompt from standard input.
