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

**While a turn runs:** Esc or Ctrl+C stops it. Other keys typed during a turn are discarded (there
is no type-ahead queue). **In an approval menu:** ↑/↓ (or j/k, Tab) move, Enter confirms, 1-4 pick
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

Inside: `/sessions` lists them, `/resume` shows a picker, `/fork [turn]` branches here, `/new`
(or `/clear`) starts an empty one, `/verify` checks the hash chains of the session and of forged
tools, `/search TEXT` finds text across sessions, `/export [file]` writes the session as markdown
(default `.nailong/exports/ID.md`).

**Long sessions.** The model is sent a bounded digest of earlier turns, not the transcript.
`/compact [focus]` has the model write a summary that replaces older turns in what is sent;
`--autocompact` (or `autocompact = true`) does it automatically. `/context` shows what is sent.

## 8. Undo: `/rewind`

Before each turn the CLI snapshots the files the agent changes **and** what its commands change.

```text
> /rewind          # lists the turns that changed files
> /rewind 2        # shows what would be restored and deleted, asks, then restores
```

It restores the state before turn N. It covers the project and any `--add-dir` directories. It is
a convenience, not a substitute for git: it cannot undo effects outside those directories (a
database, a network call).

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
| `/sessions` `/resume` `/fork [N]` `/new` `/clear` | sessions |
| `/search TEXT` `/export [file]` | find in sessions; write markdown |
| `/init` | draft `AGENTS.md` (asks before saving) |
| `/diff` `/review` | show your uncommitted changes; have the agent review them (read-only) |
| `/rewind [N]` | undo changes (section 8) |
| `/suggest` | three useful next prompts |
| `/expand [N]` | full output of the last (or Nth) tool call |
| `/reasoning [on\|off]` | show or hide the model's reasoning as it streams |
| `/tools` `/memory` `/skills` `/agents` `/mcp` `/todos` `/plugins` | what the agent has |
| `/sandbox` `/verify` `/forge` | sandbox details; integrity check; forged tools |
| `/add-dir PATH [name]` | give the agent another directory |
| `/jobs` `/kill ID` | background commands |
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
| `--autocompact` | summarise old turns automatically |
| `--review [BASE]` | print a read-only review of uncommitted changes and exit |
| `--forge` | let the agent propose tools for your review |
| `--verbose` | show full tool arguments and results (and reasoning in `-p` mode) |
| `--no-reasoning` | do not show the model's reasoning |
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

# behaviour
permission_mode = "default"
max_steps = 30                      # model steps per turn; at the limit you are asked whether to keep going
context_tokens = 48000              # how much of the conversation the model is shown each step
tool_result_chars = 12000           # how much of each tool result it sees in full
sandbox = "auto"                    # auto | none | docker
docker_image = "python:3.12-slim"
web = false
web_search = false
autocompact = false
show_reasoning = true             # stream the model's reasoning when it sends any
notify = false                      # terminal bell when a turn ends
editing_mode = "emacs"              # or "vi"
instructions = "extra standing instructions"
max_budget_usd = 5.0

# user file only (a project file's copy is ignored, and /doctor says so)
offline = false
add_dirs = ["/abs/path/one"]
```

Tables: `[parameters]` (model parameters), `[prices.MODEL]` (section 16), `[agents.NAME]` (section
12), `[mcp.NAME]` and `[[hooks]]` (user file only).

## 12. Extending it

**Instruction files.** `NAILONG.md`, `AGENTS.md`, `CLAUDE.md` in the project (and
`~/.config/nailong/NAILONG.md` for yourself) are added to the agent's instructions. `/memory`
lists what was found.

**Custom commands.** A markdown file `.nailong/commands/ship.md` becomes `/ship`. Its text is sent
as the prompt; `$ARGUMENTS` is replaced by what you typed after the name.

**Skills.** Markdown files in `.nailong/skills/` (or `~/.config/nailong/skills/`). The agent is
given a one-line index (the first line of each file is its description) and loads a skill's full
text with a `use_skill` tool only when the task matches. A skill is limited to 20,000 characters.

**Hooks.** Shell commands the CLI runs around the agent, set in your **user** file only (a hook is
a program, so a project cannot add one):

```toml
[[hooks]]
event = "pre_tool"       # pre_tool | post_tool | session_start | session_end | prompt_submit | turn_end
tool = "run_command"     # glob, default *; for tool events
command = "scripts/check.sh"
```

A `pre_tool` hook that exits non-zero refuses the call (the model is told). A failing
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

## Known limits

- Not run against a real self-hosted model (none was available).
- bubblewrap and Seatbelt are tested on the command lines they build, not executed.
- Background jobs, `--worktree` and the interactive prompt were exercised only through test harnesses (a captured terminal), not by a person at a real terminal.
- A shell command can still edit `.nailong/config.toml` unless a sandbox keeps it read-only; saved
  rules and hooks live outside the project, so such an edit cannot grant itself permission.
- `/rewind` cannot undo effects outside the project and added directories.
- Deliberately absent: login/OAuth and accounts, IDE integration, desktop/remote/cloud sessions, a
  plugin marketplace, `/cd`. See [cli-parity.md](cli-parity.md).
