# nailong-agent-sdk - workflow and function reference

This is a complete, per-file description of `src/nailong_agent_sdk`: what every module is
for, what each class and function does, and where it sits in the end-to-end workflow. It
covers **123 files, 397 classes and 1,010 functions** (methods and nested closures included).
The structure and signatures were extracted from the source with Python's `ast` module and
every item carries a hand-written description; a mechanical check fails the build if any
class or function lacks one.

**How to read it.** Start with the workflows below, then open the page for the folder you are
changing. Each file entry has a one-line role, a *Role in the workflow* paragraph, a *Contents*
list (every class, method and function with who calls it), and, where a file implements an
algorithm or has a known limitation, an *Algorithms & invariants* note. "Called by" lists are
static: they are shown only when a name is unique in the package, and protocol hooks, validators
and MCP tools have none.

## Layers

Folder-level imports, computed from the source (a folder is listed under what it imports):

```
foundations      imports nothing in the package: contracts, typed errors, PCKP optimiser,
                 atomic file IO, cycle finder
observability    foundations                      telemetry ledger, audit logs, metrics, profiler
specifications   foundations, observability       manifest -> trees -> Gate 1 -> lock -> retrieval
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
involved import in a safe sequence, and every import starts at the root `__init__.py`, so that
file's import order is the order that gets exercised.

## Workflow 1 - one agent task (`BaseAgent.run`)

1. **Validate and assemble.** The task is checked against the definition's input schema and the
   immutable prompt is built once (`memory/context.py`): identity, instructions, tool
   definitions, output schema, scoped task, skills.
2. **Open per-run state.** A fresh episode graph and episode store; the project state for
   `scope.boundaries["project_id"]` (else the task id); a profiler root span; telemetry and
   audit entries `run-started`, `task-received`, `context-assembled`, `state-loaded`.
3. **Loop `iteration = 1..max_iterations`.** Stop if the run deadline passed (FAILED) or the
   caller cancelled (CANCELLED).
4. **Project context** (`ContextProjector.project`): compact episodes over budget, keep the
   newest observations that fit, and build the references of compacted episodes. Emit the
   `context-projected` event and the context metrics.
5. **Three guards** end the run BLOCKED before any model call: `CONTEXT_DEADLOCK` (nothing can
   be compacted), `PROJECT_STATE_BUDGET_EXCEEDED` (the mandatory state exceeds its 2,000-token
   view) and `CONTEXT_BUDGET_EXCEEDED` (prompt plus state exceed the context budget).
6. **Call the model** under the per-turn watchdog (streaming if a listener is set and the
   adapter supports it). A transient provider error (429, 5xx, connection) becomes an
   `agent-error` observation and costs one iteration; any other error ends FAILED at once.
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
  (one line per retained episode); `compacted_episodes` (one-line references with a status and a
  `handle_id` for episodes whose content was dropped, within a 1,500-token allowance, newest
  first); and `omitted_compacted_count`.
* **continuation** - for providers that issued tool calls, the raw assistant tool calls and the
  matching bounded tool results, only for the latest batch.

Defaults: context budget 12,000 estimated tokens, episode budget 6,000, tool-result preview
1,024 characters, reference budget 1,500, reference summary 160 characters, project-state view 2,000.
Estimates are characters divided by four. The model never sees raw tool output beyond the
preview: the full result is journalled and the model can fetch it with the `get_tool_result`
tool and a handle id.

**Compaction.** Episodes are OPEN, CLOSED or COMPACTED. When closed episodes exceed the episode
budget, the default strategy (exact PCKP, `foundations/optimization`) keeps a mandatory set
(the latest batch, active and open episodes, action episodes whose manifest is incomplete, and
their prerequisites) and chooses the rest by solving a dependency-closed knapsack exactly
(tree DP for rooted forests up to a 50,000 budget, otherwise branch and bound with a cap that
degrades to BEST_EFFORT). Compacted episodes keep only a tombstone plus their summary; the
reference list keeps them findable.

## Workflow 3 - how a tool call is governed

`BaseAgent._execute_tool_call` rejects an undeclared tool, a missing executor and arguments that
violate the declared JSON schema (each ends the run FAILED), then runs pre-tool hooks (a denial
ends BLOCKED), the host's `ToolExecutor` and post-tool hooks, all under the tool watchdog. With
the SDK's `HarnessToolExecutor` (`tools/registry.py`): the tool must be in the closed
`HarnessToolRegistry`; `CapabilityPolicy` checks sensitive paths first, then the role's
capability grant, path containment and any typed approval (a pending approval returns a
`blocked` result); the handler then runs a core tool (`tools/core`), a supervised process
(`tools/supervisor.py`) or a sandbox backend. The result is stored in the journal, becomes an
episode and an observation with a handle, is reduced into project state, and is written to the
audit log and telemetry.

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
the controller to bounded repair, then escalation.

## Workflow 5 - specification to retrievable, versioned requirements

`SpecificationPreprocessor` parses the manifest into source-preserving document trees; a host
builds a `UnifiedSpecification`; `SpecificationGate` runs the deterministic Gate 1 checks and
the designer soft-locks; `SpecificationVersionService` writes a Git annotated tag plus a
structural snapshot and recommends the next semantic-version bump from the structural diff;
`GroundedRetrievalService` ranks references (lexical or Qdrant) and re-verifies each against
local trees; `StructuralContextSelector` selects the required closure and packs optional
evidence with the exact PCKP solver.

## Workflow 6 - evidence and integrity

| Evidence | Where it lives under the run root | Integrity mechanism |
|---|---|---|
| Telemetry events and metrics | `.agent-telemetry/` (SQLite, WAL) | per-run sequence plus previous-event hash |
| Audit transcript | `.agent-audit-logs/` (JSONL per run, Markdown on request) | previous-entry hash, fsync per entry, cross-process lock |
| Run profile | inside the result and telemetry | hash-sealed at `finish_run` |
| Full tool results | `.agent-tool-results/` | content hash in the handle |
| Episode records | `.agent-memory/` | per-record state |
| Project state | `.agent-project-state/` | state hash plus hash-chained event file per revision |
| Graph runs | `.agent-runs/` | `run_hash` plus snapshot-named history-prefix hash |
| Controllers | `.agent-controllers/` | event-prefix hash named by the snapshot |
| Orchestrations | `.agent-orchestrations/` | atomic replace, policy immutability |
| Specification locks | `.agent-git-locks/` | Git tag plus content-addressed snapshot |

## Where do I change...?

| I want to | Start here |
|---|---|
| add a core tool | `tools/core/definitions.py` (schema), `tools/core/services.py` (dispatcher), `tools/registry.py` (capability and side-effect class) |
| gate a tool differently | `tools/policy.py` (`CapabilityPolicy`), `tools/approvals.py` |
| add a model provider | implement `AgentModel` (`agent/model.py`); use `agent/openai_compatible/chat.py` as the template; wrap several in `FailoverAgentModel` |
| change what the model sees | `agent/openai_compatible/chat.py::_chat_payload`, `memory/context_projection.py`, `foundations/contracts.py` (`ModelContext` fields live in `agent/model.py`) |
| change compaction | `memory/episode_store.py` (strategies), `memory/episode_scoring.py`, `foundations/optimization/` (solvers) |
| add a verification gate | register on a `VerificationGateRegistry` (`agent/verification.py`) |
| add a project-state transition | `state/project_state_models.py` (`StateTransitionKind`), `ProjectStateReducer.apply` in `state/project_state_engine.py` |
| add a graph node kind or executor | `state/graph_models.py` (`GraphNodeKind`), the executor map passed to `execute_graph` |
| add a metric | `observability/metric_definitions.py` and the emit site (`record_metric_value`) |
| add an MCP tool | the matching `mcp/*_tools.py` `register_*` function, using `ctx` |
| support a new specification format | `specifications/documents.py` (`DocumentFormat`) and `SpecificationPreprocessor._parse` |
| debug a run | MCP `get_telemetry_events` / `get_audit_log` / `render_audit_transcript`, or `nailong-agent-sdk-dev inspect-run <root> <run-id>` |

## Glossary

* **Episode** - one tool call and its result as a unit of memory; *exploratory* (read-only) or
  *action* (consumes other episodes). **Observation** - the model-facing message about a call.
* **Projection** - the bounded view of memory and state built for one turn. **Compaction** -
  dropping an episode's content while keeping a tombstone. **Reference** - the one-line, handle-bearing
  residue of a compacted episode. **Handle** - an opaque id of a journalled full result.
* **Manifest** - the declared inputs and outputs of an EDA action; `requires_manifest` tools
  cannot be compacted until it is complete.
* **Wave** - the set of graph nodes that are runnable together. **Controller** - the vertical
  state machine (plan, approval, dispatch, repair, escalation). **Harness** - the deterministic
  runtime around the model: policy, journal, state, audit.
* **Gate 1** - the deterministic specification check. **Soft-lock** - designer approval of a
  Gate 1 result. **Lock** - the Git tag and snapshot that freeze a specification version.
* **Discovery** - a closed, source-backed lateral finding published into graph state.
* **PCKP** - precedence-constrained knapsack: choose items under a budget when picking an item
  requires its prerequisites.

## File index

| File | Lines | Role |
|---|---:|---|
| [`__init__.py`](root.md#__init__py---the-public-api-surface-re-exports-only) | 744 | the public API surface (re-exports only) |
| [`agent/__init__.py`](agent.md#agent__init__py---package-marker-for-agent-execution) | 3 | package marker for agent execution |
| [`agent/base_agent/__init__.py`](agent.md#agentbase_agent__init__py---public-surface-of-the-baseagent-runtime-package) | 28 | public surface of the BaseAgent runtime package |
| [`agent/base_agent/agent.py`](agent.md#agentbase_agentagentpy---the-single-task-agent-loop) | 1918 | the single-task agent loop |
| [`agent/base_agent/types.py`](agent.md#agentbase_agenttypespy---small-value-types-used-by-the-baseagent-loop) | 74 | small value types used by the BaseAgent loop |
| [`agent/graph_agent_executor.py`](agent.md#agentgraph_agent_executorpy---runs-registered-baseagent-bindings-as-graph-nodes) | 195 | runs registered BaseAgent bindings as graph nodes |
| [`agent/model.py`](agent.md#agentmodelpy---provider-neutral-model-contract-streaming-events-and-failover) | 326 | provider-neutral model contract, streaming events and failover |
| [`agent/openai_compatible/__init__.py`](agent.md#agentopenai_compatible__init__py---public-surface-of-the-openai-compatible-adapters) | 44 | public surface of the OpenAI-compatible adapters |
| [`agent/openai_compatible/chat.py`](agent.md#agentopenai_compatiblechatpy---chat-completions-adapter-for-the-agent-model-contract) | 576 | Chat Completions adapter for the agent model contract |
| [`agent/openai_compatible/embeddings.py`](agent.md#agentopenai_compatibleembeddingspy---synchronous-embedding-provider-for-retrieval-indexes) | 68 | synchronous embedding provider for retrieval indexes |
| [`agent/openai_compatible/semantic_gap.py`](agent.md#agentopenai_compatiblesemantic_gappy---model-proposed-semantic-findings-for-gate-1) | 232 | model-proposed semantic findings for Gate 1 |
| [`agent/openai_compatible/transport.py`](agent.md#agentopenai_compatibletransportpy---json-http-transports-and-endpoint-configuration) | 379 | JSON HTTP transports and endpoint configuration |
| [`agent/openai_compatible/vision.py`](agent.md#agentopenai_compatiblevisionpy---image-proposals-through-a-verified-byte-loader) | 162 | image proposals through a verified byte loader |
| [`agent/orchestrator/__init__.py`](agent.md#agentorchestrator__init__py---public-surface-of-the-orchestrator-package) | 31 | public surface of the orchestrator package |
| [`agent/orchestrator/models.py`](agent.md#agentorchestratormodelspy---user-owned-orchestration-policy-request-and-record-contracts) | 170 | user-owned orchestration policy, request and record contracts |
| [`agent/orchestrator/orchestrator.py`](agent.md#agentorchestratororchestratorpy---deterministic-composition-of-policy-plan-controller-and-graph-execution) | 637 | deterministic composition of policy, plan, controller and graph execution |
| [`agent/orchestrator/state_store.py`](agent.md#agentorchestratorstate_storepy---atomic-local-persistence-of-orchestration-records-and-policies) | 57 | atomic local persistence of orchestration records and policies |
| [`agent/runtime.py`](agent.md#agentruntimepy---composition-root-for-the-durable-stores-around-an-agent) | 100 | composition root for the durable stores around an agent |
| [`agent/specialists.py`](agent.md#agentspecialistspy---declarative-agent-definitions-for-the-frameworks-roles) | 178 | declarative agent definitions for the framework's roles |
| [`agent/verification.py`](agent.md#agentverificationpy---host-registered-bounded-output-acceptance-gates) | 223 | host-registered, bounded output-acceptance gates |
| [`developer_tools/__init__.py`](developer_tools.md#developer_tools__init__py---public-surface-of-the-developer-utilities) | 33 | public surface of the developer utilities |
| [`developer_tools/catalog.py`](developer_tools.md#developer_toolscatalogpy---public-api-catalogue-generation) | 87 | public API catalogue generation |
| [`developer_tools/cli.py`](developer_tools.md#developer_toolsclipy---the-nailong-agent-sdk-dev-command-line) | 98 | the `nailong-agent-sdk-dev` command line |
| [`developer_tools/inspect.py`](developer_tools.md#developer_toolsinspectpy---read-only-run-evidence-inspection) | 84 | read-only run evidence inspection |
| [`developer_tools/quality.py`](developer_tools.md#developer_toolsqualitypy---closed-ruff-quality-check) | 85 | closed Ruff quality check |
| [`developer_tools/validate.py`](developer_tools.md#developer_toolsvalidatepy---contract-file-validation) | 92 | contract-file validation |
| [`foundations/__init__.py`](foundations.md#foundations__init__py---package-marker-for-the-dependency-free-layer) | 4 | package marker for the dependency-free layer |
| [`foundations/atomic_io.py`](foundations.md#foundationsatomic_iopy---crash-safe-publication-of-a-prepared-temporary-file) | 29 | crash-safe publication of a prepared temporary file |
| [`foundations/benchmarks.py`](foundations.md#foundationsbenchmarkspy---reproducible-exact-vs-greedy-pckp-benchmark-harness) | 87 | reproducible exact-vs-greedy PCKP benchmark harness |
| [`foundations/contracts.py`](foundations.md#foundationscontractspy---the-serializable-baseagent-contract-definitions-tasks-turns-results-context-types) | 481 | the serializable BaseAgent contract: definitions, tasks, turns, results, context types |
| [`foundations/dependency_graph.py`](foundations.md#foundationsdependency_graphpy---deterministic-cycle-and-blast-radius-traversal-over-dependent-prerequisite-edges) | 100 | deterministic cycle and blast-radius traversal over (dependent, prerequisite) edges |
| [`foundations/errors.py`](foundations.md#foundationserrorspy---typed-sdk-errors-and-secretreasoning-redaction-for-durable-records) | 210 | typed SDK errors and secret/reasoning redaction for durable records |
| [`foundations/logging.py`](foundations.md#foundationsloggingpy---opt-in-stdlib-logging-namespace-for-the-few-paths-outside-structured-telemetry) | 25 | opt-in stdlib logging namespace for the few paths outside structured telemetry |
| [`foundations/optimization/__init__.py`](foundations.md#foundationsoptimization__init__py---re-exports-the-pckp-models-and-solvers) | 17 | re-exports the PCKP models and solvers |
| [`foundations/optimization/models.py`](foundations.md#foundationsoptimizationmodelspy---pckp-problem-item-and-solution-contracts) | 98 | PCKP problem, item and solution contracts |
| [`foundations/optimization/solvers.py`](foundations.md#foundationsoptimizationsolverspy---exact-tree-dp--branch-and-bound-and-greedy-pckp-solvers) | 445 | exact (tree DP / branch-and-bound) and greedy PCKP solvers |
| [`integrations/__init__.py`](integrations.md#integrations__init__py---public-surface-of-the-integrations-package) | 113 | public surface of the integrations package |
| [`integrations/_utils.py`](integrations.md#integrations_utilspy---private-sanitiser-digest-and-optional-import-helpers) | 129 | private sanitiser, digest and optional-import helpers |
| [`integrations/contracts.py`](integrations.md#integrationscontractspy---framework-neutral-interop-contracts) | 147 | framework-neutral interop contracts |
| [`integrations/jev/__init__.py`](integrations.md#integrationsjev__init__py---public-surface-of-the-jev-package) | 59 | public surface of the Jev package |
| [`integrations/jev/advisory.py`](integrations.md#integrationsjevadvisorypy---verification-gate-that-adds-a-non-authoritative-jev-signal) | 116 | verification gate that adds a non-authoritative Jev signal |
| [`integrations/jev/architecture.py`](integrations.md#integrationsjevarchitecturepy---monotonic-single-to-multi-routing-advice) | 151 | monotonic single-to-multi routing advice |
| [`integrations/jev/decision.py`](integrations.md#integrationsjevdecisionpy---optional-typesafe-jev-evaluator) | 296 | optional TypeSafe Jev evaluator |
| [`integrations/jev/exploration.py`](integrations.md#integrationsjevexplorationpy---optional-prioritisation-of-an-already-approved-candidate-set) | 139 | optional prioritisation of an already-approved candidate set |
| [`integrations/jev/models.py`](integrations.md#integrationsjevmodelspy---jev-question-answer-request-result-and-receipt-contracts) | 163 | Jev question, answer, request, result and receipt contracts |
| [`integrations/jev/receipts.py`](integrations.md#integrationsjevreceiptspy---receipt-sinks-for-jev-evaluations) | 57 | receipt sinks for Jev evaluations |
| [`integrations/langchain.py`](integrations.md#integrationslangchainpy---optional-langchain-adapters) | 226 | optional LangChain adapters |
| [`integrations/langgraph.py`](integrations.md#integrationslanggraphpy---optional-langgraph-adapters) | 298 | optional LangGraph adapters |
| [`integrations/receipts.py`](integrations.md#integrationsreceiptspy---receipt-sinks-for-external-operations) | 65 | receipt sinks for external operations |
| [`mcp/__init__.py`](mcp.md#mcp__init__py---package-marker-for-the-mcp-surface) | 3 | package marker for the MCP surface |
| [`mcp/_shared.py`](mcp.md#mcp_sharedpy---shared-context-and-error-formatting-for-every-tool-group) | 77 | shared context and error formatting for every tool group |
| [`mcp/agent_tools.py`](mcp.md#mcpagent_toolspy---mcp-tools-for-agent-contract-validation-context-assembly-and-scripted-runs) | 134 | MCP tools for agent contract validation, context assembly and scripted runs |
| [`mcp/client.py`](mcp.md#mcpclientpy---outbound-mcp-client-lifecycle) | 290 | outbound MCP client lifecycle |
| [`mcp/client_bridge.py`](mcp.md#mcpclient_bridgepy---opt-in-bridge-from-external-mcp-tools-to-governed-harness-tools) | 55 | opt-in bridge from external MCP tools to governed harness tools |
| [`mcp/client_types.py`](mcp.md#mcpclient_typespy---typed-configuration-and-status-for-the-outbound-mcp-client) | 68 | typed configuration and status for the outbound MCP client |
| [`mcp/controller_tools.py`](mcp.md#mcpcontroller_toolspy---mcp-tools-for-the-deterministic-controller) | 217 | MCP tools for the deterministic controller |
| [`mcp/git_tools.py`](mcp.md#mcpgit_toolspy---mcp-tools-for-local-git-inspection-and-specification-version-locks) | 109 | MCP tools for local Git inspection and specification version locks |
| [`mcp/orchestration_tools.py`](mcp.md#mcporchestration_toolspy---mcp-tools-for-plan-validation-graph-runs-and-orchestration-compilation) | 115 | MCP tools for plan validation, graph runs and orchestration compilation |
| [`mcp/project_state_tools.py`](mcp.md#mcpproject_state_toolspy---mcp-tools-for-project-working-memory) | 126 | MCP tools for project working memory |
| [`mcp/run_tools.py`](mcp.md#mcprun_toolspy---mcp-tools-for-graph-run-state-cancellation-approvals-and-resumption) | 59 | MCP tools for graph run state, cancellation, approvals and resumption |
| [`mcp/server.py`](mcp.md#mcpserverpy---mcp-server-factory-and-loopback-entry-point) | 131 | MCP server factory and loopback entry point |
| [`mcp/specification_tools.py`](mcp.md#mcpspecification_toolspy---mcp-tools-for-the-specification-pipeline-and-gate-1) | 132 | MCP tools for the specification pipeline and Gate 1 |
| [`mcp/telemetry_tools.py`](mcp.md#mcptelemetry_toolspy---mcp-tools-for-telemetry-audit-logs-and-metrics) | 145 | MCP tools for telemetry, audit logs and metrics |
| [`memory/__init__.py`](memory.md#memory__init__py---package-marker-for-episode-memory-and-context-assembly) | 3 | package marker for episode memory and context assembly |
| [`memory/context.py`](memory.md#memorycontextpy---builds-the-fixed-initial-prompt-for-a-run) | 51 | builds the fixed initial prompt for a run |
| [`memory/context_projection.py`](memory.md#memorycontext_projectionpy---per-turn-bounded-context-projection-and-the-tool-result-journal) | 327 | per-turn bounded context projection and the tool-result journal |
| [`memory/context_selection.py`](memory.md#memorycontext_selectionpy---pre-run-specification-selection-by-design-stage-eda) | 170 | pre-run specification selection by design stage (EDA) |
| [`memory/episode_models.py`](memory.md#memoryepisode_modelspy---episode-records-compaction-policy-and-the-retention-contracts) | 176 | episode records, compaction policy and the retention contracts |
| [`memory/episode_scoring.py`](memory.md#memoryepisode_scoringpy---deterministic-lexical-relevance-and-cost-helpers-for-retention) | 54 | deterministic lexical relevance and cost helpers for retention |
| [`memory/episode_store.py`](memory.md#memoryepisode_storepy---episode-lifecycle-and-the-three-deterministic-compaction-strategies) | 857 | episode lifecycle and the three deterministic compaction strategies |
| [`memory/episodes.py`](memory.md#memoryepisodespy---task-scoped-episode-graph-of-one-line-summaries) | 76 | task-scoped episode graph of one-line summaries |
| [`observability/__init__.py`](observability.md#observability__init__py---package-marker-for-the-observability-layer) | 3 | package marker for the observability layer |
| [`observability/audit_log.py`](observability.md#observabilityaudit_logpy---append-only-hash-chained-per-run-jsonl-audit-transcript) | 372 | append-only, hash-chained, per-run JSONL audit transcript |
| [`observability/metric_definitions.py`](observability.md#observabilitymetric_definitionspy---the-sdks-standard-metric-catalogue) | 481 | the SDK's standard metric catalogue |
| [`observability/metrics.py`](observability.md#observabilitymetricspy---record-metric-values-against-the-standard-catalogue) | 131 | record metric values against the standard catalogue |
| [`observability/profiler.py`](observability.md#observabilityprofilerpy---privacy-conscious-timing-profile-for-one-agent-run) | 318 | privacy-conscious timing profile for one agent run |
| [`observability/telemetry_helpers.py`](observability.md#observabilitytelemetry_helperspy---tiny-constructors-for-timestamps-and-metric-observations-and-the-shared-hash-chain-checker) | 99 | tiny constructors for timestamps and metric observations, and the shared hash-chain checker |
| [`observability/telemetry_models.py`](observability.md#observabilitytelemetry_modelspy---telemetry-event-context-actor-and-metric-contracts) | 129 | telemetry event, context, actor and metric contracts |
| [`observability/telemetry_store.py`](observability.md#observabilitytelemetry_storepy---durable-telemetry-ledger-sqlite-events-with-a-per-run-hash-chain-plus-metrics) | 466 | durable telemetry ledger: SQLite events with a per-run hash chain, plus metrics |
| [`specifications/__init__.py`](specifications.md#specifications__init__py---package-marker-for-the-specification-pipeline) | 3 | package marker for the specification pipeline |
| [`specifications/documents.py`](specifications.md#specificationsdocumentspy---manifest-document-node-and-source-locator-contracts) | 128 | manifest, document, node and source-locator contracts |
| [`specifications/evidence_graph.py`](specifications.md#specificationsevidence_graphpy---source-preserving-evidence-graph-with-required-closure-and-bounded-packing) | 349 | source-preserving evidence graph with required closure and bounded packing |
| [`specifications/gate.py`](specifications.md#specificationsgatepy---gate-1-deterministic-checks-soft-lock-decision-and-artifact-persistence) | 369 | Gate 1 deterministic checks, soft-lock decision and artifact persistence |
| [`specifications/gate_models.py`](specifications.md#specificationsgate_modelspy---gate-1-requirement-gap-and-version-metadata-contracts) | 167 | Gate 1 requirement, gap and version-metadata contracts |
| [`specifications/git_models.py`](specifications.md#specificationsgit_modelspy---git-backed-version-and-variant-worktree-contracts) | 120 | Git-backed version and variant-worktree contracts |
| [`specifications/git_versioning.py`](specifications.md#specificationsgit_versioningpy---local-only-git-adapter-and-the-specification-version-lock-service) | 464 | local-only Git adapter and the specification version-lock service |
| [`specifications/preprocessing.py`](specifications.md#specificationspreprocessingpy---manifest-driven-source-preserving-specification-parsing) | 386 | manifest-driven, source-preserving specification parsing |
| [`specifications/retrieval.py`](specifications.md#specificationsretrievalpy---provenance-grounded-candidate-retrieval-with-caches-and-optional-vector-backends) | 485 | provenance-grounded candidate retrieval with caches and optional vector backends |
| [`specifications/retrieval_models.py`](specifications.md#specificationsretrieval_modelspy---retrieval-document-query-candidate-and-result-contracts) | 130 | retrieval document, query, candidate and result contracts |
| [`specifications/vision.py`](specifications.md#specificationsvisionpy---vision-extraction-adapter-protocol-and-trivial-adapters) | 43 | vision-extraction adapter protocol and trivial adapters |
| [`state/__init__.py`](state.md#state__init__py---package-marker-for-durable-run-state) | 3 | package marker for durable run state |
| [`state/controller_runtime.py`](state.md#statecontroller_runtimepy---durable-facade-over-the-controller-the-graph-run-project-state-and-telemetry) | 488 | durable facade over the controller, the graph run, project state and telemetry |
| [`state/coordination_records.py`](state.md#statecoordination_recordspy---durable-run-record-and-its-integrity-hash) | 45 | durable run record and its integrity hash |
| [`state/graph.py`](state.md#stategraphpy---deterministic-wave-scheduler-and-authoritative-typed-run-state) | 708 | deterministic wave scheduler and authoritative typed run state |
| [`state/graph_models.py`](state.md#stategraph_modelspy---typed-node-edge-event-and-shared-state-contracts-for-the-run-graph) | 176 | typed node, edge, event and shared-state contracts for the run graph |
| [`state/harness_coordinator.py`](state.md#stateharness_coordinatorpy---run-lifecycle-plan-validation-graph-start-wave-execution-approvals-cancel-and-recovery) | 291 | run lifecycle: plan validation, graph start, wave execution, approvals, cancel and recovery |
| [`state/orchestration.py`](state.md#stateorchestrationpy---controller-state-machine-and-its-crash-safe-store) | 363 | controller state machine and its crash-safe store |
| [`state/orchestration_models.py`](state.md#stateorchestration_modelspy---controller-phase-routing-rule-and-record-contracts) | 93 | controller phase, routing-rule and record contracts |
| [`state/planning.py`](state.md#stateplanningpy---typed-plan-contracts-and-the-deterministic-plan-validator) | 358 | typed plan contracts and the deterministic plan validator |
| [`state/project_state_engine.py`](state.md#stateproject_state_enginepy---mechanical-reducer-and-token-bounded-projector-over-projectstate) | 415 | mechanical reducer and token-bounded projector over `ProjectState` |
| [`state/project_state_models.py`](state.md#stateproject_state_modelspy---bounded-hash-sealed-working-memory-contracts) | 354 | bounded, hash-sealed working-memory contracts |
| [`state/project_state_store.py`](state.md#stateproject_state_storepy---in-memory-and-file-backed-project-state-stores-with-a-hash-chained-audit-trail) | 204 | in-memory and file-backed project-state stores with a hash-chained audit trail |
| [`state/run_state_store.py`](state.md#staterun_state_storepy---crash-safe-run-persistence-as-a-fixed-state-snapshot-plus-a-growing-state-sidecar) | 377 | crash-safe run persistence as a fixed-state snapshot plus a growing-state sidecar |
| [`state/shared_state.py`](state.md#stateshared_statepy---typed-lateral-state-payloads-exact-routing-references-and-provenance-records) | 333 | typed lateral-state payloads, exact routing references and provenance records |
| [`state/stage_gates.py`](state.md#statestage_gatespy---deterministic-stage-completeness-gate) | 94 | deterministic stage-completeness gate |
| [`tools/__init__.py`](tools.md#tools__init__py---package-marker-for-governed-tool-execution) | 3 | package marker for governed tool execution |
| [`tools/approvals.py`](tools.md#toolsapprovalspy---typed-approval-gates-for-state-changing-actions) | 73 | typed approval gates for state-changing actions |
| [`tools/artifacts.py`](tools.md#toolsartifactspy---content-addressed-artifact-store-with-immutable-write-attribution) | 255 | content-addressed artifact store with immutable write attribution |
| [`tools/core/__init__.py`](tools.md#toolscore__init__py---public-surface-of-the-portable-core-tools) | 25 | public surface of the portable core tools |
| [`tools/core/definitions.py`](tools.md#toolscoredefinitionspy---typed-declarations-of-the-governed-core-tool-set) | 288 | typed declarations of the governed core tool set |
| [`tools/core/helpers.py`](tools.md#toolscorehelperspy---private-validation-http-and-isolated-regex-helpers-behind-the-core-tools) | 436 | private validation, HTTP and isolated-regex helpers behind the core tools |
| [`tools/core/services.py`](tools.md#toolscoreservicespy---portable-governed-tools-dispatcher-services-and-web-search) | 437 | portable governed tools: dispatcher, services and web search |
| [`tools/delegation.py`](tools.md#toolsdelegationpy---delegated-sub-runs-on-isolated-git-worktrees) | 113 | delegated sub-runs on isolated git worktrees |
| [`tools/policy.py`](tools.md#toolspolicypy---deny-by-default-capability-policy) | 153 | deny-by-default capability policy |
| [`tools/registry.py`](tools.md#toolsregistrypy---capability-bound-harness-tool-registry-and-its-baseagent-executor) | 323 | capability-bound harness tool registry and its BaseAgent executor |
| [`tools/sandbox.py`](tools.md#toolssandboxpy---pluggable-execution-backends-for-registered-command-templates) | 247 | pluggable execution backends for registered command templates |
| [`tools/sandbox_models.py`](tools.md#toolssandbox_modelspy---typed-configuration-for-sandbox-backends) | 50 | typed configuration for sandbox backends |
| [`tools/supervisor.py`](tools.md#toolssupervisorpy---registered-command-execution-with-timeout-bounded-output-and-process-tree-kill) | 437 | registered-command execution with timeout, bounded output and process-tree kill |
| [`tools/task_models.py`](tools.md#toolstask_modelspy---records-for-background-tasks) | 43 | records for background tasks |
| [`tools/tasks.py`](tools.md#toolstaskspy---background-task-lifecycle-start-poll-stop) | 208 | background task lifecycle: start, poll, stop |
| [`tools/tools.py`](tools.md#toolstoolspy---the-toolexecutor-protocol-and-two-deterministic-test-executors) | 72 | the ToolExecutor protocol and two deterministic test executors |
| [`tools/worktree_models.py`](tools.md#toolsworktree_modelspy---record-for-an-agents-git-worktree) | 25 | record for an agent's git worktree |
| [`tools/worktrees.py`](tools.md#toolsworktreespy---git-worktree-isolation-for-concurrent-agents) | 145 | git worktree isolation for concurrent agents |

*Coverage: 123 files, 397 classes and 1010 functions (including methods and nested closures), each with a written description.*

## Files outside the package

| Path | What it is |
|---|---|
| `pyproject.toml` | Package metadata (`nailong-agent-sdk` 0.17.0, Python >= 3.12), hatchling build, console script `nailong-agent-sdk-dev`, optional extras `langgraph`, `langchain`, `jev`, `redis-cache`, `interop`, Ruff and pytest settings |
| `examples/` | Seven runnable scripts using a scripted model: custom verification and profiling, developer tools, durable runtime with PASK, framework interoperability, governed core tools, grounded retrieval, orchestration |
| `scripts/run_pckp_benchmark.py`, `benchmarks/` | Runs the frozen PCKP comparison cases in `pckp_cases.json` and writes `results/pckp-report.json` |
| `test/` | Outputs of real-workload validation scenarios run against a live OpenAI-compatible provider (generated documents and a generated game). It is not a pytest suite |
| `apps/sg_job_agent/` | A Singapore job-search application built on the SDK (separate `pyproject.toml`) |
| `docs/reference/` | This reference |

The repository currently contains **no automated unit tests** for the SDK: the original suite
lives in the sibling `agent_assisted_design-dev` checkout under the old package name.

## Issues found while documenting

Each item was found by reading the code. **[reproduced]** means a script reproduced it before
any change; **[inspected]** means the code path was read but not run. Everything in the first
table was repaired in the same pass and its reproduction re-run; the descriptions in this
reference describe the code after those repairs.

### Fixed during this audit

| # | Problem | Repair |
|---|---|---|
| 1 | **[reproduced]** The MCP server held two writers over one run root: after the controller recorded a node result, a `get_run_state` poll reverted the durable run; a locked human decision written through one project-state store vanished after the other store updated the project | The controller runtime now shares the server's one coordinator and one project-state store. `HarnessCoordinator` and `FileProjectStateStore` detect that another writer changed a file (inode, modification time, size) and refresh instead of overwriting; a conflicting save raises `RUN_STATE_CONFLICT`; `get_run_state` no longer writes |
| 2 | **[reproduced]** `controller-1` was reused by every new process, overwriting the persisted controller | New ids skip any controller already on disk (`ControllerStateStore.exists`) |
| 3 | **[reproduced]** `cancel_run` during `execute_run` was ignored and its flag reset to false | The cancelled flag is sticky per coordinator and `execute_run` stops before the next wave |
| 4 | **[reproduced]** `FileToolResultJournal` handle ids collided across instances and restarts | Handle files are claimed by exclusive creation and numbered after the highest id on disk |
| 5 | **[reproduced]** `PlanValidator` accepted a proof that cited the wrong signals | Proofs are compared per edge including their signal sets; messages name the edge, rule and signals |
| 6 | **[reproduced]** A model `tool-call` turn that listed dependencies crashed `BaseAgent.run` with a raw validation error | `ToolCallTurn` rejects it, so the run ends FAILED with `MODEL_TURN_INVALID` naming the field |
| 7 | **[reproduced]** A LangGraph SDK node reported a successful run as FAILED when the output had a key such as `message` or `token` | Result digests use `content_digest`, which does not apply the credential-key sanitiser |
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

### Still open

1. **[inspected] A crash between two writes bricks a project state.**
   `FileProjectStateStore.apply` writes the event file before the state file; `load` then fails
   with revision != event count until the last event file is removed by hand. A roll-forward
   or write-ahead record would fix it.
2. **[inspected] There are no locks.** The new change detection narrows the race between
   writers, but two writers acting at the same instant can still interleave in
   `RunStateStore`, `FileProjectStateStore` and `ControllerStateStore`. Decide between file
   locks and a single-writer service.
3. **[reproduced earlier] `AuditTranscriptStore` slows about sixfold beyond 32 concurrently
   active runs** (its handle cache evicts), and nothing applies a retention policy to
   telemetry, audit logs, journals or episode records.
4. **[inspected] Linear-time hot spots.** Every `RunStateStore.save` re-reads and re-hashes the
   whole sidecar; `AuditTranscriptStore.list_entries` reads the whole file; terminal agent
   metrics and `inspect_run` use only the first 1,000 telemetry events without saying so.
5. **[inspected] The MCP client has no connect or call timeouts,** connects servers one at a
   time and raises `McpServerNotConnectedError` for every kind of failure.
6. **[inspected] Declared but unreachable:** `ArtifactStatus.COMPLETE` is never produced and no
   transition closes a blocker or open question, so `StageCompletenessGate` requirements on
   them cannot be met from reducer-recorded state; `ControllerPhase.INTAKE` and
   `GraphEdgeKind.FAN_IN` are never used; `RunSharedState` and `SharedStateStore` are legacy and
   unused; approvals are in memory only.
7. **[inspected] Duplication:** `_canonical_json` is copied in seven modules and wrapped by eight
   differently named SHA-256 helpers; the token estimate (`len(json) // 4`) is copied four
   times; the per-node executor wrapper is duplicated in `StateGraph.execute` and
   `HarnessCoordinator.execute_run`; `_normalize_verification` repeats `_normalize_decision`; two
   artifact tool implementations exist.
8. **[inspected] Metrics and exports:** repair-attempt metrics are recorded only by an explicit
   `record_stage_failure`, not when graph failure, provenance rejection or a completeness check
   triggers the repair; `verified_event_count` in the run report always equals `event_count`;
   `LangChainSdkRunnable` returns the whole `AgentResult` dump (prompt context, episode
   summaries, events, profile) to the framework without sanitising it.
9. **[inspected] Cosmetic:** eight files fail `ruff format --check` (`agent/model.py`,
   `agent/openai_compatible/chat.py`, `agent/openai_compatible/vision.py`, `mcp/client.py`,
   `tools/core/helpers.py`, `tools/core/services.py`, `tools/supervisor.py`, `tools/tasks.py`);
   `SERVER_NAME` (`agent-design-python-runtime`), `SERVER_VERSION` and the HTTP user agent
   (`agent-design-sdk/0.8`) still carry the old name or a duplicated version.
