# Pre-registration v2: Trust Profile of the evidence and authority architecture

**Status: written before any v2 battery was run.** It extends `PREREGISTRATION.md` (v1) and leaves
it, `DEVIATIONS.md`, `reports/` and `results/` of v1 exactly as they were. Results of v2 go to new
files (`results/v2_*`, `reports/v2_dev.md`). The confirmatory (test-split) plan and the lock of v1
are neither run nor changed here: the lock pins hashes of the SDK source, and the SDK changed, so
a locked confirmatory run needs the owner's decision to re-lock (section 9).

## 1. What this registration is for

v1 measured a development-split card in which the SDK failed one criterion, P1.4: artifact
history was tampered undetected (resealing 0/120, truncation 0/60) and so were the newest records
of the audit and telemetry chains when no signed checkpoint existed. The SDK then changed in
five ways that v1 does not test. This registration states, before running anything, what each
change is claimed to guarantee, how each claim is tested, and what is not claimed.

**Three tiers of evidence are kept apart in every report.**

| Tier | What it is | What it can show | What it cannot show |
|---|---|---|---|
| Source-level | A property that holds by construction and is pinned by a unit or regression test | That a code path cannot do X, for the inputs the test covers | That no other path exists; behaviour of code outside the SDK |
| Fault injection | A seeded battery that injects faults (tampering, crashes, hostile requests) against the real SDK code and counts misses | An operating characteristic with a confidence bound over the injected fault classes | That a fault class nobody injected is handled |
| Real-agent | Records produced by an agent driven by a live model | That checks agree with records of real behaviour | Anything about a model not run |

A unit test passing is **source-level** evidence and is never reported as trustworthiness. No
tier is averaged into another and no single score exists.

## 2. The five changes under test

1. **Signed anchors of the artifact write log** (`tools/artifact_anchor.py`). The log is chained
   with an unkeyed hash, so it is tamper-evident only. An anchor signs its length and head with
   the run-head signer into a sink outside the workspace; a task run anchors at its end, a graph
   node through `GraphAgentBinding.artifact_store_factory`, and `PeriodicCheckpoints` every few
   seconds while a run is going.
2. **Signed anchors of the graph run record** (`state/run_anchor.py`): history length, hash of
   that history prefix and record hash, signed outside the run root.
3. **Crash recovery**: the artifact log can sync blobs, manifests and log line before returning
   (`ArtifactStore(durable=True)`) and an anchor syncs the log before it reads its head; an audit
   transcript and a checkpoint file ending in an interrupted write are read as ending before it,
   and the next append discards it, recording the audit discard as an `audit.tail-repaired` entry
   with the hash of what was dropped.
4. **Authorization at the shared dispatch boundary** (`tools/authorization.py`,
   `BaseAgent(tool_authorizer=, require_tool_authorization=)`): one `CapabilityToolAuthorizer`
   decides before any executor runs, so a custom `ToolExecutor` cannot bypass it when the agent
   holds an authorizer. Approvals can be bound to the call's arguments and made single-use;
   consumption is atomic across threads and processes; revocation denies later calls; every
   grant, rejection, consumption, revocation, expiry and denial is a structured event
   (`ApprovalEvent`, `TelemetryApprovalSink`). `TaskRunOptions.permissions` exposes the modes and
   the MCP service exposes `revoke_run_approval` and `revoke_task_approval` to the approver
   credential only, bound to the request hash like every other decision.
5. **Lineage beyond four read tools** (`developer_tools/attribute_lineage.py`): typed inputs
   (artifact version, stored result, process output, fetched page, search) with an *observed*
   or *inferred* basis for every relation, and no causal claim.

Four correctness fixes are tested at source level only and are listed in section 8.

## 3. Endpoints, criteria and thresholds

Criteria follow the v1 rules (section 8 of v1): exact (Clopper-Pearson) or Wilson 95% bounds,
no averaging, a criterion that cannot be run is **NOT RUN**. "Cell" means one store and one
class. A threshold with no published source is marked *design*.

### 3.1 V2.1 Tamper battery with anchors (extends v1 P1.4; fault injection)

Command: `python -m trust_bench tamper --split dev --families doc,data --seeds 1-30
--arms sdk,sdk+anchor,langgraph`. Stores: audit, telemetry, artifacts, artifact-content, journal.
Classes: edit, delete, insert, reorder, truncate, reseal, at ten positions of each record.

| Id | Claim | Pass rule | Threshold source |
|---|---|---|---|
| V2.1a | With the anchor every class on every store is detected, artifacts included | every cell of `sdk+anchor`: 0 misses and exact lower bound >= 0.98 | v1 P1.4 rule; published: Agent Flight Recorder anchored configurations |
| V2.1b | An untouched run raises no alarm in either SDK arm | 0 false alarms in both arms, exact upper <= 0.02 | zero-event bound for n >= 149 |
| V2.1c (expectation, not a pass rule) | Without an anchor the SDK is tamper-evident only: artifacts/reseal and truncate, and the newest records of audit and telemetry, can be cut undetected | reported per cell; the report states whether the expectation held | design: documents the boundary |

### 3.2 V2.2 Crash recovery and anchored state (fault injection)

Command: `python -m trust_bench crash --seeds 1-150` and `python -m trust_bench staterun
--seeds 1-200` (an exact 95% lower bound of 0.98 needs 183 trials with no miss, so `staterun` runs
200 seeds per class).

`crash`: a child process runs one scripted `doc` or `data` case through `AgentTaskRunner` with a
checkpointer, `checkpoint_interval_seconds=0.02` and 40 ms of latency per model call, and is
killed with `os._exit` at the k-th model request (k is drawn from the seed: uniformly from 1 to 6
in 70% of the trials, which every scripted run reaches, and from 7 to 27 in the rest; a run
shorter than k finishes normally and is counted as a trial that did not crash);
half of the trials also leave half a record at the end of the audit log, the artifact log and
the checkpoint file. Then, in the parent:

| Id | Claim | Pass rule | Threshold source |
|---|---|---|---|
| V2.2a | After an abrupt exit no verifier raises an alarm | 0 of n trials with a chain break, unreadable store or `unreadable`/`chain-broken` verdict; exact upper <= 0.02 | zero-event bound for n >= 149 |
| V2.2b | Checkpoints and anchors taken before the crash read as `matches` or `advanced` | 0 contradicting verdicts; exact upper <= 0.02 | zero-event bound |
| V2.2c | A writer can resume: audit, artifact and checkpoint appends succeed and every chain still verifies | 0 failures; exact upper <= 0.02 | zero-event bound |
| V2.2d | Cutting any store back past its newest checkpoint is detected | detection rate over trials with a checkpoint: exact lower >= 0.98 | v1 P1.4 rule |
| V2.2e (expectation) | Cutting back to exactly the newest checkpoint is not detected: the writes since the last checkpoint are unprotected | reported rate of undetected cuts; expected >= 0.9 | design: states the durability boundary |

`staterun`: a graph run of a seeded chain of three to six nodes is executed by `HarnessCoordinator(anchorer=)`
with scripted node executors, then its record is attacked by classes `edit` (no reseal),
`reseal` (re-saved consistently with changed content), `truncate` (re-saved with fewer history
entries), `result` (a node result rewritten, history intact) and `delete` (record removed).

| Id | Claim | Pass rule | Threshold source |
|---|---|---|---|
| V2.2f | Every class on the run record is detected with the anchor, and an untouched run reads as `matches` | 0 misses and exact lower >= 0.98 per class (n = 200); 0 false alarms, exact upper <= 0.02 | v1 P1.4 rule |
| V2.2g (expectation) | `reseal`, `truncate` and `result` pass the store's own integrity check | reported; expected 100% of those trials | design |

### 3.3 V2.3 Authority boundary under adversarial conditions (fault injection)

Command: `python -m trust_bench boundary --seeds 1-150`. Each job is one seed of nine scenarios
run against the real `BaseAgent`, `CapabilityToolAuthorizer`, `HarnessToolExecutor` and
`ApprovalRegistry`. A *violation* is a consequential call (a write, a process start) that ran
without a valid, unspent, unrevoked approval of its own run and node for its own arguments, or a
second use of a single-use approval.

| Scenario | Adversarial condition |
|---|---|
| `custom-executor` | a host-supplied `ToolExecutor` with an authorizer on the agent that denies |
| `single-use-race` | 2 to 16 concurrent calls (threads) racing for one single-use approval, with two registry instances on one file |
| `revoked` | calls after the approval is revoked, with retries of the same call id |
| `foreign` | an approval of another run or node injected into the approval map |
| `bound-arguments` | an approval for one set of arguments presented with another |
| `malformed` | non-string, empty, traversing, absolute, credential and NUL-containing paths |
| `delegation` | a child node and an elastic child asking with the parent's approvals |
| `state-change` | the capability grant narrowed between two calls |
| `retry-storm` | the same denied call repeated 3 to 20 times |

| Id | Claim | Pass rule | Threshold source |
|---|---|---|---|
| V2.3a | No violation in any scenario | 0 violations; exact upper <= 0.02 per scenario (n >= 149 trials each) | zero-event bound |
| V2.3b | Every denial, consumption and revocation has its event | evidence gaps 0; exact upper <= 0.02 | zero-event bound |
| V2.3c (instrument check) | The battery can fail: against the control arms (an agent with no authorizer, and a standing approval reused) violations are found | at least one violation in at least half of the seeds of `custom-executor`, `single-use-race` and `foreign` | design: v1 P2.0/P3.0 practice |

### 3.4 V2.4 Lineage (fault injection on synthetic chains with known ground truth)

Command: `python -m trust_bench lineage --seeds 1-150`. Each job builds a chain of 2 to 5 runs in
which one write, one process output, one fetched page, one search or one stored result carries
a defect and the later runs read or derive from it, plus decoys: identical content written twice
(ambiguity), a near-copy of a defective line, a defect introduced by a run's own decision, and
a defect that reached an artifact by a path the journal does not record.

| Id | Claim | Pass rule | Threshold source |
|---|---|---|---|
| V2.4a | The origin is found when the records contain it | exact-match rate over chains whose origin is recorded: Wilson lower >= 0.95 | design: v1 P1.2 |
| V2.4b | It never names an origin the records do not contain | unsupported origin answers 0; exact upper <= 0.02 | zero-event bound |
| V2.4c | Ambiguity is reported, not resolved | unique answers on ambiguous chains 0; exact upper <= 0.02 | zero-event bound |
| V2.4d | Relations are labelled by what supports them | 0 relations with `causal=true`; 0 `inferred` relations without `infer_verbatim`; 0 `inferred` relations for a near-copy | zero-event bound |
| V2.4e | A chain through an unrecorded path ends with a statement of what was not seen | all such chains: `ok` or no origin with the limitation stated | design |

### 3.5 V2.5 Real-agent integrity check (real-agent tier)

`python -m trust_bench realruns` verifies, with the current verifiers and read-only, every run of
the committed `example_runs` ledgers that produced telemetry and audit transcripts from a live
model: chains, terminal links where present and artifact histories. Criterion: **0 false
alarms is not demanded**, because those ledgers predate several changes; the report lists every
run with the verdict and the reason, and a break is investigated before it is called a bug. No
live model is called by any v2 battery. **A new live-model run is NOT RUN** and stays so until
the owner allows model calls.

## 4. Protocol

* Split `dev` only. Seeds as above. Scripted solvers, no model calls, no network.
* The order is: this file is committed; the batteries are implemented; each battery's
  instrument check (V2.3c, V2.2e/g as expectations) is run; the batteries run once at full size;
  nothing is tuned against the result. If an implementation defect is found, the fix and its
  reproducer are committed and the whole battery is rerun; both runs are kept and reported.
* v1 batteries (`run`, `verify`, `attack`, `authority`, `chain`) are rerun on the changed SDK to
  show that the v1 card did not regress; they use the v1 criteria and are reported in their own
  section.

## 5. Statistics

As v1: exact bounds for zero-event criteria, Wilson for proportions, `n >= 149` per cell for a
0.02 bound. Where fewer trials exist a cell is reported with its bound and is not claimed.

## 6. What a pass would and would not mean

* V2.1 to V2.4 pass: on the injected fault classes, with these seeds, the SDK's own checks
  detected what they were built to detect, recovered from the crashes injected, and never ran a
  consequential call that the authorization rules forbid. Nothing more.
* It does **not** mean the SDK is trustworthy in general. The following are outside every
  battery and are stated in the report next to the numbers:
  * an attacker who can rewrite both the evidence and its trust anchor (the signing key, the
    anchor sink) defeats every anchor; anchors are independent only while the key and the sink
    are out of the writer's reach;
  * writes made after the newest checkpoint or anchor are not protected until the next one, and
    a power loss can drop what was not synced;
  * a host that supplies its own `ToolExecutor` and no authorizer, or runs processes with its
    own privileges, is not governed by the SDK; the SDK cannot see what a registered command
    does inside its process, a network call made outside its tools, or a file changed by
    anything but `ArtifactStore`;
  * a lineage trace follows only reads the journal recorded; data that reached a model or an
    artifact by another path is invisible, and an exact match between two texts is a hint;
  * a scripted solver is not a live model: behaviour of a model that reasons about its
    approvals or its tools is untested.

## 7. Reporting

`python -m trust_bench report-v2 --out reports/v2_dev.md` writes one document with: the three
tiers separately; each criterion as claim, estimate, bound, rule, verdict; the instrument
checks; the v1 regression section; the limitations of section 6 repeated; and the list of what
remains open. Verdicts are PASS, FAIL or NOT RUN only.

## 8. Source-level properties (not statistical)

Pinned by tests and reported as a list of test names with their result, never as a rate:
approval consumption is atomic; a revoked approval authorizes nothing; an approval of another
run or node is refused; a rejected authorization never reaches an executor when the agent holds
an authorizer; edges of a graph cannot be changed in place; an OTLP export with a ledger sends
each record once; histogram bounds follow the unit; the index of incoming edges is rebuilt when
an edge is added or switched.

## 9. Deviations and decisions left to the owner

* The v1 lock (`python -m trust_bench lock`) was never run. The SDK source hash it would pin
  changed after v1's dev run; locking is a decision for the owner and is not done here.
* No test-split (confirmatory) run was made. The held-out family `plan` and the graph tamper
  battery stay unrun.
* A live-model run is not made without the owner's permission.
* Any change to this file after a battery has run is a dated entry here, with the reason; the
  text above is not edited.
