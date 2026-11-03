# The interactive command line: where it lives and how it behaves

## Where it lives

Not in the SDK package, and not in `agent_assisted_design`.

| Place | Why not / why |
|---|---|
| `nailong_agent_sdk` (the library) | The SDK is the agent a backend calls. A terminal UI adds a dependency, a lifecycle and opinions (colours, key bindings, prompts) that every embedder would inherit. |
| `agent_assisted_design` | That is the EDA design product: a TypeScript backend that owns design sessions and the G1-G4 review gates. A general coding CLI inside it would couple an unrelated tool to one product's stages. That backend keeps talking to the SDK through MCP. |
| **`apps/nailong_cli`** (new package in this repository) | Same pattern as `apps/sg_job_agent`: its own `pyproject.toml`, depends on `nailong-agent-sdk`, ships its own entry point, is not in the SDK wheel. |

The SDK's job is to expose seams the CLI plugs into. It already has them:

| CLI need | SDK seam |
|---|---|
| Ask before a risky action | `Agent(approve=...)` receives the tool and its arguments |
| Show progress as text arrives | `Agent.stream()` / `on_stream=` |
| Show each tool call and result | `before_tool=` and `after_tool=` hooks |
| Stop a run | `CancelToken`, or cancelling the asyncio task |
| Continue, branch, list | `agent.session`, `agent.resume`, `session.fork`, `agent.sessions()` |
| Run code safely | `dev_tools(root, sandbox=default_os_sandbox())` or `docker_image=` |
| Review model-written tools | `Forge(reviewer=...)`, `forge.pending()`, `forge.approve()` |
| Cost and status | `answer.cost`, `answer.steps` |
| Credentials | the CLI reads them and passes them in; the SDK never looks in the environment |

Sessions in the starter layer are a convenience. The core and the MCP surface stay session-free,
and the design product's backend keeps owning its own sessions and gates.

## How it should behave

**Start.** `nailong` in a project directory opens a prompt. Settings come from flags, then
`./.nailong/config.toml`, then `~/.config/nailong/config.toml`. The config names the provider, the
model and the *name* of the environment variable that holds the key; the CLI reads that variable
and passes the value in. Nothing is guessed: with no model configured it says so and exits.

**One turn.** The user types a prompt. The CLI streams the answer as it arrives. Each tool call is
one line (`read_file src/app.py`, `run_command pytest -q`), its result is collapsed to a status and
a few lines, and `Ctrl+O` expands it. The final answer is printed under a rule with steps and cost.

**Approvals.** Reading is free. Writing a file or running a command pauses with the exact action:
for an edit, a unified diff; for a command, the command line and whether a sandbox is active.
Choices: yes once, yes for this session, no, no and tell the model why. "Always allow" rules
(`run_command` matching `pytest*`) are saved in the *user's* `~/.config/nailong/permissions.toml`,
keyed by project path, never inside the project: a file the agent can write must not be able to
grant the agent permission. The SDK holds no such list. The project's `.nailong/` directory and
`NAILONG.md` are also protected from the agent's file tools. With no sandbox available the prompt says **not sandboxed** in plain words, and
the CLI will not run commands unattended in that state.

**Interrupt.** First `Ctrl+C` cancels the current run and returns to the prompt with the partial
output kept; the run ends as `cancelled` and the audit record says so. A second `Ctrl+C` within two
seconds exits. A command already started is stopped by the supervisor, which ends its process tree.

**Sessions.** Every conversation is an SDK session in the workspace.
`nailong --continue` resumes the latest, `--resume <id>` a named one, `--fork <id> [--at N]`
branches. Inside: `/sessions`, `/resume`, `/fork [turn]`, `/new`. On resume the CLI says how much
history the model is given and how many turns were left out, because the model sees a bounded
digest, not the whole transcript.

**Slash commands.** `/help`, `/model`, `/tools`, `/cost`, `/sandbox` (what is active and what each
command can reach), `/forge` (pending, active, review, rollback), `/verify` (checks the session and
forge hash chains and the run's signed checkpoints), `/exit`.

**Forged tools.** A proposal opens a review screen: the source, the diff against the active
version, the imports and calls the code reaches for, the agent's tests, and the isolation that will
be used (Docker by default). Approval is typed, not a single key, when the code touches the
network or processes. A rejection reason goes back to the agent.

**Non-interactive.** `nailong -p "prompt"` runs once and prints the answer; `--output json` prints
the status, answer, steps and cost for scripts; exit code 0 means completed, 1 failed, 2 blocked
for approval, 130 interrupted. With no TTY, an action that needs approval is refused rather than
assumed, unless the rule for it is already in the project config.

**Status.** `apps/nailong_cli` implements this page and the baseline that Claude Code and Codex
share; [cli-parity.md](cli-parity.md) lists what is built, partly built and deliberately not built (accounts and OAuth among them: this is research tooling that runs locally with your own API key). It is tested
with scripted models and has not been run against a live model.

**Output.** Plain text that works in any terminal, including Windows; colour only on a TTY and
off with `--no-color`. No cursor tricks that break when piped.

## What the SDK may still need for this

These are gaps, not blockers: a richer approval request (tool description, a preview the tool
computes, which sandbox applies) so the CLI does not re-derive them; a tool-start event with the
arguments in the stream, so the CLI need not wrap hooks to draw progress; and cancellation of a
model call in flight, which works through asyncio task cancellation but is reported only
approximately for providers that cannot stop a request.
