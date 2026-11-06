# Building a team of agents

This tutorial builds three kinds of multi-agent setup, from lightest to most governed. Every code
block comes from a file in `examples/` that runs offline with scripted models, and a test
(`tests/starter/test_multi_agent_examples.py`) runs those files, so what you read here works.

| You want | Use | Example file |
|---|---|---|
| One agent that hands parts of a job to specialists and answers once | `Agent.as_tool` | `examples/multi_agent_starter.py` |
| Several independent jobs at once, then one that combines them | `asyncio.gather` | same file |
| A reviewed plan, per-role limits, approval before anything runs, resume after a crash | `Orchestrator` | `examples/multi_agent_pipeline.py` |
| A model that splits a plain request into tasks for you, and you choose team or one agent | `Planner` | `examples/multi_agent_planner.py` |
| An agent that may ask for extra help, within limits you set | elastic nodes | `examples/elastic_exploration.py` |

Start with the first. Let a `Planner` write the tasks when you do not want to. Move to the `Orchestrator` when you need approvals, role limits or a run you
can recover and audit as one unit.

**Who decides between one agent and several?** It depends on the path:

- **Parts 1 and 2 (starter, CLI):** the model decides while it works. A supervisor calls a
  specialist when its instructions and the tool description say to; the CLI's `[agents.NAME]`
  subagents work the same way. Nothing classifies your prompt in advance, which is also how
  Claude Code's subagents behave.
- **Part 3 (`Planner`):** a model drafts the tasks, code validates them, a fixed rule suggests
  team or one agent from the finished plan, and **you** make the final choice.
- **Part 4 (`Orchestrator`):** your code decides, with rules rather than a model. You supply the
  plan (by hand, or from a `Planner`) and a few numbers about it, and a fixed rule picks the
  architecture (see "How one agent or many is chosen").

To run an example: `python examples/multi_agent_starter.py` (needs the SDK installed). To use a
real model, replace the scripted model; nothing else changes.

---

## 1. A supervisor that calls specialists

A specialist is an ordinary `Agent`. `as_tool` turns it into a tool another agent can call.

```python
researcher = Agent(
    model=openai_chat(...),                      # or scripted(...) in the example
    instructions="You research one question and report only what the sources say.",
    workspace=workspace / "researcher",
    name="researcher",
)
supervisor = Agent(
    model=...,
    instructions="Delegate research, then have the claim reviewed, then answer once.",
    tools=[
        researcher.as_tool(
            name="research",
            description="Ask the researcher one question; returns what the sources say.",
            read_only=True,
        ),
        reviewer.as_tool(name="review", description="...", read_only=True),
    ],
    workspace=workspace / "supervisor",
)
answer = await supervisor.run("What is the part's temperature rating?")
```

What happens: the supervisor's model calls `research` with a `task` string; the researcher runs a
complete run of its own and its answer text comes back as the tool result; the supervisor reads it
and calls `review`; then it answers.

Things to know:

- **Only text crosses the boundary.** A specialist sees the `task` string and nothing else, and the
  supervisor sees only the answer text. Put everything a specialist needs in the task.
- **A specialist that fails becomes a failed tool call.** `as_tool` raises with the specialist's
  reason; the supervisor sees it as a failure it can react to (retry, ask differently, or
  report).
- **Each agent has its own workspace, ledger and session.** Use a separate `workspace` per agent
  (as above) so their audit logs and sessions do not mix.
- **Approvals and permissions belong to each agent.** Give a specialist the tools it needs and no
  more. `needs_approval=True` on `as_tool` makes the supervisor ask before delegating at all.
  `read_only=True` marks the delegation as exploratory and safe to run in parallel with other
  calls in the same step (a supervisor can ask two specialists at once); it does not skip any
  approval.
- **Cost adds up.** The supervisor pays for its own steps, each specialist for theirs. Set
  `max_steps` on each.

## 2. Fan out, then combine

For independent jobs, run agents concurrently and hand the results to one more agent:

```python
answers = await asyncio.gather(*(agent.run(f"Cover {name}.") for name, agent in workers.items()))
notes = "\n".join(f"- {name}: {a.text}" for name, a in zip(workers, answers, strict=True))
brief = await editor.run(f"Write the brief from these notes:\n{notes}")
```

You decide the shape in plain Python, so loops, retries and conditionals are just code. Two cautions:
do not let concurrent agents write the same file, and mind provider rate limits (web search backends
in particular reject bursts; the built-in `web_search` already spaces calls about a second apart).

## 3. Let a model plan it: `Planner`

You do not have to write the tasks yourself. `Planner` asks a model to split a plain request, with
no specification, and gives you a validated plan to approve before anything runs.

```python
planner = Planner(
    model=openai_chat(...),
    roles=[Role("researcher", "finds facts and reports only what sources say"),
           Role("writer", "writes the final document", writable=True)],   # your roles
)
proposal = await planner.plan("Compare vendors A and B on pricing and security, then write a brief.")
print(proposal.summary())        # tasks, roles, what waits for what, and a suggestion
```

What happens inside:

1. The model drafts tasks: an id, a role, instructions, a "done when", and two lists, the results
   the task `produces` and the results it `needs`. That is all it writes.
2. Code compiles the draft into a core `Plan`: dependencies follow from who produces what, the
   dependency proofs are generated, and the request text stands in for the specification.
3. `PlanValidator` checks it. A draft with an unknown role, a result nobody produces, two producers
   of one result or tasks waiting on each other goes back to the model with the exact problems
   (three attempts by default). Harmless slips such as `Read_And Summarize` are fixed, not retried.
4. Tasks of **writable** roles are chained so two tasks that change things never run together;
   read-only roles run side by side.
5. The suggestion comes from the finished plan, not from the model: **a team when two or more
   roles are involved or two tasks are independent, otherwise one agent**, with the reasons.

You decide, then run:

```python
if proposal.choice.team and user_wants_a_team:        # you ask, however you like
    result = await proposal.run_team(worker_factory, max_parallel=3)
else:
    answer = await proposal.run_solo(solo_factory)    # same steps, one agent
```

`worker_factory(role, task)` returns the `Agent` for one task (give it that role's tools) and
`solo_factory(roles)` returns one agent for the whole job. A task whose dependency fails is
**skipped**, not run, and `TeamResult.outcomes` says which and why. The proposal also carries
`proposal.plan` and `proposal.gap_metadata`, so you can hand the same plan to the `Orchestrator`
(Part 4) when you need approvals and recovery. Run `examples/multi_agent_planner.py` to see it.

The CLI uses this: `/plan <request>` shows the plan, asks "team or one agent?" when it would be a
team, and runs the tasks one at a time (see the CLI guide).

Limits to know: the validator proves the plan is consistent, not that it is a good way to do the
job, so read the plan before approving; planning costs one extra model call; and a model that
ignores the format fails after the allowed attempts with the last problems named.

## 4. A governed team with the Orchestrator

When a plan must be reviewed, limits enforced, and a run recoverable, use the core `Orchestrator`.
It separates **who decides what**:

| You provide | It decides |
|---|---|
| `OrchestrationPolicy`: allowed models, roles (`AgentExecutionProfile`), `max_total_agents`, `max_parallel_agents`, routing rules | what is allowed at all |
| `OrchestrationRequest`: the stage, a snapshot of the inputs, and a `Plan` of tasks | what is asked for |
| a person's `approve(...)` | that the plan may run |
| a binding factory | how each task actually runs (model, tools, task text) |

The example is a research team: two researchers work in parallel, and a writer starts only after
both are done.

### The plan and its dependencies

```python
PlanTask(
    task_id="write-brief",
    scope="brief",
    locked_interface={"signals": [{"id": "pricing_summary"}, {"id": "security_summary"}]},
    dependencies=["research-pricing", "research-security"],
    signal_uses=[TaskSignalUse(task_id="write-brief", signal_id="pricing_summary",
                               role=SignalRole.CONSUME, source_spans=["brief:brief"],
                               scope_pointer="brief"), ...],
    ...
)
```

**Dependencies are proven, not asserted.** The planner cannot simply write "B depends on A". A
task that produces a signal declares `SignalRole.PRODUCE` for it, a task that needs it declares
`SignalRole.CONSUME`, both list the signal in their `locked_interface`, and the `Plan` carries a
`DependencyProof` (rule `PRODUCER_TO_CONSUMER`) for each edge. `Orchestrator.prepare` recomputes
the edges from the signals and refuses a plan whose declared dependencies do not match. If you see
`DEPENDENCY_SET_MISMATCH: ... no shared locked-interface signal derives`, a task lists a
dependency that no signal justifies. This is deliberate: no unproven ordering ever runs.

### Prepare, approve, run

```python
orchestrator = Orchestrator(run_root, policy)
prepared  = await orchestrator.prepare(request)                   # validates the plan
presented = orchestrator.submit_for_approval(prepared.orchestration_id)
approved  = orchestrator.approve(presented.orchestration_id, True, "Approved by the owner.")
completed = await orchestrator.dispatch_and_execute(
    approved.orchestration_id, AgentRuntimeServices.open(run_root), binding_factory
)
```

Nothing runs before `approve`. Running the example prints, for this plan:

```text
architecture: multi-agent    status: executed
node_statuses: node:research-pricing, node:research-security, node:write-brief -> completed
writer_received: the pricing and security summaries, keyed by node id
```

The two researchers form the first wave and run together (up to `max_parallel_agents`); the
writer is the second wave.

### How one agent or many is chosen

`prepare` computes the architecture from the request's `GapMetadata`, which **you** fill in:
`categories_touched` (the areas the work touches), `blast_radius` (a whole number for how much
could be affected) and `gap_types` (labels). The policy's `ComplexityRoutingRules` then apply a
fixed test, in this order:

1. the number of distinct `categories_touched` is at least `multi_agent_min_categories`, or
2. `blast_radius` is at least `multi_agent_min_blast_radius`, or
3. any of `gap_types` is in `multi_agent_gap_types`

If any holds the route is `multi-agent`; otherwise `single-agent`. That is the whole rule; no
model is asked. The example touches two categories and the rule asks for two, hence `multi-agent`.
Three more points:

- **Single-agent means the plan is collapsed**, not skipped: all tasks are merged into one task
  whose instructions are the originals joined together, run by one agent.
- **`OrchestrationPolicy.multi_agent_enabled=False` forbids multi-agent**: `prepare` refuses a
  request that routes there.
- **An optional advisor can only raise the route.** If you pass a `jev_router` to the
  `Orchestrator`, it may lift `single-agent` to `multi-agent` within its confidence policy; it can
  never lower what the rule chose.

Because you supply the metadata, the `Orchestrator` does not read requests itself. The `Planner`
in Part 3 is the piece that does: `proposal.plan` and `proposal.gap_metadata` are exactly what
`prepare` takes.

### The binding factory: how a task becomes an agent

For every node the orchestrator calls your `binding_factory(context)` and expects a
`GraphAgentBinding`: the `AgentDefinition`, a `task_adapter`, a `model_factory`, and optionally a
tool executor, hooks and a watchdog. The two pieces to understand:

```python
def task_adapter(node, graph_context) -> ScopedAgentTask:
    upstream = {node_id: result.output for node_id, result in graph_context.dependencies.items()}
    return ScopedAgentTask(id=..., input={"upstream": upstream}, ...)
```

`graph_context.dependencies` is **the only channel** for upstream results. A node cannot read its
siblings' outputs, only those of the tasks it was proven to depend on. This is how the writer gets
the two summaries.

The binding you return is checked against what was assigned: its `definition.identity` must be
the assigned worker, its `definition.model_binding` must equal the `binding` of the
`UserModelSelection` the task got (the example passes `context.model.binding`), and its tools must
be a subset of the profile's `allowed_tool_names`. A mismatch raises before anything runs, so a
task cannot run on a model or tool nobody approved. For a real model, `model_factory` returns an
adapter whose provider and model equal that binding (the adapter checks it too):

```python
def model_factory(node, graph_context):
    return OpenAICompatibleAgentModel(endpoint, provider="openai-compatible", model="my-model")
```

with `binding={"provider": "openai-compatible", "model": "my-model"}` in the policy. (This
snippet is not exercised by the offline example.)

### Letting an agent ask for help

An approved task may be allowed to request more work: give its profile the
`request_elastic_node` tool and set `max_elastic_depth` and `max_elastic_nodes` in the plan and
policy. The requester is paused behind a join, the explorers run, and the requester resumes with
what they found. The limits are hard: a request over the node cap is deferred or refused, and the
join counts against the cap, so a cap of 2 admits one request. See
`examples/elastic_exploration.py` for a complete run.

### When a run stops

| State of the orchestration | What to call |
|---|---|
| `blocked` (for example a node waits on an approval) | resolve it, then `resume_execution(id, services, binding_factory)` |
| `dispatched` but the process died | `recover_execution(id, services, binding_factory)`; a node that was running goes back to pending only if its binding is `idempotent`, and every other interrupted node becomes a typed failure instead of being silently repeated |
| `executed`, `failed`, `cancelled` | nothing to recover; `recover_execution` refuses with `Only a dispatched orchestration can recover execution` |

After a restart, rebuild the shell with `Orchestrator.resume(run_root, orchestration_id)`. It
restores the policy and the record; **you must pass the binding factory again**, because
executable code is never stored. `cancel(id, reason)` and `reconcile(id)` are there when you need
to stop a run or compare the orchestration record with the graph.

Mark a binding `idempotent=True` only if running the node twice is harmless (it reads, it does
not charge a card or send mail).

## 5. Looking at what happened

Everything is local, under the `run_root` you passed:

```python
graph = RunStateStore(run_root).load(completed.graph_run_id).graph
graph["statuses"]      # node id -> completed / failed / blocked / ...
graph["spawn_records"] # every elastic request and whether it was admitted
```

The audit and telemetry ledgers are hash-chained, and the developer tool reads them:

```bash
nailong-agent-sdk-dev diagnose-run <run_root> <run-id>
nailong-agent-sdk-dev attribute-failure <run_root> <run-id>
```

`diagnose-run` explains why a run ended as it did; `attribute-failure` follows the graph to the
node and the exact input responsible.

## 6. What is not here

- A `Planner` plans and runs tasks in dependency order; it is not a free-form team. There is no shared task queue or peer-to-peer message bus. Agents do not message
  each other freely: work flows along proven dependencies (Part 4) or through a supervisor's tool
  calls (Part 1).
- A plan is fixed once approved. Agents can request extra nodes within limits; they cannot rewrite
  the plan.
- The supervisor pattern gives each specialist its own run and ledger. There is not a single
  chained log across all of them outside the `Orchestrator`, where the graph run groups the nodes.

## Troubleshooting

| You see | Cause and fix |
|---|---|
| `DEPENDENCY_SET_MISMATCH` or `SIGNAL_NOT_IN_LOCKED_INTERFACE` on `prepare` | a task's dependencies and signals disagree; list each shared signal in both tasks' `locked_interface` and add a proof |
| `AgentRuntimeServices must use the orchestrator run_root` | open the services on the same directory as the orchestrator |
| a node is `blocked` | something needs approval or a dependency failed; the graph's `statuses` and `diagnose-run` say which |
| the supervisor never calls a specialist | its description is vague; say exactly when to use it |
