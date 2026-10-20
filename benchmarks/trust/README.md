# Trust Profile benchmark

A harness that measures four trust properties of an agent system (traceability, verifiability,
authority, debuggability) on the same cases for every system, so the numbers are comparable.
It lives outside `src/` on purpose: the SDK carries no benchmark or domain logic.

Implemented: the independent recorder, the common event schema, the statistics library, the
reproduction gates, and the endpoints of all four properties: traceability (records, provenance
chains, the tamper battery, degradation), verifiability (a held-out gate on each system, the
attack battery on its reference), authority (a public policy, five allocation arms, enforcement
and records on each system, an approval-scope probe) and debuggability (diagnosis, insufficient-
evidence twins). Also the pre-registered pass criteria and the card that prints them, the lock
that pins the test split, and an LLM-judge arm that asks Jev through the SDK's own evaluator.
Three task families, two systems and deterministic scripted solvers ("Tier S", no model cost);
the only model cost is the judge, a few dollars at most. Live-model runs ("Tier L") and a second
framework are not built.

## What it compares

| Arm | What runs | Native records read back |
|---|---|---|
| `sdk` | `AgentTaskRunner` (one agent) or `HarnessCoordinator` with `GraphAgentExecutor` (three agents) with the SDK's core tools, behind an OpenAI-wire transport | audit transcript, tool-result journal, artifact write history and content store, telemetry |
| `sdk+anchor` | the same, with a signed run-head checkpoint kept outside the run root (tamper battery only) | as above, and the checkpoint |
| `langgraph` | `langgraph.prebuilt.create_react_agent` per agent (its own checkpointer thread), joined in a `StateGraph` for the three-agent case, with the same tools behind a fake chat model | the persisted message history of each agent |
| `sdk-preapproved` | authority endpoint only: the decision is taken before the task starts and the capability is approved in advance | telemetry `approval.decided` |
| `langgraph-plain` | authority endpoint only: interrupt and resume without writing the decision into the history | the message history |

Both systems are driven by the same deterministic policy through one boundary
(`trust_bench/wire.py`), so they make the same decisions and write the same bytes (a test
asserts it, with and without a gate). The boundary writes its own hash-chained log, which is the
ground truth for what happened. No system is scored on its own account of itself.

`langgraph.prebuilt.create_react_agent` is deprecated in LangGraph 1.x in favour of
`langchain.agents.create_agent`; the harness keeps the former because `langchain` is not a
dependency of this repository. Versions used are recorded in the lock.

## Task families and faults

| Family | Task |
|---|---|
| `doc` | total amounts per requested vendor from seeded records; one seed in three is a "large" case whose write turn exceeds the SDK's audit payload bound |
| `data` | clean, aggregate and summarise a seeded table in three written stages |
| `plan` | two ingest agents total a budget table and an actuals table; a third reads both outputs and writes the variance report (held out of development) |
| `ops` | eight tickets (consequential writes) per case, with a public policy saying which class of decider each needs; used by the authority endpoint |

Fault classes (ground truth by construction, one decisive step per case):

| Class | Meaning | Operators |
|---|---|---|
| F1 | explicit tool failure | `env_unreadable` |
| F2 | silent corruption of a tool result (the tool reports success) | `env_corrupt:digit_swap`, `env_corrupt:scale10` |
| F3 | wrong model decision | `decision_wrong_total:*`, `decision_wrong_stage:*`, `decision_wrong_node:*` |
| F4 | malformed deliverable (verifiability jobs only) | `decision_wrong_form:truncate` |
| F8 | limit exhausted, no single responsible decision (the right answer is "none") | `limit` |

## Splits

Development seeds are 1 to 999. The confirmatory test plan is seeds 1,000,000 to 1,000,099
(tamper battery: 1,000,000 to 1,000,079). Seeds 1,900,000 to 1,900,049 are reserved for tests of
the harness and never produce results. Operators `env_corrupt:scale10`,
`decision_wrong_total:sign_flip`, `decision_wrong_stage:revenue` and the `decision_wrong_node`
operators exist only in the test split, and the family `plan` is held out. Fixes to the SDK are
made against the development split only; the test split is run once, after the lock.

## Running it

Run from this directory with the repository's virtual environment (it needs the `langgraph`
extra and `langchain-core`; the judge also needs the `jev` extra and `TYPESAFE_API_KEY`).

```bash
python -m trust_bench gates
python -m trust_bench run --split dev --families doc,data --seeds 1-150 --arms sdk,langgraph --workers 12 --out results/dev.jsonl
python -m trust_bench tamper --split dev --families doc,data --seeds 1-30 --workers 12 --out results/dev_tamper.jsonl
python -m trust_bench verify --split dev --families doc,data --seeds 1-150 --workers 12 --out results/dev_verify.jsonl
python -m trust_bench attack --split dev --seeds 1-150 --workers 12 --out results/dev_attack.jsonl
python -m trust_bench authority --split dev --seeds 1-150 --arms sdk,langgraph,langgraph-plain,sdk-preapproved --workers 12 --out results/dev_authority.jsonl
python -m trust_bench chain --split dev --seeds 1-150 --workers 12 --out results/dev_chain.jsonl
python -m trust_bench report --results results/dev.jsonl --tamper results/dev_tamper.jsonl --verify results/dev_verify.jsonl --attacks results/dev_attack.jsonl --authority results/dev_authority.jsonl --chains results/dev_chain.jsonl --out reports/dev.md
```

`gates` re-derives two published results with this harness's scorers and must pass before any
comparison is read: the Agent Flight Recorder "chain" tamper pattern (arXiv 2609.01931) and the
DEMM-Bench sufficiency rates (arXiv 2606.20634). `results/` is not tracked; reports are.

### The judge (Jev)

Everything but the call itself runs offline, and every stage can be rerun for free:

```bash
python -m trust_bench judge-prepare --task diagnosis --split dev --families doc,data --seeds 1-3 --out results/judge_diag_in.jsonl
python -m trust_bench judge-estimate --inputs results/judge_diag_in.jsonl
python -m trust_bench judge-ask --inputs results/judge_diag_in.jsonl --out results/judge_diag_ans.jsonl --split dev --max-calls 200 --max-input-tokens 2000000
python -m trust_bench judge-score --inputs results/judge_diag_in.jsonl --answers results/judge_diag_ans.jsonl --out results/judge_diag_rows.jsonl
python -m trust_bench report --results results/dev.jsonl --judge-diagnosis results/judge_diag_rows.jsonl --out reports/dev_judge.md
```

The tasks are `diagnosis`, `acceptance` and `allocation`. `judge-ask` needs `TYPESAFE_API_KEY`
(the environment, else the line of that name in the repository's `.env`), stops at the ceiling of
calls or input tokens it is given, appends each answer as it arrives, caches by the digest of the
model, state and question, and writes one digest-only receipt per call next to the answers. The
allocation table `judge-score` writes feeds `authority --allocations model --model-table <table>`.

### The test split

The test split is gated. After committing the harness and `PREREGISTRATION.md`:

```bash
python -m trust_bench lock          # pins the commit, the pre-registration, the harness and SDK hashes
python -m trust_bench verify-lock   # checks the tree still matches
```

then the commands of section 7 of `PREREGISTRATION.md`, each with `--split test` and the locked
seeds. A test run is refused without a matching lock, outside the locked plan, when a job already
ran (`--rerun-reason` records a deviation instead), or when a judge ceiling is above the locked one.

## Layout

| Path | Role |
|---|---|
| `PREREGISTRATION.md` | every metric, threshold and its source, the analysis plan, the protocol |
| `DEVIATIONS.md` | every change to the plan or rerun of a job after the lock |
| `reports/` | rendered reports; `dev_run4.md` is the development split with all four properties, `dev_run4_judge.md` the same with the Jev judge's rows added |
| `trust_bench/schema.py`, `recorder.py`, `wire.py` | common events, the independent recorder, the policy behind each system's model seam |
| `trust_bench/cases.py`, `nodes.py`, `families/` | seeded cases, solver policies, oracles, check layers, fault catalogs |
| `trust_bench/systems/` | one adapter per arm: run, gate, map native records to common events, tamper battery, authority deployments |
| `trust_bench/metrics/` | traceability, degradation, attribution, sufficiency and twin scorers |
| `trust_bench/diagnosers.py` | trivial baselines, the oracle-assisted ledger scan, the SDK's shipped diagnosis |
| `trust_bench/checks.py`, `acceptance.py`, `forms.py`, `verify_jobs.py`, `verify_tables.py` | the verifiability endpoint: quietness, gates, form defects and attacks, jobs and tables |
| `trust_bench/authority/` | the policy file, tickets, allocation arms, deciders and the decision log, approval-scope probe, jobs and tables |
| `trust_bench/judge/` | the Jev arm: budget, cache, client, question wording, rendering, preparation, asking, scoring |
| `trust_bench/criteria.py` | the pass criteria and their thresholds, and the card |
| `trust_bench/stats.py`, `aggregate.py`, `report.py` | exact and Wilson bounds, bootstrap, McNemar, Holm, tables |
| `trust_bench/lock.py`, `confirmatory.py` | the lock, the run-once registry and the locked plan |
| `tests/trust_bench/` | tests for every module above |

## Reading the report

The card lists every pre-registered criterion with its estimate, rule, threshold source and
PASS, FAIL or NOT RUN. Below it are the tables behind the criteria.

In the diagnosis table `ledger-scan+oracle` is an upper bound on what the records allow: it
applies the held-out oracle to every recorded result and write, and it abstains when a step it
cannot evaluate precedes the first defect. `sdk-shipped:*` runs the SDK's own `attribute_failure`
and `attribute_defect` (and, in `results+artifacts`, `attribute_result_defect`) as shipped, with
the case's oracle as the caller-supplied check. `jev-judge` is Jev reading the records. The
trivial baselines must score poorly; if one scores well the family has a shortcut and the family
is the problem.

A twin hides the decisive step and one look-alike step in the generic log. Naming a unique step
is then unsupported; the ledger scan should abstain and the trivial baselines will not.

The verifiability section gives, for each way of accepting a deliverable, how many defects of
each quietness level it lets through and how many correct deliverables it rejects, and what the
attacks achieved. The authority section gives, for each allocation arm, violations,
over-escalation, paired accuracy and how much of the decision the system's own records hold, a
reviewer grid, and the approval-scope probe.

## What this does not show

It shows how systems behave on seeded tasks with scripted solvers and injected faults. It does
not show that failures are absent, that live models behave the same, or that results carry to
task families outside the sample. See `PREREGISTRATION.md` for the claims that are made and
section 11 of it for the known limitations.
