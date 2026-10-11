# nailong-agent-sdk - workflow and function reference

This is a complete, per-file description of `src/nailong_agent_sdk`: what every module is
for, what each class and function does, and where it sits in the end-to-end workflow. It
covers **131 files, 416 classes and 1,327 functions** (methods and nested closures included).
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
caller configures each: the output check registered in `agent/verification.py` (a refused
answer goes back to the agent as an observation so it can correct itself), the capability
policy with typed tool approvals (`tools/policy.py`, `tools/approvals.py`), and the plan and
orchestration approval steps of the controller, which the caller can answer at once.

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
developer_tools  foundations, observability, specifications, state    catalogue, validate, inspect
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
   newest observations that fit, and build the stubs of compacted episodes. Emit the
   `context-projected` event and the context metrics.
5. **Three guards** end the run BLOCKED before any model call: `CONTEXT_DEADLOCK` (nothing can
   be compacted), `PROJECT_STATE_BUDGET_EXCEEDED` (the mandatory state exceeds its 2,000-token
   view) and `CONTEXT_BUDGET_EXCEEDED` (prompt plus state exceed the context budget).
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
  first); and `omitted_compacted_count`.
* **continuation** - for providers that issued tool calls, the raw assistant tool calls and the
  matching bounded tool results, only for the latest batch.

Defaults: context budget 12,000 estimated tokens, episode budget 6,000, tool-result preview
1,024 characters, stub budget 1,500, stub summary 160 characters, project-state view 2,000.
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

## Workflow 3 - how a tool call is governed

`BaseAgent._execute_tool_call` rejects an undeclared tool and a missing executor (each ends the
run FAILED); arguments that violate the declared JSON schema, or an action that consumes
episodes that are unavailable, come back to the model as a failed result so it can correct
them. It then runs pre-tool hooks (a denial ends BLOCKED), the host's `ToolExecutor` and
post-tool hooks, all under the tool watchdog. With the SDK's `HarnessToolExecutor`
(`tools/registry.py`): the tool must be in the closed `HarnessToolRegistry`;
`CapabilityPolicy` checks sensitive paths first, then the role's capability grant, path
containment and any typed approval (a pending approval returns a `blocked` result; once it is
decided, `resume_run` re-opens the node); the handler then runs a core tool (`tools/core`), a
supervised process (`tools/supervisor.py`) or a sandbox backend. The result is stored in the
journal, becomes an episode and an observation with a handle, is reduced into project state,
and is written to the audit log and telemetry.

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
| add a verification gate | register on a `VerificationGateRegistry` (`agent/verification.py`) |
| add a project-state transition | `state/project_state_models.py` (`StateTransitionKind`), `ProjectStateReducer.apply` in `state/project_state_engine.py` |
| add a graph node kind or executor | `state/graph_models.py` (`GraphNodeKind`), the executor map passed to `execute_graph` |
| let agents explore beyond the plan, or change the elastic rules | `state/elastic.py` (requests, limits, admission checks), `StateGraph._apply_spawn_requests` (commit-time handling), `StateGraph._reserve_elastic` (per-wave ledger), `tools/elastic_requests.py` (agent queue), `agent/orchestrator/` (policy ceilings, derived bindings) |
| change what happens to nodes a crash interrupted | `StateGraph.recover_interrupted`, `GraphAgentExecutor.is_replayable`, `Orchestrator.recover_execution` |
| add a metric | `observability/metric_definitions.py` and the emit site (`record_metric_value`) |
| add an MCP tool | the matching `mcp/*_tools.py` `register_*` function, registering with `ctx.tool(server, name, exclusive=...)` |
| support a new specification format | `specifications/documents.py` (`DocumentFormat`) and `SpecificationPreprocessor._parse` |
| keep a run root from growing | `agent/retention.py` (`RunRetention.prune` with a `RetentionPolicy`), MCP `prune_runs`, `nailong-agent-sdk-dev prune-runs <root> --older-than-seconds N [--apply]` |
| fetch what a task wrote, or answer its approvals while it runs | `agent/task_files.py`, `AgentTaskRunner.workspace_for`, `approvals` and `decide_approval`; MCP `list_task_files`, `read_task_file`, `list_task_approvals`, `decide_task_approval` |
| debug a run | MCP `get_telemetry_events` / `get_audit_log` / `render_audit_transcript`, `nailong-agent-sdk-dev inspect-run <root> <run-id>`, or `nailong-agent-sdk-dev verify-evidence <root> <project-id>` |

## Glossary

* **Episode** - one tool call and its result as a unit of memory; *exploratory* (read-only) or
  *action* (consumes other episodes). **Observation** - the model-facing message about a call.
* **Projection** - the bounded view of memory and state built for one turn. **Compaction** -
  dropping an episode's content while keeping a tombstone. **Stub** - the one-line, handle-bearing
  residue of a compacted episode. **Handle** - an opaque id of a journalled full result.
* **Wave** - the set of graph nodes that are runnable together. **Controller** - the vertical
  state machine (plan, approval, dispatch, repair, escalation). **Harness** - the deterministic
  runtime around the model: policy, journal, state, audit.
* **Discovery** - a closed, source-backed lateral finding published into graph state.
* **PCKP** - precedence-constrained knapsack: choose items under a budget when picking an item
  requires its prerequisites.

## File index

| File | Lines | Role |
|---|---:|---|
| [`__init__.py`](root.md#__init__py---the-public-api-surface-lazy-re-exports) | 789 | the public API surface (lazy re-exports) |
| [`agent/__init__.py`](agent.md#agent__init__py---package-marker-for-agent-execution) | 3 | package marker for agent execution |
| [`agent/base_agent/__init__.py`](agent.md#agentbase_agent__init__py---public-surface-of-the-baseagent-runtime-package) | 30 | public surface of the BaseAgent runtime package |
| [`agent/base_agent/agent.py`](agent.md#agentbase_agentagentpy---the-single-task-agent-loop) | 2256 | the single-task agent loop |
| [`agent/base_agent/types.py`](agent.md#agentbase_agenttypespy---small-value-types-used-by-the-baseagent-loop) | 99 | small value types used by the BaseAgent loop |
| [`agent/elastic_context.py`](agent.md#agentelastic_contextpy---bounded-task-text-for-exploration-and-continuation-nodes) | 105 | bounded task text for exploration and continuation nodes |
| [`agent/graph_agent_executor.py`](agent.md#agentgraph_agent_executorpy---runs-registered-baseagent-bindings-as-graph-nodes) | 273 | runs registered BaseAgent bindings as graph nodes |
| [`agent/model.py`](agent.md#agentmodelpy---provider-neutral-model-contract-streaming-events-and-failover) | 361 | provider-neutral model contract, streaming events and failover |
| [`agent/openai_compatible/__init__.py`](agent.md#agentopenai_compatible__init__py---public-surface-of-the-openai-compatible-adapters) | 36 | public surface of the OpenAI-compatible adapters |
| [`agent/openai_compatible/chat.py`](agent.md#agentopenai_compatiblechatpy---chat-completions-adapter-for-the-agent-model-contract) | 613 | Chat Completions adapter for the agent model contract |
| [`agent/openai_compatible/embeddings.py`](agent.md#agentopenai_compatibleembeddingspy---synchronous-embedding-provider-for-retrieval-indexes) | 68 | synchronous embedding provider for retrieval indexes |
| [`agent/openai_compatible/transport.py`](agent.md#agentopenai_compatibletransportpy---json-http-transports-and-endpoint-configuration) | 480 | JSON HTTP transports and endpoint configuration |
| [`agent/openai_compatible/vision.py`](agent.md#agentopenai_compatiblevisionpy---image-proposals-through-a-verified-byte-loader) | 163 | image proposals through a verified byte loader |
| [`agent/orchestrator/__init__.py`](agent.md#agentorchestrator__init__py---public-surface-of-the-orchestrator-package) | 31 | public surface of the orchestrator package |
| [`agent/orchestrator/models.py`](agent.md#agentorchestratormodelspy---user-owned-orchestration-policy-request-and-record-contracts) | 180 | user-owned orchestration policy, request and record contracts |
| [`agent/orchestrator/orchestrator.py`](agent.md#agentorchestratororchestratorpy---deterministic-composition-of-policy-plan-controller-and-graph-execution) | 989 | deterministic composition of policy, plan, controller and graph execution |
| [`agent/orchestrator/state_store.py`](agent.md#agentorchestratorstate_storepy---atomic-local-persistence-of-orchestration-records-and-policies) | 119 | atomic local persistence of orchestration records and policies |
| [`agent/retention.py`](agent.md#agentretentionpy---opt-in-pruning-of-finished-runs-with-a-hash-chained-tombstone-for-each) | 445 | opt-in pruning of finished runs with a hash-chained tombstone for each |
| [`agent/runtime.py`](agent.md#agentruntimepy---composition-root-for-the-durable-stores-around-an-agent) | 112 | composition root for the durable stores around an agent |
| [`agent/task_files.py`](agent.md#agenttask_filespy---bounded-paged-listing-and-reading-of-the-files-an-agent-task-left-in-its-workspace) | 213 | bounded, paged listing and reading of the files an agent task left in its workspace |
| [`agent/task_runner.py`](agent.md#agenttask_runnerpy---caller-driven-task-runner-model-tools-permissions-and-limits-all-come-from-the-caller) | 648 | caller-driven task runner: model, tools, permissions and limits all come from the caller |
| [`agent/verification.py`](agent.md#agentverificationpy---host-registered-bounded-output-acceptance-gates) | 187 | host-registered, bounded output-acceptance gates |
| [`developer_tools/__init__.py`](developer_tools.md#developer_tools__init__py---public-surface-of-the-developer-utilities) | 42 | public surface of the developer utilities |
| [`developer_tools/catalog.py`](developer_tools.md#developer_toolscatalogpy---public-api-catalogue-generation) | 88 | public API catalogue generation |
| [`developer_tools/cli.py`](developer_tools.md#developer_toolsclipy---the-nailong-agent-sdk-dev-command-line) | 181 | the `nailong-agent-sdk-dev` command line |
| [`developer_tools/inspect.py`](developer_tools.md#developer_toolsinspectpy---read-only-run-evidence-inspection) | 211 | read-only run evidence inspection |
| [`developer_tools/quality.py`](developer_tools.md#developer_toolsqualitypy---closed-ruff-quality-check) | 92 | closed Ruff quality check |
| [`developer_tools/validate.py`](developer_tools.md#developer_toolsvalidatepy---contract-file-validation) | 97 | contract-file validation |
| [`foundations/__init__.py`](foundations.md#foundations__init__py---package-marker-for-the-dependency-free-layer) | 4 | package marker for the dependency-free layer |
| [`foundations/atomic_io.py`](foundations.md#foundationsatomic_iopy---crash-safe-file-publication-exclusive-claims-and-cross-process-locks) | 158 | crash-safe file publication, exclusive claims and cross-process locks |
| [`foundations/benchmarks.py`](foundations.md#foundationsbenchmarkspy---reproducible-exact-vs-greedy-pckp-benchmark-harness) | 88 | reproducible exact-vs-greedy PCKP benchmark harness |
| [`foundations/contracts.py`](foundations.md#foundationscontractspy---the-serializable-baseagent-contract-definitions-tasks-turns-results-context-types) | 526 | the serializable BaseAgent contract: definitions, tasks, turns, results, context types |
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
| [`foundations/text.py`](foundations.md#foundationstextpy---newline-only-line-splitting-and-utf-8-well-formedness-for-durable-text) | 50 | newline-only line splitting and UTF-8 well-formedness for durable text |
| [`foundations/version.py`](foundations.md#foundationsversionpy---the-installed-package-name-and-version-and-the-identity-strings-derived-from-them) | 23 | the installed package name and version, and the identity strings derived from them |
| [`integrations/__init__.py`](integrations.md#integrations__init__py---public-surface-of-the-integrations-package) | 109 | public surface of the integrations package |
| [`integrations/_utils.py`](integrations.md#integrations_utilspy---private-sanitiser-digest-and-optional-import-helpers) | 152 | private sanitiser, digest and optional-import helpers |
| [`integrations/contracts.py`](integrations.md#integrationscontractspy---framework-neutral-interop-contracts) | 98 | framework-neutral interop contracts |
| [`integrations/jev/__init__.py`](integrations.md#integrationsjev__init__py---public-surface-of-the-jev-package) | 61 | public surface of the Jev package |
| [`integrations/jev/advisory.py`](integrations.md#integrationsjevadvisorypy---verification-gate-that-adds-a-non-authoritative-jev-signal) | 109 | verification gate that adds a non-authoritative Jev signal |
| [`integrations/jev/architecture.py`](integrations.md#integrationsjevarchitecturepy---monotonic-single-to-multi-routing-advice) | 164 | monotonic single-to-multi routing advice |
| [`integrations/jev/decision.py`](integrations.md#integrationsjevdecisionpy---optional-typesafe-jev-evaluator) | 297 | optional TypeSafe Jev evaluator |
| [`integrations/jev/exploration.py`](integrations.md#integrationsjevexplorationpy---optional-prioritisation-of-an-already-approved-candidate-set) | 149 | optional prioritisation of an already-approved candidate set |
| [`integrations/jev/models.py`](integrations.md#integrationsjevmodelspy---jev-question-answer-request-result-and-receipt-contracts) | 171 | Jev question, answer, request, result and receipt contracts |
| [`integrations/jev/receipts.py`](integrations.md#integrationsjevreceiptspy---receipt-sinks-for-jev-evaluations) | 57 | receipt sinks for Jev evaluations |
| [`integrations/langchain.py`](integrations.md#integrationslangchainpy---optional-langchain-adapters) | 266 | optional LangChain adapters |
| [`integrations/langgraph.py`](integrations.md#integrationslanggraphpy---optional-langgraph-adapters) | 299 | optional LangGraph adapters |
| [`integrations/receipts.py`](integrations.md#integrationsreceiptspy---receipt-sinks-for-external-operations) | 65 | receipt sinks for external operations |
| [`mcp/__init__.py`](mcp.md#mcp__init__py---package-marker-for-the-mcp-surface) | 3 | package marker for the MCP surface |
| [`mcp/_shared.py`](mcp.md#mcp_sharedpy---shared-context-and-error-formatting-for-every-tool-group) | 117 | shared context and error formatting for every tool group |
| [`mcp/agent_tools.py`](mcp.md#mcpagent_toolspy---mcp-tools-for-agent-contract-validation-context-assembly-and-scripted-runs) | 159 | MCP tools for agent contract validation, context assembly and scripted runs |
| [`mcp/client.py`](mcp.md#mcpclientpy---outbound-mcp-client-lifecycle) | 541 | outbound MCP client lifecycle |
| [`mcp/client_bridge.py`](mcp.md#mcpclient_bridgepy---opt-in-bridge-from-external-mcp-tools-to-governed-harness-tools) | 92 | opt-in bridge from external MCP tools to governed harness tools |
| [`mcp/client_types.py`](mcp.md#mcpclient_typespy---typed-configuration-and-status-for-the-outbound-mcp-client) | 89 | typed configuration and status for the outbound MCP client |
| [`mcp/controller_tools.py`](mcp.md#mcpcontroller_toolspy---mcp-tools-for-the-deterministic-controller) | 269 | MCP tools for the deterministic controller |
| [`mcp/orchestration_tools.py`](mcp.md#mcporchestration_toolspy---mcp-tools-for-plan-validation-graph-runs-and-orchestration-compilation) | 126 | MCP tools for plan validation, graph runs and orchestration compilation |
| [`mcp/project_state_tools.py`](mcp.md#mcpproject_state_toolspy---mcp-tools-for-project-working-memory) | 192 | MCP tools for project working memory |
| [`mcp/run_tools.py`](mcp.md#mcprun_toolspy---mcp-tools-for-graph-run-state-cancellation-approvals-and-resumption) | 59 | MCP tools for graph run state, cancellation, approvals and resumption |
| [`mcp/security.py`](mcp.md#mcpsecuritypy---bearer-token-authentication-and-host-checks-for-the-http-service) | 253 | bearer-token authentication and host checks for the HTTP service |
| [`mcp/server.py`](mcp.md#mcpserverpy---mcp-server-factory-and-loopback-entry-point) | 177 | MCP server factory and loopback entry point |
| [`mcp/specification_tools.py`](mcp.md#mcpspecification_toolspy---mcp-tool-for-specification-parsing) | 40 | MCP tool for specification parsing |
| [`mcp/telemetry_tools.py`](mcp.md#mcptelemetry_toolspy---mcp-tools-for-telemetry-audit-logs-and-metrics) | 176 | MCP tools for telemetry, audit logs and metrics |
| [`memory/__init__.py`](memory.md#memory__init__py---package-marker-for-episode-memory-and-context-assembly) | 3 | package marker for episode memory and context assembly |
| [`memory/context.py`](memory.md#memorycontextpy---builds-the-fixed-initial-prompt-for-a-run) | 51 | builds the fixed initial prompt for a run |
| [`memory/context_projection.py`](memory.md#memorycontext_projectionpy---per-turn-bounded-context-projection-and-the-tool-result-journal) | 464 | per-turn bounded context projection and the tool-result journal |
| [`memory/episode_models.py`](memory.md#memoryepisode_modelspy---episode-records-compaction-policy-and-the-retention-contracts) | 172 | episode records, compaction policy and the retention contracts |
| [`memory/episode_scoring.py`](memory.md#memoryepisode_scoringpy---deterministic-lexical-relevance-and-cost-helpers-for-retention) | 53 | deterministic lexical relevance and cost helpers for retention |
| [`memory/episode_store.py`](memory.md#memoryepisode_storepy---episode-lifecycle-and-the-three-deterministic-compaction-strategies) | 815 | episode lifecycle and the three deterministic compaction strategies |
| [`memory/episodes.py`](memory.md#memoryepisodespy---task-scoped-episode-graph-of-one-line-summaries) | 76 | task-scoped episode graph of one-line summaries |
| [`observability/__init__.py`](observability.md#observability__init__py---package-marker-for-the-observability-layer) | 3 | package marker for the observability layer |
| [`observability/audit_log.py`](observability.md#observabilityaudit_logpy---append-only-hash-chained-per-run-jsonl-audit-transcript) | 447 | append-only, hash-chained, per-run JSONL audit transcript |
| [`observability/metric_definitions.py`](observability.md#observabilitymetric_definitionspy---the-sdks-standard-metric-catalogue) | 400 | the SDK's standard metric catalogue |
| [`observability/metrics.py`](observability.md#observabilitymetricspy---record-metric-values-against-the-standard-catalogue) | 131 | record metric values against the standard catalogue |
| [`observability/profiler.py`](observability.md#observabilityprofilerpy---privacy-conscious-timing-profile-for-one-agent-run) | 330 | privacy-conscious timing profile for one agent run |
| [`observability/telemetry_helpers.py`](observability.md#observabilitytelemetry_helperspy---tiny-constructors-for-timestamps-and-metric-observations-and-the-shared-hash-chain-checker) | 99 | tiny constructors for timestamps and metric observations, and the shared hash-chain checker |
| [`observability/telemetry_models.py`](observability.md#observabilitytelemetry_modelspy---telemetry-event-context-actor-and-metric-contracts) | 201 | telemetry event, context, actor and metric contracts |
| [`observability/telemetry_store.py`](observability.md#observabilitytelemetry_storepy---durable-telemetry-ledger-sqlite-events-with-a-per-run-hash-chain-plus-metrics) | 632 | durable telemetry ledger: SQLite events with a per-run hash chain, plus metrics |
| [`observability/trace_context.py`](observability.md#observabilitytrace_contextpy---w3c-trace-context-ids-the-traceparent-header-and-the-process-resource) | 157 | W3C Trace Context ids, the traceparent header and the process resource |
| [`specifications/__init__.py`](specifications.md#specifications__init__py---package-marker-for-the-specification-pipeline) | 3 | package marker for the specification pipeline |
| [`specifications/documents.py`](specifications.md#specificationsdocumentspy---manifest-document-node-and-source-locator-contracts) | 126 | manifest, document, node and source-locator contracts |
| [`specifications/evidence_graph.py`](specifications.md#specificationsevidence_graphpy---source-preserving-evidence-graph-with-required-closure-and-bounded-packing) | 358 | source-preserving evidence graph with required closure and bounded packing |
| [`specifications/preprocessing.py`](specifications.md#specificationspreprocessingpy---manifest-driven-source-preserving-specification-parsing) | 392 | manifest-driven, source-preserving specification parsing |
| [`specifications/retrieval.py`](specifications.md#specificationsretrievalpy---provenance-grounded-candidate-retrieval-with-caches-and-optional-vector-backends) | 548 | provenance-grounded candidate retrieval with caches and optional vector backends |
| [`specifications/retrieval_models.py`](specifications.md#specificationsretrieval_modelspy---retrieval-document-query-candidate-and-result-contracts) | 121 | retrieval document, query, candidate and result contracts |
| [`specifications/vision.py`](specifications.md#specificationsvisionpy---vision-extraction-adapter-protocol-and-trivial-adapters) | 43 | vision-extraction adapter protocol and trivial adapters |
| [`state/__init__.py`](state.md#state__init__py---package-marker-for-durable-run-state) | 3 | package marker for durable run state |
| [`state/controller_runtime.py`](state.md#statecontroller_runtimepy---durable-facade-over-the-controller-the-graph-run-project-state-and-telemetry) | 873 | durable facade over the controller, the graph run, project state and telemetry |
| [`state/coordination_records.py`](state.md#statecoordination_recordspy---durable-run-record-and-its-integrity-hash) | 42 | durable run record and its integrity hash |
| [`state/elastic.py`](state.md#stateelasticpy---typed-elastic-node-requests-specs-spawn-records-and-the-admission-checks) | 353 | typed elastic-node requests, specs, spawn records and the admission checks |
| [`state/graph.py`](state.md#stategraphpy---deterministic-wave-scheduler-and-authoritative-typed-run-state) | 1463 | deterministic wave scheduler and authoritative typed run state |
| [`state/graph_models.py`](state.md#stategraph_modelspy---typed-node-edge-event-and-shared-state-contracts-for-the-run-graph) | 227 | typed node, edge, event and shared-state contracts for the run graph |
| [`state/harness_coordinator.py`](state.md#stateharness_coordinatorpy---run-lifecycle-plan-validation-graph-start-wave-execution-approvals-cancel-and-recovery) | 366 | run lifecycle: plan validation, graph start, wave execution, approvals, cancel and recovery |
| [`state/orchestration.py`](state.md#stateorchestrationpy---controller-state-machine-and-its-crash-safe-store) | 439 | controller state machine and its crash-safe store |
| [`state/orchestration_models.py`](state.md#stateorchestration_modelspy---controller-phase-routing-rule-and-record-contracts) | 110 | controller phase, routing-rule and record contracts |
| [`state/planning.py`](state.md#stateplanningpy---typed-plan-contracts-and-the-deterministic-plan-validator) | 496 | typed plan contracts and the deterministic plan validator |
| [`state/project_state_engine.py`](state.md#stateproject_state_enginepy---mechanical-reducer-and-token-bounded-projector-over-projectstate) | 504 | mechanical reducer and token-bounded projector over `ProjectState` |
| [`state/project_state_models.py`](state.md#stateproject_state_modelspy---bounded-hash-sealed-working-memory-contracts) | 363 | bounded, hash-sealed working-memory contracts |
| [`state/project_state_store.py`](state.md#stateproject_state_storepy---in-memory-and-file-backed-project-state-stores-with-a-hash-chained-audit-trail) | 252 | in-memory and file-backed project-state stores with a hash-chained audit trail |
| [`state/run_state_store.py`](state.md#staterun_state_storepy---crash-safe-run-persistence-as-a-fixed-state-snapshot-plus-a-growing-state-sidecar) | 435 | crash-safe run persistence as a fixed-state snapshot plus a growing-state sidecar |
| [`state/shared_state.py`](state.md#stateshared_statepy---typed-lateral-state-payloads-exact-routing-references-and-provenance-records) | 240 | typed lateral-state payloads, exact routing references and provenance records |
| [`tools/__init__.py`](tools.md#tools__init__py---package-marker-for-governed-tool-execution) | 3 | package marker for governed tool execution |
| [`tools/approvals.py`](tools.md#toolsapprovalspy---typed-approval-gates-for-state-changing-actions) | 178 | typed approval gates for state-changing actions |
| [`tools/artifacts.py`](tools.md#toolsartifactspy---content-addressed-artifact-store-with-immutable-write-attribution) | 265 | content-addressed artifact store with immutable write attribution |
| [`tools/core/__init__.py`](tools.md#toolscore__init__py---public-surface-of-the-portable-core-tools) | 25 | public surface of the portable core tools |
| [`tools/core/definitions.py`](tools.md#toolscoredefinitionspy---typed-declarations-of-the-governed-core-tool-set) | 356 | typed declarations of the governed core tool set |
| [`tools/core/helpers.py`](tools.md#toolscorehelperspy---private-validation-http-and-isolated-regex-helpers-behind-the-core-tools) | 581 | private validation, HTTP and isolated-regex helpers behind the core tools |
| [`tools/core/regex_worker.py`](tools.md#toolscoreregex_workerpy---standalone-regex-worker-run-in-an-isolated-interpreter) | 38 | standalone regex worker run in an isolated interpreter |
| [`tools/core/services.py`](tools.md#toolscoreservicespy---portable-governed-tools-dispatcher-services-and-web-search) | 488 | portable governed tools: dispatcher, services and web search |
| [`tools/delegation.py`](tools.md#toolsdelegationpy---delegated-sub-runs-on-isolated-git-worktrees) | 128 | delegated sub-runs on isolated git worktrees |
| [`tools/elastic_requests.py`](tools.md#toolselastic_requestspy---agent-side-queue-and-tool-executor-for-elastic-spawn-requests) | 160 | agent-side queue and tool executor for elastic spawn requests |
| [`tools/policy.py`](tools.md#toolspolicypy---deny-by-default-capability-policy) | 176 | deny-by-default capability policy |
| [`tools/registry.py`](tools.md#toolsregistrypy---capability-bound-harness-tool-registry-and-its-baseagent-executor) | 327 | capability-bound harness tool registry and its BaseAgent executor |
| [`tools/sandbox.py`](tools.md#toolssandboxpy---pluggable-execution-backends-for-registered-command-templates) | 322 | pluggable execution backends for registered command templates |
| [`tools/sandbox_models.py`](tools.md#toolssandbox_modelspy---typed-configuration-for-sandbox-backends) | 59 | typed configuration for sandbox backends |
| [`tools/supervisor.py`](tools.md#toolssupervisorpy---registered-command-execution-with-timeout-bounded-output-and-process-tree-kill) | 491 | registered-command execution with timeout, bounded output and process-tree kill |
| [`tools/task_models.py`](tools.md#toolstask_modelspy---records-for-background-tasks) | 43 | records for background tasks |
| [`tools/tasks.py`](tools.md#toolstaskspy---background-task-lifecycle-start-poll-stop) | 258 | background task lifecycle: start, poll, stop |
| [`tools/tools.py`](tools.md#toolstoolspy---the-toolexecutor-protocol-and-two-deterministic-test-executors) | 73 | the ToolExecutor protocol and two deterministic test executors |
| [`tools/worktree_models.py`](tools.md#toolsworktree_modelspy---record-for-an-agents-git-worktree) | 20 | record for an agent's git worktree |
| [`tools/worktrees.py`](tools.md#toolsworktreespy---git-worktree-isolation-for-concurrent-agents) | 181 | git worktree isolation for concurrent agents |

*Coverage: 131 files, 416 classes and 1327 functions (including methods and nested closures), each with a written description.*

## Files outside the package

| Path | What it is |
|---|---|
| `pyproject.toml` | Package metadata (`nailong-agent-sdk` 0.1.0, Python >= 3.12), hatchling build, console script `nailong-agent-sdk-dev`, optional extras `langgraph`, `langchain`, `jev`, `redis-cache`, `interop`, Ruff and pytest settings (strict markers; `docker` marks the tests that need a Docker daemon) |
| `examples/` | Eight runnable scripts using a scripted model: custom verification and profiling, developer tools, durable runtime with PASK, elastic exploration, framework interoperability, governed core tools, grounded retrieval, orchestration |
| `scripts/run_pckp_benchmark.py`, `benchmarks/` | Runs the frozen PCKP comparison cases in `pckp_cases.json` and writes `results/pckp-report.json` |
| `tests/` | The pytest suite (`python -m pytest tests`, more than 1,000 tests): unit and integration tests per package folder, cross-module, concurrency and long-run checks in `tests/links/`, and a spawned MCP server and a stub evaluator in `tests/support/`. No test calls a real model provider. `python -m pytest -m "not docker"` skips the live Docker sandbox tests, which also skip themselves when no daemon answers; Windows-only and POSIX-only tests skip on the other platform |
| `.github/workflows/ci.yml` | Ruff, the suite on ubuntu and windows for Python 3.12 and 3.13, and the Docker sandbox tests against a real daemon |
| `test/` | Outputs of real-workload validation scenarios run against a live OpenAI-compatible provider (generated documents and a generated game). It is not a pytest suite |
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
| `run_agent_task` | The files an agent wrote can be listed and read (`list_task_files`, `read_task_file`: bounded pages as UTF-8 or base64; paths outside the workspace, `.agent-*` state and credential patterns are refused); an approval can be decided during the run (`permissions.approval_mode` `wait`, `list_task_approvals`, `decide_task_approval`; the request and the decision are telemetry events); and `cancel_agent_task` interrupts the model call, tool call, hook, gate or approval wait in flight, because the cancellation token is raced against every awaited operation and the model request and the network tools run on detached daemon threads that the run does not wait for. The server registers 53 tools |
| Packaging | `jsonschema-specifications` and `referencing` are declared dependencies; `# noqa` directives for rules that are not enabled were removed |
| Trace context | A run has a W3C trace id and every profile span a random 16-hex span id (`observability/trace_context.py`); `TelemetryContext` refuses a `trace_id`, `span_id` or `parent_span_id` that is not W3C-shaped, naming the field (events already stored with another shape stay readable); `BaseAgent` stamps the trace id and the run's root span id on every event of a run and continues a caller's trace (`telemetry_context`, or `TaskRunOptions.traceparent`); the `agent.profile-completed` event now persists the trace id, the process resource and the most recent 512 spans, so a trace can be rebuilt from the ledger alone; model-turn and tool spans carry `gen_ai.*` attributes (operation, provider, model, usage, tool name and call id) and receive their own `traceparent` (`ModelContext.trace_parent`, `ToolInvocationContext.trace_parent`); an endpoint sends it to the provider only with `propagate_trace_context`; metric definitions declare an OpenTelemetry-style instrument kind. A tool-call id or name longer than 4,096 characters no longer fails the run when its span is opened (attribute text is cut to 256 characters). Cost: about 3 microseconds more per context that carries trace ids and about 3 ms once per 80-span run, against about 1.7 ms for each emitted event |

### Still open

1. **[inspected] Retention covers telemetry, audit transcripts and tool-result journals of
   finished runs and nothing else.** Project states, graph runs, controllers,
   orchestrations, workspaces and exported reports are kept for ever, as are journal files
   recorded without a run id. A run that receives an event in the instant between the last
   check and its deletion loses its history (the telemetry sequence check before and at the
   delete catches every other revival), so `older_than_seconds` should be far longer than
   any task's resubmission delay. A tombstone log has no outside anchor: removing its last
   tombstone cannot be detected. The telemetry database reuses the pages of pruned rows but
   keeps its file size until someone runs a `VACUUM`.
2. **[inspected] A run's persistence cost grows with its size.** Each `RunStateStore.save` still
   rewrites the whole fixed-state snapshot (nodes, edges, statuses, results) and hashes the
   whole run, so a save costs time proportional to the run and the total to its square, with
   a small constant: a 400-node dependency chain takes about 4 s, 600 nodes about 7 s and
   1,000 nodes about 17 s on the development machine, about half of it inside
   `RunStateStore.save` (two fsyncs and the snapshot write per save). Making a save
   proportional to what changed needs a delta format for those collections and a loader for
   the existing snapshots.
3. **[inspected] MCP.** The HTTP service's token identifies the host, not an approver:
   whoever holds it can record human decisions and decide task approvals. A second
   credential for approvers needs the TypeScript backend to hold two secrets, and waits for
   that work.
4. **[inspected] Platform coverage beyond Windows and Ubuntu.** The suite passes on Windows
   (Python 3.13) and on Ubuntu 24.04 under WSL2 (Python 3.12), and
   `.github/workflows/ci.yml` runs Ruff and the suite on ubuntu and windows for Python 3.12 and
   3.13. That workflow and the live Docker sandbox tests
   (`tests/tools/test_docker_sandbox_live.py`, skipped without a daemon) have not been executed
   yet. macOS, a run root shared between Windows and WSL processes, and Docker engines other
   than Docker Desktop's are untried.
5. **[inspected] Elastic nodes.** A join re-runs the requester's definition with the
   findings and the requester's `handoff` notes in its task text, not the original
   conversation, and a node replayed after a crash starts its task again from the
   beginning, which is why only bindings declared idempotent are replayed.
6. **[inspected] `run_agent_task` limits.** Python hooks, verification gates and sub-agents
   are available to the Python API (`AgentTaskRunner.run`) but not over MCP, where the
   caller has only the options `TaskRunOptions` names. A cancelled model request on the
   default (non-streaming) transport is abandoned, not aborted: the provider may finish and
   bill it, and the thread ends when it answers or its timeout passes.
7. **[inspected] Trace context reaches one agent run at a time.** `GraphAgentExecutor`,
   `Orchestrator` and the LangGraph node build their agents without a `telemetry_context`,
   so each node's run has its own trace; the graph's node execution context does not
   carry the graph run id, which a shared trace needs. Spans are stored in the
   `agent.profile-completed` event (the most recent 512) and nothing exports them to an
   OTLP endpoint; `tracestate` and baggage are not carried. A trace id a caller chose
   before ids were checked is still read and verified from the ledger, but cannot be
   given to a new `TelemetryContext`.
