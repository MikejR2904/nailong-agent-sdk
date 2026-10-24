# nailong-agent-sdk - workflow and function reference

This is a complete, per-file description of `src/nailong_agent_sdk`: what every module is
for, what each class and function does, and where it sits in the end-to-end workflow. It
covers **166 files, 598 classes and 2,128 functions** (methods and nested closures included).
The structure and signatures were extracted from the source with Python's `ast` module and
every item carries a hand-written description; `tests/test_reference_docs.py` fails if any
class or function lacks an entry or an entry names one that no longer exists.

**How to read it.** Start with the workflows below, then open the page for the folder you are
changing. Each file entry has a one-line role, a *Role in the workflow* paragraph, a *Contents*
list (every class, method and function with who calls it), and, where a file implements an
algorithm or has a known limitation, an *Algorithms & invariants* note. "Called by" lists are
static: they are shown only when a name is unique in the package, and protocol hooks, validators
and MCP tools have none.

## Scope

This package is the agent a backend calls. It runs one scoped agent task, or a graph of
them, enforces the permissions it is handed, records what happened and returns the result.
It does not own sessions, review gates, the approval of a specification or version locks:
the interface that calls it decides when to ask a human and what counts as approved. No
specialist role is built in either; the tools (`AgentDefinition.tools`), skills
(`ScopedAgentTask.skills`) and permissions (`CapabilityGrant`) arrive from the caller, and
an `OrchestrationPolicy` carries them for a multi-agent run.

Three mechanisms that act like gates remain because the agent loop needs them, and the
caller configures each: the output checks (an answer that fails the definition's output
schema goes back to the agent as an observation so it can correct itself, while a
verification gate registered in `agent/verification.py` that refuses it ends the run FAILED
with the gate's reason), the capability policy with typed tool approvals
(`tools/policy.py`, `tools/approvals.py`), and the plan and orchestration approval steps of
the controller, which the caller can answer at once.

## Layers

Folder-level imports, computed from the source (a folder is listed under what it imports):

```
foundations      imports nothing in the package: contracts, typed errors, PCKP optimiser,
                 atomic file IO, cycle finder
observability    foundations                      telemetry ledger, audit logs, metrics, profiler
specifications   foundations, observability       manifest -> trees -> evidence -> retrieval
memory           foundations, specifications      episodes, compaction, context projection
tools            foundations, observability, memory, state      tool definitions, policy, core tools
state            foundations, observability, tools              plan validator, graph, stores
agent            all of the above and integrations              BaseAgent, models, orchestrator
integrations     foundations, observability, tools, state, agent    Jev, LangChain, LangGraph
mcp              everything except integrations                 MCP server and client
developer_tools  foundations, observability, specifications, memory, tools, state, agent
                                                    catalogue, validate, inspect, diagnose, attribute
```

Two import cycles exist: `tools` with `state` (`tools/registry.py` imports `state/planning.py`;
`state/harness_coordinator.py` imports `tools/approvals.py`) and `agent` with `integrations`
(`agent/orchestrator` imports `integrations/jev/architecture.py`; the integrations import the
agent runtime). Neither is a loop between two modules; each works because the particular modules
involved import in a safe sequence. The package root imports nothing (it resolves names
lazily), so no module depends on its import order: every module imports cleanly as the first
import of a fresh interpreter, which the test suite checks for all of them.

## Workflow 1 - one agent task (`BaseAgent.run`)

1. **Validate and assemble.** The task is checked against the definition's input schema and the
   immutable prompt is built once (`memory/context.py`): identity, instructions, tool
   definitions, output schema, scoped task, skills.
2. **Open per-run state.** A fresh episode graph and episode store; the project state for
   `scope.boundaries["project_id"]` (else the task id); a profiler root span and the run's
   W3C trace id (the caller's, or a new one) stamped on every event; telemetry and
   audit entries `run-started`, `task-received`, `context-assembled`, `state-loaded`.
3. **Loop `iteration = 1..max_iterations`.** Stop if the run deadline passed (FAILED) or the
   caller cancelled (CANCELLED).
4. **Project context** (`ContextProjector.project`): compact episodes over budget, keep the
   newest observations that fit, and build the stubs of compacted episodes. With a semantic
   episode store (`SemanticCompactor`) the episodes PCKP did not keep are also archived,
   summarized and verified, on a worker thread under the run deadline and the cancellation
   token, and the verified summaries join the projection. Emit the `context-projected`
   event (and `semantic-compaction` when a pass ran) and the context metrics.
5. **Three guards** end the run BLOCKED before any model call: `CONTEXT_DEADLOCK` (nothing can
   be compacted), `PROJECT_STATE_BUDGET_EXCEEDED` (the mandatory state exceeds its 2,000-token
   view) and `CONTEXT_BUDGET_EXCEEDED` (prompt plus state exceed the context budget); each is
   a typed failure carrying its numbers, as is every other FAILED or BLOCKED exit.
6. **Call the model** under the per-turn watchdog (streaming if a listener is set and the
   adapter supports it). A transient provider error (429, 5xx, connection) is retried: it
   becomes an `agent-error` observation, the run waits with exponential backoff (honouring
   `Retry-After`, capped) and repeats the turn at the cost of one iteration; any other error
   ends FAILED at once.
7. **Validate the response** as one of `final`, `blocked`, `tool-call`, `tool-batch`
   (`MODEL_TURN_INVALID` names up to five bad fields). Provider-reported usage becomes metrics.
8. **Act.** `blocked` ends BLOCKED. `final` is checked against the output schema (a mismatch
   becomes an observation and the loop continues), then the named verification gate (a
   rejection ends FAILED), then the run is COMPLETED. A tool turn runs a governed batch
   (Workflow 3) whose results become observations, episodes and project-state transitions.
9. **Terminate** (`_terminate`, the single exit): record the agent result in project state,
   escalate if the status is not COMPLETED and the definition names a target, seal the profile,
   emit telemetry and audit entries, return `AgentResult`.

### Recipe - accepting an output only when something outside the model agrees

`VerificationGate` is the extension point; the SDK ships no domain oracle. Two checks that need
no new API, composed so the run is COMPLETED only when both pass (the exit code of a
host-declared command through `ProcessSupervisor`, and the hash of a file the agent wrote):

```python
import hashlib

from nailong_agent_sdk import (
    AllOfVerificationGate,
    CallableVerificationGate,
    TelemetryAuthority,
    VerificationDecision,
    VerificationGateRegistry,
)


def exit_code(supervisor, template, cwd):
    async def check(context):
        record = await supervisor.execute(template, cwd=cwd)
        return VerificationDecision(
            record.successful,
            None if record.successful else f"{template} exited {record.return_code}",
            verifier_id=f"command:{template}",
            authority=TelemetryAuthority.TOOL,
            evidence={"return_code": record.return_code, "duration_ns": record.duration_ns},
        )

    return CallableVerificationGate(check)


def file_hash(path, digest):
    def check(context):
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        return VerificationDecision(
            actual == digest,
            None if actual == digest else f"{path.name} hashes to {actual[:12]}",
            verifier_id=f"sha256:{path.name}",
            authority=TelemetryAuthority.DETERMINISTIC,
            evidence={"expected": digest[:12], "actual": actual[:12]},
        )

    return CallableVerificationGate(check)


registry = VerificationGateRegistry()
registry.register(
    "release",
    AllOfVerificationGate(
        [exit_code(supervisor, "tests", workspace), file_hash(report, expected)],
        verifier_id="release-gate",
    ),
)
```

The `verification-completed` event records the composite's `verifier_id`, the member class
furthest from deterministic (`tool` here) and each member's outcome with its own evidence; a
rejection reads `<member>: <reason>`, and the run ends FAILED. A member gate that declares an
unknown `authority` or a blank `verifier_id` is refused when the composite is built, before any
member runs. The two checks were run against the final tree: with `exit-zero` and the right
hash the run completed with both members recorded, and with `exit-three` it failed with
`command:exit-three: exit-three exited 3`.

## Workflow 2 - what the model sees each turn

The Chat Completions adapter sends three messages plus any continuation:

* **system** - fixed instructions (untrusted proposal component, declared tools only, JSON
  `AgentTurn` otherwise, how to read the context below).
* **stable context** - task id and input, the immutable prompt, the output schema. It is
  byte-identical every turn so the provider's prompt cache can match the whole prefix.
* **volatile context** - the bounded *project state view*; the iteration number;
  `recent_observations` (the newest observations that fit the budget); `episode_summaries`
  (one line per retained episode); `compacted_episodes` (one-line stubs with a status and a
  `handle_id` for episodes whose content was dropped, within a 1,500-token allowance, newest
  first); `omitted_compacted_count`; and, only when a semantic episode store produced them,
  `semantic_summaries` (verified digests of groups of compacted episodes, each with the
  episode ids and journal handles it came from, at most 16 of each listed beside their
  counts, within a 1,200-token allowance) and `omitted_summary_count`.
* **continuation** - for providers that issued tool calls, the raw assistant tool calls and the
  matching bounded tool results, only for the latest batch.

Defaults: context budget 12,000 estimated tokens, episode budget 6,000, tool-result preview
1,024 characters, stub budget 1,500, stub summary 160 characters, semantic-summary budget
1,200, project-state view 2,000.
Estimates are characters divided by four. The model never sees raw tool output beyond the
preview: the full result is journalled and the model can fetch it with the `get_tool_result`
tool and a handle id.

**Compaction.** Episodes are OPEN, CLOSED or COMPACTED. When closed episodes exceed the episode
budget, the default strategy (exact PCKP, `foundations/optimization`) keeps a mandatory set
(the latest batch, active and open episodes, and
their prerequisites) and chooses the rest by solving a dependency-closed knapsack exactly
(a Pareto-frontier tree DP for rooted forests whose state count stays within 50,000, otherwise
branch and bound with a cap that degrades to BEST_EFFORT). Compacted episodes keep only a tombstone plus their summary; the
stub list keeps them findable.

**Semantic compaction** (opt-in: give the episode store a `SemanticCompactor`). PCKP's
choice is unchanged; what changes is what happens to the episodes it did not keep. They
keep their payloads until their group has been archived (`memory/episode_archive.py`:
content-addressed, read back and compared), summarized by the host's `EpisodeSummarizer`
(`ExtractiveEpisodeSummarizer` needs no model) and verified: the archive entries are
intact, dependencies resolve, every claim quotes its cited episodes verbatim, every failed
or blocked episode is mentioned, no number or identifier appears that the sources do not
contain, and the digest is small enough; an optional judge can then only veto. Only then
is the payload released and the record linked to its summary and archive entry. A group
that fails any step keeps its originals and is retried up to a limit; groups follow real
`depends_on` edges, never similarity. When more than 12 summaries are active they are
consolidated into parents (up to level 3, children kept for provenance), an optional
ceiling on active records and edges evicts archived, summarized records from the active
graph, and `rehydrate` (or `rehydration_token_budget`, for an action that consumes a
compacted episode) restores originals from the archive within a token budget.

## Workflow 3 - how a tool call is governed

`BaseAgent._execute_tool_call` rejects an undeclared tool (`TOOL_NOT_DECLARED`) and a missing
executor (`TOOL_EXECUTOR_MISSING`), each ending the run FAILED; arguments that violate the
declared JSON schema, or an action that consumes
episodes that are unavailable, come back to the model as a failed result so it can correct
them. It then runs pre-tool hooks (a denial ends BLOCKED), the host's `ToolExecutor` and
post-tool hooks, all under the tool watchdog. With the SDK's `HarnessToolExecutor`
(`tools/registry.py`): the tool must be in the closed `HarnessToolRegistry`;
`CapabilityPolicy` checks sensitive paths first, then the role's capability grant, path
containment and any typed approval (a pending approval returns a `blocked` result; once it is
decided, `resume_run` re-opens the node); the handler then runs a core tool (`tools/core`), a
supervised process (`tools/supervisor.py`) or a sandbox backend. The result is stored in the
journal, becomes an episode and an observation with a handle, is reduced into project state,
and is written to the audit log and telemetry. `tool-batch-completed` lists every outcome and,
for one that did not succeed, its failure code and a message of at most 300 characters, so the
reason a call never ran (bad arguments, a denial) is in the ledger.

## Workflow 4 - several agents (`Orchestrator`)

`prepare` validates the plan, routes it single or multi-agent from deterministic thresholds (an
optional Jev advisory can only raise single to multi), compiles the execution plan and assigns
each task a profile, model, skills and tool subset from the user's policy. `submit_for_approval`
creates a controller and presents the plan; `approve` records the human decision;
`dispatch_and_execute` starts a graph run (`ControllerRuntime` -> `HarnessCoordinator` ->
`StateGraph`), builds and validates the host bindings, and runs waves: independent nodes run
concurrently up to the parallelism cap, each through `GraphAgentExecutor` -> a fresh `BaseAgent`;
results commit in node-id order and are reduced into project work items. Agents never exchange
conversation: lateral data moves only as typed discoveries and immutable values in the graph's
shared state, routed by exact reference to consumers that have not started. A failed node moves
the controller to bounded repair, then escalation. The orchestration ends FAILED, BLOCKED,
CANCELLED or EXECUTED according to the node statuses, and a controller cannot complete while a
node has not completed. An agent can also ask for exploration beyond the plan (Workflow 7).
A failure between two of the writes an operation makes (the run, then project state; the
controller, then the orchestration record) leaves the stores disagreeing. `reconcile` (MCP
`reconcile_orchestration` and `reconcile_controller`) derives each later store from the earlier
one, and `submit_for_approval`, `approve`, `cancel`, `dispatch_and_execute` and
`recover_execution` do it before they act, so retrying an operation that failed completes it.
After a crash, `recover_execution` continues a DISPATCHED orchestration: a node whose binding
is declared idempotent is replayed (elastic nodes included), any other interrupted node fails,
and the remaining waves run.

## Workflow 7 - exploring beyond the plan (elastic nodes)

An agent whose profile grants `graph.elastic.request` can declare the `request_elastic_node`
tool. Calling it queues a typed `ElasticSpawnRequest` (a request id, the scope it explores,
instructions, the reason, extra dependencies the agent can already see, and routing references
that may only narrow its own); the agent keeps working and finishes normally. When the node
completes, `GraphAgentExecutor` puts the queued requests in its `GraphNodeResult` and
`StateGraph.mark_terminal` judges them in the same commit that stores the result:

1. Each request is checked (unique id, visible dependencies, narrowing references, a free node
   id); an invalid one is refused and recorded, the rest continue.
2. The valid ones are checked together against the plan's caps, and the join counts: a batch of
   N requests costs N + 1 of `max_elastic_nodes` (default 3, so up to two requests fit by default).
   If they fit, the scheduler creates one exploration node per request
   (`elastic:<node>:<id>`, one level deeper) and one join (`join:<node>`) that waits for all
   of them, and puts the node's not-yet-started dependents behind the join. Every request
   gets a `GraphSpawnRecord`.
3. The children run in the following waves through `GraphAgentExecutor`, which builds their
   bindings (through the `Orchestrator`: the root's profile, model, skills, tools and
   capabilities, never more). A failed child does not stop the join; a blocked or cancelled
   one does.
4. The join resumes the requester: a fresh run of the same definition whose task text lists
   every child's status and output, marked as untrusted data. Dependents see the join's result.
5. A batch that exceeds the plan's depth or node cap is deferred as a whole: the join is blocked
   by its own result, so the controller cannot complete until someone calls
   `grant_elastic_capacity` (raise the caps and run the batch) or `decline_elastic_requests`
   (complete the join without running it). By default an agent is told at once when no room
   remains; a binding with `escalate_elastic_overflow` queues the request for that decision.
   Every run also has ceilings, which the orchestrator takes from `OrchestrationPolicy`, that
   bound a plan's caps and every grant. A batch that would pass a ceiling is never deferred:
   each of its requests is refused with `ELASTIC_NODE_CEILING_REACHED` or
   `ELASTIC_DEPTH_CEILING_REACHED`, and the agent is told at once.
6. **Siblings.** The agents of one wave run at the same time, so the scheduler keeps a per-wave
   ledger and hands each agent an `ElasticReservation`. `request_elastic_node` reserves the
   agent's whole queue (its requests plus the join) against what the other running agents
   already hold, first come, first served, and the receipt reports what is left. A node that
   does not complete gives its hold back. The commit stays the authority, but a request an
   agent was told is queued is not refused at commit because of a sibling.
7. **Crash.** A node persisted as RUNNING when the process died is replayed only when the
   caller names it or the replay policy accepts it (`GraphAgentExecutor.is_replayable`: the
   binding is declared idempotent, and an elastic node's binding is built from the factory
   for the check); every other one fails with `interrupted-non-idempotent`.
   `Orchestrator.recover_execution` does this for a DISPATCHED orchestration and runs the
   remaining waves.

## Workflow 5 - specification to retrievable evidence

`SpecificationPreprocessor` parses the manifest into source-preserving document trees;
`GroundedRetrievalService` ranks references (lexical or Qdrant) and re-verifies each against
local trees; `StructuralContextSelector` selects the required closure and packs optional
evidence with the exact PCKP solver. Nothing here approves, locks or versions a
specification: that belongs to the interface that calls the agent.

## Workflow 6 - evidence and integrity

| Evidence | Where it lives under the run root | Integrity mechanism |
|---|---|---|
| Telemetry events and metrics | `.agent-telemetry/` (SQLite, WAL) | per-run sequence plus previous-event hash |
| Audit transcript | `.agent-audit-logs/` (JSONL per run, Markdown on request) | previous-entry hash, fsync per entry, cross-process lock |
| Run profile | inside the result and telemetry | hash-sealed at `finish_run` |
| Full tool results | `.agent-tool-results/` | content hash in the handle (`verify-evidence` re-computes it) |
| Episode records | `.agent-memory/` | per-record state |
| Project state | `.agent-project-state/` | state hash plus hash-chained event file per revision, one cross-process lock per project |
| Graph runs | `.agent-runs/` | `run_hash` plus snapshot-named history-prefix hash, one cross-process lock per run |
| Approvals | `.agent-runs/<run>.approvals.json` | atomic replace under a cross-process lock |
| Controllers | `.agent-controllers/` | event-prefix hash named by the snapshot, one cross-process lock per controller |
| Orchestrations | `.agent-orchestrations/` | atomic replace under one cross-process lock per record, policy immutability |
| Run-head checkpoints | outside the run root, in a `CheckpointSink` (by default one file per run: `<destination>/<run>.checkpoints.jsonl`) | a signature (HMAC-SHA256, or a host signer) over the sequence and head hash of the telemetry and audit chains, appended under a lock, taken by the runtime when a `RunHeadCheckpointer` is configured and checked by `verify-checkpoints` against the run and, for a pruned run, retention's tombstone |

Each chain detects an edit in the middle; the artifact write log (`.agent-artifacts/history/writes.jsonl`) is chained the same way and checked by `ArtifactStore.verify_history`. The removal of the newest entries, or a rewrite that reseals a chain, is detected only against a record kept outside the run root: the tombstone anchor for the retention log and a signed run-head checkpoint (`export_run_head_checkpoint`, `nailong-agent-sdk-dev checkpoint-run`) for a run. At the end of a run its `agent.profile-completed` event records the audit chain's head and a final `terminal-link` audit entry records that event's hash, so one intact store vouches for the other (`verify_terminal_link`); the link is an entry of its own so that a small audit payload bound cannot cut it. A run that retention pruned verifies as `pruned`, not `truncated`, when its tombstone holds the checkpoint's final heads. Nothing is checkpointed unless asked: by `checkpoint-run`, or by the runtime when `AgentRuntimeServices.open(checkpointer=...)` (the MCP service: `AGENT_RUNTIME_CHECKPOINT_DIR` and `AGENT_RUNTIME_CHECKPOINT_KEY_FILE`) names a `RunHeadCheckpointer`.

Verifying the chains says whether the evidence can be trusted, not what the run did.
`nailong-agent-sdk-dev diagnose-run <root> <run-id>` (`diagnose_run`) reads the same ledgers and,
for a graph run, its record, and reports the terminal finding, the first problem and the first
error, and the failing nodes with the nodes each one held up; it writes nothing.

`nailong-agent-sdk-dev attribute-failure <root> <run-id>` (`attribute_failure`) goes one step
further. For a graph run it ranks the nodes that stopped it by how much of the stoppage each
explains (`responsibility`) and says what repairing each one alone would release
(`released_alone`) and what else it needs (`also_requires`); `--node <id>` explains why one
blocked node is blocked. For an agent run, and for the agent behind each of the ten most
responsible roots, it names the model turn in the audit transcript that issued the failing
call, produced the rejected output or reported itself blocked, and says plainly when the
failure came from a limit or the environment and no single decision caused it.
`nailong-agent-sdk-dev attribute-defect <dir> <path> --contains TEXT` (`attribute_defect`)
answers the file-level question: which write of an artifact introduced a defect, and which
agent, node, run, tool call and iteration made it.
`nailong-agent-sdk-dev attribute-result <root> <run-id> --contains TEXT` (`attribute_result_defect`)
answers the same question for a tool result: which result of an agent run first fails a check
you supply (a tool that reported success with a corrupted payload), and which model turn asked
for it. All three read only.

## Workflow 8 - calling the agent from a backend (`AgentTaskRunner`)

A backend sends three things to MCP `run_agent_task` (or `AgentTaskRunner.run`): an
`AgentDefinition` (identity, instructions, schemas, the tools the model may call, the model
binding with its fallbacks), a `ScopedAgentTask` (input, scope, acceptance criteria, skills) and
the run options. Everything else is a parameter too:

```json
{
  "model_endpoint": {"base_url": "https://api.example.com/v1", "api_key": "..."},
  "permissions": {
    "capabilities": ["filesystem.read", "draft.write", "mcp.backend"],
    "approved_capabilities": ["draft.write", "mcp.backend"]
  },
  "builtin_tools": ["read_file", "write_draft"],
  "declared_output_paths": ["report.md"],
  "mcp_servers": [{"name": "backend", "url": "https://backend.example/mcp",
                   "headers": {"Authorization": "Bearer ..."}}],
  "run_deadline_seconds": 600
}
```

1. The options are validated as a whole; a contradiction (an endpoint together with scripted
   turns, an MCP server whose `mcp.<name>` capability is not granted) is refused naming the
   field.
2. Options that start processes (`command_templates`, stdio MCP servers) are refused unless the
   operator started the service with `AGENT_RUNTIME_ALLOW_PROCESS_OPTIONS=1`.
3. The named MCP servers are connected and their tools are offered to the model as
   `mcp__<server>__<tool>`; a server that does not connect is named with the reason.
4. Every tool the definition (plus `builtin_tools`) declares must have an implementation, and
   each call goes through `CapabilityPolicy`: a capability that is not granted ends the run
   BLOCKED naming it, and a mutating or process capability also needs to be in
   `approved_capabilities`, or, with `permissions.approval_mode` `wait`, to be approved while
   the run is paused (`decide_task_approval`); a rejection, or no answer within
   `approval_timeout_seconds`, ends the run BLOCKED naming the approval.
5. File tools work inside the workspace (`workspaces/<task id>` unless `workspace` names another
   path below the service root).
6. The model is the OpenAI-compatible adapter for the definition's binding on the given
   endpoint; with fallbacks, each fallback adapter receives its own binding.
7. The result is the usual `AgentResult`. `cancel_agent_task` stops a running task and
   interrupts the model call, tool call or approval wait in flight; the files the run wrote
   are read back with `list_task_files` and `read_task_file`; a second run of the same task
   id is refused while one is running.

## Where do I change...?

| I want to | Start here |
|---|---|
| add a core tool | `tools/core/definitions.py` (schema), `tools/core/services.py` (dispatcher), `tools/registry.py` (capability and side-effect class) |
| define a specialist (its tools, skills, permissions) | nothing is built in: the caller passes `AgentDefinition.tools`, `ScopedAgentTask.skills` and a `CapabilityGrant`, or an `AgentExecutionProfile` inside an `OrchestrationPolicy` for a multi-agent run |
| change what a backend can pass for a run | `TaskRunOptions` and `AgentTaskRunner` in `agent/task_runner.py`; the MCP tool is a wrapper in `mcp/agent_tools.py` |
| gate a tool differently | `tools/policy.py` (`CapabilityPolicy`), `tools/approvals.py` |
| add a model provider | implement `AgentModel` (`agent/model.py`); use `agent/openai_compatible/chat.py` as the template; wrap several in `FailoverAgentModel` |
| change what the model sees | `agent/openai_compatible/chat.py::_chat_payload`, `memory/context_projection.py`, `foundations/contracts.py` (`ModelContext` fields live in `agent/model.py`) |
| change compaction | `memory/episode_store.py` (strategies), `memory/episode_scoring.py`, `foundations/optimization/` (solvers) |
| change how compacted episodes are summarized, verified, evicted or restored | `memory/semantic_compaction.py` (`SemanticCompactor` and its checks), `memory/episode_archive.py`, `SemanticCompactionPolicy` in `memory/episode_models.py`, `InMemoryEpisodeStore._semantic_pass` and `rehydrate` |
| add a verification gate | register on a `VerificationGateRegistry` (`agent/verification.py`); a gate may declare a `verifier_id`, an `authority` class and `evidence`, and `register_all_of` composes registered gates |
| record who decided an approval or a plan | pass a `DecisionSource` to `AgentTaskRunner.decide_approval`, `ApprovalRegistry.submit`, `ControllerRuntime.approve_plan`, `grant_elastic_capacity`, `decline_elastic_requests` or `Orchestrator.approve`; the MCP decision tools pass the principal |
| see who may do what | `nailong-agent-sdk-dev authority-matrix` or `authority_matrix()` (`mcp/authority_matrix.py`); the reducer's tables are in `state/project_state_engine.py` and the credential rules in `McpAuthorityPolicy.check` |
| keep proof outside the run root that a run was not cut short | `observability/run_checkpoints.py` (`export_run_head_checkpoint`, `verify_run_head_checkpoints`), `nailong-agent-sdk-dev checkpoint-run` and `verify-checkpoints` |
| have the runtime checkpoint every run when it ends | `agent/run_checkpointer.py` (`RunHeadCheckpointer`), `AgentRuntimeServices.open(checkpointer=)`, `AgentRuntimeServices.checkpoint_after_run`; for the MCP service `AGENT_RUNTIME_CHECKPOINT_DIR`, `AGENT_RUNTIME_CHECKPOINT_KEY_FILE` and `AGENT_RUNTIME_CHECKPOINT_SIGNER_ID` in `mcp/security.py` |
| keep checkpoints in a store other than a directory, or sign with another key scheme | implement `CheckpointSink` or `CheckpointSigner` (`observability/run_checkpoints.py`) |
| change how much of an audit payload is kept | `AuditTranscriptStore(max_payload_chars=)`, `AgentRuntimeServices.open(audit_max_payload_chars=)`, `AGENT_RUNTIME_AUDIT_MAX_PAYLOAD_CHARS` |
| add a project-state transition | `state/project_state_models.py` (`StateTransitionKind`), `ProjectStateReducer.apply` in `state/project_state_engine.py` |
| add a graph node kind or executor | `state/graph_models.py` (`GraphNodeKind`), the executor map passed to `execute_graph` |
| let agents explore beyond the plan, or change the elastic rules | `state/elastic.py` (requests, limits, admission checks), `StateGraph._apply_spawn_requests` (commit-time handling), `StateGraph._reserve_elastic` (per-wave ledger), `tools/elastic_requests.py` (agent queue), `agent/orchestrator/` (policy ceilings, derived bindings) |
| change what happens to nodes a crash interrupted | `StateGraph.recover_interrupted`, `GraphAgentExecutor.is_replayable`, `Orchestrator.recover_execution` |
| add a metric | `observability/metric_definitions.py` and the emit site (`record_metric_value`) |
| add an MCP tool | the matching `mcp/*_tools.py` `register_*` function, registering with `ctx.tool(server, name, exclusive=...)` |
| support a new specification format | `specifications/documents.py` (`DocumentFormat`) and `SpecificationPreprocessor._parse` |
| keep a run root from growing | `agent/retention.py` (`RunRetention.prune` with a `RetentionPolicy`), MCP `prune_runs`, `nailong-agent-sdk-dev prune-runs <root> --older-than-seconds N [--apply]` |
| fetch what a task wrote, or answer its approvals while it runs | `agent/task_files.py`, `AgentTaskRunner.workspace_for`, `approvals` and `decide_approval`; MCP `list_task_files`, `read_task_file`, `list_task_approvals`, `decide_task_approval` |
| debug a run | `nailong-agent-sdk-dev diagnose-run <root> <run-id or graph-run-id>` for where it first went wrong and what that held up; MCP `get_telemetry_events` / `get_audit_log` / `render_audit_transcript`, `nailong-agent-sdk-dev inspect-run <root> <run-id>` for the raw ledgers, or `nailong-agent-sdk-dev verify-evidence <root> <project-id>` |
| give a failure a typed code | the `AgentFailure(code=...)` at the exit in `agent/base_agent/agent.py` (a tool result carries it in `ToolExecutionResult.failure`); a graph node result carries it as a `failure-code:` diagnostic (`state/graph_models.py`) |
| change what a diagnosis reports | `developer_tools/diagnose.py` (`_EXTRACTORS` for the event types that become findings, `diagnose_graph` for roots and what they held up) |
| change how responsibility or a repair is computed | `developer_tools/attribute_failure.py` (`_tally`, `attribute_graph`) over the relation in `state/graph.py` (`root_cause_sets`, `released_by`, `blocking_path`) |
| change which failures are tied to a model decision | `developer_tools/decision_links.py` (`_CALL_CODES`, `_OUTPUT_CODES`, `_BLOCKED_CODES`, `_decision`) |
| find which tool result first went wrong | `nailong-agent-sdk-dev attribute-result <root> <run-id> --contains TEXT [--tool NAME]`, or `attribute_result_defect` with your own check; the results come from the journal under `.agent-tool-results/` |
| find who wrote a defect into a file | `nailong-agent-sdk-dev attribute-defect <dir> <path> --contains TEXT`, or `attribute_defect` with your own check; the history behind it is `ArtifactStore.history` |
| change what a write records about its writer | `tools/registry.py` (`HarnessToolExecutor.execute` builds the writer fields) and `tools/core/services.py` (`CoreToolDispatcher._manifest`) |

## Glossary

* **Episode** - one tool call and its result as a unit of memory; *exploratory* (read-only) or
  *action* (consumes other episodes). **Observation** - the model-facing message about a call.
* **Projection** - the bounded view of memory and state built for one turn. **Compaction** -
  dropping an episode's content while keeping a tombstone. **Stub** - the one-line, handle-bearing
  residue of a compacted episode. **Handle** - an opaque id of a journalled full result.
* **Semantic summary** - a verified, provenance-carrying digest of compacted episodes
  (`SemanticEpisodeSummary`): derived text that names its source episodes, their archived
  snapshots and journal handles, and is never evidence in itself. **Archive** - the
  content-addressed store of original episode snapshots. **Rehydration** - restoring an
  archived episode to live.
* **Wave** - the set of graph nodes that are runnable together. **Controller** - the vertical
  state machine (plan, approval, dispatch, repair, escalation). **Harness** - the deterministic
  runtime around the model: policy, journal, state, audit.
* **Discovery** - a closed, source-backed lateral finding published into graph state.
* **PCKP** - precedence-constrained knapsack: choose items under a budget when picking an item
  requires its prerequisites.

## File index

| File | Lines | Role |
|---|---:|---|
| [`__init__.py`](root.md#__init__py---the-public-api-surface-lazy-re-exports) | 931 | the public API surface (lazy re-exports) |
| [`agent/__init__.py`](agent.md#agent__init__py---package-marker-for-agent-execution) | 3 | package marker for agent execution |
| [`agent/base_agent/__init__.py`](agent.md#agentbase_agent__init__py---public-surface-of-the-baseagent-runtime-package) | 30 | public surface of the BaseAgent runtime package |
| [`agent/base_agent/agent.py`](agent.md#agentbase_agentagentpy---the-single-task-agent-loop) | 2643 | the single-task agent loop |
| [`agent/base_agent/types.py`](agent.md#agentbase_agenttypespy---small-value-types-used-by-the-baseagent-loop) | 99 | small value types used by the BaseAgent loop |
| [`agent/cost_model.py`](agent.md#agentcost_modelpy---token-prices-per-call-and-per-run-cost-and-prefix-cache-forecasting) | 261 | token prices, per-call and per-run cost, and prefix-cache forecasting |
| [`agent/elastic_context.py`](agent.md#agentelastic_contextpy---bounded-task-text-for-exploration-and-continuation-nodes) | 105 | bounded task text for exploration and continuation nodes |
| [`agent/graph_agent_executor.py`](agent.md#agentgraph_agent_executorpy---runs-registered-baseagent-bindings-as-graph-nodes) | 320 | runs registered BaseAgent bindings as graph nodes |
| [`agent/model.py`](agent.md#agentmodelpy---provider-neutral-model-contract-streaming-events-and-failover) | 439 | provider-neutral model contract, streaming events and failover |
| [`agent/openai_compatible/__init__.py`](agent.md#agentopenai_compatible__init__py---public-surface-of-the-openai-compatible-adapters) | 42 | public surface of the OpenAI-compatible adapters |
| [`agent/openai_compatible/chat.py`](agent.md#agentopenai_compatiblechatpy---chat-completions-adapter-for-the-agent-model-contract) | 796 | Chat Completions adapter for the agent model contract |
| [`agent/openai_compatible/embeddings.py`](agent.md#agentopenai_compatibleembeddingspy---synchronous-embedding-provider-for-retrieval-indexes) | 68 | synchronous embedding provider for retrieval indexes |
| [`agent/openai_compatible/transport.py`](agent.md#agentopenai_compatibletransportpy---json-http-transports-and-endpoint-configuration) | 537 | JSON HTTP transports and endpoint configuration |
| [`agent/openai_compatible/vision.py`](agent.md#agentopenai_compatiblevisionpy---image-proposals-through-a-verified-byte-loader) | 163 | image proposals through a verified byte loader |
| [`agent/orchestrator/__init__.py`](agent.md#agentorchestrator__init__py---public-surface-of-the-orchestrator-package) | 31 | public surface of the orchestrator package |
| [`agent/orchestrator/models.py`](agent.md#agentorchestratormodelspy---user-owned-orchestration-policy-request-and-record-contracts) | 180 | user-owned orchestration policy, request and record contracts |
| [`agent/orchestrator/orchestrator.py`](agent.md#agentorchestratororchestratorpy---deterministic-composition-of-policy-plan-controller-and-graph-execution) | 1004 | deterministic composition of policy, plan, controller and graph execution |
| [`agent/orchestrator/state_store.py`](agent.md#agentorchestratorstate_storepy---atomic-local-persistence-of-orchestration-records-and-policies) | 119 | atomic local persistence of orchestration records and policies |
| [`agent/retention.py`](agent.md#agentretentionpy---opt-in-pruning-of-finished-runs-with-a-hash-chained-tombstone-for-each) | 666 | opt-in pruning of finished runs with a hash-chained tombstone for each |
| [`agent/retention_anchor.py`](agent.md#agentretention_anchorpy---anchors-of-the-tombstone-chains-head-kept-outside-the-run-root) | 179 | anchors of the tombstone chain's head, kept outside the run root |
| [`agent/retention_inventory.py`](agent.md#agentretention_inventorypy---read-only-inventory-of-every-persistent-resource-class-under-a-run-root) | 980 | read-only inventory of every persistent resource class under a run root |
| [`agent/run_checkpointer.py`](agent.md#agentrun_checkpointerpy---opt-in-signed-checkpoints-of-a-runs-chain-heads-taken-when-a-run-ends) | 71 | opt-in signed checkpoints of a run's chain heads, taken when a run ends |
| [`agent/runtime.py`](agent.md#agentruntimepy---composition-root-for-the-durable-stores-around-an-agent) | 222 | composition root for the durable stores around an agent |
| [`agent/task_files.py`](agent.md#agenttask_filespy---bounded-paged-listing-and-reading-of-the-files-an-agent-task-left-in-its-workspace) | 213 | bounded, paged listing and reading of the files an agent task left in its workspace |
| [`agent/task_runner.py`](agent.md#agenttask_runnerpy---caller-driven-task-runner-model-tools-permissions-and-limits-all-come-from-the-caller) | 749 | caller-driven task runner: model, tools, permissions and limits all come from the caller |
| [`agent/verification.py`](agent.md#agentverificationpy---host-registered-bounded-output-acceptance-gates) | 474 | host-registered, bounded output-acceptance gates |
| [`developer_tools/__init__.py`](developer_tools.md#developer_tools__init__py---public-surface-of-the-developer-utilities) | 94 | public surface of the developer utilities |
| [`developer_tools/attribute_defect.py`](developer_tools.md#developer_toolsattribute_defectpy---which-write-of-an-artifact-introduced-a-defect-and-which-agent-made-it) | 286 | which write of an artifact introduced a defect, and which agent made it |
| [`developer_tools/attribute_failure.py`](developer_tools.md#developer_toolsattribute_failurepy---which-failure-and-which-model-decision-stopped-a-run) | 390 | which failure, and which model decision, stopped a run |
| [`developer_tools/attribute_lineage.py`](developer_tools.md#developer_toolsattribute_lineagepy---trace-a-failing-run-back-through-the-artifact-versions-it-read) | 292 | trace a failing run back through the artifact versions it read |
| [`developer_tools/attribute_result.py`](developer_tools.md#developer_toolsattribute_resultpy---which-tool-result-of-an-agent-run-first-failed-the-callers-check-and-which-model-turn-asked-for-it) | 207 | which tool result of an agent run first failed the caller's check, and which model turn asked for it |
| [`developer_tools/catalog.py`](developer_tools.md#developer_toolscatalogpy---public-api-catalogue-generation) | 88 | public API catalogue generation |
| [`developer_tools/cli.py`](developer_tools.md#developer_toolsclipy---the-nailong-agent-sdk-dev-command-line) | 607 | the `nailong-agent-sdk-dev` command line |
| [`developer_tools/decision_links.py`](developer_tools.md#developer_toolsdecision_linkspy---which-model-turn-a-failing-finding-traces-back-to) | 355 | which model turn a failing finding traces back to |
| [`developer_tools/diagnose.py`](developer_tools.md#developer_toolsdiagnosepy---where-a-run-first-went-wrong-what-ended-it-and-what-it-held-up) | 711 | where a run first went wrong, what ended it and what it held up |
| [`developer_tools/inspect.py`](developer_tools.md#developer_toolsinspectpy---read-only-run-evidence-inspection) | 226 | read-only run evidence inspection |
| [`developer_tools/quality.py`](developer_tools.md#developer_toolsqualitypy---closed-ruff-quality-check) | 92 | closed Ruff quality check |
| [`developer_tools/validate.py`](developer_tools.md#developer_toolsvalidatepy---contract-file-validation) | 97 | contract-file validation |
| [`foundations/__init__.py`](foundations.md#foundations__init__py---package-marker-for-the-dependency-free-layer) | 4 | package marker for the dependency-free layer |
| [`foundations/atomic_io.py`](foundations.md#foundationsatomic_iopy---crash-safe-file-publication-exclusive-claims-and-cross-process-locks) | 174 | crash-safe file publication, exclusive claims and cross-process locks |
| [`foundations/benchmarks.py`](foundations.md#foundationsbenchmarkspy---reproducible-exact-vs-greedy-pckp-benchmark-harness) | 88 | reproducible exact-vs-greedy PCKP benchmark harness |
| [`foundations/contracts.py`](foundations.md#foundationscontractspy---the-serializable-baseagent-contract-definitions-tasks-turns-results-context-types) | 534 | the serializable BaseAgent contract: definitions, tasks, turns, results, context types |
| [`foundations/dependency_graph.py`](foundations.md#foundationsdependency_graphpy---deterministic-cycle-and-blast-radius-traversal-over-dependent-prerequisite-edges) | 104 | deterministic cycle and blast-radius traversal over (dependent, prerequisite) edges |
| [`foundations/detached.py`](foundations.md#foundationsdetachedpy---run-a-blocking-call-on-a-daemon-thread-that-the-awaiting-task-may-abandon) | 42 | run a blocking call on a daemon thread that the awaiting task may abandon |
| [`foundations/errors.py`](foundations.md#foundationserrorspy---typed-sdk-errors-and-secretreasoning-redaction-for-durable-records) | 284 | typed SDK errors and secret/reasoning redaction for durable records |
| [`foundations/hashing.py`](foundations.md#foundationshashingpy---canonical-json-encodings-sha-256-digests-and-the-token-estimate) | 56 | canonical JSON encodings, SHA-256 digests and the token estimate |
| [`foundations/identifiers.py`](foundations.md#foundationsidentifierspy---identifier-validation-injective-file-names-and-collision-free-sequential-ids) | 90 | identifier validation, injective file names and collision-free sequential ids |
| [`foundations/json_limits.py`](foundations.md#foundationsjson_limitspy---depth-bound-for-untrusted-json-like-payloads) | 28 | depth bound for untrusted JSON-like payloads |
| [`foundations/logging.py`](foundations.md#foundationsloggingpy---opt-in-stdlib-logging-namespace-for-the-few-paths-outside-structured-telemetry) | 25 | opt-in stdlib logging namespace for the few paths outside structured telemetry |
| [`foundations/optimization/__init__.py`](foundations.md#foundationsoptimization__init__py---re-exports-the-pckp-models-and-solvers) | 17 | re-exports the PCKP models and solvers |
| [`foundations/optimization/models.py`](foundations.md#foundationsoptimizationmodelspy---pckp-problem-item-and-solution-contracts) | 90 | PCKP problem, item and solution contracts |
| [`foundations/optimization/solvers.py`](foundations.md#foundationsoptimizationsolverspy---exact-tree-dp--branch-and-bound-and-greedy-pckp-solvers) | 503 | exact (tree DP / branch-and-bound) and greedy PCKP solvers |
| [`foundations/paths.py`](foundations.md#foundationspathspy---comparison-of-windows-extended-length-path-spellings-as-one-location) | 40 | comparison of Windows extended-length path spellings as one location |
| [`foundations/record_locks.py`](foundations.md#foundationsrecord_lockspy---per-record-cross-process-locks-re-entrant-for-the-holding-thread) | 62 | per-record cross-process locks, re-entrant for the holding thread |
| [`foundations/run_fence.py`](foundations.md#foundationsrun_fencepy---per-run-write-fence-that-retention-raises-before-it-deletes-a-runs-records) | 139 | per-run write fence that retention raises before it deletes a run's records |
| [`foundations/suffix_search.py`](foundations.md#foundationssuffix_searchpy---where-a-defect-began-in-an-ordered-history-in-olog-d-probes) | 105 | where a defect began in an ordered history, in O(log d) probes |
| [`foundations/text.py`](foundations.md#foundationstextpy---newline-only-line-splitting-and-utf-8-well-formedness-for-durable-text) | 50 | newline-only line splitting and UTF-8 well-formedness for durable text |
| [`foundations/version.py`](foundations.md#foundationsversionpy---the-installed-package-name-and-version-and-the-identity-strings-derived-from-them) | 23 | the installed package name and version, and the identity strings derived from them |
| [`integrations/__init__.py`](integrations.md#integrations__init__py---public-surface-of-the-integrations-package) | 109 | public surface of the integrations package |
| [`integrations/_utils.py`](integrations.md#integrations_utilspy---private-sanitiser-digest-and-optional-import-helpers) | 152 | private sanitiser, digest and optional-import helpers |
| [`integrations/contracts.py`](integrations.md#integrationscontractspy---framework-neutral-interop-contracts) | 98 | framework-neutral interop contracts |
| [`integrations/jev/__init__.py`](integrations.md#integrationsjev__init__py---public-surface-of-the-jev-package) | 61 | public surface of the Jev package |
| [`integrations/jev/advisory.py`](integrations.md#integrationsjevadvisorypy---verification-gate-that-adds-a-non-authoritative-jev-signal) | 143 | verification gate that adds a non-authoritative Jev signal |
| [`integrations/jev/architecture.py`](integrations.md#integrationsjevarchitecturepy---monotonic-single-to-multi-routing-advice) | 164 | monotonic single-to-multi routing advice |
| [`integrations/jev/decision.py`](integrations.md#integrationsjevdecisionpy---optional-typesafe-jev-evaluator) | 297 | optional TypeSafe Jev evaluator |
| [`integrations/jev/exploration.py`](integrations.md#integrationsjevexplorationpy---optional-prioritisation-of-an-already-approved-candidate-set) | 149 | optional prioritisation of an already-approved candidate set |
| [`integrations/jev/models.py`](integrations.md#integrationsjevmodelspy---jev-question-answer-request-result-and-receipt-contracts) | 171 | Jev question, answer, request, result and receipt contracts |
| [`integrations/jev/receipts.py`](integrations.md#integrationsjevreceiptspy---receipt-sinks-for-jev-evaluations) | 57 | receipt sinks for Jev evaluations |
| [`integrations/langchain.py`](integrations.md#integrationslangchainpy---optional-langchain-adapters) | 266 | optional LangChain adapters |
| [`integrations/langgraph.py`](integrations.md#integrationslanggraphpy---optional-langgraph-adapters) | 308 | optional LangGraph adapters |
| [`integrations/receipts.py`](integrations.md#integrationsreceiptspy---receipt-sinks-for-external-operations) | 65 | receipt sinks for external operations |
| [`mcp/__init__.py`](mcp.md#mcp__init__py---package-marker-for-the-mcp-surface) | 3 | package marker for the MCP surface |
| [`mcp/_shared.py`](mcp.md#mcp_sharedpy---shared-context-and-error-formatting-for-every-tool-group) | 156 | shared context and error formatting for every tool group |
| [`mcp/agent_tools.py`](mcp.md#mcpagent_toolspy---mcp-tools-for-agent-contract-validation-context-assembly-and-scripted-runs) | 217 | MCP tools for agent contract validation, context assembly and scripted runs |
| [`mcp/arguments.py`](mcp.md#mcpargumentspy---refuse-tool-arguments-the-mcp-surface-does-not-define-instead-of-dropping-them-silently) | 124 | refuse tool arguments the MCP surface does not define instead of dropping them silently |
| [`mcp/authority.py`](mcp.md#mcpauthoritypy---who-may-call-which-tool-host-and-approver-credentials-decision-binding-and-its-audit) | 445 | who may call which tool: host and approver credentials, decision binding and its audit |
| [`mcp/authority_matrix.py`](mcp.md#mcpauthority_matrixpy---one-export-of-who-may-do-what-state-transitions-mcp-credentials-and-tool-classes) | 118 | one export of who may do what: state transitions, MCP credentials and tool classes |
| [`mcp/client.py`](mcp.md#mcpclientpy---outbound-mcp-client-lifecycle) | 550 | outbound MCP client lifecycle |
| [`mcp/client_bridge.py`](mcp.md#mcpclient_bridgepy---opt-in-bridge-from-external-mcp-tools-to-governed-harness-tools) | 94 | opt-in bridge from external MCP tools to governed harness tools |
| [`mcp/client_types.py`](mcp.md#mcpclient_typespy---typed-configuration-and-status-for-the-outbound-mcp-client) | 90 | typed configuration and status for the outbound MCP client |
| [`mcp/controller_tools.py`](mcp.md#mcpcontroller_toolspy---mcp-tools-for-the-deterministic-controller) | 340 | MCP tools for the deterministic controller |
| [`mcp/orchestration_tools.py`](mcp.md#mcporchestration_toolspy---mcp-tools-for-plan-validation-graph-runs-and-orchestration-compilation) | 156 | MCP tools for plan validation, graph runs and orchestration compilation |
| [`mcp/project_state_tools.py`](mcp.md#mcpproject_state_toolspy---mcp-tools-for-project-working-memory) | 274 | MCP tools for project working memory |
| [`mcp/run_tools.py`](mcp.md#mcprun_toolspy---mcp-tools-for-graph-run-state-cancellation-approvals-and-resumption) | 107 | MCP tools for graph run state, cancellation, approvals and resumption |
| [`mcp/security.py`](mcp.md#mcpsecuritypy---bearer-token-authentication-and-host-checks-for-the-http-service) | 443 | bearer-token authentication and host checks for the HTTP service |
| [`mcp/server.py`](mcp.md#mcpserverpy---mcp-server-factory-and-loopback-entry-point) | 205 | MCP server factory and loopback entry point |
| [`mcp/specification_tools.py`](mcp.md#mcpspecification_toolspy---mcp-tool-for-specification-parsing) | 40 | MCP tool for specification parsing |
| [`mcp/telemetry_tools.py`](mcp.md#mcptelemetry_toolspy---mcp-tools-for-telemetry-audit-logs-and-metrics) | 212 | MCP tools for telemetry, audit logs and metrics |
| [`mcp/tool_groups.py`](mcp.md#mcptool_groupspy---the-ordered-tool-groups-every-mcp-server-registers) | 27 | the ordered tool groups every MCP server registers |
| [`memory/__init__.py`](memory.md#memory__init__py---package-marker-for-episode-memory-and-context-assembly) | 3 | package marker for episode memory and context assembly |
| [`memory/context.py`](memory.md#memorycontextpy---builds-the-fixed-initial-prompt-for-a-run) | 51 | builds the fixed initial prompt for a run |
| [`memory/context_projection.py`](memory.md#memorycontext_projectionpy---per-turn-bounded-context-projection-and-the-tool-result-journal) | 644 | per-turn bounded context projection and the tool-result journal |
| [`memory/entailment_screen.py`](memory.md#memoryentailment_screenpy---deterministic-lexical-screen-for-claims-that-contradict-or-outrun-their-cited-evidence) | 117 | deterministic lexical screen for claims that contradict or outrun their cited evidence |
| [`memory/episode_archive.py`](memory.md#memoryepisode_archivepy---content-addressed-archive-of-the-original-snapshots-of-compacted-episodes) | 290 | content-addressed archive of the original snapshots of compacted episodes |
| [`memory/episode_models.py`](memory.md#memoryepisode_modelspy---episode-records-compaction-policy-and-the-retention-contracts) | 649 | episode records, compaction policy and the retention contracts |
| [`memory/episode_scoring.py`](memory.md#memoryepisode_scoringpy---deterministic-lexical-relevance-and-cost-helpers-for-retention) | 77 | deterministic lexical relevance and cost helpers for retention |
| [`memory/episode_store.py`](memory.md#memoryepisode_storepy---episode-lifecycle-and-the-three-deterministic-compaction-strategies) | 2267 | episode lifecycle and the three deterministic compaction strategies |
| [`memory/episodes.py`](memory.md#memoryepisodespy---task-scoped-episode-graph-of-one-line-summaries) | 97 | task-scoped episode graph of one-line summaries |
| [`memory/learned_retention.py`](memory.md#memorylearned_retentionpy---opt-in-learned-utility-coefficients-for-dependency-closed-retention) | 215 | opt-in learned utility coefficients for dependency-closed retention |
| [`memory/memory_snapshot.py`](memory.md#memorymemory_snapshotpy---one-file-generation-numbered-snapshots-of-episodic-memory-and-their-validation) | 251 | one-file, generation-numbered snapshots of episodic memory and their validation |
| [`memory/retention_outcomes.py`](memory.md#memoryretention_outcomespy---retention-decisions-and-their-outcomes-recorded-for-offline-learning) | 200 | retention decisions and their outcomes, recorded for offline learning |
| [`memory/utility_objectives.py`](memory.md#memoryutility_objectivespy---generalized-training-objectives-regret-weighted-pairwise-listwise-regression-group-dro) | 404 | generalized training objectives: regret-weighted pairwise, listwise, regression, Group-DRO |
| [`memory/utility_calibration.py`](memory.md#memoryutility_calibrationpy---utility-calibration-distribution-shift-evidence-and-logged-static-fallback) | 288 | utility calibration, distribution-shift evidence and logged static fallback |
| [`memory/utility_features_v2.py`](memory.md#memoryutility_features_v2py---feature-schema-v2-task-conditioned-bipartite-graph-and-failure-propagation-features) | 225 | feature schema v2: task-conditioned, bipartite-graph and failure-propagation features |
| [`memory/tiered_retention.py`](memory.md#memorytiered_retentionpy---three-level-retention-keep-summarise-or-drop-under-one-budget) | 466 | three-level retention: keep, summarise or drop under one budget |
| [`memory/semantic_compaction.py`](memory.md#memorysemantic_compactionpy---dependency-aware-summarization-verification-and-safe-commit-of-compacted-episodes) | 1133 | dependency-aware summarization, verification and safe commit of compacted episodes |
| [`memory/summarization_scheduler.py`](memory.md#memorysummarization_schedulerpy---bounded-background-queue-for-summarization-work-that-never-blocks-its-caller) | 190 | bounded background queue for summarization work that never blocks its caller |
| [`memory/summary_ledger.py`](memory.md#memorysummary_ledgerpy---the-summaries-of-one-episode-store-and-where-each-one-lives) | 192 | the summaries of one episode store and where each one lives |
| [`memory/utility_features.py`](memory.md#memoryutility_featurespy---selection-time-features-for-learned-episode-utility) | 131 | selection-time features for learned episode utility |
| [`memory/utility_models.py`](memory.md#memoryutility_modelspy---additive-linear-and-mlp-utility-models-with-digest-checked-artifacts) | 293 | additive linear and MLP utility models with digest-checked artifacts |
| [`memory/utility_training.py`](memory.md#memoryutility_trainingpy---pairwise-logistic-ranking-loss-derivatives-and-explicit-trainers) | 339 | pairwise logistic ranking loss, derivatives and explicit trainers |
| [`observability/__init__.py`](observability.md#observability__init__py---package-marker-for-the-observability-layer) | 3 | package marker for the observability layer |
| [`observability/audit_log.py`](observability.md#observabilityaudit_logpy---append-only-hash-chained-per-run-jsonl-audit-transcript) | 463 | append-only, hash-chained, per-run JSONL audit transcript |
| [`observability/metric_definitions.py`](observability.md#observabilitymetric_definitionspy---the-sdks-standard-metric-catalogue) | 450 | the SDK's standard metric catalogue |
| [`observability/metrics.py`](observability.md#observabilitymetricspy---record-metric-values-against-the-standard-catalogue) | 131 | record metric values against the standard catalogue |
| [`observability/otlp.py`](observability.md#observabilityotlppy---otlphttp-json-export-of-recorded-runs-spans-log-records-and-metric-points) | 863 | OTLP/HTTP JSON export of recorded runs: spans, log records and metric points |
| [`observability/profiler.py`](observability.md#observabilityprofilerpy---privacy-conscious-timing-profile-for-one-agent-run) | 330 | privacy-conscious timing profile for one agent run |
| [`observability/run_checkpoints.py`](observability.md#observabilityrun_checkpointspy---signed-checkpoints-of-a-runs-chain-heads-kept-outside-the-run-root) | 864 | signed checkpoints of a run's chain heads, kept outside the run root |
| [`observability/telemetry_helpers.py`](observability.md#observabilitytelemetry_helperspy---tiny-constructors-for-timestamps-and-metric-observations-and-the-shared-hash-chain-checker) | 99 | tiny constructors for timestamps and metric observations, and the shared hash-chain checker |
| [`observability/telemetry_models.py`](observability.md#observabilitytelemetry_modelspy---telemetry-event-context-actor-and-metric-contracts) | 239 | telemetry event, context, actor and metric contracts |
| [`observability/telemetry_store.py`](observability.md#observabilitytelemetry_storepy---durable-telemetry-ledger-sqlite-events-with-a-per-run-hash-chain-plus-metrics) | 768 | durable telemetry ledger: SQLite events with a per-run hash chain, plus metrics |
| [`observability/trace_context.py`](observability.md#observabilitytrace_contextpy---w3c-trace-context-ids-the-traceparent-header-and-the-process-resource) | 181 | W3C Trace Context ids, the traceparent header and the process resource |
| [`specifications/__init__.py`](specifications.md#specifications__init__py---package-marker-for-the-specification-pipeline) | 3 | package marker for the specification pipeline |
| [`specifications/documents.py`](specifications.md#specificationsdocumentspy---manifest-document-node-and-source-locator-contracts) | 126 | manifest, document, node and source-locator contracts |
| [`specifications/evidence_graph.py`](specifications.md#specificationsevidence_graphpy---source-preserving-evidence-graph-with-required-closure-and-bounded-packing) | 371 | source-preserving evidence graph with required closure and bounded packing |
| [`specifications/preprocessing.py`](specifications.md#specificationspreprocessingpy---manifest-driven-source-preserving-specification-parsing) | 392 | manifest-driven, source-preserving specification parsing |
| [`specifications/retrieval.py`](specifications.md#specificationsretrievalpy---provenance-grounded-candidate-retrieval-with-caches-and-optional-vector-backends) | 548 | provenance-grounded candidate retrieval with caches and optional vector backends |
| [`specifications/retrieval_models.py`](specifications.md#specificationsretrieval_modelspy---retrieval-document-query-candidate-and-result-contracts) | 121 | retrieval document, query, candidate and result contracts |
| [`specifications/vision.py`](specifications.md#specificationsvisionpy---vision-extraction-adapter-protocol-and-trivial-adapters) | 43 | vision-extraction adapter protocol and trivial adapters |
| [`state/__init__.py`](state.md#state__init__py---package-marker-for-durable-run-state) | 3 | package marker for durable run state |
| [`state/controller_runtime.py`](state.md#statecontroller_runtimepy---durable-facade-over-the-controller-the-graph-run-project-state-and-telemetry) | 911 | durable facade over the controller, the graph run, project state and telemetry |
| [`state/coordination_records.py`](state.md#statecoordination_recordspy---durable-run-record-and-its-integrity-hash) | 42 | durable run record and its integrity hash |
| [`state/elastic.py`](state.md#stateelasticpy---typed-elastic-node-requests-specs-spawn-records-and-the-admission-checks) | 353 | typed elastic-node requests, specs, spawn records and the admission checks |
| [`state/graph.py`](state.md#stategraphpy---deterministic-wave-scheduler-and-authoritative-typed-run-state) | 1652 | deterministic wave scheduler and authoritative typed run state |
| [`state/graph_models.py`](state.md#stategraph_modelspy---typed-node-edge-event-and-shared-state-contracts-for-the-run-graph) | 255 | typed node, edge, event and shared-state contracts for the run graph |
| [`state/harness_coordinator.py`](state.md#stateharness_coordinatorpy---run-lifecycle-plan-validation-graph-start-wave-execution-approvals-cancel-and-recovery) | 374 | run lifecycle: plan validation, graph start, wave execution, approvals, cancel and recovery |
| [`state/orchestration.py`](state.md#stateorchestrationpy---controller-state-machine-and-its-crash-safe-store) | 453 | controller state machine and its crash-safe store |
| [`state/orchestration_models.py`](state.md#stateorchestration_modelspy---controller-phase-routing-rule-and-record-contracts) | 110 | controller phase, routing-rule and record contracts |
| [`state/planning.py`](state.md#stateplanningpy---typed-plan-contracts-and-the-deterministic-plan-validator) | 496 | typed plan contracts and the deterministic plan validator |
| [`state/project_state_engine.py`](state.md#stateproject_state_enginepy---mechanical-reducer-and-token-bounded-projector-over-projectstate) | 533 | mechanical reducer and token-bounded projector over `ProjectState` |
| [`state/project_state_models.py`](state.md#stateproject_state_modelspy---bounded-hash-sealed-working-memory-contracts) | 363 | bounded, hash-sealed working-memory contracts |
| [`state/project_state_store.py`](state.md#stateproject_state_storepy---in-memory-and-file-backed-project-state-stores-with-a-hash-chained-audit-trail) | 252 | in-memory and file-backed project-state stores with a hash-chained audit trail |
| [`state/run_state_store.py`](state.md#staterun_state_storepy---crash-safe-run-persistence-as-a-fixed-state-snapshot-plus-a-growing-state-sidecar) | 445 | crash-safe run persistence as a fixed-state snapshot plus a growing-state sidecar |
| [`state/shared_state.py`](state.md#stateshared_statepy---typed-lateral-state-payloads-exact-routing-references-and-provenance-records) | 240 | typed lateral-state payloads, exact routing references and provenance records |
| [`tools/__init__.py`](tools.md#tools__init__py---package-marker-for-governed-tool-execution) | 3 | package marker for governed tool execution |
| [`tools/approvals.py`](tools.md#toolsapprovalspy---typed-approval-gates-for-state-changing-actions) | 261 | typed approval gates for state-changing actions |
| [`tools/artifact_anchor.py`](tools.md#toolsartifact_anchorpy---signed-anchors-of-the-artifact-write-log-kept-outside-the-workspace) | 195 | signed anchors of the artifact write log, kept outside the workspace |
| [`tools/artifacts.py`](tools.md#toolsartifactspy---content-addressed-artifact-store-with-immutable-write-attribution-and-a-per-path-write-history) | 602 | content-addressed artifact store with immutable write attribution and a per-path write history |
| [`tools/core/__init__.py`](tools.md#toolscore__init__py---public-surface-of-the-portable-core-tools) | 25 | public surface of the portable core tools |
| [`tools/core/definitions.py`](tools.md#toolscoredefinitionspy---typed-declarations-of-the-governed-core-tool-set) | 356 | typed declarations of the governed core tool set |
| [`tools/core/helpers.py`](tools.md#toolscorehelperspy---private-validation-http-and-isolated-regex-helpers-behind-the-core-tools) | 581 | private validation, HTTP and isolated-regex helpers behind the core tools |
| [`tools/core/regex_worker.py`](tools.md#toolscoreregex_workerpy---standalone-regex-worker-run-in-an-isolated-interpreter) | 38 | standalone regex worker run in an isolated interpreter |
| [`tools/core/services.py`](tools.md#toolscoreservicespy---portable-governed-tools-dispatcher-services-and-web-search) | 501 | portable governed tools: dispatcher, services and web search |
| [`tools/delegation.py`](tools.md#toolsdelegationpy---delegated-sub-runs-on-isolated-git-worktrees) | 128 | delegated sub-runs on isolated git worktrees |
| [`tools/elastic_requests.py`](tools.md#toolselastic_requestspy---agent-side-queue-and-tool-executor-for-elastic-spawn-requests) | 160 | agent-side queue and tool executor for elastic spawn requests |
| [`tools/policy.py`](tools.md#toolspolicypy---deny-by-default-capability-policy) | 201 | deny-by-default capability policy |
| [`tools/registry.py`](tools.md#toolsregistrypy---capability-bound-harness-tool-registry-and-its-baseagent-executor) | 464 | capability-bound harness tool registry and its BaseAgent executor |
| [`tools/result_cache.py`](tools.md#toolsresult_cachepy---opt-in-cache-of-successful-results-of-read-only-tools) | 160 | opt-in cache of successful results of read-only tools |
| [`tools/sandbox.py`](tools.md#toolssandboxpy---pluggable-execution-backends-for-registered-command-templates) | 322 | pluggable execution backends for registered command templates |
| [`tools/sandbox_models.py`](tools.md#toolssandbox_modelspy---typed-configuration-for-sandbox-backends) | 59 | typed configuration for sandbox backends |
| [`tools/supervisor.py`](tools.md#toolssupervisorpy---registered-command-execution-with-timeout-bounded-output-and-process-tree-kill) | 491 | registered-command execution with timeout, bounded output and process-tree kill |
| [`tools/task_models.py`](tools.md#toolstask_modelspy---records-for-background-tasks) | 43 | records for background tasks |
| [`tools/tasks.py`](tools.md#toolstaskspy---background-task-lifecycle-start-poll-stop) | 258 | background task lifecycle: start, poll, stop |
| [`tools/tools.py`](tools.md#toolstoolspy---the-toolexecutor-protocol-and-two-deterministic-test-executors) | 73 | the ToolExecutor protocol and two deterministic test executors |
| [`tools/worktree_models.py`](tools.md#toolsworktree_modelspy---record-for-an-agents-git-worktree) | 20 | record for an agent's git worktree |
| [`tools/worktrees.py`](tools.md#toolsworktreespy---git-worktree-isolation-for-concurrent-agents) | 187 | git worktree isolation for concurrent agents |

*Coverage: 166 files, 598 classes and 2128 functions (including methods and nested closures), each with a written description.*

## Files outside the package

| Path | What it is |
|---|---|
| `pyproject.toml` | Package metadata (`nailong-agent-sdk` 0.1.0, Python >= 3.12), hatchling build, console script `nailong-agent-sdk-dev`, optional extras `langgraph`, `langchain`, `jev`, `redis-cache`, `interop`, Ruff and pytest settings (strict markers; `docker` marks the tests that need a Docker daemon) |
| `examples/` | Eight runnable scripts using a scripted model: custom verification and profiling, developer tools, durable runtime with PASK, elastic exploration, framework interoperability, governed core tools, grounded retrieval, orchestration |
| `scripts/run_pckp_benchmark.py`, `benchmarks/` | Runs the frozen PCKP comparison cases in `pckp_cases.json` and writes `results/pckp-report.json` |
| `scripts/run_memory_benchmark.py`, `scripts/run_state_save_benchmark.py` | The long-horizon memory comparison (PCKP alone, extractive digests, a bounded graph, and a corrupting summarizer with and without a verifier; it writes `benchmarks/results/memory-benchmark-report.json`) and the run-state save profile (`run-state-save-report.json`). `scripts/`, `benchmarks/` and `examples/` are in `.gitignore`, so neither the scripts nor their reports are tracked |
| `tests/` | The pytest suite (`python -m pytest tests`, more than 1,000 tests): unit and integration tests per package folder, cross-module, concurrency and long-run checks in `tests/links/`, and a spawned MCP server and a stub evaluator in `tests/support/`. No test calls a real model provider. `python -m pytest -m "not docker"` skips the live Docker sandbox tests, which also skip themselves when no daemon answers; Windows-only and POSIX-only tests skip on the other platform |
| `.github/workflows/ci.yml` | Ruff, the suite on ubuntu and windows for Python 3.12 and 3.13, and the Docker sandbox tests against a real daemon |
| `example_runs/` | Outputs of real-workload validation scenarios run against a live OpenAI-compatible provider (a generated game and documents, a research-tool run, a tool capability survey, and the university and house-fence scenarios; one folder each). It is not a pytest suite |
| `apps/sg_job_agent/` | A Singapore job-search application built on the SDK (separate `pyproject.toml`) |
| `docs/reference/` | This reference |

The reference above is kept in step with the source by `tests/test_reference_docs.py`; the
behavioural claims in it are exercised by the rest of `tests/`.

## Issues found while documenting, and the later production-readiness pass

Each item in the first table was found by reading the code. **[reproduced]** means a script
reproduced it before any change; **[inspected]** means the code path was read but not run.
Everything in the first table was repaired in the same pass and its reproduction re-run; the
descriptions in this reference describe the code after those repairs. A later audit that ran
every module against its documented behaviour found 113 further problems; the second table
summarises how they were resolved, each with a test in `tests/`. A re-audit of the whole
package, started from the four known limits of the elastic-node design, closed those limits
and the further defects it found; the third table lists them, again each with a test in
`tests/`.

### Fixed during this audit

| # | Problem | Repair |
|---|---|---|
| 1 | **[reproduced]** The MCP server held two writers over one run root: after the controller recorded a node result, a `get_run_state` poll reverted the durable run; a locked human decision written through one project-state store vanished after the other store updated the project | The controller runtime now shares the server's one coordinator and one project-state store. `HarnessCoordinator` and `FileProjectStateStore` detect that another writer changed a file (inode, modification time, size) and refresh instead of overwriting; a conflicting save raises `RUN_STATE_CONFLICT`; `get_run_state` no longer writes |
| 2 | **[reproduced]** `controller-1` was reused by every new process, overwriting the persisted controller | New ids skip any controller already on disk (`ControllerStateStore.exists`) |
| 3 | **[reproduced]** `cancel_run` during `execute_run` was ignored and its flag reset to false | The cancelled flag is sticky per coordinator and `execute_run` stops before the next wave |
| 4 | **[reproduced]** `FileToolResultJournal` handle ids collided across instances and restarts | Handle files are claimed by exclusive creation and numbered after the highest id on disk |
| 5 | **[reproduced]** `PlanValidator` accepted a proof that cited the wrong signals | Proofs are compared per edge including their signal sets; messages name the edge, rule and signals |
| 6 | **[reproduced]** A model `tool-call` turn that listed dependencies crashed `BaseAgent.run` with a raw validation error | `ToolCallTurn` rejects it, so the run ends FAILED with `MODEL_TURN_INVALID` naming the field |
| 7 | **[reproduced]** A LangGraph SDK node reported a successful run as FAILED when the output had a key such as `message` or `token` | Result digests use `canonical_hash`, which does not apply the credential-key sanitiser |
| 8 | **[reproduced]** Jev: one candidate raised a raw validation error; an option labelled `approval` made `evaluate` raise; unavailability hid its cause | A clear two-candidate minimum, option labels are not sanitised, and reasons carry SDK-authored detail (missing install, response mismatch, timeout, field paths) |
| 9 | **[reproduced]** `nailong-agent-sdk-dev quality` and `inspect-run` exited 0 on failure | The exit status looks at `valid`, `passed`, `telemetry_chain_valid` and `audit_chain_valid` |
| 10 | **[reproduced]** An invalid host binding left an orchestration DISPATCHED with a started run | Bindings are built and validated before dispatch |
| 11 | **[reproduced]** Dependency chains longer than about 990 nodes raised `RecursionError` | One iterative cycle finder serves plan validation and the graph (checked identical to the old one on 6,000 random graphs; 50,000-node chains work) |
| 12 | **[reproduced]** `FailoverAgentModel` reported attempts cumulatively across calls | The message and payload list the failing call's attempts |
| 13 | **[inspected]** `verify_run_chain` and `AuditTranscriptStore.verify` returned only a boolean | `chain_break` names the first bad sequence and why (altered content, removed or reordered predecessor, unreadable line); exposed in the MCP tools, the run report, `inspect_run` and the audit transcript |
| 14 | **[reproduced]** An unreachable provider's error hid the cause | Messages name host, exception, root cause and a likely cause (DNS, refusal, TLS, dropped connection, timeout, redirect) |
| 15 | **[reproduced]** `CROSS_SESSION_STORE_REQUIRED` suggested an injected store would help | The message states that cross-session memory is not supported yet |
| 16 | **[inspected]** Install hints named `agent-design-agent-sdk[...]` | They name `nailong-agent-sdk[...]` |
| 17 | **[inspected]** `__all__` listed `DependencyProof` twice; two lint errors | Removed; fixed |

### Resolved by the production-readiness pass

| Area | Resolved |
|---|---|
| Foundations | Secret redaction runs in linear time and also covers PEM blocks; lone surrogates are scrubbed or rejected; JSON payloads are depth-bounded (64) so pydantic can always serialize them; caller-supplied ids are validated and mapped to injective file names (`identifiers.py`); text is split on newlines only; writers use unique temporary files, exclusive claims and a cross-process lock; the cycle finder and both PCKP solvers are iterative (any chain length), and the tree DP and branch and bound break ties identically |
| Observability | Audit transcripts: injective file names, chunked tail reads, a run-ownership guard and a named corrupt-tail error; telemetry summaries are derived from the hashed event JSON, not forgeable columns; report names are safe; both stores can be opened read-only |
| State | Ids are validated and `run-N`, `controller-N`, `orchestration-N` are reserved atomically across processes; a crash between a project-state event and state write is tolerated; COMPLETE artifacts come only from a controller or human `ARTIFACT_STATUS_UPDATED` transition; plan and reducer errors name the field; a declared dependency order is accepted when it implies the derived ordering and proofs are required only for declared edges; plan validation scales with shared signals; the default parallelism cap is 32; approvals persist per run and `resume_run` re-opens nodes whose approvals were granted; a controller cannot complete with unfinished nodes; run-history hashing is incremental |
| Agent | Transient provider errors are retried with backoff and `Retry-After`; watchdog expiry is typed (`WatchdogExpired`); invalid tool arguments and unavailable consumed episodes are recoverable; parallel tool calls are bounded; stream listeners are guarded; terminal metrics cover the whole run; the OpenAI-compatible adapter reports the provider's redacted error body, truncated and filtered completions and mid-stream errors; orchestrations end FAILED or BLOCKED when their nodes do |
| Tools | The supervisor detects exit by polling, drains output with a grace period and propagates cancellation; the Docker sandbox handles kill failures, newline environment values and cancellation; the native sandbox starts from a minimal environment; the task manager bounds retained records and requires a loop; worktrees are serialized per slug, reuse branches and require the repository root; delegated cleanup problems become warnings; credential patterns are case-insensitive; file tools honour the role's read scope and never read SDK-internal `.agent-*` state; `web_fetch` pins connections to validated public addresses (including IPv6-embedded IPv4) under one deadline; regex search runs in an isolated worker |
| Specifications | Document paths, BOMs, nesting, decompressed sizes and CSV fields are bounded and named in errors; retrieval survives a failing cache and batches Qdrant upserts with the server's error text |
| MCP | The client runs each connection in its own task with a connect timeout, cleans up on cancellation, builds valid and distinct tool names, marks external output untrusted and separates not-connected from call errors; the HTTP service requires a bearer token, validates Host and Origin for every loopback bind and runs tools on worker threads |
| Integrations | The sanitiser inspects string values as well as keys; envelope validation reports a `ValidationError`; the architecture router refuses `escalate`; the exploration advisor checks its bounds and ids; the LangChain tool facade validates arguments against the tool's schema |
| Developer tools | BOM-tolerant validation, readable operational errors with exit status 3, read-only inspection of every event, UTF-8 Ruff output and `verify-evidence` |
| Elastic nodes | `StateGraph.spawn_elastic_child`, which nothing could call, is replaced by continuation: an agent queues typed requests with `request_elastic_node`, the scheduler validates them when it commits the result, creates the exploration children and a join that resumes the requester, holds the requester's dependents behind the join and records every request; a child can depend only on what its requester can see and may only narrow its routing references; exhausting the caps defers the batch behind a blocked join until the controller grants capacity or declines (MCP `grant_elastic_capacity`, `decline_elastic_requests`); the orchestrator derives elastic bindings from the root's authority and bounds plans with policy ceilings; earlier discoveries reach late-spawned nodes; spawns are persisted with the run and reported as telemetry |
| Packaging | `py.typed` ships; the package root is lazy (importing it loads 58 modules in about 10 ms and neither `pydantic` nor `mcp`) |

### Resolved by the re-audit

| Area | Resolved |
|---|---|
| Scope | The gating that belonged to the interface was removed: Gate 1 and the designer soft-lock (`SpecificationGate`, `Gate1ArtifactStore`), the Git version locks and variant worktrees (`SpecificationVersionService`, `GitRepositoryAdapter`), the stage-completeness gate and the semantic-gap analyzer that only fed Gate 1, together with their six MCP tools. The design-stage selector and its `select_task_context` tool, the EDA taxonomy of document categories (a category is now a label the caller chooses), the legacy EDA manifest layout, the SystemRDL, SDC, UPF and WaveDrom file formats, the `research.*` metric definitions and the `requires_manifest` rule for action episodes were removed as well. The hardcoded specialists (`rtl_worker_definition`, `planning_agent_definition`, `placeholder_specialist_definition`), the `validate-rtl-task-result` gate and the Verilator, Yosys, OpenROAD and OpenSTA tool declarations were removed too, so the server then registered 46 tools (the multi-store repair below added two) and the default registry declares 19. `run_agent_task` now runs a real model with everything taken from the request (endpoint and key, permissions, built-in tools, workspace, command templates, MCP servers), where it used to replay scripted turns only |
| Elastic nodes | The four known limits are closed. (1) A grant is bounded by per-run ceilings, not only by the hard limits: `OrchestrationPolicy.max_elastic_depth` and `max_elastic_nodes` become the controller's `elastic_depth_ceiling` and `elastic_nodes_ceiling` (default: the hard limits), a plan cannot declare caps above them, a grant cannot pass them, and a batch that would pass a ceiling is refused (`ELASTIC_NODE_CEILING_REACHED`, `ELASTIC_DEPTH_CEILING_REACHED`) instead of deferred. (2) Tasks that run at the same time see each other's queued requests: the scheduler keeps a per-wave reservation ledger that agents reach through `ElasticReservation`, first come, first served, released when a node does not complete. (3) After a crash an interrupted node is replayed when the caller names it or `is_replayable` accepts it (`GraphAgentExecutor.is_replayable` builds an elastic node's binding from the factory), and `Orchestrator.recover_execution` resumes a DISPATCHED orchestration. (4) Joins count against `max_elastic_nodes`, so a batch of N requests costs N + 1 and a run never holds more elastic agents than the cap; the default cap is therefore 3 (it was 2). A snapshot written by an earlier build whose elastic nodes now exceed the cap fails to load with a message naming both numbers |
| Foundations | A schema's `$ref` can no longer make validation fetch a URL or read a file: references resolve only inside the schema and the JSON Schema metaschemas, otherwise `SCHEMA_REFERENCE_UNRESOLVABLE`; redaction also covers tuples, sets and bytes, `Authorization: Basic`, `Digest` and `Token` credentials, URL passwords, current token formats (GitHub fine-grained, GitLab, Hugging Face, Google, npm, Fireworks, `sk_live_`/`sk_test_`, AWS session ids, JSON web tokens), `passwd`, `passphrase`, access and signing keys and quoted values that contain spaces, and `redact_hidden_reasoning` and `assert_no_hidden_reasoning` look inside sets and tuples; the package name and version come from one module (`foundations/version.py`); `exclusive_file_lock` honours its timeout on POSIX as it already did on Windows (it blocked for ever there); every validator that rejects a repeated id now names what repeats (`require_unique`: `plan task IDs must be unique; repeated: "T1"`), and a self-dependency, identical endpoints or an unknown relation endpoint name the id involved |
| Observability | Several processes opening one run root at once no longer fail with `database is locked` (the WAL switch and the schema creation retry for up to ten seconds); the run report and the Markdown transcript count only the events or entries before the first integrity break as verified, and the verified-through sequence stops there |
| State | A question and a blocker can be closed (`QUESTION_RESOLVED`, `BLOCKER_CLEARED`; `ControllerRuntime.resolve_question` and `clear_blocker`); every stage failure records the repair-attempt and escalation metrics, whether it comes from the explicit request, a failed graph, or a rejected provenance check; the per-node executor wrapper exists once (`StateGraph.execute_node`); the `ProjectStateRepository` protocol now declares the `summary_max_chars` argument that `BaseAgent` passes, so a store written to the protocol no longer fails on its first tool outcome; cancelling a completed or cancelled controller is refused before its graph run is touched (`ControllerStateMachine.require_cancellable`), where it used to mark the finished run cancelled and then raise; publishing a discovery, writing a shared value, recording a node result or adding a lateral dependency is refused once the controller is completed or cancelled (they used to change the finished run, and a node result its project state); a controller transition or a run write whose save raises no longer leaves the cached controller or graph ahead of the disk (`ControllerRuntime` restores the machine, `HarnessCoordinator._save` drops the cached graph), so retrying the call works instead of failing on a state it never stored |
| Agent | The OpenAI-compatible transports treat a redirect on a streaming call as a failure instead of an empty success, classify any socket error as transient and report a body that cannot be decoded as `OPENAI_COMPATIBLE_RESPONSE_INVALID`; `FailoverAgentModel` can give each fallback adapter its own model binding (`bindings`), where an adapter that validates the binding rejected every fallback model as a mismatch |
| Tools | `stop_task` no longer swallows the caller's own cancellation; the Docker env file that holds secrets is checked before it is created and removed when writing fails; a PDF page longer than `max_chars` is reported as cut (`text_truncated`, `omitted_chars`) instead of looking complete; a file that Windows reports in its extended-length spelling (`\\?\C:\...`, which `Path.resolve()` returns when the check that would drop the marker fails while another process replaces the file) is no longer refused as escaping the root: the artifact store, the core tools, the capability scopes, the specification and image loaders and a task's workspace compare paths with `relative_to_base` |
| MCP | The HTTP service refuses a websocket without the token (it only checked HTTP requests); the server reports the installed package's name and version and the web clients identify as `nailong-agent-sdk/<version>`; a refused request's body is read before the 401 is sent, so a client that closes the connection after each request (Python's `urllib`) no longer loses the 401 to a TCP reset; `resolve_project_question` and `clear_project_blocker` close questions and blockers |
| Integrations | `LangChainSdkRunnable` returns the run's outcome only; the prompt context, episodes, events and profile need `include_trace=True`; the Jev gate, router and advisor are typed against the new `JevDecisionProvider` protocol (they were annotated with the generic `ExternalDecisionProvider`, whose request and result are not the Jev types they pass, so a type checker rejected `TypeSafeJevDecisionEvaluator`) |
| Dead code | `RunSharedState` and `SharedStateStore` (superseded by `GraphSharedState`, which `StateGraph` keeps in the run record and the controller tools publish to), `ControllerPhase.INTAKE` (the state machine starts in `planning`) and the `ExternalDecisionProvider`, `ExternalDecisionRequest` and `ExternalDecisionResult` contracts (the Jev classes use `JevDecisionProvider`) were removed; nothing in the package, the tests or the TypeScript backend referenced them, and the package root then had 344 names |
| Duplication | `foundations/hashing.py` defines the canonical JSON encodings (`canonical_json`, `strict_canonical_json`, `model_canonical_json`), `sha256_hex`, `canonical_hash` and `estimate_tokens` once. The seven `_canonical_json` copies and their differently named SHA-256 wrappers, the four token estimates, the two verification-result normalisers (`normalize_verification_return` serves the gate registry and the Jev advisory gate) and the second copy of the artifact read and grep tools in the registry (it now checks the plan task's authorization and calls the core implementation) are gone. The encodings differ for values JSON cannot hold, so every call site keeps the one it used; the audit, telemetry, journal, project-state, run-record, provenance, episode-checkpoint, evidence-graph, retrieval and integration digests were compared byte for byte before and after, and `tests/foundations/test_hashing.py` freezes the encodings. The only change is that the episode store counts compact JSON, which lowers its token estimates by about a tenth |
| Platform | The first run on Linux (Ubuntu 24.04 under WSL2, Python 3.12) passed apart from three failures: two metadata tests because the package was not installed in that environment (CI installs it) and a glob pattern such as `C:\Windows\*` that Windows refused and POSIX accepted (`_leaves_run_root` now refuses rooted, drive-qualified and `..` patterns everywhere, naming the pattern). Tests against the real kernel then found that a child killed for exceeding its CPU limit was reported as an ordinary non-zero exit: the supervisor set the soft and hard limits equal, so the kernel answered with `SIGKILL`. The CPU limit is now soft at the requested seconds and hard one second later, so the child receives `SIGXCPU` and the record says `resource-limit`. New tests cover rlimit enforcement, a lock held by a process that is killed, and the Docker sandbox against a real daemon (skipped without one); `.github/workflows/ci.yml` runs Ruff and the suite on ubuntu and windows for Python 3.12 and 3.13 |
| Concurrency | The stores for runs, controllers, orchestrations and project states were single-writer: two processes changing one record at the same instant could lose an update, and two store instances saving one run could truncate each other's history sidecar. Each record now has a cross-process lock (`foundations/record_locks.py`; files under `<store>/.locks/`, released by the operating system if the holder dies, re-entrant for the holding thread; timeout codes `RUN_LOCK_TIMEOUT`, `CONTROLLER_LOCK_TIMEOUT`, `ORCHESTRATION_LOCK_TIMEOUT`, `PROJECT_STATE_LOCK_TIMEOUT`). `HarnessCoordinator`, `ControllerRuntime` and `Orchestrator` hold it across each read-modify-write, `FileProjectStateStore.ensure` and `apply` take it themselves, and a store refuses to save over a file that changed since it last read or wrote it (`RUN_STATE_CONFLICT`, `CONTROLLER_STATE_CONFLICT`, `ORCHESTRATION_STATE_CONFLICT`), checking only after the lock is held. The long paths (`execute_run`, `execute_graph`, `dispatch_and_execute`) hold no lock while the executors run: a change another process makes meanwhile surfaces as a conflict from `execute_run`, and a cancel made while the graph runs is no longer overwritten by the execution outcome. A `ControllerRuntime` reloads a controller another process changed. Four tests start several processes against one record and fail, with lost updates and a corrupt run history, when the locks are removed (`tests/state/test_concurrent_writers.py`) |
| Run cost | `HarnessCoordinator.execute_run` saves once per wave, persisting the results of a wave together with the start of the next, where it saved once per wave and once per node (the saves of a dependency chain halve and so do the bytes written), and `StateGraph.snapshot` dumps each node, edge, result, event, spawn record and grant once and reuses the dump while the graph holds the same object (a fixture in `tests/state/conftest.py` compares every snapshot of the state tests with a fresh dump). On the development machine a 100-node dependency chain went from 1.4 s to 0.6 s, 200 nodes from 4.3 s to 1.5 s, 400 from 10.4 s to 3.8 s and 600 from 17 s to 7 s. `TelemetryStore.create_run_report` reads the events once, one at a time, through the chain check and the counters instead of holding the run in memory, reports an event that cannot be read as the integrity failure at its own sequence (it used to raise, and `chain_break` named the first sequence of the page), and has the schema `run-report-v2`: `evidence_head_hash`, the hash of the last verified event, replaces the list of every event hash, which `include_event_hashes=True` brings back (MCP `create_telemetry_report` takes the same flag) |
| Multi-store operations | A call that writes several stores in turn (a node result into the run and then into project state; a cancel into the run, then the controller, then the orchestration record; a submit, an approval or a dispatch into the controller and then the orchestration record) could fail between two writes and leave the stores disagreeing, so that retrying the call failed on the state the first attempt had already changed. The authority runs downward (orchestration, controller, graph run, project state) and `reconcile` derives each later store from the earlier one: `ControllerRuntime.reconcile` writes a work item for every terminal result of the run and cancels a controller whose run is cancelled; `Orchestrator.reconcile` then takes its status from the controller (cancelled, plan held for approval, plan approved or rejected, run dispatched) and from a finished run. The mutating operations of the orchestrator align the record first, so the retry of a failed approve, cancel, submit or dispatch completes, and `submit_for_approval` records the new controller's id before it submits the plan. `ControllerRuntime.cancel` now also writes the nodes it cancelled to project state, as `execute_graph` always did, so that the run's results and the work items agree after every operation. `tests/state/test_multi_store_atomicity.py` fails each store write of ten operations in turn, in the same process and after a restart, and requires the same final state as the fault-free run after `reconcile` and a retry and, for every operation but recording a node result (whose retry is refused because the node is already terminal), after the retry alone. What remains: a failure between creating a controller and recording its id leaves an unreferenced controller in PLANNING (the retry creates another), a run started for a dispatch that then failed stays unreferenced, and two processes executing the same run still end in `RUN_STATE_CONFLICT` for one of them |
| Elastic hand-off | A join used to know only its task text and the exploration results. A request can now carry a `handoff` (up to 4,000 characters; 8,000 across one task's requests, refused with `ELASTIC_HANDOFF_LIMIT_REACHED` and the numbers) that `render_elastic_instructions` prints in the join's task word for word, so the requester's own notes survive the continuation |
| MCP client and service | The client bounds a call (`call_timeout_seconds` on the config or `timeout_seconds` on the call): an unanswered call raises `McpCallTimeoutError` naming the server, the call and the seconds, and the session stays usable. It can ping a server (`ping_interval_seconds`, `ping_timeout_seconds`): one that stops answering is marked FAILED with its command or endpoint named and, with `reconnect_on_ping_failure`, reconnected. The service serves HTTPS when `AGENT_RUNTIME_TLS_CERTFILE` and `AGENT_RUNTIME_TLS_KEYFILE` are set (the pair is loaded at startup, and a bad pair is refused naming the variable or file) and refuses a non-loopback bind without TLS unless `AGENT_RUNTIME_ALLOW_PLAIN_HTTP=1` says a proxy terminates it; such a bind used to start in plain HTTP. `McpServerOptions` is the new base of the two config classes |
| Audit handles and retention | `AgentRuntimeServices.open(audit_max_open_handles=)` and `AGENT_RUNTIME_AUDIT_MAX_OPEN_HANDLES` set the audit store's append-handle budget; evictions are counted (`handle_evictions`) and one warning names the budget when the count reaches it. Retention is opt-in: `RunRetention.prune(policy)` (MCP `prune_runs`, `nailong-agent-sdk-dev prune-runs`) deletes finished runs that have been idle for `older_than_seconds` - their telemetry events and metrics, audit transcript and the tool-result files recorded under the run id (the journal now indexes handles by run, and never reuses a handle number) - whole runs only, after writing a hash-chained tombstone with the final chain heads and every handle's hash. It reports by default, deletes nothing without a policy, leaves a run whose chain is broken as evidence, finishes an interrupted prune without a second tombstone, and `verify-evidence` accepts a pruned handle that a tombstone vouches for. Episode records are single files that are replaced, so they do not grow |
| `run_agent_task` | The files an agent wrote can be listed and read (`list_task_files`, `read_task_file`: bounded pages as UTF-8 or base64; paths outside the workspace, `.agent-*` state and credential patterns are refused); an approval can be decided during the run (`permissions.approval_mode` `wait`, `list_task_approvals`, `decide_task_approval`; the request and the decision are telemetry events); and `cancel_agent_task` interrupts the model call, tool call, hook, gate or approval wait in flight, because the cancellation token is raced against every awaited operation and the model request and the network tools run on detached daemon threads that the run does not wait for. The server registered 53 tools at that point; `list_run_approvals` and `inventory_retention` were added later, so it registers 55 now |
| Packaging | `jsonschema-specifications` and `referencing` are declared dependencies; `# noqa` directives for rules that are not enabled were removed |
| Trace context | A run has a W3C trace id and every profile span a random 16-hex span id (`observability/trace_context.py`); `TelemetryContext` refuses a `trace_id`, `span_id` or `parent_span_id` that is not W3C-shaped, naming the field (events already stored with another shape stay readable); `BaseAgent` stamps the trace id and the run's root span id on every event of a run and continues a caller's trace (`telemetry_context`, or `TaskRunOptions.traceparent`); the `agent.profile-completed` event now persists the trace id, the process resource and the most recent 512 spans, so a trace can be rebuilt from the ledger alone; model-turn and tool spans carry `gen_ai.*` attributes (operation, provider, model, usage, tool name and call id) and receive their own `traceparent` (`ModelContext.trace_parent`, `ToolInvocationContext.trace_parent`); an endpoint sends it to the provider only with `propagate_trace_context`; metric definitions declare an OpenTelemetry-style instrument kind. The agents, controller and orchestration of one graph run share the trace derived from the run id (`graph_run_trace_id`), and the agent roots hang under the run's `invoke_workflow` span; `observability/otlp.py` and `nailong-agent-sdk-dev export-otlp` send spans, log records and metric points to an OTLP/HTTP collector from the ledger; MCP servers receive `traceparent` in the request's `_meta` only with `propagate_trace_context`, and `run_agent_task` continues the request's `_meta.traceparent`. A tool-call id or name longer than 4,096 characters no longer fails the run when its span is opened (attribute text is cut to 256 characters). Cost: about 3 microseconds more per context that carries trace ids and about 3 ms once per 80-span run, against about 1.7 ms for each emitted event |
| Episode memory | `InMemoryEpisodeGraph` kept every tool payload in a second list for the life of the run although nothing read it, so the payload of a compacted episode was never freed; it no longer stores payloads (the `payload` arguments are accepted and ignored). Compacted episodes can now be archived, summarized and verified before their payloads are released (`SemanticCompactor`, `FileEpisodeArchive`, `ExtractiveEpisodeSummarizer`), consolidated into a bounded hierarchy, evicted from the active graph and restored on demand; the projector shows verified summaries within their own budget and `ModelContext.semantic_summaries` carries them. A compaction the run abandons (cancellation, deadline) commits nothing afterwards (`CompactionGuard`). It is all opt-in: a store without a compactor behaves exactly as before. |

### Resolved by the open-items remediation

Each row starts from a written list of open items. None was treated as fixed until it was
reproduced or its code path read; each names the original failure, the change and the tests
that fail without it. A suggested change that measurement did not justify is listed after the
table, not built.

| Area | Resolved |
|---|---|
| Semantic memory after a restart | `FileEpisodeStore.persist` wrote `episodes.json`, `summaries.json` and `evicted.json` with no loader, so a restart lost the summaries, the evicted index and every attempt counter, and files written at different moments could not be told apart. It now writes one sealed snapshot per generation, `.agent-memory/snapshots/snapshot-<generation>.json` (live records, evicted index, resident summaries, the index of every summary, quarantine, counters and the structural graph hash; temporary file, fsync, atomic rename, directory fsync), and `FileEpisodeStore.restore()` adopts the newest generation that passes `snapshot_problems` (schema, integrity hash, duplicate or dangling ids, summary and archive hashes, archive entries present and equal), reporting each rejected generation with its reasons. Nothing is restored into a store that already holds episodes, `retain_generations` (3) bounds the disk, and a crash while writing leaves the previous generation whole. `tests/memory/test_memory_persistence.py` |
| Bounded summary lifecycle | Summaries, superseded ones too, stayed in memory for the life of the store, so a long run's summary set had no ceiling. A `SummaryLedger` now tracks each summary as `active`, `superseded` or `archived`: a parent supersedes its children and a superseded summary is written to the summary archive (`summary-archive-<sha256>`, read back and compared before its resident copy is dropped); `max_active_summaries` and `max_active_summary_tokens` archive the oldest unpinned top-level summaries when consolidation is not enough, and a summary that covers an episode that is live again is pinned. An archived summary stays retrievable (`load_summary`, `summary_for(..., resolve_archived=True)`) and its child ids, source hashes and archive ids still lead to the original episodes. `BaseAgent(episode_history_limit=)` (`InMemoryEpisodeGraph(retained_limit=)`) bounds the per-run one-line list while keeping every episode's kind, so a later action can still name an episode whose line was dropped; `AgentResult.episodes` stays the complete history by default and, with a limit, is the newest N with `episodes_total` and `episodes_omitted` saying what was dropped. `tests/memory/test_memory_persistence.py`, `tests/agent/test_episode_history.py` |
| Semantic verification | A passing deterministic check was recorded as verification although a claim that contradicts its own quoted evidence passes it. A summary now carries a `SummaryVerificationRecord`: every stage that ran, with its kind (`deterministic-extractive`, `heuristic-entailment-screen`, `claim-judge`, `summary-judge`), verifier id and version and the policy hash, and `extractive_verified`, `entailment_checked` and `host_judged` keep the three claims apart. New optional stages: the lexical `HeuristicEntailmentScreen` (`entailment_screen`); a host `ClaimJudge` that receives each claim with the excerpts it cites (`require_claim_verdicts` turns a missing or unclear verdict into a failure); the existing `SummaryJudge`. `actions-covered` (on by default) requires every action episode to be cited, a rejected summary can be kept for audit (`on_semantic_failure="quarantine"`, at most `max_quarantined`) and a `fallback` summarizer is tried when the first rejects or fails. A judge can only veto: it runs after the deterministic checks and never rescues a summary that failed one. Measured on the labeled adversarial set (`tests/support/adversarial_summaries.py`: 24 scenarios, 264 wrong and 72 faithful summaries in 11 and 3 categories) the deterministic checks alone accept 45.5% of the wrong summaries (every status flip, negation, fabricated fact, reworded flip and quantifier flip) and reject none of the faithful ones; adding the lexical screen gives 9.1% and 33.3% (it still accepts every reworded flip and rejects every far paraphrase); a judge that knows the label gives 0% and 0% by construction, and one that errs on 10% of its calls gives 0.8% and 20.8%. The last two rows describe the pipeline, not a model: the SDK ships no model judge. `tests/memory/test_semantic_verification.py` |
| Summarization off the projection path | With a slow or unavailable provider the projection step waited for the summarizer. `SemanticCompactionPolicy.background_summarization` hands each group to a `SummarizationScheduler` (daemon workers, a queue bounded by `max_queued_summarizations`, a per-job timeout; a job past it is abandoned and counted, and once the abandoned ones still running reach a limit the scheduler refuses work). The projection returns at once and the payloads stay (`pending_episode_ids`); a later pass commits a finished job only when its source episodes are still compacted, unchanged and unsummarized (otherwise `jobs_stale`), so a late result changes nothing. `settle_background` and `close_background` wait for or drop the work, `BaseAgent` closes it when a run ends, and the abandon guard still stops every commit. Measured over eight groups with a summarizer that takes 0.1, 0.5 or 1.0 s per group: `compact` returns in 2 ms, 3 ms or 1 ms in background mode and takes 0.81 s, 4.0 s or 8.0 s in synchronous mode; one worker commits all eight jobs in the same total time and four workers in 0.21 s, 1.0 s or 2.0 s. `tests/memory/test_background_summarization.py` |
| Pruning against concurrent writers | A run that received an event, a metric, an audit entry or a tool result between the last check and the delete lost it silently; only a telemetry append that landed before the final sequence check was caught. Retention now raises a per-run fence (`.agent-retention/fences/<run>.fence`) before it examines a run. Every writer waits for it to lift (10 s) and checks it again inside its own critical section, so a record is accepted wholly before the fence or refused with `RUN_PRUNING`; retention then waits for writes already inside their transaction (`quiesce`), measures, writes the tombstone, deletes and releases the fence. An interrupted prune keeps the fence, so writers stay out until the next applied pass finishes it, and a fence left before any tombstone is released by the next applied pass. The `mcp-decisions` stream is protected from retention. `tests/agent/test_retention_fence.py` (a writer arriving mid-prune is refused and not deleted; writers that wait out a prune continue as a new incarnation and lose nothing; a write inside its transaction is kept; a writer that passed its pre-check just before the fence is still refused; interrupted prunes) |
| Retention evidence, inventory and vacuum | A tombstone log had no outside anchor, so removing its newest entries went unnoticed. `export_tombstone_anchor` and `verify_tombstone_anchor` write and check an anchor kept outside the run root (verdicts `matches`, `advanced`, `truncated`, `diverged`, `chain-broken`, `unreadable`); `RunRetention(anchor_path=)`, `prune-runs --anchor` and `AGENT_RUNTIME_RETENTION_ANCHOR_PATH` check it before a pass, refuse to continue over a log that no longer matches and refresh it after. `inventory_run_root` (developer command `inventory`, MCP `inventory_retention`) reports every resource class under a run root with its owner, what references it and a disposition (`prunable`, `reclaimable`, `protected` with the rule), writing nothing. `TelemetryStore.maintenance_report` shows the reclaimable bytes of the telemetry file and `vacuum` (`vacuum-telemetry --apply`) returns them, refusing while a run is fenced or without free space for the rewrite and reporting `PRAGMA integrity_check`. `tests/agent/test_retention_anchor.py`, `tests/agent/test_retention_inventory.py`, `tests/observability/test_telemetry_maintenance.py`, `tests/mcp/test_retention_tools.py` |
| MCP approval authority | The HTTP service's single token let whoever held it approve what it had started. There are now two credentials: `AGENT_RUNTIME_AUTH_TOKEN` (host: starts and steers work and reads) and `AGENT_RUNTIME_APPROVER_TOKEN` (approver: reads state and decides approvals). Every tool has an operation class (read, host or approval; an unclassified tool is host), the credential decides the class and nothing the caller writes does, and without an approver token the approval tools are disabled unless `AGENT_RUNTIME_ALLOW_HOST_APPROVALS=1` opts back into one credential. A decision must carry the digest of what the approver reviewed (`request_hash`, `plan_hash`, `state_hash` or `run_hash`, read from the matching read tool) and `issued_at_utc` (at most `AGENT_RUNTIME_APPROVAL_MAX_AGE_SECONDS`, 300 by default, old). A replayed, expired, stale, unbound or unauthorized decision is refused before the operation runs, and every decision writes hash-chained `mcp.decision.authorized` (before the operation) and `applied` or `failed` (after) events naming the credential by a hash prefix in the protected `mcp-decisions` stream. The TypeScript backend (`runtimeCredentials.ts`, `PythonAgentRuntimeClient.ts`) reads the two secrets apart, refuses equal values and sends the binding with each decision. `tests/mcp/test_authority_*.py` (36 tests in four files) and the updated server and client tests |
| Python-only capabilities over MCP | A caller that sent `verification_gates`, `pre_tool_hooks` or `cancellation` to a tool over MCP saw a successful call that ignored them, because the MCP library drops arguments a tool does not declare. `ToolArgumentGuard` (server middleware) now refuses a call that names an argument the tool does not define, names it and, for a Python-only capability of `AgentTaskRunner.run`, says why MCP cannot carry it; `run_agent_task`'s `runtime_options` refuse the same names, and a test pins the runner's parameters against `PYTHON_ONLY_CAPABILITIES`. `tests/mcp/test_capability_boundaries.py` |
| Model-request cancellation | A cancelled run said nothing about the provider's request. `model-turn-cancelled` now records what happened at three levels: logical (always: the task is cancelled and a late response commits nothing), transport (`connection-closed` through the async httpx transport or a closed stream, `detached` for a blocking request that keeps running on its daemon thread, `not-started`, `unreported`) and provider (`confirmed` only when a host `provider_cancel` hook said so, `requested` when it was asked and did not confirm, `unsupported` for Chat Completions, which has no operation that stops a request, so the provider may finish and bill it). `AgentTaskRunner` uses `HttpxAsyncJsonTransport` by default, so a cancelled non-streaming request closes its connection instead of being abandoned on a thread. `tests/agent/test_model_cancellation.py` (13 tests) |
| Trace export | A span whose parent was not in the export pointed at a span the collector never received without saying so, and the `invoke_workflow` span ended at the last controller bookkeeping event. Spans whose parent is outside the export now keep their `parentSpanId` and carry `nailong.parent_span_exported=false` (counted as `parents_not_exported`); the graph-run span runs from the first controller event to the last `graph.executed` event and states its `nailong.window.basis` and pass counts; dependency links carry `nailong.link.basis=declared-graph-edge`. `tests/observability/test_otlp_coverage.py` |
| Elastic recovery (verified, no defect found) | The crash-recovery path was exercised end to end instead of assumed: only bindings declared idempotent are replayed; a replayed node starts its task over with no conversation; the join receives the findings and the handoff after a crash and no finished child runs again; a join that is not declared replay-safe fails and keeps the findings; a failed non-idempotent node enters the bounded repair path; the host can replay one interrupted node explicitly; recovering twice repeats nothing; a second process that lost the declaration fails the node instead of guessing. `tests/agent/test_orchestrator_elastic_recovery.py` (9 tests) |

Examined and left alone:

| Item | Decision |
|---|---|
| Delta persistence of run state | Not built. `scripts/run_state_save_benchmark.py` (a chain of no-op nodes, one save per node, development machine) gives 1.8 s for 200 nodes (8.9 ms per save, 29 MB written), 8.5 s for 600 (14.1 ms, 260 MB) and 21.4 s for 1,000 (21.4 ms, 722 MB); at 1,000 nodes fsync is about 35% of the time, the snapshot file 26%, the run hash 24% and the graph snapshot 6%. A delta format would remove most of the snapshot bytes but neither the fsyncs nor the run hash, and needs a loader for existing snapshots; a save stays in tens of milliseconds at these sizes. |
| Multiple-choice PCKP | Not implemented, by instruction; the evaluation is under "Still open" item 8. |
| Deleting `reclaimable` resources | No deletion path. Workspaces hold an agent's work product, archives are content-addressed evidence and the rest are records other records name; the inventory says what the reference rules would allow and nothing removes it. |
| A provider-side cancel for Chat Completions | The protocol has none. The `provider_cancel` hook exists for a host whose provider has one; the SDK reports `unsupported` otherwise. |

### Resolved by the decision-recording pass

The work started from a review of suggested traceability, verifiability, authority and
debuggability changes. Each row below closes a gap in what the SDK already recorded; none adds
a second engine. Each is backward compatible: a caller that passes nothing gets the records it
got before.

| Area | Resolved |
|---|---|
| Who verified an output | `verification-completed` recorded passed, reason and failure code under a fixed deterministic authority, and `VerificationDecision` was only `(passed, reason)`. A decision may now declare a `verifier_id`, an authority class (`TelemetryAuthority`) and a bounded `evidence` mapping (JSON, at most 4,096 characters, kept as a copy); the event records the gate id, a digest of the candidate output and what the decision declared, and takes the decision's class as its telemetry authority. `AllOfVerificationGate` and `VerificationGateRegistry.register_all_of` compose one to 16 gates (fail-fast unless told otherwise) and record each member's verifier, outcome and class. The Jev advisory gate declares itself model-mediated with its probability and policy. A gate that declares nothing is recorded as before. Tests: `tests/agent/test_verification_decisions.py`, `tests/integrations/test_jev_advisory.py`. |
| Who decided an approval or a plan | A decision made through the Python API recorded a status and a reason and nothing about who decided it; only the MCP path recorded a principal, in its own stream. `ApprovalRequest` now has `decided_by` and `decision_authority`, and `DecisionSource` is accepted by `AgentTaskRunner.decide_approval`, `HarnessCoordinator.submit_approval`, `ControllerRuntime.approve_plan`, `grant_elastic_capacity` and `decline_elastic_requests`, and `Orchestrator.approve`. The controller event, the orchestration event and the telemetry event (whose authority class becomes the decider's) carry the same fields; a timeout is attributed to `approval-timeout` (system) and an advance approval to `run-options` (deterministic). The MCP tools pass the principal with the human class, and a project-state decision writes it into its transition so `last_action` names it. Without a source the record names no decider. Tests: `tests/tools/test_approval_decision_sources.py`, `tests/state/test_plan_decision_sources.py`, `tests/agent/test_task_approvals.py`, `tests/mcp/test_authority_decision_records.py`. |
| One authority matrix | Which actor may produce which project-state transition was a set of `if` statements inside the reducer, and the credential rules were spread over `McpAuthorityPolicy` and each tool's registration. The reducer now reads two tables, `TRANSITION_AUTHORITY` and `QUESTION_RESOLUTION_AUTHORITY` (the refusal wording is unchanged), and `authority_matrix()` and `nailong-agent-sdk-dev authority-matrix` export them with what each MCP credential may do under each approval policy (probed from `McpAuthorityPolicy.check`) and the operation class of every registered tool. `TOOL_GROUPS` in `mcp/tool_groups.py` lists the tool groups once, for the server and the matrix. The matrix says plainly which kinds accept every actor (`tool-outcome` and `agent-result` did until the checklist-validation pass restricted them to the harness; `question-opened` still does). Tests: `tests/mcp/test_authority_matrix.py` probes the reducer with every actor and compares the tools with a real server's. |
| Signed run heads | The telemetry and audit chains detected an edit in the middle but not the removal of the newest entries, nor a rewrite that resealed the chain; only the tombstone log had an outside anchor. `export_run_head_checkpoint` and `nailong-agent-sdk-dev checkpoint-run` verify both chains, sign their sequence and head hash with a `CheckpointSigner` (`HmacSha256Signer` from the standard library; an asymmetric signer is the host's) and append the record to a file outside the run root, refusing a destination inside it and a run that no longer matches the file. `verify_run_head_checkpoints` reports `matches`, `advanced`, `truncated`, `diverged`, `chain-broken`, `signature-invalid`, `no-checkpoint` or `unreadable`. A run's terminal telemetry event records the audit head and its terminal audit entry the event's hash (`verify_terminal_link`). Tests: `tests/observability/test_run_checkpoints.py`. |
| Reading one kind of event | A reader that wanted `controller.plan-approved` paged through every event of the run. `TelemetryStore.list_events`, `iter_events` and the MCP `get_telemetry_events` take `event_types` (at most 64 names; a bare string, an empty list and a blank name are refused naming what to pass). The filter reads the stored event JSON, so a forged `event_type` column cannot hide an event. Tests: `tests/observability/test_telemetry_event_filter.py`, `tests/mcp/test_server_tools.py`. |

Examined and left alone:

| Item | Decision |
|---|---|
| A routing `AuthorityPolicy` engine | Not built. It would move gating into the SDK, against the scope stated at the top; the matrix reports what the code enforces, and the caller's own policy decides who may approve what. |
| Merkle batching on the write path, anchoring on a public ledger | Not built. Detecting truncation or a rewrite needs only the head hash and count outside the run root, which a checkpoint gives; a Merkle tree adds inclusion proofs, and changing the write path would invalidate every stored chain. A host that wants a ledger implements `CheckpointSigner` and ships the checkpoint file. |
| Domain-specific verification oracles in the SDK | Not built. `VerificationGate` is the extension point; a formal-property or consensus check is the host's code. |
| Counterfactual replay as a memory feature | Not built. It needs a resume-from-iteration API, recorded model turns (the audit log keeps them only to 8,192 characters) and a live model; episodic memory exists for context compaction. |

### Resolved by the checklist-validation pass

The work started from a checklist of suggested next steps (traceability, authority,
verifiability, debuggability, scenario tests, documentation). Each item was checked against
the code first, by experiment where a claim could be tested: about a third was already done,
a third rested on a wrong premise or a wrong place, and the rest was built, some of it
reshaped. Each row names the failure, the change and the tests that fail without it.

| Area | Resolved |
|---|---|
| A pruned run read as truncated | A run was checkpointed (`matches`, 8 events, 10 entries), pruned by retention and verified again: `truncated`, although the tombstone holds the same final heads. Checkpointing every run would have raised that false alarm for every pruned run. `pruned_heads_lookup(run_root)` (`agent/retention.py`) turns the tombstone log into `PrunedHeads`, and `verify_run_head_checkpoints(..., pruned_lookup=)`, `check_run_heads` and the developer commands use it. A run that is gone is `pruned` (ok) when a tombstone no older than the newest checkpoint holds that checkpoint's heads, or is longer and says the events between are gone; it is `truncated` when the tombstone is shorter than the checkpoint, `diverged` when it is as long with another head, and `truncated` with the unreadable log named when no tombstone can be read. A run id used again after a prune: the checkpoints a tombstone accounts for belong to the earlier incarnation, the counts only have to grow within one incarnation, the verdict is `no-checkpoint` until the new run is checkpointed and an export starts a new series. `tests/observability/test_run_checkpoint_pruning.py` (11 tests) |
| Chain verification ran twice | A re-export checked both chains to verify the existing file and again to build the record, and a verification scanned the audit chain three times. One inspection now serves both steps: each chain gets its chain check and one pass that yields its head and the hash at the newest checkpoint's sequence. On a run of 3,000 events and 3,000 entries (development machine, both versions in one process) a re-export took 1,047 ms before and 503 ms after; a first export is unchanged at about 505 ms, because the two chain checks (228 ms telemetry, 134 ms audit) dominate. `test_re_exporting_scans_each_chain_twice_not_five_times` |
| Checkpoints were taken only by a person | `export_run_head_checkpoint` was called by one developer command and nothing else, so a run nobody checkpointed had no record outside the run root. `RunHeadCheckpointer(signer, sink)` (`agent/run_checkpointer.py`) is now accepted by `AgentRuntimeServices.open(checkpointer=)`, which refuses a sink inside the run root; `AgentTaskRunner.run` checkpoints after `run.completed` or `run.terminated` and `GraphAgentExecutor` after each node agent returns (a graph run's controller and orchestration have no audit chain, so only its node agents are covered). The export runs on a worker thread; a failure is logged and recorded as a `run.checkpoint-failed` event (error type, code and the message cut to 500 characters) and never changes the run's result. Nothing is signed unless a checkpointer is given: there is no default key and no default destination. `CheckpointSink` (`location`, `read_all`, `append`, `is_inside`) is the storage seam and `FileCheckpointSink` ships with the SDK; a time-stamp token is a signer's concern, not a sink's. The MCP service reads `AGENT_RUNTIME_CHECKPOINT_DIR` and `AGENT_RUNTIME_CHECKPOINT_KEY_FILE` (set together; the key, at least 32 bytes, is read from the file and never from an environment value) and `AGENT_RUNTIME_CHECKPOINT_SIGNER_ID`, and exits with status 2 naming the variable when they are inconsistent, the key cannot sign or the sink is inside the run root. `tests/agent/test_run_checkpointer.py` (7 tests), `tests/mcp/test_service_checkpoints.py` (15 tests) |
| The terminal link depended on the audit payload bound | Running the service with `AGENT_RUNTIME_AUDIT_MAX_PAYLOAD_CHARS=300` showed the link `broken`: the `profile-completed` audit payload, which carried the telemetry head, exceeded the bound and was replaced by a preview. The audit half of the link is now its own small `terminal-link` entry (`{telemetry_head: {sequence, integrity_hash}}`), and `verify_terminal_link` still reads the earlier layout (`telemetry_head` inside `profile-completed`) for runs written before. `tests/mcp/test_service_checkpoints.py`, `tests/observability/test_run_checkpoints.py` (a removed entry is half a link, not an absent one; a run in the earlier layout still verifies) |
| Gate metadata was validated after the gate ran | A `CallableVerificationGate(verifier_id="  ")` or `authority="oracle"` registered without complaint, and a composite found an invalid member `authority` only while verifying, after earlier members had run. `VerificationGateRegistry.register` and `AllOfVerificationGate.__init__` now check the declared `verifier_id` and `authority` and refuse with `VERIFICATION_GATE_METADATA_INVALID` naming the gate (or the member's index and name); a gate changed after registration still fails at run time, naming it. A composite also kept only each member's verifier, outcome, reason and class, dropping the member's own evidence (a return code, a hash prefix); it now attaches each member's evidence in order while the whole stays within the 4,096-character limit (`{omitted_chars}` for one that does not fit), and a failure reads `<member>: <reason>`. `tests/agent/test_verification_decisions.py` (30 tests) |
| Tool outcomes and agent results from any actor | The matrix said plainly that `tool-outcome`, `agent-result` and `question-opened` accept every actor. The first two are produced only by the harness (every production producer passes `HARNESS`), so `TRANSITION_AUTHORITY` now restricts them to it; another actor is refused with the reducer's `Only <actors> authority may <action>.` message. `question-opened` stays open: opening a question claims nothing, and resolving one is the restricted act. An experiment with the restriction in place ran the 813 tests of the state, agent, links, integrations and MCP authority subsets and all passed, so no producer used the freedom. `tests/mcp/test_authority_matrix.py` (17 tests) probes the reducer with every actor |
| The audit payload bound could not be chosen by the service | `AuditTranscriptStore(max_payload_chars=)` existed (default 8,192, at least 256) but `AgentRuntimeServices.open` and the MCP service always used the default. `AgentRuntimeServices.open(audit_max_payload_chars=)`, `create_mcp_server(audit_max_payload_chars=)` and `AGENT_RUNTIME_AUDIT_MAX_PAYLOAD_CHARS` now set it; a value below 256, or one that is not a whole number, is refused naming the variable and the value. What the bound costs is open item 10. `tests/mcp/test_service_checkpoints.py`, `tests/agent/test_run_checkpointer.py` |
| Behaviour no test pinned | Removing the forwarding of `source` from `Orchestrator.approve` to the controller left 33 tests passing, so an orchestration's plan approval could have lost its decider unnoticed; `tests/agent/test_orchestrator_decision_sources.py` (3 tests) fails without it. The evidence limit is pinned at its boundary (4,096 characters accepted, 4,097 refused). The numbers in `root.md` (public names, deep modules, line count) were not enforced and had drifted; `tests/test_reference_docs.py` now compares them with `__init__.py` |
| Ledger completeness of a whole scenario | Each store was tested alone; nothing asked whether a complete run left complete ledgers. `tests/support/ledger_checks.py::ledger_summary` takes one run's telemetry and audit and asserts: both chains intact and numbered from 1; every audit entry that names a telemetry event names one that exists, of the matching type; every `agent.*` telemetry event is named by an audit entry (the terminal `profile-completed` event is linked instead); tool requests, completions and results agree; the terminal link is not broken. `tests/links/test_ledger_completeness.py` (4 tests) runs a scripted two-tool task and a three-node orchestrated graph under a checkpointer and requires `matches` and `linked`, then drops one event and reseals the chain, once in telemetry and once in the audit log, and requires the harness (and, for the audit case, the checkpoint and the link) to catch it. `tests/links/test_committed_ledgers.py` (7 tests, one per committed scenario) applies the same checks offline to `example_runs`: 44 telemetry runs (2,743 events) and 33 audit transcripts (3,431 entries) are intact, tool counts agree and every model turn has a turn started; all 33 terminal links report `absent`, because those runs predate the cross-link |

Examined and left alone:

| Item | Decision |
|---|---|
| Rotation of checkpoint files | Not built. The file is per run with one record per export, so nothing accumulates within a run and the file count tracks the run count, like telemetry rows. The retention question that mattered was the prune interaction, resolved above. |
| One registry for the authority rules | Not built; mostly already true. The reducer and the matrix read the same table objects (editing `TRANSITION_AUTHORITY` changes both), and the matrix probes `McpAuthorityPolicy.check`. The capability policy has no static table to share: grants are per-instance caller input. |
| One enum for `StateAuthority` and `TelemetryAuthority` | Not built. They are different axes: who may mutate state (3 values) and what produced a fact (5). `model-mediated`, `tool` and `system` must never become reducer actors, and a merged enum would allow it. |
| Reference oracles as SDK API | Not built; the recipe after Workflow 1 shows two in about ten lines each. A JSON-schema oracle duplicates the mandatory and already validated `output_schema`, and a shipped subprocess oracle is an execution surface the host should declare. |
| A `resume_from` stub | Not built. There is no `NotImplementedError` in `src`; it would be an interface with no behaviour, pointing at the counterfactual replay that is declined above. |
| An `event_types` filter on episodes | Not applicable. `event_types` is a telemetry concept; episodes have an `EpisodeKind` and a state, and the store lists and gets them. |
| A scenario test asserting a live model's output | Not built. A live run is not deterministic, and the one live-provider script is scratch outside the repository; a scripted scenario and the committed ledgers carry the invariants. A turn count is an inequality, not an equality: the committed `monopoly_game` ledger has 115 turns started and 112 audit model turns, the three extra being retries by older code that emitted no retry event (current code emits `model-turn-retrying`). |

### Resolved by the failure-diagnosis pass

The question was whether the SDK stops an error before it moves on and can say where a run
first went wrong. It does the first at most boundaries; the second had to be assembled by
hand. Each row names what was found, by running a failing agent run and a failing four-node
chain, and what changed.

| Area | Resolved |
|---|---|
| Terminal failures without a code | Five FAILED exits and several BLOCKED ones ended a run with free text only: `failure` was None and the `terminated` event's `failure_code` null. The FAILED ones were an undeclared tool, a missing executor, a verification rejection, the iteration limit and an unresolved tool batch; the BLOCKED ones the three context guards, a model turn that reports itself blocked and a hook or policy denial. Each now carries an `AgentFailure` whose code the `terminated` event repeats: `TOOL_NOT_DECLARED` (tool, call, declared tools), `TOOL_EXECUTOR_MISSING`, `VERIFICATION_GATE_REJECTED` (gate id, verifier id, output digest; `verification-completed` repeats the code), `MAX_ITERATIONS_REACHED`, `TOOL_BATCH_DEPENDENCIES_UNRESOLVED`, `CONTEXT_DEADLOCK`, `PROJECT_STATE_BUDGET_EXCEEDED` and `CONTEXT_BUDGET_EXCEEDED` (each with its numbers), `MODEL_REPORTED_BLOCKED`, `TOOL_DENIED_BY_HOOK` and, from the harness executor, `TOOL_APPROVAL_REQUIRED` (names the approval to decide), `TOOL_DENIED_BY_POLICY` and `HARNESS_TOOL_UNKNOWN`. A cancelled run keeps its status as its only code. Messages and `reason` text are unchanged. `tests/agent/test_failure_codes.py` (18 tests), `tests/tools/test_harness_failure_codes.py` (4 tests) |
| The reason a tool call never ran was not recorded | A call whose arguments violated the tool's schema never reached the executor, so it had no `tool-requested`, `tool-completed` or audit `tool-result`: the ledger held `status: failed` in `tool-batch-completed` and nothing else, and the path (`$.path: 7 is not of type 'string'`) lived only in the model's observation. Every non-succeeded outcome of that event now carries `failure_code` and `error` (cut to 300 characters); succeeded outcomes are unchanged, and `output-rejected` carries the schema code. The same tests |
| A blocked node named only its parent | In A to B to C to D with A failed, D read `Dependency "C" did not complete.` and the root was found by walking the event log. A dependency-blocked result now carries `root-cause:<node>` for every node that stopped the graph for its own reason (failed, cancelled or blocked by its own result), at most eight listed with `root-causes-omitted:<n>`, and `StateGraph.root_causes` and `blocked_descendants` answer from the current statuses. A node blocked before its other parents had failed first held only one root, so the set is settled lazily on read (`result`, `snapshot`, the end of `execute`) and shared between nodes with one blocker: refreshing on every result took 1.7 s for 300 failing roots feeding one join, settling on read takes 0.1 s; a chain of 1,500 nodes failing at the first costs 41 ms against 27 ms. A saved run holds the complete set. `tests/state/test_graph_root_cause.py` (14 tests) |
| Node results did not say which agent or which code | `GraphAgentExecutor` adds `agent-run:<task id>` and, for a failed or blocked agent, `failure-code:<code>`; its own failures carry `GRAPH_BINDING_MISSING`, `GRAPH_ELASTIC_BINDING_FACTORY_MISSING`, `GRAPH_ELASTIC_BINDING_FACTORY_FAILED`, `GRAPH_BINDING_MISMATCH` and `GRAPH_AGENT_INVOCATION_FAILED`, and `StateGraph.execute_node` `GRAPH_NODE_EXECUTOR_MISSING` and `GRAPH_NODE_EXECUTOR_RAISED`. `GraphNodeResult.failure_code`, `agent_run_id` and `root_causes` read them back. `tests/agent/test_graph_executor_diagnostics.py` (6 tests) |
| Nothing answered where a run first went wrong | `diagnose_run(run_root, run_id)` and `nailong-agent-sdk-dev diagnose-run <root> <id>` take a telemetry run id or a graph run id and report the verdict and `ok`, the `terminal` finding, the `first_problem` and `first_error` (up to 50 findings in sequence order, each with code, message, iteration and subject), whether both chains and the terminal link verify, and for a graph the roots ordered by when they stopped, each with its code, reason, the nodes it held up and the terminal finding of the agent run behind it. It exits 1 when `ok` is false and 3 for an unknown id or an unreadable root. It reads only: `RunStateStore(read_only=True)` creates no directory and the only files a read adds are SQLite's `-wal` and `-shm`. `tests/developer_tools/test_diagnose.py` (17 tests), `tests/developer_tools/test_diagnose_graph.py` (10 tests), `tests/state/test_run_store_read_only.py` (3 tests) |

Examined and left alone:

| Item | Decision |
|---|---|
| A typed code for a cancelled run | Not added. The status is the code, and what was interrupted is in `reason`. |
| An MCP `diagnose_run` tool | Not added. The TypeScript client enumerates the 55 registered tools, and a remote operator already has `get_telemetry_events` with `event_types`; the function and the command cover a local run root. |
| Failing a join when a fan-in parent failed | Not changed; see "Still open" item 11. |
| Automatic semantic checks of an output | Not built. A well-formed but wrong output is the host's verification gate (item 8). |

### Resolved by the attribution pass

The diagnosis says where a run first went wrong. Two questions were left: which of several
failures is responsible for a run stopping, and which agent wrote a defect into a file. Neither
is statistical causal inference. The first is exact structural attribution over the declared
dependency graph, plus a lookup of the model turn behind a failure; the second is a search of a
recorded write history with a check the caller supplies. Each row names what was found, by
building both and measuring them, and what changed.

| Area | Resolved |
|---|---|
| Which failure is responsible | `attribute_graph(graph)` and `nailong-agent-sdk-dev attribute-failure <root> <id> [--node ID]` rank the nodes that stopped a graph run by `responsibility`: 1 for stopping, plus 1/k for every node held up by k independent failures. That is the Shapley value of repairing the node, and equals the Chockler-Halpern degree of responsibility for a node that is blocked if any one of its causes fails. They also give `released_alone` (what runs if only this node is repaired: an exact intervention, `StateGraph.released_by`), `also_requires`, and for a blocked node its independent causes and a path. The responsibilities add up to the number of roots plus the number of blocked nodes. `released_by` was checked against actually re-executing 120 random graphs (hard and fan-in edges, failed, blocked and cancelled nodes) with the repaired nodes completed, and `responsibility` against the Shapley value computed by enumerating every coalition on 120 more. `tests/state/test_graph_interventions.py` (182 tests), `tests/state/test_graph_responsibility.py` (487, one of them a scaling guard: 2,000 nodes attributed and diagnosed in under a second), `tests/developer_tools/test_attribute_failure_graph.py` (13) |
| A diagnosis listed nodes a root did not hold up | `StateGraph.blocked_descendants` follows every edge, so a failed fan-in parent, which does not block, was listed as holding up a node that was blocked for another reason, and the diagnosis summed the lists, counting a node once per root that held it. The property test found the first and the summing sentence the second. `diagnose_graph` now lists a root's nodes from `root_cause_sets()` and reports `blocked_downstream`, the number of distinct blocked nodes. |
| The diagnosis was quadratic on large graphs | It recomputed each blocked node's causes separately, and graph validation rebuilt the set of node ids once per node and scanned every edge once per node. For 5,000 nodes with 4,700 blocked the diagnosis took 50 s; it now takes 0.19 s, loading the graph 0.17 s (was 0.65 s). For 2,000 nodes with 3,600 explicit edges the root-cause sets took 477 ms and take 12 ms, the attribution 515 ms and 29 ms. One walk shared by every node, a hoisted set and an incoming-edge index built once per read. |
| Which model decision a failure traces to | `link_decisions` pairs a finding with the audit `model-turn` entry of its iteration: the turn that issued the failing call (the one call of a batch when the finding names it), that produced the rejected output or that reported itself blocked. A failure that comes from a limit, the provider, the context budget, a gate that could not decide or the environment is returned `unlinked` with the reason. A call that ended the run before its batch completed (an undeclared tool in a batch of three) had no outcome in the ledger, so the `terminated` event now carries the failure's bounded `failure_details` (`tool`, `tool_call_id`) and the terminal finding names the tool and call. `tests/developer_tools/test_attribute_failure_agents.py` (16 tests) |
| A write did not say which agent made it | The manifest of a `write_draft`, `edit_draft` or `notebook_edit` held the run, node, task and role. `HarnessToolExecutor` now adds the agent identity, agent run id, tool, call id and iteration, and the occurrence record gains a store-wide `write_sequence`. `tests/tools/test_core_tools_drafts.py` pins the full manifest |
| A file had no ordered history | `ArtifactStore.history(path)` returns the versions of one path in order, each with its writer; `paths()` lists the paths with writes and `read_bytes(sha256)` returns a hash-checked blob. They read one append-only log, `.agent-artifacts/history/writes.jsonl`, written under one store lock. Per-path history files measured four to six times slower per write, so the log is global: it adds about 0.6 to 0.8 ms to a write of about 3 ms on a quiet Windows machine, and about 2 ms while file operations there are slow for minutes at a time (the old store's 3 ms became 9 ms, the new one's 3.7 ms became 11 ms), with a process-wide tail cache because re-opening a just-written file costs about 12 ms where Windows Defender is active. A store opened `read_only` creates nothing, and a store that predates the log is adopted on its first write and read in the meantime from its occurrence records. `tests/tools/test_artifact_history.py` (11 tests) Each record is chained to the one before it (`previous_hash`, `integrity_hash`) and `ArtifactStore.verify_history()` reports the first record that does not match; a log from before the chain verifies as unchained. Removing the newest records, or rewriting a tail and re-chaining it, is not detected without a head kept outside the run root. |
| Which write introduced a defect | `attribute_defect(dir, path, check)` and `attribute-defect <dir> <path> (--contains \| --lacks \| --matches \| --sha256) [--strategy gallop\|scan]` find the first defective version of the unbroken defective suffix and its writer, the last clean version and the later writers. `find_suffix_start` probes the newest version, steps back 1, 2, 4, ... and bisects, memoised by content hash: for a defect 50 versions deep it makes 12 evaluations at 100, 1,000 and 3,000 versions where scanning makes 51 (the 3,000-version history is read in 22 ms, 7 microseconds a version, which is then the whole cost for a cheap check; a check that runs a test suite is what the evaluations save). `tests/foundations/test_suffix_search.py` (48 tests), `tests/developer_tools/test_attribute_defect.py` (15), `tests/links/test_defect_attribution_agents.py` (3: three agents writing one file through the real runner) |
| Which tool result first went wrong | `attribute_result_defect(run_root, run_id, check)` and `attribute-result <root> <run-id> (--contains \| --lacks \| --matches) [--tool NAME]` scan the run's journaled tool results in the order they were produced, stop at the first one the caller's check rejects and name the model turn that asked for it (`link_call` in `decision_links.py`). `attribute_defect` only sees what was written to a file, so a tool that reported success with a corrupted payload left no defective version to find: in the benchmark's development split the shipped tools located none of those faults, and with this tool they locate all. `tests/developer_tools/test_attribute_result.py` (16 tests) |

Examined and left alone:

| Item | Decision |
|---|---|
| Statistical causal estimation (causal discovery, effect sizes over many runs) | Not built. One run has one outcome, and over the declared graph the structural answer is exact; an estimate needs a population of comparable runs the SDK does not keep and returns a probability instead of a record. |
| Replaying a run with a different model decision, or ablating one | Not built. The audit entry of a turn is bounded and can be cut (item 2), the model is not deterministic and tools have side effects; `released_by` answers the structural question without running anything. |
| Tracking what a node read into what it wrote | Not built. A write names its writer, not the artifacts the writer read, so a defect derived from an earlier defective artifact is attributed to the agent that wrote the derived file. |
| A decision graph inside one agent run | Not built. A turn records what the model did and what it saw, never its reasoning (hidden reasoning is stripped by design); `consumed_episode_ids` and `depends_on_call_ids` are declared links and are in the audit turn. |
| MCP `attribute_failure` and `attribute_defect` tools | Not added, for the reason `diagnose_run` was not: the TypeScript client enumerates the 55 registered tools, and the functions and commands cover a local run root. |
| `fsync` of the write log | Not added. The blob and occurrence files are not synced either; item 15 states what a crash can lose. |

### Still open

1. **[inspected] Retention covers telemetry, audit transcripts and tool-result journals of
   finished runs and nothing else.** Project states, graph runs, controllers,
   orchestrations, workspaces, exported reports, semantic memory and its archive are kept for
   ever, as are journal files recorded without a run id. `inventory_run_root` lists them with
   their owner and what references them and marks some `reclaimable`, but no SDK operation
   deletes them: a deletion rule needs the host to say what it may drop. A tombstone anchor
   detects the removal or rewriting of entries it covers; it does not cover entries written
   after it and is only as independent as the place it is kept. A run that is pruned and later
   run again under the same id starts a new chain with its own tombstone, and a reader that
   held the old handles finds them gone, so `older_than_seconds` should be far longer than any
   task's resubmission delay. The telemetry file keeps its size until someone runs `vacuum`.
2. **[measured] A run's persistence cost grows with its size.** Each `RunStateStore.save`
   rewrites the whole fixed-state snapshot and hashes the whole run, so the bytes written grow
   with the square of the node count: 200 nodes take 1.8 s, 600 take 8.5 s and 1,000 take
   21.4 s on the development machine (8.9, 14.1 and 21.4 ms per save; 29, 260 and 722 MB
   written), most of it fsync, the snapshot file and the run hash. A delta format was not
   built, for the reasons under "Examined and left alone"; it becomes the next step for graphs
   of several thousand nodes or for storage that charges for bytes written.
3. **[inspected] MCP approvals separate roles, not people.** The two credentials separate who
   runs work from who approves it; whoever holds the approver token is "the approver" for
   every approval, `approver_label` is a note the caller writes, and the credential's hash
   prefix in the `mcp-decisions` stream says which secret decided, not who held it. A
   deployment that needs per-person accountability needs its own identity layer in front of
   the service. `AGENT_RUNTIME_ALLOW_HOST_APPROVALS=1` puts both roles back on one token and is
   recorded in each decision. The argument guard uses the MCP library's server-middleware API
   (`mcp` 2.2.0, marked provisional upstream). The TypeScript backend's changes were
   type-checked against stubs only, because its dependencies could not be installed offline,
   and its unit and end-to-end tests have not been run.
4. **[measured] Platform coverage beyond Windows and Ubuntu.** Executed on 2026-10-17 from
   the final working tree: the full suite on Windows 11 with Python 3.13.1 (2,812 passed, 10
   skipped: six live Docker sandbox tests, which skip because no daemon answers, and four
   resource-limit tests that apply on POSIX only) and on Ubuntu 24.04 under WSL2 with Python
   3.12.3 (2,800 passed, 22 skipped: the same six Docker tests and sixteen tests of Windows
   or case-insensitive filesystem behaviour such as extended-length path spelling, reserved
   device names and NTFS aliases). Ruff check and format check pass on `src` and `tests`.
   Not executed: Windows with Python 3.12 and Ubuntu with Python 3.13 (a 3.12 interpreter
   exists on the Windows machine but has no dependencies installed), the GitHub Actions
   workflow itself (`.github/workflows/ci.yml` has never run, so the matrix, the lint job
   and the Docker job are untested as configured), the live Docker sandbox tests (Docker
   Desktop does not start on this machine), macOS, a run root shared between Windows and WSL
   processes, and Docker engines other than Docker Desktop's. Support is not claimed for
   those.
5. **[inspected] Elastic nodes.** A join re-runs the requester's definition with the
   findings and the requester's `handoff` notes in its task text, not the original
   conversation, and a node replayed after a crash starts its task again from the
   beginning, which is why only bindings declared idempotent are replayed.
   `tests/agent/test_orchestrator_elastic_recovery.py` pins this.
6. **[inspected] `run_agent_task` limits.** Python hooks, verification gates, cancellation
   tokens, sub-agents and a model object are available to the Python API
   (`AgentTaskRunner.run`) but not over MCP; a call that names one is refused with the
   argument's name and the reason instead of being ignored. A cancelled model request is
   cancelled logically at once and its late response commits nothing, but the provider is not
   told: Chat Completions has no operation that stops a request, so the provider may finish
   generating and bill it. `ModelCancellationOutcome.provider` is `confirmed` only when the
   host supplies a `provider_cancel` hook that says so. The default non-streaming transport
   closes the connection; a custom blocking transport is detached on a daemon thread until
   the provider answers or its timeout passes.
7. **[inspected] Trace export is a read-side JSON exporter.** `export-otlp` sends OTLP/HTTP
   JSON only (no protobuf, no gRPC), one request per run and signal, read from the ledger
   after the fact; a run's profile event keeps its most recent 512 spans (`spans_omitted`
   reports the rest); `tracestate` and baggage are not read or sent; a node's dependencies
   become span links only when `--graph-run` (or `graph_dependencies`) supplies them from
   the run record, and a run executed without a controller has no `invoke_workflow` span, so
   its node roots name a parent that is not exported (they are marked
   `nailong.parent_span_exported=false` and counted); the workflow span runs from the first
   controller event to the last execution pass and says so, it is not the nodes' own timing;
   the built-in web tools reach public hosts only and never send `traceparent`, and an MCP
   server gets it only with `propagate_trace_context`. A trace id a caller chose before ids
   were checked is still read and verified from the ledger, but cannot be given to a new
   `TelemetryContext`.
8. **[measured] Semantic compaction is verified, not proven.** Without a host judge the
   checks show that each claim's evidence occurs verbatim in the episodes it cites, that
   failed episodes and action episodes are mentioned and that no new number or identifier
   appears, but not that the claim follows from the evidence: on the labeled adversarial set
   they accept 45.5% of the wrong summaries and reject none of the faithful ones. The lexical
   screen lowers that to 9.1% at the price of rejecting 33.3% of the faithful ones, and only
   a `ClaimJudge` or `SummaryJudge` the host supplies can close the rest; the SDK ships no
   model summarizer and no model judge. `ExtractiveEpisodeSummarizer` quotes rather than
   abstracts, so a digest of one episode is about as large as the stub it replaces; the gain
   is the freed payload, bounded growth through consolidation and archiving, and the
   provenance. A group whose sources total fewer than `compression_floor_tokens` is not
   summarized and keeps its payload. Over a synthetic 240-episode run with one fixed context
   budget (`scripts/run_memory_benchmark.py`, seeds 7, 8 and 9, a deterministic text reader
   in place of a model) PCKP alone leaves task reconstruction at 0.19 (failure F1 0.43) with
   4.1k visible tokens, and extractive digests give 0.43 (F1 0.89) with 5.3k visible tokens
   and faithfulness 1.0; the bounded arm keeps 60 of the 240 active records at the same
   accuracy and reads the evicted ones through the archive. A summarizer that corrupts 35% of
   its groups is caught by the deterministic checks except for contradictions of the quoted
   evidence (one seed of three accepted 2 of 12 contradicted claims, faithfulness 0.83), and
   a rule that sees the ground truth removes those. These numbers bound what a model could
   recover from the context; they do not measure reasoning, and the verifier arms use
   ground truth that a deployment does not have. The projection pass runs on a worker thread
   inside the projection step unless `background_summarization` is on, in which case a result
   commits one or more passes later. A multiple-choice form of PCKP (one choice per episode
   between the full payload, a digest and a tombstone under a single budget) was evaluated
   and not implemented: the utility of a digest cannot be measured without a model or
   labelled tasks, an action's prerequisites must stay full whatever a digest says, and the
   episode and summary budgets already bound the two representations separately.
9. **[inspected] Run-head checkpoints are taken only when the host configures them, and
   they are only as independent as where they are kept.** `export_run_head_checkpoint` and
   `checkpoint-run` sign the heads when they are called, and the runtime does it when a run
   ends only if `AgentRuntimeServices` was opened with a `RunHeadCheckpointer` (the MCP
   service: `AGENT_RUNTIME_CHECKPOINT_DIR` and `AGENT_RUNTIME_CHECKPOINT_KEY_FILE`). There is
   no default key or destination, so a run of a service that was not configured has no record
   outside the run root. A checkpoint that fails is logged and recorded as
   `run.checkpoint-failed` and the run still completes, so a host that needs the record has to
   watch for that event. Only runs with both chains are covered: a graph run's controller and
   orchestration have no audit transcript, so only its node agents are checkpointed and
   cross-linked. A sink on the same volume as the run root is refused only when it is inside
   the root, and is no better than the root when the volume is shared. The HMAC key is a
   shared secret: whoever holds it can sign, so it belongs outside the run root, and an
   asymmetric signer is the host's to supply through `CheckpointSigner`. Someone who can
   delete the newest checkpoints leaves an older, shorter one, against which the run verifies
   as `advanced`. A run written before runs cross-linked their heads reports its terminal link
   as `absent`, not `broken`.
10. **[measured] The audit log keeps a long payload by its hash and a preview.** An entry
    whose canonical JSON exceeds `max_payload_chars` (default 8,192, at least 256; set through
    `AuditTranscriptStore`, `AgentRuntimeServices.open(audit_max_payload_chars=)`,
    `create_mcp_server(audit_max_payload_chars=)` or `AGENT_RUNTIME_AUDIT_MAX_PAYLOAD_CHARS`)
    is replaced whole by `{truncated, content_hash, original_chars, preview}`: the hash (of
    the redacted canonical JSON) and the length let a reader confirm a copy kept elsewhere,
    and the preview is the first `max_payload_chars` characters; nothing else of the payload
    survives. In the committed `example_runs` ledgers 214 of 3,431 entries (179 tool results,
    35 model turns) were cut at the default. A tool's full output is also in the tool-result
    journal, but the audit log is where a model's turn is recorded, which is why a replay from
    a recorded turn is not offered. A cut entry also loses every other field, among them the
    `telemetry_event_id` that pairs an entry written for a lifecycle event with its telemetry
    event; the ledger harness would report such an event as uncited, and none of the committed
    scenarios has one. A larger bound costs disk and read time: each entry is one JSON line
    that the chain check reads whole.
11. **[inspected] A failed fan-in parent still lets its join run.** A fan-in edge is meant to
    settle on partial results, so a FAILED parent is allowed to proceed while a BLOCKED or
    CANCELLED one holds the join. The join receives the failed parent's result (status,
    reason, `failure-code:`) in its execution context and has to notice it. The SDK does not
    run `ProvenanceContractGate` at that boundary; the host calls `verify_provenance_contract`
    (MCP or `ControllerRuntime`). A diagnosis lists the failed parent as a root with nothing
    below it, because nothing was blocked.
12. **[inspected] A diagnosis is only as complete as what was recorded.** Findings come from
    the event types `_EXTRACTORS` knows, so a new event type is invisible until an extractor
    exists; the reason a tool call never ran is recorded only for runs written after
    `tool-batch-completed` carried it (an older run has the status alone) and is cut to 300
    characters; a root's agent run is drilled into only while that run's telemetry is in the
    same run root; and the id must be a telemetry run id or a graph run id, not a controller
    or orchestration id.
13. **[inspected] Attribution is structural, over declared dependencies only.** Responsibility
    and repair follow the scheduler's rules: hard dependencies are an AND and a failed fan-in
    parent does not block. A node that read an artifact another node wrote without declaring a
    dependency, or a join that ignored its failed fan-in parent (item 11), is invisible to
    them. They do not weigh how likely a repair is to work or what it costs, they say nothing
    about why a model chose an action, and nothing replays a run with a different decision.
14. **[inspected] A decision link is a lookup, and only as complete as the ledger.** It needs
    the `model-turn` entry of that iteration in the audit transcript of the same run root; an
    entry cut at the audit payload cap (item 2) still names the call when the finding does but
    not its arguments. A terminal tool failure names its call only in runs written after the
    `terminated` event carried `failure_details`; older runs give `issued-the-batch` with every
    call as a candidate. The table of failure codes that name a decision is closed: a new code
    is `unlinked` until someone classifies it. A graph run links its ten most responsible
    roots, and only while their telemetry is in the same run root.
15. **[inspected] Artifact attribution covers writes made through `ArtifactStore`, and
    `gallop` assumes the defect persists.** A file changed by anything else (a shell command,
    an editor) has no version. The write log is appended without `fsync`: an operating-system
    crash can drop its last lines, after which `history` omits those writes although their
    occurrence files exist. A store written before the log is read from its occurrence records
    ordered by time, the order of the clock rather than of the lock. On Windows two paths that
    differ only in case are one path. `gallop` can report an older introduction when a defect
    was fixed and introduced again (`--strategy scan` reports the newest), and the answer is
    only as good as the caller's check. The command needs the directory that holds
    `.agent-artifacts`, which for a task service is a workspace and not the run root.
16. **[measured] The scheduler still scans every edge per pending node.**
    `_refresh_runnable` and `_block_unreachable_nodes` call `_split_dependencies`, which reads
    the whole edge list, once per node: a graph of 2,000 nodes with 3,600 explicit edges took
    11 s to execute where reading its root causes now takes 12 ms. Validation, root causes and
    attribution index the incoming edges once; the scheduler's own loops are unchanged, so a
    graph with many explicit edges pays for them on every wave.