# nailong-agent-sdk - architecture diagrams

Every diagram below was drawn from the source on branch `sdk_fix` (commit `5812431`), not from
the reference docs alone. File names in the labels are real modules under
`src/nailong_agent_sdk/`. Render with any Mermaid viewer (GitHub, VS Code "Markdown Preview
Mermaid Support", mermaid.live).

Reading order: D1 (where things live) -> D2 (one agent run) -> D3-D5 (the memory contribution)
-> D6 (governed tool call) -> D7 (many agents) -> D8-D9 (evidence and debugging) -> D10
(contribution map).

---

## D1. Layers: who may import whom

The package is a strict stack. `foundations` imports nothing; `agent` sits on top of everything
except `integrations`/`mcp`. The model is *not* a layer: it is an untrusted proposal source that
the `agent` layer calls and the lower layers constrain.

```mermaid
flowchart TB
    subgraph EXT["Outside the SDK"]
        BE["Backend / host application<br/>(owns sessions, approvals, domain roles)"]
        LLM["LLM provider<br/>(untrusted proposal source)"]
    end

    subgraph SDK["nailong_agent_sdk"]
        MCP["mcp/<br/>MCP server + client, 55 tools,<br/>two credentials (host / approver)"]
        INT["integrations/<br/>Jev, LangChain, LangGraph bridges"]
        AG["agent/<br/>BaseAgent, Orchestrator, task runner,<br/>retention, run checkpointer"]
        ST["state/<br/>project state, graph, controller,<br/>elastic nodes, run stores"]
        TL["tools/<br/>registry, capability policy,<br/>approvals, core tools, supervisor"]
        MEM["memory/<br/>episodes, PCKP compaction,<br/>semantic summaries, context projection"]
        SPEC["specifications/<br/>manifest, trees, evidence,<br/>grounded retrieval"]
        OBS["observability/<br/>telemetry ledger, audit log,<br/>profiler, run-head checkpoints"]
        FND["foundations/<br/>contracts, typed errors, PCKP solver,<br/>atomic IO, hashing, run fence"]
    end

    BE --> MCP
    MCP --> AG
    INT --> AG
    AG --> ST
    AG --> TL
    AG --> MEM
    ST --> TL
    TL --> ST
    ST --> OBS
    TL --> OBS
    MEM --> SPEC
    MEM --> FND
    SPEC --> OBS
    OBS --> FND
    ST --> FND
    TL --> FND
    AG -. "model call" .-> LLM
```

Two folder cycles exist and are deliberate: `tools <-> state` and `agent <-> integrations`; the
package root imports lazily so each module still imports cleanly on its own.

---

## D2. One agent run (`BaseAgent.run`)

The loop is deterministic apart from the single model call. Three guards stop the run before
the model is called; every exit goes through one `_terminate`.

```mermaid
flowchart TD
    A([Task + AgentDefinition]) --> B["Validate input schema<br/>build immutable prompt once<br/>(memory/context.py)"]
    B --> C["Open per-run state<br/>episode graph + store, project state,<br/>profiler root span, W3C trace id"]
    C --> D{"iteration <= max_iterations<br/>deadline ok, not cancelled?"}
    D -- no --> X["FAILED or CANCELLED<br/>typed failure code"]
    D -- yes --> E["Project context<br/>(PCKP compaction, stubs, semantic summaries)"]
    E --> G{"Guards"}
    G -- "CONTEXT_DEADLOCK<br/>PROJECT_STATE_BUDGET_EXCEEDED<br/>CONTEXT_BUDGET_EXCEEDED" --> BL["BLOCKED<br/>failure carries its numbers"]
    G -- ok --> M["Model call<br/>(watchdog, transient retry with backoff)"]
    M --> V{"Validate turn shape<br/>final / blocked / tool-call / tool-batch"}
    V -- invalid --> X2["FAILED MODEL_TURN_INVALID<br/>names up to 5 bad fields"]
    V -- blocked --> BL
    V -- tool turn --> T["Governed tool batch (D6)<br/>results become observations,<br/>episodes, project-state transitions"]
    T --> D
    V -- final --> S{"Output schema ok?"}
    S -- no --> O["Mismatch returned as observation<br/>agent may correct itself"] --> D
    S -- yes --> VG{"Verification gate<br/>(host-registered, deterministic first)"}
    VG -- rejected --> X3["FAILED<br/>reason + verifier_id + authority class"]
    VG -- accepted --> OK["COMPLETED"]
    X --> TERM
    X2 --> TERM
    X3 --> TERM
    BL --> TERM
    OK --> TERM["_terminate (single exit)<br/>project state result, escalate,<br/>seal profile, telemetry + audit,<br/>terminal-link, optional checkpoint"]
    TERM --> R([AgentResult])
```

---

## D3. Memory architecture: what the model sees vs. what the harness keeps

This is the central picture. The model's context is a *projection*, rebuilt every turn, from
three typed stores. Nothing the model sees is the only copy of anything.

```mermaid
flowchart LR
    subgraph DUR["Durable, harness-owned (never the model's only copy)"]
        PS["ProjectState<br/>typed working memory:<br/>decisions, artifacts, work items,<br/>open questions, blockers<br/>changes only by evidence-linked<br/>StateTransition, hash-chained events"]
        EG["Episode graph<br/>one episode per tool call<br/>exploratory (frontier) / action (consumes)<br/>states: OPEN, CLOSED, COMPACTED"]
        JR["Result journal<br/>full tool output by content-hash handle<br/>(.agent-tool-results)"]
        AR["Episode archive<br/>content-addressed snapshots<br/>read back and compared before release"]
    end

    subgraph PROJ["Per-turn projection (memory/context_projection.py)"]
        P1["PCKP compaction<br/>exact dependency-closed knapsack"]
        P2["Verified semantic summaries<br/>(opt-in SemanticCompactor)"]
        P3["Stubs: one line + handle_id<br/>for compacted episodes"]
    end

    subgraph CTX["ModelContext (budget: 12,000 est. tokens)"]
        C1["stable prefix<br/>prompt, schema (cache-friendly)"]
        C2["project state view (<= 2,000)"]
        C3["recent observations"]
        C4["episode summaries + stubs (<= 1,500)"]
        C5["semantic summaries with provenance (<= 1,200)"]
    end

    EG --> P1
    P1 -- "kept set" --> C3
    P1 -- "not kept" --> P2
    P1 -- "dropped content" --> P3
    P2 --> AR
    AR --> P2
    P2 --> C5
    P3 --> C4
    PS --> C2
    JR -. "get_tool_result(handle)" .-> C3
    P3 -. "handle" .-> JR
    C1 --> M(["Model"])
    C2 --> M
    C3 --> M
    C4 --> M
    C5 --> M
    M -- "tool call / answer" --> EG
    M -- "proposed outcome" --> PS
```

What flows where, in one sentence each:

- The **episode graph** holds what happened (a typed DAG; actions may only depend on the
  exploratory episodes they consumed - enforced in `memory/episodes.py`).
- **PCKP** decides which episodes keep their content under the token budget; closure over
  `depends_on` is mandatory, so an action is never kept without the observation it relied on.
- The **journal** keeps every full result; the model gets a bounded preview plus a handle.
- **Semantic summaries** digest the episodes PCKP did *not* keep, but only after archive and
  verification (D4). They are derived text and never count as evidence.
- **ProjectState** is separate from episodes: it is the harness's structured record of the task,
  not a transcript.

---

## D4. Semantic compaction: archive before release, verify before trust

A group that fails any step keeps its original payloads and is retried up to a limit. Groups
follow real `depends_on` edges, never similarity.

```mermaid
flowchart TD
    A["Episodes PCKP did not keep<br/>(state CLOSED, payload still held)"] --> B["plan_groups<br/>group along depends_on edges"]
    B --> C["Archive each episode<br/>content-addressed snapshot,<br/>read back and compared"]
    C --> D["Summarizer proposes a draft<br/>(ExtractiveEpisodeSummarizer needs no model,<br/>or a host-supplied model summarizer)"]
    D --> E{"Deterministic verification (_verify_group)"}
    E --> E1["sources-archived-and-intact"]
    E --> E2["dependencies-resolve"]
    E --> E3["citations stay inside the group"]
    E --> E4["claims-grounded / decisions-grounded<br/>quote cited episode text verbatim"]
    E --> E5["every failed or blocked episode mentioned"]
    E --> E6["no number or identifier absent<br/>from the sources"]
    E --> E7["compression within token ceiling"]
    E1 & E2 & E3 & E4 & E5 & E6 & E7 --> F{"All pass?"}
    F -- no --> R["Keep originals<br/>record failing check by name<br/>retry up to limit, then quarantine"]
    F -- yes --> G{"Optional veto stages<br/>entailment screen, claim judge, summary judge"}
    G -- "veto" --> R
    G -- "no veto" --> H["Commit<br/>release payload, link record to<br/>summary_id + archive_id + content_hash"]
    H --> I["Summary ledger<br/>resident / superseded / archived"]
    I --> J{">12 active summaries?"}
    J -- yes --> K["Consolidate into parents<br/>(level <= 3, children kept for provenance)"]
    J -- no --> L
    K --> L{"Ceiling on active records?"}
    L -- yes --> M["Evict archived+summarized records<br/>from active graph"]
    L -- no --> N([Context sees summary + stubs])
    M --> N
    N -. "action consumes a compacted episode" .-> Q["rehydrate within token budget<br/>restore originals from archive"]
```

Order of trust: deterministic checks decide; a model judge may only *veto*, never approve.

---

## D5. Why this memory is debuggable: one question, three answers

The same failure is traced through a plain-text transcript memory and through this design.

```mermaid
flowchart TD
    Q(["Agent gave a wrong final answer at iteration 37"])

    subgraph TXT["Plain-text rolling summary"]
        T1["Older turns replaced by a model-written paragraph"] --> T2["Original text gone"]
        T2 --> T3["Cannot tell if error was in the<br/>tool result, the summary, or the model"]
    end

    subgraph OURS["This SDK"]
        O1["Telemetry + audit chain:<br/>which projection the model saw at 37<br/>(context-projected event)"] --> O2["Which summaries were included,<br/>with source episode ids and handles"]
        O2 --> O3["Check verification record:<br/>which claim passed, which check<br/>(grounding, identifier, mandatory)"]
        O3 --> O4["Rehydrate the source episode<br/>from the archive, compare to the summary"]
        O4 --> O5["Locate the layer:<br/>tool output / summarizer / selection / model turn"]
    end

    Q --> T1
    Q --> O1
```

---

## D6. How one tool call is governed

The model can only *name* a tool. Whether it runs is decided by deterministic code, in order.

```mermaid
flowchart TD
    A["Model proposes tool call"] --> B{"Declared in AgentDefinition?"}
    B -- no --> F1["FAILED TOOL_NOT_DECLARED"]
    B -- yes --> C{"Executor exists?"}
    C -- no --> F2["FAILED TOOL_EXECUTOR_MISSING"]
    C -- yes --> D{"Arguments valid against JSON schema?<br/>consumed episodes available?"}
    D -- no --> R1["Failed result returned to model<br/>so it can correct"]
    D -- yes --> E["Pre-tool hooks"]
    E -- deny --> BL["BLOCKED"]
    E -- ok --> P["CapabilityPolicy"]
    P --> P1["sensitive paths first"]
    P1 --> P2["role capability grant"]
    P2 --> P3["path containment"]
    P3 --> P4{"Typed approval needed?"}
    P4 -- pending --> BL2["BLOCKED, resume_run after decision<br/>(DecisionSource recorded)"]
    P4 -- ok --> H["Handler: core tool, supervised process,<br/>or sandbox backend<br/>(tool watchdog)"]
    H --> J["Result stored in journal -> handle"]
    J --> K["Episode + observation (bounded preview + handle)"]
    K --> L["Project-state transition (reducer)"]
    L --> M["Audit entry + telemetry<br/>tool-batch-completed lists every outcome<br/>with failure code, message <= 300 chars"]
```

---

## D7. Many agents: controller, waves and elastic nodes

Agents never exchange conversation. They exchange typed discoveries and immutable values in
graph state, routed by exact reference.

```mermaid
stateDiagram-v2
    [*] --> PLANNING
    PLANNING --> AWAITING_PLAN_APPROVAL: submit_plan (deterministic validation)
    AWAITING_PLAN_APPROVAL --> DISPATCH_READY: approve (DecisionSource recorded)
    AWAITING_PLAN_APPROVAL --> PLANNING: rejected, plan must be revised
    DISPATCH_READY --> EXECUTING: begin_dispatch
    EXECUTING --> COMPLETED: every node completed
    EXECUTING --> REPAIR_REQUIRED: node failed, attempts left
    EXECUTING --> ESCALATED: node failed, repair budget spent
    REPAIR_REQUIRED --> AWAITING_PLAN_APPROVAL: revised plan submitted
    PLANNING --> CANCELLED: cancel (any non-terminal phase)
    EXECUTING --> CANCELLED: cancel
    COMPLETED --> [*]
    CANCELLED --> [*]
    ESCALATED --> [*]
```

```mermaid
flowchart TD
    subgraph WAVE["One wave (nodes runnable together, up to the parallelism cap)"]
        N1["Node A<br/>fresh BaseAgent"]
        N2["Node B<br/>fresh BaseAgent"]
    end
    SCH["StateGraph scheduler<br/>commit in node-id order"] --> WAVE
    WAVE --> RES["GraphNodeResult<br/>output + discoveries + elastic requests"]
    RES --> COM{"mark_terminal<br/>(atomic commit)"}
    COM --> ADM{"Elastic requests admitted?<br/>unique id, visible deps, narrowing refs,<br/>N+1 <= max_elastic_nodes, ceilings"}
    ADM -- fit --> SP["Spawn exploration nodes + one join<br/>dependents moved behind the join"]
    ADM -- "exceeds plan caps" --> DEF["Deferred whole batch: join blocked<br/>until grant_elastic_capacity or decline"]
    ADM -- "passes ceiling" --> REF["Refused: ELASTIC_*_CEILING_REACHED<br/>agent told at once"]
    SP --> SCH
    COM --> NEXT["Next wave / terminal status"]
    NEXT --> SCH
```

---

## D8. Evidence: independent records, cross-linked

Verifying a chain says the evidence can be trusted. The links between stores and the signed
checkpoint outside the run root are what catch tail truncation and a resealed chain.

```mermaid
flowchart LR
    subgraph ROOT["Run root"]
        TEL["Telemetry ledger<br/>SQLite WAL<br/>per-run sequence + previous-event hash"]
        AUD["Audit transcript<br/>JSONL, previous-entry hash,<br/>fsync per entry, cross-process lock"]
        PRO["Run profile<br/>hash-sealed at finish_run"]
        JRN["Tool-result journal<br/>content hash in handle"]
        PST["Project state<br/>state hash + hash-chained events"]
        GRF["Graph run record<br/>run_hash + history-prefix hash"]
    end

    subgraph OUT["Outside the run root"]
        CKP["Signed run-head checkpoints<br/>HMAC-SHA256 or host signer<br/>sequence + head hash of both chains"]
        TMB["Retention tombstone anchor<br/>(for pruned runs)"]
    end

    TEL -- "audit_head in<br/>agent.profile-completed" --> AUD
    AUD -- "terminal-link entry<br/>carries telemetry_head" --> TEL
    TEL --> CKP
    AUD --> CKP
    CKP -- "verdicts: ok / truncated /<br/>diverged / advanced / pruned" --> V(["verify-checkpoints"])
    TMB --> V
```

---

## D9. From failure to root cause

```mermaid
flowchart TD
    F["Run ends FAILED or BLOCKED"] --> C["Typed failure code at the exit<br/>AgentFailure(code=...), and the failing<br/>tool call in failure_details"]
    C --> L["Ledgers: telemetry events + audit entries"]
    L --> D["diagnose_run<br/>(developer_tools/diagnose.py, read-only)"]
    D --> D1["terminal finding"]
    D --> D2["first problem / first error"]
    D --> D3["graph runs: root-cause nodes<br/>and the nodes each one held up"]
    D1 & D2 & D3 --> REP["Report names exact field,<br/>status or exception"]
    L --> A["attribute_failure<br/>(developer_tools/attribute_failure.py, read-only)"]
    A --> A1["graph runs: responsibility per root<br/>(Shapley value of repairing it) and the<br/>nodes released by repairing it alone"]
    A --> A2["decision_links.py: the model turn that<br/>issued the failing call, produced the<br/>rejected output or reported itself blocked"]
    W["ArtifactStore write log<br/>(agent, run, tool, call per write)"] --> X["attribute_defect<br/>gallop + bisect over versions"]
    X --> X1["first defective version<br/>and the agent that wrote it"]
```

---

## D10. Contribution map: what is established vs. what is ours

```mermaid
flowchart LR
    subgraph EST["Established practice (we adopt, not claim)"]
        E1["Tool-calling agent loop"]
        E2["Rolling / LLM summarization of context"]
        E3["Episodic memory notion"]
        E4["Graph orchestration (LangGraph-style)"]
        E5["OpenTelemetry-style tracing"]
    end

    subgraph NOV["Contribution of this work"]
        N1["Typed episode DAG with enforced<br/>exploratory/action dependency rule"]
        N2["Compaction as an exact<br/>precedence-constrained knapsack<br/>with an optimality certificate"]
        N3["Summaries as derived,<br/>verified, provenance-carrying objects<br/>archive-before-release, reversible"]
        N4["Evidence layer designed for diagnosis:<br/>cross-linked hash chains,<br/>signed run-head checkpoints,<br/>typed failure codes, diagnose-run,<br/>structural attribution of failures<br/>and of defects to agents"]
        N5["Bounded agent-initiated graph growth<br/>(elastic nodes) with commit-time admission"]
    end

    E2 -- "replaced by" --> N3
    E3 -- "made structural" --> N1
    E2 -- "budget rule replaced by" --> N2
    E5 -- "extended to tamper-evidence" --> N4
    E4 -- "extended with" --> N5
```
