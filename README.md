# nailong-agent-sdk

A Python SDK for running agents whose work you can check afterwards. An agent runs under
permissions you hand it, and every step is written to a tamper-evident record that a third
party can verify without trusting the machine that produced it.

It has two layers:

- **`nailong_agent_sdk.starter`** - the short path. A model, instructions and a few tools, then
  `agent.run(...)`. Start here.
- **`nailong_agent_sdk`** - the full API underneath: agent definitions, governed tools, memory,
  multi-agent graphs, verification gates, an MCP server, and the evidence layer. Every starter
  object exposes it, so nothing is hidden when you outgrow the short path.

Requires Python 3.12 or newer. Status: alpha (`0.1.0`).

## Install

```bash
pip install -e .                       # the SDK, from a checkout
uv tool install --editable apps/nailong_cli   # the `nailong` command, on PATH everywhere
```

Both are built as wheels in CI and installed into a clean environment as a check. They are not on
PyPI yet; `.github/workflows/release.yml` publishes them (SDK first, then the CLI) when a `v*` tag is
pushed, once a PyPI trusted publisher is set up for each project.

## A first agent

```python
from nailong_agent_sdk.starter import Agent, openai_chat, tool


@tool(read_only=True)
def word_count(text: str) -> int:
    """Count the words in a piece of text."""
    return len(text.split())


agent = Agent(
    model=openai_chat(base_url="https://api.example.com/v1", api_key=KEY, model="model-name"),
    instructions="Answer briefly.",
    tools=[word_count],
)

answer = agent.run_sync("How many words are in 'to be or not to be'?")
print(answer.text if answer.ok else answer.reason)
```

The URL, key and model name are always explicit. The SDK reads no environment variable and picks
no default model. `examples/quick_start.py` runs the same shape offline with a scripted model, so
you can try it without spending tokens.

`run_sync` is for scripts. In async code use `await agent.run(...)`. Use `with Agent(...) as
agent:` (or call `agent.close()`) when you need to delete or move the workspace afterwards.

## What you get back

`agent.run` returns an `Answer`:

| | |
|---|---|
| `answer.ok` | the run completed |
| `answer.text` | the answer string (or the output as JSON when it is not one) |
| `answer.output` | the structured output; a pydantic object if you asked for one |
| `answer.reason` | why it did not complete, in one line, when it did not |
| `answer.steps` | model turns used |
| `answer.result` | the full `AgentResult`, with events, episodes and profile |

A run is bounded: `max_steps` (default 12) is the most model turns it may take. Pick the smallest
number your task needs.

## Tools

A tool is a plain function. Its docstring tells the model what it does, and its type hints become
the argument schema. Sync and async functions both work. An exception does not crash the run: the
model sees a failed result that names the tool and the exception.

```python
@tool(needs_approval=True)
def send_email(to: str, body: str) -> str:
    """Send an email."""
    ...
```

- `read_only=True` marks a tool that changes nothing, so calls to it may run side by side.
- `needs_approval=True` asks your `approve=` callback before each call. With no callback the call
  is blocked, never run.

```python
async def approve(request):
    return input(f"Allow {request.tool}({request.arguments})? [y/N] ") == "y"


agent = Agent(..., tools=[send_email], approve=approve)
```

The SDK ships governed file tools you can opt into:

```python
from nailong_agent_sdk.starter import core_tools

core_tools(Path("project"))                                # read_file, glob, grep
core_tools(Path("project"), writable=("drafts/notes.md",))  # plus write_draft, edit_draft
```

Writes are limited to the paths you declare. Nothing else is given to the agent: no shell, no
network, no files, unless you pass it.

## Structured output

```python
from pydantic import BaseModel


class Verdict(BaseModel):
    status: str
    score: int


agent = Agent(..., output=Verdict)
answer = agent.run_sync("Score this draft.")
answer.output.score
```

`output=` also takes a JSON Schema dict. Without it the agent answers with a single `answer`
string.

## Models and providers

Two adapters are built in. Everything else is a base URL.

```python
from nailong_agent_sdk.starter import OPENAI_COMPATIBLE_BASE_URLS, anthropic_messages, openai_chat

claude = anthropic_messages(api_key=KEY, model="claude-model-name")
router = openai_chat(
    base_url=OPENAI_COMPATIBLE_BASE_URLS["openrouter"], api_key=KEY, model="vendor/model-name"
)
```

| Provider | Use | What to know |
|---|---|---|
| Claude (Anthropic) | `anthropic_messages(...)` | Native Messages API: tool use, `x-api-key` auth, prompt-cache marker on the stable prefix, tool results returned with thinking blocks untouched. Not implemented: streaming, server-side tools. |
| OpenRouter | `openai_chat(base_url=OPENAI_COMPATIBLE_BASE_URLS["openrouter"], ...)` | Documented as OpenAI-compatible with tools. Model ids look like `vendor/model`. Routing options go in `parameters={"provider": {...}}`. An upstream failure that comes back as `finish_reason: "error"` is reported with the provider's message. |
| Gemini | `openai_chat(base_url=OPENAI_COMPATIBLE_BASE_URLS["gemini"], ...)` | Google's OpenAI-compatible endpoint, which Google calls beta. Tools are documented as supported. |
| Grok (xAI) | `openai_chat(base_url=OPENAI_COMPATIBLE_BASE_URLS["xai"], ...)` | The Chat Completions path is the one this adapter uses. xAI documents its Responses API as the main one, which this SDK does not call, so xAI-hosted tools such as web search are unavailable. |
| OpenAI | `openai_chat(base_url=OPENAI_COMPATIBLE_BASE_URLS["openai"], ...)` | Chat Completions. |
| Local servers (Ollama, vLLM, LM Studio, llama.cpp) | `openai_chat(base_url="http://localhost:...", allow_insecure_http=True, ...)` | Works only with a model that supports tool calls. If the server rejects `response_format`, pass `json_mode=False`. |
| Other OpenAI-compatible hosts (Fireworks, Together, Groq, Mistral, DeepSeek) | `openai_chat(...)` | The adapter was used against Fireworks in this repository's benchmark work. Check the host's tool-call support before relying on it. |
| Anthropic's OpenAI-compatibility endpoint | not recommended | Anthropic says it is for testing, and it rejects `json_object`, which the adapter sends when an agent has no tools. Use `anthropic_messages`. |
| Azure OpenAI, Amazon Bedrock, Google Vertex AI | not supported | They use different URLs and request signing. Write an `AgentModel` adapter. |

Use more than one by listing fallbacks:

```python
Agent(model=claude, fallbacks=[router], ...)
```

A transient failure (rate limit, 5xx, 529 overloaded, dropped connection) is retried with backoff
on the same model; any other failure moves to the next one.

## Control and observation

```python
def no_secrets(event):
    if "password" in str(event.arguments):
        return "arguments must not contain credentials"   # a reason refuses the call


agent = Agent(
    ...,
    before_tool=[no_secrets],            # None/True allows, False or a reason refuses
    after_tool=[log_result],             # sees the call and its result
    check=lambda out: out["answer"] != "",   # accept an answer only when this agrees
    on_stream=print_delta,               # text and tool-call fragments as they arrive
    deadline_seconds=120,                # wall-clock limit for the whole run
    tool_timeout_seconds=30,
)
```

- **Hooks** are plain functions, sync or async. A hook that returns something unexpected fails
  with a message naming it.
- **`check`** is the agent's verification gate. It returns `True` to accept, `False` or a reason to
  reject, or `(accepted, reason)`. A check that returns `None` is an error, so a forgotten
  `return` cannot pass an answer.
- **Streaming**: `async for item in agent.stream("...")` yields text and tool-call fragments as the
  model produces them, then the `Answer` last. A model that cannot stream yields only the
  `Answer`. Both the Claude and the OpenAI-style adapters stream.
- **Cancel**: pass `cancellation=CancelToken()` and call `token.cancel()`; the run ends as
  `cancelled`.
- **Cost**: give `prices=PriceTable(...)` and `answer.cost` holds tokens, cached tokens and money.
- **Skills**: `skills=[skill(Path("style.md"))]` hands the model a markdown file as task context.

## Sessions: resume and fork

An agent keeps no chat transcript by design: each run is a scoped task. A session adds continuity
you can see and check.

```python
session = agent.session("plan-1")             # new; "plan-1" is also the project id
session.run_sync("Draft the plan.")
session.run_sync("Tighten step 3.")           # the model is given a digest of turn 1

later = agent.resume("plan-1")                # in a new process, from disk
branch = later.fork("plan-1-alt", at_turn=1)  # a branch from turn 1; the original is untouched
branch.run_sync("Try a different approach.")
```

- Turns are saved under `.agent-sessions/` in the workspace as a hash chain. `resume` verifies it,
  so an edited, dropped or reordered turn is reported with the session and line.
- The model sees a **bounded digest** of earlier turns (prompt, status, answer, short tool
  summaries), newest kept first within `history_turns` and `history_chars`, plus how many turns
  were left out. It is not a model-written summary and not a full transcript.
- A turn is recorded when its run ends, so a run that crashes mid-way leaves no record.
- `fork` copies turns up to a point into a new session and records the source turn and its hash.
- Lower-level: `session="name"` on `agent.run` shares one project id, and `resume_from=answer`
  passes one earlier answer. Restarting an interrupted graph run is a core feature
  (`Orchestrator.resume_execution`, `recover_execution`).

## Coding tools

```python
from nailong_agent_sdk.starter import dev_tools

agent = Agent(..., tools=dev_tools(Path("my-project")), approve=ask_user)
```

`dev_tools(root)` gives the agent `read_file`, `glob` and `grep` (read-only), `write_file` and
`edit_file` (confined to the project), and `run_command`.

- Writes refuse paths outside the project, credential files such as `.env`, `.git` and the SDK's
  own `.agent-*` directories.
- Writes and commands **ask for approval by default**. Pass `approve_writes=False` or
  `approve_commands=False` to relax that, or `allow_writes=False` / `allow_commands=False` to leave
  a tool out.
- Commands have a time limit, an output cap and a short environment allowlist, so API keys in your
  environment are not passed on (name extra variables with `environment=[...]`).
- **A command runs with your user's rights unless you sandbox it.** It can read any file you can
  and use the network. Three ways to isolate it:
  - `dev_tools(root, docker_image="python:3.12-slim")`: a container with no network and only the
    project mounted. Needs a Docker daemon.
  - `dev_tools(root, sandbox=BubblewrapSandbox())` on Linux or `SeatbeltSandbox()` on macOS
    (`from nailong_agent_sdk.tools.os_sandbox import ...`): the project is writable, the rest of
    the filesystem is read-only, the home directory is hidden and the network is off.
    `default_os_sandbox()` picks the right one or returns `None`. Windows has no equivalent here;
    use Docker.
  - your own `sandbox=` backend (anything with the `SandboxBackend.run` shape).
  The OS backends are unit-tested for the command line and profile they build; the tests that run
  a real sandbox (`pytest -m os_sandbox`) only run on Linux or macOS and have not been run yet.
  They narrow what a command can reach; they are not a complete jail (no seccomp filter, and the
  command keeps your rights over what is left open).
- On Windows a command is written to a temporary batch file so it runs as written.

## Tools the agent writes itself

Yes, the SDK can create and modify its own tools, through a reviewed process rather than freely.

```python
from nailong_agent_sdk.starter import Forge

def review(proposal):                     # a person reads the code, the diff and the note
    print(proposal.diff, proposal.report)
    return input("Approve? [y/N] ") == "y"

forge = Forge(Path(".forge"), reviewer=review)
agent = Agent(..., tools=[forge])
```

The agent gets `propose_tool`, `use_forged_tool` and `list_forged_tools`. To add a capability it
sends Python source defining `run(**arguments)`, a JSON Schema and **test cases**.

1. **Nothing runs on proposal.** The proposal is stored and the reviewer sees the source, a diff
   against the current version and a note on what the code imports and calls.
2. **After approval, the tests run** in a disposable subprocess (fresh directory, short
   environment, time and output limits). Every test must pass or the tool stays inactive, and the
   agent is told which test failed so it can propose a fix.
3. **An approved tool is versioned.** It is usable at once through `use_forged_tool`, and from the
   next run it appears under its own name (`forged_<name>`) with its real schema.
4. **Modifying is a new version** with a diff. The host can `rollback` or `retire`; the agent can
   only propose.
5. Every step is in a hash-chained event log, and the source hash recorded at approval is checked
   before every call, so a file edited afterwards is refused.

Without a `reviewer`, proposals wait; approve them out of band with `await forge.approve("p-0001")`
or `forge.reject(...)`.

What this does **not** do, so you know what you are relying on:

- By default tests and calls run in a **Docker container**: no network, read-only root, the forge
  directory mounted read-only, all capabilities dropped, no new privileges, 64 processes, memory
  and CPU limits, an unprivileged user. It needs a reachable Docker daemon and an image with
  `python` (`docker_image=`, default `python:3.12-slim`; pin yours by digest). This path is tested
  here with a recording backend; the tests that start a real container (`pytest -m docker`) need
  a running daemon and were not run on the machine that wrote them.
- `Forge(..., isolation="native")` runs tools as a subprocess with your user's rights; you must ask
  for it, and then the reviewer reading the code is the only control.
- The code note shown to the reviewer is a reading aid, not a security check.
- Forged tools are plain functions of their arguments using the standard library. They cannot
  call other tools or see the agent's files.
- Only tools evolve. Instructions, skills and the agent's own code are not self-modified.
- There is no automatic judgement of whether a tool is *good*; the tests are the agent's own, so a
  weak test proves little. Review the tests as well as the code.

## MCP servers

```python
from nailong_agent_sdk.mcp.client_types import McpStdioServerConfig
from nailong_agent_sdk.starter import connect_mcp

async with await connect_mcp([McpStdioServerConfig(name="files", command="...", args=[...])]) as mcp:
    agent = Agent(..., tools=[mcp], approve=ask_user)
    answer = await agent.run("...")
```

- Tool names are `mcp__<server>__<tool>`, and results are marked untrusted.
- Because the SDK cannot know what an external tool does, calls need approval by default. Pass
  `needs_approval=False` to `connect_mcp` for a server you trust.
- The connection belongs to one event loop: use `await agent.run(...)`, not `run_sync`, and close
  the group yourself (the `async with` above does).

## Agents as tools

```python
researcher = Agent(..., name="researcher")
lead = Agent(..., tools=[researcher.as_tool(name="research", description="Look something up.")])
```

The lead calls the researcher like any tool. If the researcher does not finish, the lead sees a
failed result that names the researcher and why. For plans with approvals, parallel nodes and
exploration, use the core `Orchestrator`.

## Where each core feature shows up

| Core feature | In the starter |
|---|---|
| Tool executor and capability checks | `tools=[...]`, `@tool`, `approve=`; function tools are allow-listed by being passed |
| Pre/post tool hooks | `before_tool=`, `after_tool=` |
| Verification gates | `check=` |
| Streaming | `on_stream=`, `agent.stream(...)` |
| Cancellation | `cancellation=CancelToken()` |
| Watchdog limits | `deadline_seconds=`, `model_turn_timeout_seconds=`, `tool_timeout_seconds=` |
| Skills | `skills=` |
| Project state, resume and fork | `agent.session(...)`, `agent.resume(...)`, `session.fork(...)`, `session=`, `resume_from=` |
| Context projection and memory compaction | `context=`, `memory=`, `history_limit=` |
| Cost model | `prices=`, `answer.cost` |
| MCP client | `connect_mcp(...)` |
| Fallback models | `fallbacks=` |
| Durable stores, audit, signed checkpoints | `workspace=`, `evidence=`, `services=` |
| Delegation | `agent.as_tool(...)` |
| Process supervisor, sandbox backends | `dev_tools(...)` (`run_command`, `docker_image=`, `sandbox=`) |
| Governed tool creation | `Forge(...)` |

Not in the starter, and used through the core instead: the `Orchestrator` and state graph
(plans, approvals, elastic nodes, resumption), the capability registry and policy
(`HarnessToolRegistry`), the task runner for backends, git worktree isolation, run retention
and pruning, retrieval and specifications, and the MCP server. `agent.services`,
`agent.definition` and `answer.result` give you the core objects to start from.

## What is recorded

Each agent writes to its `workspace` (default `.nailong-agent`): a hash-chained audit log, a
telemetry store and the results of tool calls. The run head is signed and kept outside the
workspace, so a later edit or truncation of the record is detectable.

```bash
nailong-agent-sdk-dev --help
```

The developer command inspects runs, finds where a failed one went wrong and verifies the
evidence. How the evidence works, and what it does and does not protect against, is in
[docs/standards-mapping.md](docs/standards-mapping.md).

## A terminal app

`apps/nailong_cli` is an interactive command line built on this layer: approvals before writes
and commands, optional sandboxing, saved and forkable sessions and reviewed tool creation. See
[apps/nailong_cli/README.md](apps/nailong_cli/README.md) and
[docs/interactive-cli.md](docs/interactive-cli.md); [docs/cli-parity.md](docs/cli-parity.md); hosting your own model, including an offline mode: [docs/local-models.md](docs/local-models.md); building a team of agents: [docs/multi-agent-tutorial.md](docs/multi-agent-tutorial.md)
compares it with Claude Code and Codex. It has not been run against a live model.

## Going deeper

| To | Read |
|---|---|
| find the file that owns a behaviour | [docs/where-things-live.md](docs/where-things-live.md) |
| see every module, class and function | [docs/reference/README.md](docs/reference/README.md) |
| understand the evidence layer against standards | [docs/standards-mapping.md](docs/standards-mapping.md) |
| run several agents as a graph, add memory, expose an MCP server | the examples in `examples/` and the reference above |

The starter layer only composes these parts. Reach the full objects through `agent.definition`,
`agent.services` and `answer.result`.

## Limits

- The starter layer has been tested with scripted models only. It has not been run against a live
  provider yet, so the first live run is the real check of the prompt and the output format.
- Two adapters are built in: the Claude Messages API and OpenAI-style Chat Completions. A provider
  with neither needs an adapter that implements `AgentModel`; wrap it in `ModelSetup` to use it
  with `Agent`. Neither adapter has been run against a paid live provider since the starter layer
  was added; they are tested against a local server that returns each provider's documented
  response shapes.
- HTTP errors from the Claude adapter carry `OPENAI_COMPATIBLE_*` codes and say "OpenAI-compatible
  provider", because both adapters share one JSON transport. The `ANTHROPIC_MESSAGES_*` codes
  cover everything after the response arrives.
- `dev_tools` gives a shell and file editing but no web tool and no interactive terminal. Native
  command execution is supervised, not isolated.
- Streaming is implemented for both adapters but only tested against a local server that sends
  each provider's documented event format.
- Evidence signing keeps its key and anchors in your home directory by default
  (`NAILONG_ANCHOR_HOME`). That protects against edits to the workspace, not against a process
  that can also read that directory. See the threat model in the reference.

## License

MIT. See [LICENSE](LICENSE).
