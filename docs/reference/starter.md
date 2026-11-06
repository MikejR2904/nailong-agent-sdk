# `starter/` - the short path: a model, instructions and a few tools, then one call

A wrapper over the existing parts for a caller who wants a working agent without assembling an `AgentDefinition`, `AgentRuntimeServices`, `ScopedAgentTask`, executor and model binding by hand. It chooses nothing for the caller: the model, every tool, the approval callback and the workspace are arguments. The full API underneath stays available through `Agent.definition`, `Agent.services` and `Answer.result`.

| File | Lines | Role |
|---|---:|---|
| [`starter/__init__.py`](#starter__init__py---the-short-path-public-names-of-the-starter-layer) | 53 | the short path: public names of the starter layer |
| [`starter/agent.py`](#starteragentpy---one-object-that-builds-the-definition-runtime-and-task-runs-them-and-reports-plainly) | 397 | one object that builds the definition, runtime and task, runs them and reports plainly |
| [`starter/cancel.py`](#startercancelpy---a-flag-a-caller-sets-to-stop-a-run-between-steps) | 16 | a flag a caller sets to stop a run between steps |
| [`starter/checks.py`](#startercheckspy---output-checks-as-plain-functions-accept-an-answer-only-when-something-else-agrees) | 40 | output checks as plain functions: accept an answer only when something else agrees |
| [`starter/chain_log.py`](#starterchain_logpy---an-append-only-jsonl-file-whose-records-are-chained-by-hash) | 79 | an append-only JSONL file whose records are chained by hash |
| [`starter/dev_tools.py`](#starterdev_toolspy---coding-tools-for-a-project-directory-read-search-write-edit-and-run-commands) | 228 | coding tools for a project directory: read, search, write, edit and run commands |
| [`starter/forge.py`](#starterforgepy---tools-an-agent-proposes-a-reviewer-approves-tests-prove-and-the-host-can-roll-back) | 533 | tools an agent proposes, a reviewer approves, tests prove and the host can roll back |
| [`starter/forge_execution.py`](#starterforge_executionpy---run-a-forged-tools-source-in-a-disposable-subprocess-and-read-back-its-json-result) | 131 | run a forged tool's source in a disposable subprocess and read back its JSON result |
| [`starter/forge_report.py`](#starterforge_reportpy---what-a-proposed-tools-source-reaches-for-as-a-note-for-the-reviewer) | 54 | what a proposed tool's source reaches for, as a note for the reviewer |
| [`starter/forge_runner.py`](#starterforge_runnerpy---standalone-script-a-forged-tool-runs-under) | 29 | standalone script a forged tool runs under |
| [`starter/hooks.py`](#starterhookspy---tool-hooks-as-plain-functions-look-at-a-call-before-it-runs-or-at-its-result-after) | 79 | tool hooks as plain functions: look at a call before it runs, or at its result after |
| [`starter/images.py`](#starterimagespy---pictures-to-show-the-model-a-file-becomes-a-size-checked-base64-attachment) | 47 | pictures to show the model: a file becomes a size-checked, base64 attachment |
| [`starter/mcp_tools.py`](#startermcp_toolspy---tools-from-external-mcp-servers-offered-to-an-agent-like-any-other-tool) | 96 | tools from external MCP servers, offered to an agent like any other tool |
| [`starter/notebook_tools.py`](#starternotebook_toolspy---edit-a-cell-of-a-jupyter-notebook-anywhere-in-a-project-without-running-it) | 60 | edit a cell of a Jupyter notebook anywhere in a project, without running it |
| [`starter/planner.py`](#starterplannerpy---turn-a-plain-request-into-a-validated-plan-then-run-it-as-one-agent-or-as-a-team) | 591 | turn a plain request into a validated plan, then run it as one agent or as a team |
| [`starter/session_store.py`](#startersession_storepy---a-sessions-turns-on-disk-as-a-hash-chain) | 118 | a session's turns on disk as a hash chain |
| [`starter/sessions.py`](#startersessionspy---sessions-continue-a-conversation-across-runs-and-processes-or-fork-it) | 146 | sessions: continue a conversation across runs and processes, or fork it |
| [`starter/skills.py`](#starterskillspy---skills-as-files-a-markdown-file-becomes-task-context-the-model-is-given) | 23 | skills as files: a markdown file becomes task context the model is given |
| [`starter/core_tools.py`](#startercore_toolspy---the-sdks-governed-file-tools-offered-to-an-agent-with-one-call) | 51 | the SDK's governed file tools, offered to an agent with one call |
| [`starter/models.py`](#startermodelspy---name-a-model-once-the-adapter-and-the-binding-the-agent-definition-needs) | 92 | name a model once: the adapter and the binding the agent definition needs |
| [`starter/toolbox.py`](#startertoolboxpy---everything-an-agent-may-call-behind-one-executor-that-runs-only-what-was-handed-to-it) | 129 | everything an agent may call, behind one executor that runs only what was handed to it |
| [`starter/tools.py`](#startertoolspy---turn-a-plain-python-function-into-a-tool-the-sdk-can-declare-validate-and-run) | 110 | turn a plain Python function into a tool the SDK can declare, validate and run |

---

### `starter/__init__.py` - the short path: public names of the starter layer

*Re-exports the starter layer's public names so one import line is enough: `from nailong_agent_sdk.starter import Agent, tool, openai_chat`.*

**Role in the workflow.** The entry point for a caller who wants a working agent in a few lines. Everything it names is built from existing SDK parts; it adds no tool, permission or model of its own.

**Contents**

- (no definitions)

---

### `starter/agent.py` - one object that builds the definition, runtime and task, runs them and reports plainly

*397 lines · depends on: `agent/base_agent/types.py`, `agent/cost_model.py`, `agent/evidence_profile/__init__.py`, `agent/model.py`, `agent/runtime.py`, `agent/verification.py`, `foundations/contracts.py`, `memory/context_projection.py`, `memory/episode_store.py`, `starter/checks.py`, `starter/hooks.py`, `starter/models.py`, `starter/session_store.py`, `starter/sessions.py`, `starter/toolbox.py`, `starter/tools.py` · used by: `starter/__init__.py`, `starter/sessions.py` · not re-exported at the package root*

**Role in the workflow.** `Agent` is the wrapper over `AgentDefinition`, `AgentRuntimeServices`, `ScopedAgentTask` and `BaseAgent`. Its constructor takes a `ModelSetup`, instructions and the tools the caller hands over, and builds the definition once (default output: an object with `status` `"complete"` and a string `answer`; or a JSON Schema dict; or a pydantic class). Each `run` makes a fresh task id and a fresh `BaseAgent`, so one `Agent` can serve concurrent runs. The result is an `Answer`: `ok`, `text`, `output`, `reason`, `steps`, and the full `AgentResult` in `result`.

**Contents**

- **class `Answer`** - What a run returns: `status`, `output`, `reason`, `steps`, the full `AgentResult` in `result`, the `session` (project id) it ran under and `cost` (the cost ledger as JSON when the agent was given prices, otherwise none).
  - `Answer.ok(self) -> bool` - Property: true only when the run's status is `completed`.
  - `Answer.text(self) -> str` - Property: the output's `answer` string, a pydantic output as JSON, other output as sorted JSON, or an empty string when there is none.
  - `Answer.__str__(self) -> str` - The text of a completed run, otherwise the status and the recorded reason on one line.
- **class `Agent`** - Builds the definition and runtime once and runs tasks on them. The caller supplies the model, every tool and any approval callback; nothing is chosen for it.
  - `Agent.__init__(self, *, model: ModelSetup, instructions: str, tools: Sequence[FunctionTool | ToolGroup]=(), fallbacks: Sequence[ModelSetup]=(), output: dict[str, Any] | type[BaseModel] | None=None, approve: Approver | None=None, recoverable_refusals: bool=False, before_tool: Sequence[BeforeTool]=(), after_tool: Sequence[AfterTool]=(), check: OutputCheck | None=None, on_stream: ModelStreamListener | None=None, skills: Sequence[SkillContext]=(), max_steps: int=12, deadline_seconds: float | None=None, model_turn_timeout_seconds: float | None=None, tool_timeout_seconds: float | None=None, context: ContextProjectionPolicy | None=None, memory: Callable[[], InMemoryEpisodeStore] | None=None, history_limit: int | None=None, prices: PriceTable | None=None, workspace: Path=DEFAULT_WORKSPACE, evidence: EvidenceProfile | None=None, services: AgentRuntimeServices | None=None, name: str='agent') -> None` - Builds the tool box, the model (a failover chain when `fallbacks` is given), the optional hooks, output check, skills, watchdog limits, context policy, memory factory, history limit and price table, then opens the durable stores below `workspace` (with `evidence` applied) unless `services` is given, and the session store beside them. `recoverable_refusals` runs the before-hooks inside the executor and returns refusals and declined approvals as failed results, so the model can adapt and the run goes on (an interactive application wants this); the default keeps the core's behaviour, where a refusal ends the run as blocked. Passing both `services` and `evidence` raises `ValueError`. `max_steps` becomes the termination policy's iteration limit.
  - `Agent.name(self) -> str` - Property: the agent's name, used in task ids and session records.
  - `Agent.definition(self) -> AgentDefinition` - Property: the `AgentDefinition` built from the agent's settings and its tools as they are now, so tools a forge activated since the last run are included.
  - `Agent.session(self, name: str | None=None, *, history_turns: int=DEFAULT_HISTORY_TURNS, history_chars: int=DEFAULT_HISTORY_CHARS) -> Session` - Starts a new, empty session under this agent (a generated id when no name is given); an existing id raises `SESSION_EXISTS`.
  - `Agent.resume(self, name: str, *, history_turns: int=DEFAULT_HISTORY_TURNS, history_chars: int=DEFAULT_HISTORY_CHARS) -> Session` - Opens an existing session after verifying its hash chain; raises `SESSION_NOT_FOUND` or `CHAIN_LOG_BROKEN` naming the session and line.
  - `Agent.sessions(self) -> list[str]` - The ids of the sessions saved in this workspace.
  - `Agent.latest_session(self) -> str | None` - The id of the session written most recently, or none when there are no sessions.
  - `Agent.delete_session(self, session_id: str) -> None` - Deletes a saved session's log for good; raises `SESSION_NOT_FOUND` naming the saved sessions when there is none by that id. The caller decides whether to ask first and which session is in use.
  - `Agent.run(self, prompt: str, *, input: dict[str, Any] | None=None, criteria: Sequence[str]=(), skills: Sequence[SkillContext]=(), session: str | None=None, resume_from: Answer | None=None, cancellation: CancellationToken | None=None) -> Answer` - Runs one prompt as a `ScopedAgentTask` with a new id on a new `BaseAgent` from the runtime services; `input`, `criteria`, `skills`, `session`, `resume_from` and `cancellation` pass through. Raises only what `BaseAgent.run` raises.
  - `Agent.stream(self, prompt: str, *, input: dict[str, Any] | None=None, criteria: Sequence[str]=(), skills: Sequence[SkillContext]=(), session: str | None=None, resume_from: Answer | None=None, cancellation: CancellationToken | None=None) -> AsyncIterator[ModelStreamEvent | Answer]` - Runs like `run` but yields each model stream event (`ModelTextDelta`, `ModelToolCallDelta`, `ModelStreamCompleted`) as it arrives and the `Answer` last. A model that cannot stream yields only the `Answer`. Leaving the loop early cancels the run.
  - `Agent.stream.listener(event: ModelStreamEvent) -> None` - Passes each event to the `on_stream` callback, then to the queue the generator reads.
  - `Agent.stream.drive() -> Answer` - Runs the task in the background and always signals the generator when it ends, so the loop cannot hang.
  - `Agent.run_sync(self, prompt: str, *, input: dict[str, Any] | None=None, criteria: Sequence[str]=(), skills: Sequence[SkillContext]=(), session: str | None=None, resume_from: Answer | None=None) -> Answer` - `asyncio.run` of `run` for scripts; inside a running event loop, or with MCP tools whose connections belong to one loop, it raises a `RuntimeError` that says to await `run` instead.
  - `Agent.as_tool(self, *, name: str, description: str, needs_approval: bool=False) -> FunctionTool` - Wraps this agent as a tool for another agent: the call runs a new task and returns its text, and a run that does not complete raises an error naming the agent and the reason, which the parent sees as a failed tool result.
  - `Agent.as_tool.delegate(task: str) -> str` - The function the parent calls: runs the task and returns the answer text.
  - `Agent.say(self, message: str) -> None` - Tells the running agent something; it reads the message at its next step, or at the start of the next run if none is active. Raises `ValueError` from `MessageInbox.put` when the message is empty, too long or the queue is full.
  - `Agent.close(self) -> None` - Closes the audit-log handles and the telemetry connection so the workspace can be moved or deleted, which Windows refuses while they are open; repeated calls are safe. Tool groups the caller connected, such as MCP, are the caller's to close.
  - `Agent.__enter__(self) -> Agent` - Returns the agent, so `with Agent(...) as agent:` closes it on exit.
  - `Agent.__exit__(self, *exception: object) -> None` - Calls `close`; an exception from the block still propagates.
  - `Agent._execute(self, prompt: str, input: dict[str, Any] | None, criteria: Sequence[str], skills: Sequence[SkillContext], session: str | None, resume_from: Answer | None, cancellation: CancellationToken | None, listener: ModelStreamListener | None) -> Answer` - Builds the task (skills from the agent and the call; `session` becomes the project id so project state accumulates; `resume_from` adds the earlier answer as `previous_answer` and reuses its session), creates a fresh `BaseAgent` with the hooks, gate, watchdog, memory and price settings, runs it, and reads the cost ledger.
  - `Agent._answer(self, result: AgentResult, session: str, cost: dict[str, Any] | None) -> Answer` - Turns an `AgentResult` into an `Answer`; with a pydantic output class, validates the output and turns a validation error into a failed answer that names the class and the error.
- `_model_for(model: ModelSetup, fallbacks: Sequence[ModelSetup]) -> AgentModel` - The primary adapter alone, or a `FailoverAgentModel` over the primary and the fallbacks with one binding each, primary first.
- `_output_schema(output: dict[str, Any] | type[BaseModel] | None) -> dict[str, Any]` - The default answer schema, a pydantic class's JSON Schema, or the dict as given.
- `_gates_for(check: OutputCheck | None) -> VerificationGateRegistry | None` - A verification-gate registry holding the caller's output check under one id, or none when there is no check.
- `_watchdog_for(deadline: float | None, model_turn: float | None, tool_call: float | None) -> AgentWatchdogPolicy | None` - An `AgentWatchdogPolicy` from the three optional limits, or none when none is set.
- `_json_safe(value: Any) -> Any` - A pydantic output as plain JSON data, otherwise the value unchanged.

---

### `starter/cancel.py` - a flag a caller sets to stop a run between steps

**Role in the workflow.** `CancelToken` satisfies the core `CancellationToken` protocol: pass it as `cancellation=` to `Agent.run`, call `cancel()` from anywhere, and the run ends with status `cancelled` at its next check.

**Contents**

- **class `CancelToken`** - A one-way flag: once cancelled it stays cancelled.
  - `CancelToken.__init__(self) -> None` - Starts not cancelled.
  - `CancelToken.cancel(self) -> None` - Marks the token cancelled.
  - `CancelToken.is_cancelled(self) -> bool` - True after `cancel` was called.

---

### `starter/checks.py` - output checks as plain functions: accept an answer only when something else agrees

**Role in the workflow.** `Agent(check=fn)` registers `fn` as the agent's verification gate. `fn` gets the candidate output and returns `True` to accept it, `False` or a non-empty reason string to reject it, or `(accepted, reason)`; sync or async. Any other return, including `None`, is an error that names the check, so a forgotten `return` cannot pass an output.

**Contents**

- `gate_callback(check: OutputCheck) -> Callable[..., Any]` - Adapts a check to the core gate callback shape and enforces the allowed return values.
- `gate_callback.callback(context: VerificationContext) -> VerificationReturn` - Calls the check (awaiting it when async) and converts its verdict to `(accepted, reason)`, raising `TypeError` for an unsupported return.

---

### `starter/chain_log.py` - an append-only JSONL file whose records are chained by hash

**Role in the workflow.** Each record carries the hash of the one before it and its own hash over the RFC 8785 form of its content, so changing, removing or reordering a line is found when the file is read. Sessions and the forge's event log both use it. It detects edits; it does not stop them, and the whole file can be rewritten consistently by someone who recomputes every hash.

**Contents**

- **class `ChainedLog`** - One chained file and the label that names it in error messages.
  - `ChainedLog.__init__(self, path: Path, label: str) -> None` - Remembers the path and the label.
  - `ChainedLog.path(self) -> Path` - Property: the file's path.
  - `ChainedLog.exists(self) -> bool` - True when the file exists.
  - `ChainedLog.append(self, record: dict[str, Any], *, new: bool=False) -> dict[str, Any]` - Seals the record with the previous hash and its own, then writes and syncs one line; `new=True` creates the file and fails if it exists.
  - `ChainedLog.read(self) -> list[dict[str, Any]]` - Every record in order, verified; raises `CHAIN_LOG_BROKEN` naming the log, the line and what is wrong (not JSON, does not follow, content does not match its hash).
  - `ChainedLog._last_hash(self) -> str` - The hash of the newest record, or the genesis value for an empty log.
  - `ChainedLog._broken(self, line_number: int, why: str) -> AgentSdkError` - The error for a failed verification.
- `_digest(record: dict[str, Any]) -> str` - The SHA-256 of a record's RFC 8785 form.
- `_seal(record: dict[str, Any]) -> dict[str, Any]` - The record with its hash added.

---

### `starter/dev_tools.py` - coding tools for a project directory: read, search, write, edit and run commands

*228 lines · depends on: `foundations/atomic_io.py`, `starter/core_tools.py`, `starter/tools.py`, `tools/policy.py`, `tools/sandbox.py`, `tools/sandbox_models.py`, `tools/supervisor.py` · used by: `starter/__init__.py` · not re-exported at the package root*

**Role in the workflow.** `dev_tools(root)` returns tools to pass in `Agent(tools=...)`: the core read tools (`read_file`, `glob`, `grep`), `write_file` and `edit_file` confined to the project directory, and `run_command`. Writes refuse paths outside the project, credential paths, `.git` and the SDK's own `.agent-*` directories. Writes and commands need approval by default (`approve_writes`, `approve_commands`). Commands run through the core `ProcessSupervisor` with a time limit, an output cap and an environment limited to a short allowlist, so API keys in the host environment do not reach them. On Windows the command is written to a temporary batch file because `cmd.exe` does not understand the escaping argument lists use. Natively a command can still read any file the host user can and use the network; for isolation pass `docker_image=` (network disabled, project mounted) or a `sandbox=` backend.

**Contents**

- `dev_tools(root: Path, *, allow_writes: bool=True, approve_writes: bool=True, allow_commands: bool=True, approve_commands: bool=True, command_timeout_seconds: float=120.0, max_output_bytes: int=65536, cpu_seconds: int | None=None, environment: Sequence[str]=(), docker_image: str | None=None, sandbox: SandboxBackend | None=None, max_file_bytes: int=MAX_FILE_BYTES, protected: Sequence[str]=()) -> list[FunctionTool | CoreTools]` - The read tools, the write tools unless `allow_writes` is false, and the command tool unless `allow_commands` is false. `protected` names directory or file names the write tools refuse anywhere in a path (an application protects its own settings this way). Refuses a missing directory and `docker_image` together with `sandbox`.
- `_write_tools(base: Path, needs_approval: bool, max_file_bytes: int, protected: frozenset[str]) -> list[FunctionTool]` - Builds `write_file` and `edit_file` with the size limit, the approval setting and the protected names.
- `_write_tools.write_file(path: str, content: str) -> dict[str, Any]` - Writes a UTF-8 file atomically, creating parent directories; refuses content over the size limit.
- `_write_tools.edit_file(path: str, old_text: str, new_text: str, replace_all: bool=False) -> dict[str, Any]` - Replaces exact text in an existing file: a miss, an empty `old_text` and an ambiguous match each raise an error that says what to change.
- `_command_tool(base: Path, needs_approval: bool, timeout_limit: float, max_output_bytes: int, cpu_seconds: int | None, extra_variables: Sequence[str], backend: SandboxBackend | None) -> FunctionTool` - Builds `run_command` for a project directory with its timeout, output cap, CPU limit, environment allowlist and optional sandbox backend.
- `_command_tool.run_command(command: str, timeout_seconds: float | None=None) -> dict[str, Any]` - Runs one shell command and returns exit code, outcome, output, truncated byte count and seconds; a non-zero exit is a result, not an error. The requested timeout is capped by the tool's limit.
- `shell_argv(command: str, *, containerized: bool) -> tuple[list[str], Path | None]` - The argument list for the platform shell, and the temporary script to delete afterwards on Windows. Public so an application can run a user's own command the same way.
- `_batch_file(command: str) -> Path` - Writes the command into a temporary `.cmd` file so Windows runs it exactly as written.
- `_confined(base: Path, relative_path: str, protected: frozenset[str]=frozenset()) -> Path` - Resolves a relative path inside the project, refusing escapes, credential paths, `.git`, `.agent-*` directories and any protected name.
- `_write_bytes(target: Path, data: bytes) -> None` - Atomic write through a temporary file.
- `_root_for(roots: Mapping[str, Path], name: str) -> Path` - The directory for a root name, refusing a name that was not given.
- `_roots(base: Path, extra: Mapping[str, Path]) -> dict[str, Path]` - The project directory plus the extra directories, by name.

---

### `starter/forge.py` - tools an agent proposes, a reviewer approves, tests prove and the host can roll back

**Role in the workflow.** The forge is the SDK's answer to an agent that wants a tool it does not have. It is a tool group: `propose_tool` (source, schema, description and test cases), `use_forged_tool` and `list_forged_tools`, plus every approved tool under its own name (`forged_<name>`) from the next run. A proposal never runs by itself: a `reviewer` callback (or the host calling `approve`) decides first; only then are the proposer's tests run in a disposable subprocess, and only if they all pass is the tool activated. Changing a tool is a new version with a diff against the active one and a note on what the code reaches for. The agent can propose but cannot approve, roll back or retire; the host can. Every step is in a hash-chained event log, and each activation records the source hash that is checked again before every call. Tests and calls run in a Docker container by default (no network, read-only root, the forge directory mounted read-only, capabilities dropped, no new privileges, 64 processes, memory and CPU limits, an unprivileged user). `isolation="native"` runs them as a subprocess with your rights and must be asked for. Limits: the container path needs a reachable Docker daemon and an image with `python` and has been tested here only with a recording backend, so run `pytest -m docker` where Docker works; the static note is not a security check; tools are stdlib-only functions of their arguments.

**Contents**

- **class `ToolProposal`** - A proposed tool as the reviewer sees it: id, full name, version, parent version, description, schema, source, tests, reason, source hash, a unified diff against the active version and the code note.
- **class `Forge`** - Stores proposals, versions and the active set under one directory and serves them as a tool group.
  - `Forge.__init__(self, directory: Path, *, reviewer: Reviewer | None=None, approved_by: str='reviewer-callback', isolation: Literal['docker', 'native']='docker', docker_image: str=DEFAULT_IMAGE, memory_bytes: int=DEFAULT_MEMORY_BYTES, cpu_limit: float=1.0, backend: SandboxBackend | None=None, timeout_seconds: float=10.0, max_tools: int=20, max_pending: int=5, min_tests: int=1) -> None` - Takes the directory, an optional reviewer, the isolation (`docker` by default, `native` only when asked for), the image, memory and CPU limits, an optional backend override for tests, the per-run timeout and the caps on active tools, waiting proposals and minimum tests; refuses nonsensical limits and any other isolation value. Docker isolation copies the runner into the directory so it is inside the read-only mount.
  - `Forge.definitions(self) -> list[ToolDefinition]` - Property: the three meta tools followed by one declaration per active forged tool, read from disk each time.
  - `Forge.run(self, name: str, call_arguments: dict[str, Any]) -> ToolExecutionResult` - Dispatches a tool call; any exception becomes a failed result naming the tool and the exception.
  - `Forge.propose(self, name: str, description: str, input_schema: dict[str, Any], source: str, tests: list[dict[str, Any]], reason: str='') -> dict[str, Any]` - Validates the proposal, stores it, then asks the reviewer; without a reviewer it stays pending. Refuses when too many are waiting.
  - `Forge.approve(self, proposal_id: str, by: str='host') -> dict[str, Any]` - Runs the proposal's tests and activates it if they pass (`approve` is the only path to activation); a failing test ends the proposal as `failed-tests` with each failure.
  - `Forge.reject(self, proposal_id: str, reason: str, by: str='host') -> dict[str, Any]` - Ends a pending proposal with a reason.
  - `Forge.rollback(self, name: str, version: int, by: str='host') -> None` - Makes an earlier activated version the active one again; refuses a version that was never active.
  - `Forge.retire(self, name: str, by: str='host') -> None` - Removes a tool from the active set; its versions stay on disk.
  - `Forge.call(self, name: str, arguments: dict[str, Any]) -> Any` - Runs an active tool after checking its stored source against the approved hash and the arguments against its schema.
  - `Forge.listing(self) -> dict[str, Any]` - The active tools and the ids of proposals waiting for review.
  - `Forge.pending(self) -> list[ToolProposal]` - The proposals no one has decided.
  - `Forge.verify(self) -> dict[str, int]` - Verifies the event log's chain and every active source hash; returns counts or raises.
  - `Forge._check(self, name: str, description: str, schema: dict[str, Any], source: str, tests: Any) -> None` - Refuses a malformed proposal before anything is stored: name, description, size, schema, parseable source with a top-level `run`, and well-formed tests whose arguments match the schema.
  - `Forge._record(self, name: str, description: str, schema: dict[str, Any], source: str, tests: list[dict[str, Any]], reason: str) -> ToolProposal` - Assigns the id and version, builds the diff and code note, writes the proposal and logs it.
  - `Forge._run_tests(self, proposal: ToolProposal) -> list[dict[str, Any]]` - Runs each test in the sandbox and returns the failures (an error, or expected against got).
  - `Forge._activate(self, proposal: ToolProposal, by: str) -> None` - Copies the source into the active set, logs the activation and updates the active record.
  - `Forge._entry(self, proposal: ToolProposal) -> dict[str, Any]` - The active-set record for a proposal.
  - `Forge._activated_versions(self, name: str) -> dict[int, str]` - The versions of a tool that were ever activated, with the proposal of each.
  - `Forge._pending(self) -> list[ToolProposal]` - Proposals with no rejection, activation or failed-test event.
  - `Forge._require_pending(self, proposal: ToolProposal) -> None` - Raises when a proposal was already decided.
  - `Forge._all_proposals(self) -> list[ToolProposal]` - Every stored proposal in id order.
  - `Forge._proposal(self, proposal_id: str) -> ToolProposal` - One stored proposal by id.
  - `Forge._active(self) -> dict[str, Any]` - The active set from disk.
  - `Forge._write_active(self, active: dict[str, Any]) -> None` - Atomically writes the active set.
  - `Forge._write_json(path: Path, value: Any) -> None` - Atomic JSON write.
- `_answer(proposal: ToolProposal, status: str, note: str, **extra: Any) -> dict[str, Any]` - The result an agent gets back for a proposal: status, ids, version and a note.
- `_sha256(text: str) -> str` - The SHA-256 of a text.
- `_meta_definition(name: str) -> ToolDefinition` - The declaration of one of the three meta tools.

---

### `starter/forge_execution.py` - run a forged tool's source in a disposable subprocess and read back its JSON result

**Role in the workflow.** Builds a command template that starts the current Python in isolated mode on `forge_runner.py` with the source path and the arguments as JSON, runs it through a sandbox backend (a fresh scratch directory, a short environment allowlist, a time limit and an output cap) and reads the marked result line. A failure reports the outcome kind, exit code and the last 500 characters of output.

**Contents**

- **class `ExecutionTarget`** - Where and how a forged tool runs: the Python to start, the runner script, the environment policy and, for a container, the host directory that is mounted at `/forge`.
  - `ExecutionTarget.locate(self, source_path: Path) -> str` - The source path as the process sees it: unchanged natively, or under `/forge` in a container.
- `native_target() -> ExecutionTarget` - The current interpreter, the runner in the package and the short native environment allowlist.
- `container_target(forge_directory: Path) -> ExecutionTarget` - Copies the runner into the forge directory (so it is inside the read-only mount) and describes running `python` there with no inherited environment.
- `container_options(forge_directory: Path, image: str, memory_bytes: int, cpu_limit: float) -> DockerSandboxOptions` - The container settings for forged tools: no network, read-only root, memory and CPU limits, the forge directory mounted read-only at `/forge`, all capabilities dropped, no new privileges, a process limit of 64 and an unprivileged user.
- `run_source(backend: SandboxBackend, scratch_parent: Path, target: ExecutionTarget, source_path: Path, arguments: dict[str, Any], timeout_seconds: float) -> tuple[bool, Any]` - Runs the source in the backend from a fresh working directory (deleted afterwards) and returns `(succeeded, value or message)`; refuses arguments over 16,000 characters.
- `_outcome(record: ProcessExecutionRecord) -> tuple[bool, Any]` - Reads the result line from a successful run, or describes the failure.

---

### `starter/forge_report.py` - what a proposed tool's source reaches for, as a note for the reviewer

**Role in the workflow.** Lists the imports, which of them are network or process/file modules, and the dynamic calls (`eval`, `exec`, `open` and similar), and builds the unified diff against the active version. It helps a person read the code and is explicitly not a security boundary.

**Contents**

- `code_report(source: str) -> dict[str, Any]` - The imports and risky names found in the source, with a note that the scan is not a security check.
- `source_diff(old: str | None, new: str, old_label: str, new_label: str) -> str` - A unified diff of two sources.

---

### `starter/forge_runner.py` - standalone script a forged tool runs under

**Role in the workflow.** Runs in the subprocess, imports only the standard library, loads the tool's source as a module, calls its `run(**arguments)` and prints the JSON result on a marked line. A missing `run` or an uncaught exception exits non-zero with the reason on stderr.

**Contents**

- `main(source_path: str, arguments_json: str) -> int` - Loads the source, calls `run` with the JSON arguments and prints the marked result; returns the exit code.

---

### `starter/images.py` - pictures to show the model: a file becomes a size-checked, base64 attachment

*47 lines · depends on: `agent/provider_context.py` · used by: `starter/agent.py`, `starter/sessions.py` · not re-exported at the package root*

**Role in the workflow.** `Agent.run(prompt, images=[path])` and `Session.run(..., images=...)` call `with_images`, which puts the loaded pictures in the task input under the shared key; the provider adapters send them as image parts. Types are PNG, JPEG, GIF and WebP, one image up to 5 MB, at most 10 per call; every refusal names the file and the limit.

**Contents**

- `load_image(path: Path) -> dict[str, str]` - Reads an image file into a media type and base64 data, refusing a wrong type, a missing file or one over 5 MB.
- `with_images(task_input: dict[str, Any], images: Sequence[Path | dict[str, str]]) -> None` - Attaches up to 10 images to a task input under the shared key; a path is loaded, a dict is copied.

---

### `starter/hooks.py` - tool hooks as plain functions: look at a call before it runs, or at its result after

*79 lines · depends on: `agent/base_agent/types.py`, `foundations/contracts.py`, `tools/tools.py` · used by: `starter/__init__.py`, `starter/agent.py`, `starter/toolbox.py` · not re-exported at the package root*

**Role in the workflow.** `Agent(before_tool=[fn])` runs `fn(ToolEvent)` before each tool call: `None` or `True` allows it, `False` or a reason string refuses it and the reason reaches the model. `Agent(after_tool=[fn])` sees the event with its result. Both may be sync or async. A before-hook returning anything else raises an error naming the hook.

**Contents**

- **class `ToolEvent`** - One call as a hook sees it: tool name, arguments, iteration and, for after-hooks, the result.
- `before_tool_hooks(callbacks: Sequence[BeforeTool]) -> list[PreToolHook]` - Wraps each callback as a core `PreToolHook`.
- `after_tool_hooks(callbacks: Sequence[AfterTool]) -> list[PostToolHook]` - Wraps each callback as a core `PostToolHook`.
- `refusal_reason(callback: BeforeTool, event: ToolEvent) -> str | None` - Calls a before-hook (awaiting it when async) and returns the reason to refuse the call, or none to allow it; any other return raises `TypeError` naming the hook.
- `event_for(context: ToolInvocationContext, result: ToolExecutionResult | None=None) -> ToolEvent` - A `ToolEvent` from the core invocation context and an optional result.
- `_before(callback: BeforeTool) -> PreToolHook` - Builds the core pre-tool hook for one callback; a refusal ends the run as blocked, which is the core's contract.
- `_before.hook(context: ToolInvocationContext) -> ToolHookDecision | None` - Asks `refusal_reason` and turns a reason into a `ToolHookDecision` that denies the call.
- `_after(callback: AfterTool) -> PostToolHook` - Builds the post-tool hook for one callback.
- `_after.hook(context: ToolInvocationContext, result: ToolExecutionResult) -> None` - Calls the callback with the event and the result, awaiting it when async.

---

### `starter/mcp_tools.py` - tools from external MCP servers, offered to an agent like any other tool

**Role in the workflow.** `await connect_mcp(configs)` connects the servers with the core `McpClientManager` and returns `McpTools`, a tool group. Names follow the core bridge (`mcp__server__tool`). Output is marked untrusted. Because the SDK cannot know an external tool's side effects, calls default to needing approval (`needs_approval=True`); pass `False` for a server you trust. The connections belong to one event loop, so use `await agent.run(...)` in the same loop and close the group yourself (`async with await connect_mcp(...)` or `await mcp.aclose()`); `run_sync` refuses it.

**Contents**

- **class `McpTools`** - The declared tools of connected servers, a route from each to its server and tool, and the approval setting.
  - `McpTools.run(self, name: str, call_arguments: dict[str, Any]) -> ToolExecutionResult` - Calls the MCP tool and returns its text as untrusted output, or a failed result that names the server, tool and exception.
  - `McpTools.aclose(self) -> None` - Closes every server connection.
  - `McpTools.__aenter__(self) -> McpTools` - Returns the group, for `async with`.
  - `McpTools.__aexit__(self, *exception: object) -> None` - Closes the connections.
- `connect_mcp(servers: Sequence[McpServerConfig], *, needs_approval: bool=True) -> McpTools` - Connects every server; if any does not connect, closes the rest and raises `ConnectionError` naming each failed server, its state and its detail. A tool whose schema the SDK cannot declare raises `ValueError` naming it.

---

### `starter/notebook_tools.py` - edit a cell of a Jupyter notebook anywhere in a project, without running it

*Starter layer · depends on: `tools/core/services.py`, `tools/artifacts.py`, `starter/tools.py` · not re-exported at the package root*

**Role in the workflow.** The core `notebook_edit` tool only edits notebooks declared in advance as output paths. A coding agent works on whichever notebook the project has, so `notebook_tools(root, protected=..., needs_approval=True)` returns one tool that checks the path itself (inside the project, a `.ipynb` file that exists, not under a protected folder) and then runs the core edit with exactly that path declared. A cell is replaced or appended to by index; a far index adds empty cells. The notebook is never executed, and every refusal names the path and the rule.

**Contents**

- `notebook_tools(root: Path, *, protected: Sequence[str]=(), needs_approval: bool=True) -> FunctionTool` - Builds the `notebook_edit` tool for a project directory; protected names are top-level folders or files the agent may not edit.
  - `notebook_tools.notebook_edit(path: str, new_source: str, cell_index: int=0, mode: str='replace') -> dict[str, Any]` *(async)* - Validates the path, declares it, runs the core edit and returns its record, or raises with the reason.

---

### `starter/planner.py` - turn a plain request into a validated plan, then run it as one agent or as a team

*Starter layer · depends on: `state/planning.py`, `starter/agent.py` · not re-exported at the package root*

**Role in the workflow.** `Planner(model=..., roles=[Role(...)]).plan(request)` asks a model for a small draft: tasks, each with a role, instructions, a done-when and the results it produces and needs. Code, not the model, turns the draft into a core `Plan`: dependencies follow from who produces what, the proofs are generated, and tasks of writable roles are chained so they never run together. `PlanValidator` is the authority; a draft it or the compiler refuses goes back to the model with the exact problems. The architecture is chosen from the finished plan (a team for two or more roles or two independent tasks, else one agent), not from anything the model claims, and the caller decides: `run_team` or `run_solo`. No specification is needed: the request is the specification.

**Contents**

- **class `Role`** - A kind of worker the caller offers the planner: a name, what it is for, optional tools and whether it changes things (`writable`).
- **class `DraftTask`** - One task as the model writes it: id, role, instructions, done_when, and the results it produces and needs.
- **class `PlanDraft`** - The model's whole answer: a rationale and the tasks; it never writes dependencies, signals or proofs.
- **class `PlanDraftError`** - A draft that cannot become a plan; carries every problem found, each naming the task and the rule.
  - `PlanDraftError.__init__(self, problems: Sequence[str]) -> None` - Keeps the list of problems and joins them as the message.
- **class `PlannerError`** - Raised when no valid plan came out of the allowed attempts.
- **class `PlannedTask`** - A task after compiling: its dependencies, and the results it produces and needs including the ordering results added for writers.
- **class `ArchitectureChoice`** - Whether the plan should run as a team, with the reasons that were found.
  - `ArchitectureChoice.architecture(self) -> str` - Property: `team` or `single`.
- **class `TaskOutcome`** - How one task ended: `completed`, `failed` or `skipped`, its text and the reason.
- **class `TeamResult`** - The result of a team run: whether every task completed, the final text, each outcome and the order tasks started.
- `_slug(text: str, joiner: str) -> str` - Lowercases text and joins its letters and digits with the given character, so `Read_And Summarize` becomes `read-and-summarize`.
- `_normalised(draft: PlanDraft, roles: Sequence[Role]) -> PlanDraft` - Fixes harmless naming slips in a draft: task ids to kebab-case, result names to snake_case, role names to the offered spelling.
- `compile_draft(draft: PlanDraft, request: str, roles: Sequence[Role], max_tasks: int=DEFAULT_MAX_TASKS) -> tuple[Plan, tuple[PlannedTask, ...]]` - Turns a draft into a core `Plan` and its planned tasks, refusing a draft with an unknown role, a duplicate id or producer, a need nobody produces or tasks that wait on each other, then runs `PlanValidator`. The request text stands in for a specification: each signal cites its first 60 characters.
- `_order(tasks: Sequence[DraftTask], producers: dict[str, str]) -> list[str]` - Orders tasks so every task follows the ones that produce what it needs; names the tasks that wait on each other.
- `_writer_chain(tasks: Sequence[DraftTask], order: list[str], by_role: dict[str, Role]) -> dict[str, str | None]` - Chains the tasks of writable roles one after another in data order, so two tasks that change things never run together.
- `_task(tasks: Sequence[DraftTask], task_id: str) -> DraftTask` - Finds a draft task by id.
- `_turn_signal(task_id: str) -> str` - The name of the ordering result a writer task hands to the next writer.
- `_planned(task: DraftTask, producers: dict[str, str], chain: dict[str, str | None]) -> PlannedTask` - One compiled task: its ordering result, and its dependencies from the producers of what it needs.
- `_plan(planned: Sequence[PlannedTask], request: str) -> Plan` - Builds the core `Plan`: locked-interface signals, signal uses, and one dependency proof per producing and consuming pair.
- `choose_architecture(tasks: Sequence[PlannedTask]) -> ArchitectureChoice` - A team when two or more roles are involved or two tasks are independent of each other; otherwise a single agent, with the reasons.
- `_ancestors(tasks: Sequence[PlannedTask]) -> dict[str, set[str]]` - For each task, every task it depends on directly or through others.
  - `_ancestors.visit(task_id: str) -> set[str]` - Collects one task's ancestors, remembering the result.
- `topological(tasks: Sequence[PlannedTask]) -> tuple[PlannedTask, ...]` - Tasks ordered so a task comes after everything it depends on.
- **class `PlanProposal`** - A validated plan awaiting a decision: the request, rationale, tasks, core plan, suggested architecture and roles. It runs nothing until `run_team` or `run_solo` is called.
  - `PlanProposal.gap_metadata(self) -> GapMetadata` - Property: the plan's roles and task count as the `GapMetadata` the core `Orchestrator` expects, for callers who want the governed path.
  - `PlanProposal.summary(self) -> str` - The plan as text for a person: the rationale, each task with its role and what it waits for, and the suggestion with its reasons.
  - `PlanProposal.solo_prompt(self) -> str` - The request followed by every task as a numbered step in dependency order, for one agent to work through.
  - `PlanProposal.run_solo(self, factory: SoloFactory, *, close: bool=True) -> Answer *(async)*` - Runs the steps in one agent built by the caller's factory from all the roles, and closes it.
  - `PlanProposal.run_team(self, factory: WorkerFactory, *, max_parallel: int=DEFAULT_PARALLEL, on_task: TaskEvent | None=None, close: bool=True, completed: Mapping[str, str] | None=None, attempts: int=1, notes: Mapping[str, str] | None=None, should_retry: Callable[[PlannedTask], bool] | None=None) -> TeamResult *(async)*` - Runs the tasks in waves, up to `max_parallel` at once, each in an agent the factory builds for its role; a task whose dependency did not complete is skipped, not run. Reports each start, completion, failure, retry, replay or skip to `on_task`. `completed` maps task ids to results an earlier run committed: those tasks are replayed, not run. `attempts` bounds how often a failing task is tried, the previous failure going into the next prompt; `should_retry` can stop that; `notes` adds text to a task's first prompt.
    - `PlanProposal.run_team.attempt_once(task: PlannedTask, extra: str) -> tuple[Answer | None, str | None] *(async)*` - Builds a worker for the task and runs it once; returns the answer or the exception named.
    - `PlanProposal.run_team.work(task: PlannedTask) -> None *(async)*` - Runs one task under the concurrency limit: replays it when committed, otherwise tries it up to `attempts` times.
  - `PlanProposal._worker_prompt(self, task: PlannedTask, outcomes: dict[str, TaskOutcome], by_id: dict[str, PlannedTask]) -> str` - A task's prompt: the original request, its instructions, when it is done and the results of the tasks it depends on.
  - `PlanProposal._final_text(self, outcomes: dict[str, TaskOutcome]) -> str` - The answer of the final task, or each final task's answer under its name when there are several.
- `_skip_reason(task: PlannedTask, outcomes: dict[str, TaskOutcome]) -> str` - Says which dependencies stopped a task from running.
- `planner_instructions(roles: Sequence[Role], max_tasks: int) -> str` - The planner model's instructions: keep plans small, one task when the job is simple, how to name tasks and results, and the roles available.
- **class `Planner`** - Asks a model for a plan draft, compiles and validates it, and feeds problems back for a corrected draft, up to `max_attempts` times. Roles come from the caller; with none, a single generic worker.
  - `Planner.__init__(self, *, model: ModelSetup, roles: Sequence[Role]=(), max_tasks: int=DEFAULT_MAX_TASKS, max_attempts: int=DEFAULT_ATTEMPTS, workspace: Path=DEFAULT_WORKSPACE / 'planner') -> None` - Takes the model, roles, task and attempt limits and the workspace; rejects a limit below one and duplicate role names.
  - `Planner.plan(self, request: str) -> PlanProposal *(async)*` - Returns a `PlanProposal` for a request, or raises `PlannerError` naming the last problems; an empty request is refused.
  - `Planner.plan_sync(self, request: str) -> PlanProposal` - `asyncio.run` of `plan`.
  - `Planner._ask(self, prompt: str, problems: list[str]) -> PlanDraft | None *(async)*` - One planner run for one prompt; returns the draft or records why there was none.
- `_retry_prompt(request: str, previous: str, problems: Sequence[str]) -> str` - The request, the previous draft and the problems, asking for a corrected plan.

**Algorithms & invariants.** `PlanValidator` accepts a task that needs a result nobody produces, so `compile_draft` rejects that itself. Naming slips (case, underscores, spaces) are fixed rather than retried, because live models repeated them. A task id or result name that has no letters or digits, a role that does not exist, a duplicate producer and a cycle are refused with the task named. Team tasks run in waves of ready tasks; one that fails makes its dependents `skipped`.

---

### `starter/session_store.py` - a session's turns on disk as a hash chain

**Role in the workflow.** One JSONL file per session under `.agent-sessions/` in the workspace: a start record (agent, time, and where it was forked from) then one record per turn (prompt, status, answer, steps, a one-line summary per tool call). Records are chained, so editing or dropping one is found when the session is resumed. A fork copies the turns up to a point into a new session and records the source, the turn and the source turn's hash. The retention inventory lists this directory as unclassified and retention does not prune it.

**Contents**

- `validate_session_id(session_id: str) -> str` - Accepts 1 to 64 letters, digits, dots, underscores and dashes, starting with a letter or digit; anything else raises `ValueError` saying so (the id becomes a file name).
- **class `SessionStore`** - The sessions of one workspace.
  - `SessionStore.__init__(self, root: Path) -> None` - Remembers the sessions directory; nothing is created until a session is.
  - `SessionStore.exists(self, session_id: str) -> bool` - True when the session's file exists.
  - `SessionStore.ids(self) -> list[str]` - The saved session ids, sorted.
  - `SessionStore.delete(self, session_id: str) -> None` - Removes one session's log file under the store's lock; raises `SESSION_NOT_FOUND` naming the saved sessions when it is missing.
  - `SessionStore.latest(self) -> str | None` - The id of the session file modified most recently, or none.
  - `SessionStore.create(self, session_id: str, agent: str, forked_from: dict[str, Any] | None=None) -> dict[str, Any]` - Writes the start record; an existing id raises `SESSION_EXISTS`.
  - `SessionStore.append(self, session_id: str, turn: dict[str, Any]) -> dict[str, Any]` - Adds the next numbered turn.
  - `SessionStore.records(self, session_id: str) -> list[dict[str, Any]]` - The verified records of a session; an unknown id raises `SESSION_NOT_FOUND`.
  - `SessionStore.fork(self, source: str, target: str, agent: str, at_turn: int | None) -> dict[str, Any]` - Creates the target session from the first `at_turn` turns of the source (all by default), keeping their times; a turn beyond what exists raises `SESSION_TURN_UNKNOWN`.
  - `SessionStore.verify(self, session_id: str) -> int` - Verifies the chain and returns the number of turns.
  - `SessionStore._log(self, session_id: str) -> ChainedLog` - The chained log for a validated id.
  - `SessionStore._existing(self, session_id: str) -> ChainedLog` - The log for a session that must exist.
  - `SessionStore._copy(self, session_id: str, kind: str, fields: dict[str, Any]) -> None` - Copies a session's turns and the summaries they cover into a new session.
  - `SessionStore.add_summary(self, session_id: str, text: str, upto: int) -> dict[str, Any]` - Appends a summary record covering turns up to a number, on the same hash chain.
- `_now() -> str` - The current UTC time as ISO text.

---

### `starter/sessions.py` - sessions: continue a conversation across runs and processes, or fork it

**Role in the workflow.** A `Session` runs the agent with the session id as the project and gives the model a bounded digest of earlier turns (`session_history` in the task input): each turn's prompt, status, answer and a few tool summaries, newest kept first within `history_turns` and `history_chars`, with the number left out stated. It is a deterministic digest, not a model summary and not a transcript. The turn is recorded after the run ends, so a run that crashes leaves no record. `fork` branches from any turn; the branches do not affect each other.

**Contents**

- **class `Session`** - A named conversation on one agent.
  - `Session.__init__(self, agent: Agent, store: SessionStore, session_id: str, history_turns: int, history_chars: int) -> None` - Takes the agent, the store, the id and the two history bounds, each at least 1.
  - `Session.turns(self) -> list[dict[str, Any]]` - Property: the recorded turns, verified.
  - `Session.forked_from(self) -> dict[str, Any] | None` - Property: the source session, turn and hash if this session is a fork, otherwise none.
  - `Session.history(self) -> dict[str, Any]` - The bounded digest of earlier turns given to the model, oldest first, with `omitted_turns`.
  - `Session.run(self, prompt: str, *, input: dict[str, Any] | None=None, criteria: Sequence[str]=(), skills: Sequence[SkillContext]=(), cancellation: CancellationToken | None=None) -> Answer` - Runs the prompt with the digest and this session's id, then records the turn (also when it failed).
  - `Session.run_sync(self, prompt: str, *, input: dict[str, Any] | None=None, criteria: Sequence[str]=(), skills: Sequence[SkillContext]=()) -> Answer` - `asyncio.run` of `run`; inside a running loop it raises an error that says to await it.
  - `Session.fork(self, name: str | None=None, at_turn: int | None=None) -> Session` - A new session holding the turns up to `at_turn`, under the given name or a generated one.
  - `Session.verify(self) -> int` - Verifies the chain and returns the turn count.
  - `Session.compact(self, summary: str, upto: int | None=None) -> int` - Records a summary of the turns not yet covered and returns how many turns it covers.
  - `Session.summary(self) -> dict[str, Any] | None` - Property: the latest summary record, or none.
  - `Session.uncompacted_turns(self) -> list[dict[str, Any]]` - The recorded turns after the last summary.
- `_turn_record(prompt: str, answer: Answer) -> dict[str, Any]` - The stored fields of a finished turn, with the answer clipped to 4,000 characters.
- `_digest_turn(record: dict[str, Any]) -> dict[str, Any]` - One turn as the model sees it, with the prompt, answer and tool lines clipped.
- `_clip(text: str, limit: int) -> str` - Truncates text with a marker.

---

### `starter/skill_files.py` - Agent Skills as folders: a SKILL.md header, a body and files beside it

*Starter layer · 220 lines · depends on: `foundations/contracts.py` · not re-exported at the package root*

**Role in the workflow.** Reads the open Agent Skills layout that Claude Code and Codex also use, so a skill written for either works here. `<name>/SKILL.md` starts with a `---` header holding `name` (lowercase letters, digits and single hyphens, equal to the folder name) and `description`, and optionally `allowed-tools`, `disable-model-invocation` and `argument-hint`; a Codex `agents/openai.yaml` with `allow_implicit_invocation: false` also makes a skill manual-only. A single `name.md` is still a skill, with its first line as the description. The module only finds, parses and renders; which skills a caller offers and what a skill may use stay with the caller. Every refusal names the file and the problem.

**Contents**

- **class `SkillDefinition`** *(dataclass)* - One skill: name, description, body, path, source label, folder, manual-only flag, allowed tools, argument hint and the bundled files (at most 50, relative paths).
- `parse_frontmatter(text: str, label: str) -> tuple[dict[str, Any], str]` - Splits a `---` header from the body; understands `key: value`, `[a, b]` and `- item` lists and true/false; an unclosed header or a line that is not `key: value` raises naming `label` and the line.
- `_scalar(text: str) -> str` - Trims a value and removes one pair of matching quotes.
- `_value(text: str) -> Any` - Turns a header value into a list, a boolean or a string.
- `_words(value: Any) -> tuple[str, ...]` - Splits `allowed-tools` into entries, keeping `Tool(pattern)` entries whole.
- `load_skill_definition(path: Path, source: str) -> SkillDefinition` - Reads and validates one skill file (not empty, at most 20,000 characters, valid name, description of at most 1,024 characters).
- `discover_skills(roots: Sequence[tuple[str, Path]]) -> list[SkillDefinition]` - Finds folder and single-file skills in each root; a later root overrides an earlier one with the same name; returns them sorted by name.
- `render_skill(definition: SkillDefinition, arguments: str='') -> str` - The text to give the model: `$ARGUMENTS` and `$1` to `$9` replaced, `ARGUMENTS:` appended when the body does not use them, and the skill folder and its bundled files listed.
- `skill_file_text(definition: SkillDefinition, relative: str) -> str` - Reads a bundled file; a path that leaves the folder, a missing file or a single-file skill raises naming the choices.
- `skill_context_of(definition: SkillDefinition, arguments: str='') -> SkillContext` - The rendered text as a run context with a version that follows its content.

### `starter/skills.py` - skills as files: a markdown file becomes task context the model is given

**Role in the workflow.** `skill(path)` reads a file into the core `SkillContext` (id from the file name, version from a hash of its content). Pass skills to `Agent(skills=[...])` for every run or to a single `run(..., skills=[...])`.

**Contents**

- `skill(path: Path, *, skill_id: str | None=None) -> SkillContext` - Reads a file into a `SkillContext`; an empty file raises `ValueError` naming the path.

---

### `starter/core_tools.py` - the SDK's governed file tools, offered to an agent with one call

*51 lines · depends on: `foundations/contracts.py`, `tools/artifacts.py`, `tools/core/definitions.py`, `tools/core/services.py` · used by: `starter/__init__.py`, `starter/dev_tools.py`, tests · not re-exported at the package root*

**Role in the workflow.** `core_tools(root)` declares `read_file`, `glob` and `grep` over a directory and runs them with the existing `CoreToolDispatcher`. Passing `writable=("out.txt",)` adds `write_draft` and `edit_draft` for exactly those declared paths; with none, the artifact store is opened read-only so nothing is written.

**Contents**

- **class `CoreTools`** - The declarations and the dispatcher for the chosen core tools, and whether each call needs approval.
  - `CoreTools.run(self, name: str, call_arguments: dict[str, Any]) -> ToolExecutionResult` - Runs one core tool by name through the dispatcher and returns its `ToolExecutionResult`.
- `core_tools(root: Path, *, writable: Sequence[str]=(), only: Sequence[str] | None=None, needs_approval: bool=False, ask_human: HumanQuestionResponder | None=None) -> CoreTools` - Builds `CoreTools` for a directory: read tools always, write tools only for the declared writable paths, or exactly the tools named in `only` (for example just `web_fetch`); `needs_approval` makes every call to the group ask first.
- **class `MultiRootCoreTools`** - The governed file tools over several named directories, each call naming its root.
  - `MultiRootCoreTools.run(self, name: str, call_arguments: dict[str, Any]) -> ToolExecutionResult` - Routes a call to the single-root tool group for the root it names.
- `_dispatcher(root: Path, writable: Sequence[str], ask_human: HumanQuestionResponder | None=None) -> CoreToolDispatcher` - Builds the executor that sends each call to the right root's tools.
- `multi_root_read_tools(roots: Mapping[str, Path]) -> MultiRootCoreTools` - Read-only file tools over several named roots, with the first named `project`.

---

### `starter/models.py` - name a model once: the adapter and the binding the agent definition needs

**Role in the workflow.** A `ModelSetup` keeps an adapter together with the provider and model names the definition's `ModelBinding` must carry, so the two cannot disagree. `openai_chat` builds one for an OpenAI-compatible endpoint (OpenAI, OpenRouter, Gemini, xAI and similar) and `anthropic_messages` builds one for the Claude Messages API, each from an explicit key and model; there is no environment lookup and no default model, matching the adapters underneath.

**Contents**

- **class `ModelSetup`** - An `AgentModel`, its provider name, its model name and optional request parameters.
  - `ModelSetup.binding(self) -> FallbackModelBinding` - Property: the `FallbackModelBinding` for this setup, used for the primary binding's fallbacks and for failover.
- `default_text_answer(text: str) -> dict[str, Any]` - The starter's default answer object for a plain-text reply: status complete and the text as the answer.
- `openai_chat(*, base_url: str, api_key: str, model: str, provider: str='openai-compatible', timeout_seconds: float=60.0, allow_insecure_http: bool=False, json_mode: bool=True, parameters: dict[str, Any] | None=None, text_answer: Callable[[str], dict[str, Any]] | None=default_text_answer, tool_mode: str='auto') -> ModelSetup` - Builds a `ModelSetup` around `OpenAICompatibleAgentModel` (plain-text and unwrapped replies become the final answer through `text_answer`; pass `None` to require strict JSON turns) for an explicit endpoint; the endpoint rejects an empty key or a plain-HTTP URL unless `allow_insecure_http` is set. `json_mode=False` omits `response_format` for providers that reject it.
- `anthropic_messages(*, api_key: str, model: str, provider: str='anthropic', base_url: str=DEFAULT_BASE_URL, max_tokens: int=DEFAULT_MAX_TOKENS, timeout_seconds: float=120.0, allow_insecure_http: bool=False, cache_stable_prefix: bool=True, extra_headers: dict[str, str] | None=None, parameters: dict[str, Any] | None=None) -> ModelSetup` - Builds a `ModelSetup` around `AnthropicMessagesAgentModel` for the Anthropic Messages API from an explicit key and model; `max_tokens` is the per-turn output limit, `extra_headers` carries beta headers, and `cache_stable_prefix` controls the prompt-cache marker.

---

### `starter/toolbox.py` - everything an agent may call, behind one executor that runs only what was handed to it

*129 lines · depends on: `foundations/contracts.py`, `starter/hooks.py`, `starter/tools.py`, `tools/tools.py` · used by: `starter/__init__.py`, `starter/agent.py` · not re-exported at the package root*

**Role in the workflow.** `Toolbox` is the `ToolExecutor` the agent gets. It holds the function tools and core tools the caller passed, refuses two tools with one name, and answers a call to anything else with a failed result. A tool marked `needs_approval` asks the `approve=` callback first; with no callback, or when the callback says no, the call returns a `blocked` result that says which.

**Contents**

- **class `ApprovalRequest`** - The tool name and the arguments of a call that needs approval.
- **class `Refusal`** - What an `approve=` callback returns to say no and why: it is false in a condition, and its `reason` is added to the declined result the model reads.
  - `Refusal.__bool__(self) -> bool` - Always false, so a refusal reads as a refusal wherever the verdict is tested.
- **class `ToolGroup`** - Anything that declares several tools and runs them by name, such as `CoreTools` and `McpTools`. A group may carry `needs_approval`, which asks the `approve=` callback before every call to it.
  - `ToolGroup.run(self, name: str, call_arguments: dict[str, Any]) -> ToolExecutionResult` - Runs one of the group's tools by name and returns its `ToolExecutionResult`.
- **class `Toolbox`** - Runs the function tools and tool groups it was given; `enforces_authorization` is true because only handed-over tools can run and approval is checked inside.
  - `Toolbox.__init__(self, tools: Sequence[FunctionTool | ToolGroup], approve: Approver | None=None, *, guards: Sequence[BeforeTool]=(), refusal_status: Literal['blocked', 'failed']='blocked') -> None` - Registers function tools and tool groups, raising `ValueError` when two tools share a name. `guards` are before-hooks run inside the executor; `refusal_status` is the status a refused or declined call returns (`blocked` ends the run, `failed` lets the model see the refusal and continue).
  - `Toolbox.definitions(self) -> list[ToolDefinition]` - Property: every function tool's declaration plus every group's, read fresh each time so a group whose tools change (the forge) is current; raises `ValueError` naming a duplicated tool.
  - `Toolbox._collect(self) -> list[ToolDefinition]` - The declarations of the function tools followed by those of each group.
  - `Toolbox._unique(found: list[ToolDefinition]) -> list[ToolDefinition]` - Returns the declarations after checking that no tool name appears twice.
  - `Toolbox.execute(self, tool: ToolDefinition, context: ToolInvocationContext) -> ToolExecutionResult` - Runs the guards (a refusal returns a result with the refusal status), then a function tool or a group's tool (after approval when either requires it), or returns a failed result naming a tool the agent was not given.
  - `Toolbox._group_for(self, name: str) -> ToolGroup | None` - The group that currently declares a tool name, or none.
  - `Toolbox._refusal(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult | None` - None when the call may run; otherwise a `blocked` result for a missing callback or a declined call. The callback may be sync or async.

---

### `starter/tools.py` - turn a plain Python function into a tool the SDK can declare, validate and run

**Role in the workflow.** `@tool` reads a function's name, docstring and type hints and produces a `FunctionTool`: a `ToolDefinition` whose JSON Schema comes from a pydantic model of the signature (no extra arguments allowed), the model itself for coercion, and the function. `read_only=True` declares an exploratory, parallel-safe tool; otherwise it is an action and runs serially. Sync functions run in a worker thread, async ones are awaited, and any exception becomes a failed result that names the tool and the exception type.

**Contents**

- **class `FunctionTool`** - A declared function: its `ToolDefinition`, the function, the argument model and whether each call needs approval.
  - `FunctionTool.name(self) -> str` - Property: the declared tool name.
  - `FunctionTool.run(self, call_arguments: dict[str, Any]) -> ToolExecutionResult` - Validates the arguments with the argument model, calls the function (a thread for sync, awaited for async) and returns its JSON-safe value, or a failed result naming the exception.
- `tool(function: Callable[..., Any] | None=None, *, name: str | None=None, description: str | None=None, read_only: bool=False, needs_approval: bool=False) -> Any` - Decorator, bare or with `name=`, `description=`, `read_only=` and `needs_approval=`. Raises `ValueError` for a missing description or a variadic parameter, naming the function.
- `tool.build(target: Callable[..., Any]) -> FunctionTool` - Builds the `FunctionTool` for one function: description, argument model, schema and the read-only or action declaration.
- `_first_paragraph(docstring: str | None) -> str` - The first paragraph of a docstring on one line, or an empty string.
- `_argument_model(function: Callable[..., Any], tool_name: str) -> type[BaseModel]` - A pydantic model of the function's parameters (type hints resolved, defaults kept, extras forbidden); refuses `*args` and `**kwargs`.
- `_without_titles(schema: Any) -> Any` - The schema without pydantic's generated `title` strings, leaving values under `default`, `const`, `enum` and `examples` untouched.

---
