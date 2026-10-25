# Trust Profile v3 pre-registration: defaults that detect and refuse

Written before any v3 run. It amends `PREREGISTRATION_V2.md` and leaves it, `PREREGISTRATION.md`,
`DEVIATIONS.md`, `reports/v2_dev*.md`, `reports/dev_run*.md` and every result file of an earlier
version unchanged. Results of v3 go to `results/v3_*` and `reports/v3_*`.

## Why a new version

The v2 report left three things open that the code could close:

1. With no anchor configured the SDK was tamper-evident only: the unanchored arm missed
   artifacts/reseal, artifacts/truncate, half of artifacts/delete, audit reseal, and telemetry
   reseal, delete and truncate. Detection depended on a host option.
2. An agent built by hand with a `ToolExecutor` and no authorizer ran its tools ungoverned; a graph
   node without a `tool_authorizer_factory` did the same.
3. The tamper battery (30 seeds) had 60 to 120 trials in the artifact cells and 120 untouched
   checks, below the 183 and 149 trials the thresholds need, so V2.1a and V2.1b were NOT RUN.

## Changes under test

* `AgentRuntimeServices.open` and `create_mcp_server` anchor by default: a random HMAC key kept in
  `NAILONG_ANCHOR_HOME` (default `~/.nailong-agent-sdk`) and a per-run-root checkpoint sink beside
  it, outside the run root. `checkpointer=None` switches it off. If the home cannot be used the
  service runs unanchored and logs the path and the exception.
* `BaseAgent` and `GraphAgentBinding` default `require_tool_authorization` to true: an executor
  that neither enforces authorization itself (`HarnessToolExecutor`) nor has an authorizer is
  refused with `TOOL_AUTHORIZER_MISSING`; `require_tool_authorization=False` opts out.
* The capped branch-and-bound above 400 items is seeded by an adaptive fill that reproduces the
  adaptive greedy selection in a fraction of the time.
* `export-otlp` keeps a ledger by default (`--no-ledger` to resend).

## Arms and sizes

Tamper (doc and data families, seeds 1-100, 200 jobs per arm): `sdk` (explicit
`checkpointer=None`), `sdk+anchor` (explicit checkpointer), `sdk-default` (nothing passed),
`langgraph`. The default arm points `NAILONG_ANCHOR_HOME` into the trial directory and writes
the benchmark key there, so the verifier knows the key; the property tested is that the default
configuration detects, not that a key nobody knows stays secret.

Boundary: the nine v2 scenarios plus `no-authorizer-default` (an agent with a custom executor and
nothing else; control arm sets `require_tool_authorization=False`), 150 seeds each.

Other batteries (staterun, crash, lineage, realruns, source-tests) are rerun unchanged at the v2
sizes, and the v1 batteries are rerun on the dev split at the v1 sizes.

## Criteria

| id | claim | pass rule |
|---|---|---|
| V3.1a | in the default configuration every class on every store is detected | every cell: 0 misses and exact lower bound >= 0.98 (at least 183 trials per cell) |
| V3.1b | an untouched run raises no alarm | 0 alarms and exact upper bound <= 0.02 (at least 149 checks) |
| V3.1c | (expectation) without any anchor the SDK is tamper-evident only | listed, no pass or fail |
| V3.3a | no violation in any boundary scenario, including `no-authorizer-default` | 0 violations, exact upper <= 0.02 |
| V3.3c | the controls violate | at least half the seeds of every control arm |
| V3.6 | the seed of the capped search equals the greedy selection | unit test on three 450-item DAGs; on 2,000 items timing is reported, not a criterion |

A cell with no miss but fewer trials than its bound needs is reported NOT RUN, never PASS and never
FAIL. All other v2 criteria keep their rules.

## Not covered and not run

* No battery calls a model. A live-model run, the Jev judge arm, the v1 lock and the test-split
  confirmatory run need the owner's go-ahead and are NOT RUN here.
* An attacker who rewrites the evidence and the key and sink is not resisted, and the default
  anchor shares the OS user of the writer, so it covers changes made to the run root and not a
  process that can also read `NAILONG_ANCHOR_HOME`.
* Writes after the newest checkpoint are not protected.
* A host that passes `require_tool_authorization=False` is ungoverned by choice.
