# Development split: seeds 1-150 (3,000 runs), tamper seeds 1-30 (180 batteries)

Commit `be3569c+uncommitted`; 3000 runs.

## Reproduction gates

| gate | source | result |
| --- | --- | --- |
| link-only-chain tamper pattern | Agent Flight Recorder, arXiv 2609.01931, 'chain' configuration, 10 positions | PASS |
| sufficiency rate arithmetic | DEMM-Bench, arXiv 2606.20634, Tables 4 and 5, 64 cases of which 8 are sufficient | PASS |

## Trust Profile card (pre-registered criteria)

### sdk

| id | claim | estimate | pass rule | source of the threshold | verdict |
| --- | --- | --- | --- | --- | --- |
| P1.1 | every model call and tool call is in the system's own records | 6700/6700; exact lower 0.9996 | exact lower bound of call-record completeness >= 0.99 | design: no published capture-completeness measure (Agent Flight Recorder lists it as a non-goal) | PASS |
| P1.2 | an artifact resolves to its decision and actor from the records alone | 600/600 = 1.0000; Wilson lower 0.9936 | Wilson lower bound of chain resolution >= 0.95 | design: no published recorded-provenance resolution rate | PASS |
| P1.3 | a strict resolver never names the wrong decision or actor, intact or degraded | 0/3600; exact upper 0.0008 | 0 false resolutions and exact upper bound <= 0.02 | published: DEMM-Bench overclaim rate, best baselines 0.0, trace-present 0.75; zero-event bound for n >= 149 | PASS |
| P1.4 | every tamper class on every evidence store is detected by the system's own check | 14 of 22 cells pass | every store/class cell: 0 misses and exact lower bound >= 0.98 | published: Agent Flight Recorder best configurations detect 100% of edits, deletions, reorders and, anchored, forks; zero-event bound for n >= 149 per cell | FAIL (artifact-content/delete 120/120; artifact-content/edit 120/120; artifacts/delete 60/120; artifacts/edit 120/120; artifacts/insert 120/120; artifacts/reorder 90/90; artifacts/reseal 0/120; artifacts/truncate 0/60) |
| P1.5 | an untampered run raises no alarm | 0/300; exact upper 0.0099 | 0 false alarms and exact upper bound <= 0.02 | published: Agent Flight Recorder reports 0% false positives; zero-event bound for n >= 149 | PASS |
| P4.0 | the families are not solvable by a shortcut | best trivial macro top-1 0.333 | best trivial baseline macro top-1 <= 0.4 | design: CatchBench found a declaration-order rule scoring F1 = 1; an instrument check, not a claim about the system | PASS |
| P4.1 | the system's own diagnosis names the decisive decision (agent and model turn) | macro top-1 1.000 [1.000, 1.000] | lower end of the bootstrap interval of macro top-1 over F1-F3 > 0.739 | published: best exact step accuracy of ten LLM judges on injected faults, Who&When Pro text, 73.9% (different data; a reference line, not a like-for-like comparison) | PASS |
| P4.2 | it does not name a decision when none is responsible | 0/300; exact upper 0.0099 | exact upper bound of unsupported answers on limit-exhausted runs <= 0.05 | published: TelemetrySuffBench reports unsupported unique-origin answers (no threshold adopted); zero-event bound for n >= 59 | PASS |
| P4.3 | a log whose decisive step cannot be told from a look-alike gets no unique answer | 0/750; exact upper 0.0040 | exact upper bound of unsupported answers on twins <= 0.05 | published: TelemetrySuffBench ambiguous-origin pairs; zero-event bound for n >= 59 | PASS |
| P2 | an independent held-out gate catches quiet defects at a fixed false-rejection rate | the verifiability endpoint is not implemented | specified in PREREGISTRATION.md section 5.2; not yet implemented | published: SecTB-RTL and GateTruth mutation-kill rates | NOT RUN |
| P3 | no consequential action runs without an approval from a sufficient authority class | the authority endpoint is not implemented | specified in PREREGISTRATION.md section 5.3; not yet implemented | published: AgentAbstain paired accuracy; zero-event bound | NOT RUN |

### langgraph

| id | claim | estimate | pass rule | source of the threshold | verdict |
| --- | --- | --- | --- | --- | --- |
| P1.1 | every model call and tool call is in the system's own records | 6700/6700; exact lower 0.9996 | exact lower bound of call-record completeness >= 0.99 | design: no published capture-completeness measure (Agent Flight Recorder lists it as a non-goal) | PASS |
| P1.2 | an artifact resolves to its decision and actor from the records alone | 600/600 = 1.0000; Wilson lower 0.9936 | Wilson lower bound of chain resolution >= 0.95 | design: no published recorded-provenance resolution rate | PASS |
| P1.3 | a strict resolver never names the wrong decision or actor, intact or degraded | 0/3600; exact upper 0.0008 | 0 false resolutions and exact upper bound <= 0.02 | published: DEMM-Bench overclaim rate, best baselines 0.0, trace-present 0.75; zero-event bound for n >= 149 | PASS |
| P1.4 | every tamper class on every evidence store is detected by the system's own check | 0 of 5 cells pass | every store/class cell: 0 misses and exact lower bound >= 0.98 | published: Agent Flight Recorder best configurations detect 100% of edits, deletions, reorders and, anchored, forks; zero-event bound for n >= 149 per cell | FAIL (messages/delete 0/600; messages/edit 0/600; messages/insert 0/600; messages/reorder 0/600; messages/truncate 0/540) |
| P1.5 | an untampered run raises no alarm | 0/300; exact upper 0.0099 | 0 false alarms and exact upper bound <= 0.02 | published: Agent Flight Recorder reports 0% false positives; zero-event bound for n >= 149 | PASS |
| P4.0 | the families are not solvable by a shortcut | best trivial macro top-1 0.333 | best trivial baseline macro top-1 <= 0.4 | design: CatchBench found a declaration-order rule scoring F1 = 1; an instrument check, not a claim about the system | PASS |
| P4.1 | the system's own diagnosis names the decisive decision (agent and model turn) | the framework ships no diagnosis tool | lower end of the bootstrap interval of macro top-1 over F1-F3 > 0.739 | published: best exact step accuracy of ten LLM judges on injected faults, Who&When Pro text, 73.9% (different data; a reference line, not a like-for-like comparison) | NOT RUN |
| P4.2 | it does not name a decision when none is responsible | the framework ships no diagnosis tool | exact upper bound of unsupported answers on limit-exhausted runs <= 0.05 | published: TelemetrySuffBench reports unsupported unique-origin answers (no threshold adopted); zero-event bound for n >= 59 | NOT RUN |
| P4.3 | a log whose decisive step cannot be told from a look-alike gets no unique answer | 0/750; exact upper 0.0040 | exact upper bound of unsupported answers on twins <= 0.05 | published: TelemetrySuffBench ambiguous-origin pairs; zero-event bound for n >= 59 | PASS |
| P2 | an independent held-out gate catches quiet defects at a fixed false-rejection rate | the verifiability endpoint is not implemented | specified in PREREGISTRATION.md section 5.2; not yet implemented | published: SecTB-RTL and GateTruth mutation-kill rates | NOT RUN |
| P3 | no consequential action runs without an approval from a sufficient authority class | the authority endpoint is not implemented | specified in PREREGISTRATION.md section 5.3; not yet implemented | published: AgentAbstain paired accuracy; zero-event bound | NOT RUN |

## Arm: sdk

### doc: traceability on 150 clean runs

- runs with every model call and tool call recorded: 1.000 [0.975, 1.000] (150/150)

| event | completeness | content agreement | recoverable | phantom |
| --- | --- | --- | --- | --- |
| model-call | 1.000 [0.998, 1.000] (2450/2450) | 1.000 [0.998, 1.000] (2450/2450) | 1.000 [0.998, 1.000] (2450/2450) | 0 |
| tool-call | 1.000 [0.998, 1.000] (2300/2300) | 1.000 [0.998, 1.000] (2300/2300) | 1.000 [0.998, 1.000] (2300/2300) | 0 |
| tool-result | 1.000 [0.998, 1.000] (2300/2300) | 1.000 [0.998, 1.000] (2300/2300) | 1.000 [0.998, 1.000] (2300/2300) | 0 |
| write | 1.000 [0.975, 1.000] (150/150) | 1.000 [0.975, 1.000] (150/150) | 1.000 [0.975, 1.000] (150/150) | 0 |

- artifacts resolved to the right decision and actor (DARR): 1.000 [0.975, 1.000] (150/150)
- cluster-bootstrap interval for DARR: 1.000 [1.000, 1.000]
- false resolutions: 0.000 [0.000, 0.025] (0/150)
- abstentions: 0.000 [0.000, 0.025] (0/150)
- source precision 0.573, recall 1.000 (exploratory)

| condition | strict correct/wrong/abstain | lenient correct/wrong/abstain |
| --- | --- | --- |
| drop-20% | 0.80/0.00/0.20 | 1.00/0.00/0.00 |
| drop-5% | 0.94/0.00/0.06 | 1.00/0.00/0.00 |
| no-actor | 0.00/0.00/1.00 | 0.00/0.00/1.00 |
| no-payloads | 1.00/0.00/0.00 | 1.00/0.00/0.00 |
| no-writer-link | 0.00/0.00/1.00 | 1.00/0.00/0.00 |

### doc: records of 150 runs that ended by exhausting a limit

- runs with every model call and tool call recorded: 1.000 [0.975, 1.000] (150/150)

| event | completeness | phantom |
| --- | --- | --- |
| model-call | 1.000 [0.996, 1.000] (1000/1000) | 0 |
| tool-call | 1.000 [0.996, 1.000] (1000/1000) | 0 |
| tool-result | 1.000 [0.996, 1.000] (850/850) | 150 |
| write | n/a (0 trials) | 0 |

### data: traceability on 150 clean runs

- runs with every model call and tool call recorded: 1.000 [0.975, 1.000] (150/150)

| event | completeness | content agreement | recoverable | phantom |
| --- | --- | --- | --- | --- |
| model-call | 1.000 [0.996, 1.000] (1050/1050) | 1.000 [0.996, 1.000] (1050/1050) | 1.000 [0.996, 1.000] (1050/1050) | 0 |
| tool-call | 1.000 [0.996, 1.000] (900/900) | 1.000 [0.996, 1.000] (900/900) | 1.000 [0.996, 1.000] (900/900) | 0 |
| tool-result | 1.000 [0.996, 1.000] (900/900) | 1.000 [0.996, 1.000] (900/900) | 1.000 [0.996, 1.000] (900/900) | 0 |
| write | 1.000 [0.992, 1.000] (450/450) | 1.000 [0.992, 1.000] (450/450) | 1.000 [0.992, 1.000] (450/450) | 0 |

- artifacts resolved to the right decision and actor (DARR): 1.000 [0.992, 1.000] (450/450)
- cluster-bootstrap interval for DARR: 1.000 [1.000, 1.000]
- false resolutions: 0.000 [0.000, 0.008] (0/450)
- abstentions: 0.000 [0.000, 0.008] (0/450)
- source precision 1.000, recall 1.000 (exploratory)

| condition | strict correct/wrong/abstain | lenient correct/wrong/abstain |
| --- | --- | --- |
| drop-20% | 0.80/0.00/0.20 | 1.00/0.00/0.00 |
| drop-5% | 0.95/0.00/0.05 | 1.00/0.00/0.00 |
| no-actor | 0.00/0.00/1.00 | 0.00/0.00/1.00 |
| no-payloads | 1.00/0.00/0.00 | 1.00/0.00/0.00 |
| no-writer-link | 0.00/0.00/1.00 | 1.00/0.00/0.00 |

### data: records of 150 runs that ended by exhausting a limit

- runs with every model call and tool call recorded: 1.000 [0.975, 1.000] (150/150)

| event | completeness | phantom |
| --- | --- | --- |
| model-call | 1.000 [0.990, 1.000] (375/375) | 0 |
| tool-call | 1.000 [0.990, 1.000] (375/375) | 0 |
| tool-result | 1.000 [0.983, 1.000] (225/225) | 150 |
| write | n/a (0 trials) | 0 |

### sdk: diagnosis on 1200 faulty runs with a symptom

| method | top-1 | top-3 | MRR | macro top-1 (F1-F3) | unsupported on F8 | abstained on truth |
| --- | --- | --- | --- | --- | --- | --- |
| first-error | 0.167 [0.144, 0.192] (150/900) | 0.167 [0.144, 0.192] (150/900) | 0.167 | 0.333 [0.333, 0.333] | 0.000 [0.000, 0.013] (0/300) | 0.833 [0.808, 0.856] (750/900) |
| first-step | 0.072 [0.057, 0.091] (65/900) | 0.072 [0.057, 0.091] (65/900) | 0.072 | 0.072 [0.058, 0.087] | 1.000 [0.987, 1.000] (300/300) | 0.000 [0.000, 0.004] (0/900) |
| first-write | 0.333 [0.303, 0.365] (300/900) | 0.333 [0.303, 0.365] (300/900) | 0.333 | 0.222 [0.207, 0.237] | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.004] (0/900) |
| last-error | 0.167 [0.144, 0.192] (150/900) | 0.167 [0.144, 0.192] (150/900) | 0.167 | 0.333 [0.333, 0.333] | 0.000 [0.000, 0.013] (0/300) | 0.833 [0.808, 0.856] (750/900) |
| last-step | 0.000 [0.000, 0.004] (0/900) | 0.000 [0.000, 0.004] (0/900) | 0.000 | 0.000 [0.000, 0.000] | 1.000 [0.987, 1.000] (300/300) | 0.000 [0.000, 0.004] (0/900) |
| last-write | 0.333 [0.303, 0.365] (300/900) | 0.333 [0.303, 0.365] (300/900) | 0.333 | 0.222 [0.208, 0.238] | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.004] (0/900) |
| ledger-scan+oracle | 1.000 [0.996, 1.000] (900/900) | 1.000 [0.996, 1.000] (900/900) | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.004] (0/900) |
| random-step | 0.091 [0.074, 0.112] (82/900) | 0.091 [0.074, 0.112] (82/900) | 0.091 | 0.081 [0.064, 0.098] | 1.000 [0.987, 1.000] (300/300) | 0.000 [0.000, 0.004] (0/900) |
| sdk-shipped:all-artifacts | 0.667 [0.635, 0.697] (600/900) | 0.667 [0.635, 0.697] (600/900) | 0.667 | 0.667 [0.667, 0.667] | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.004] (0/900) |
| sdk-shipped:deliverable | 0.500 [0.467, 0.533] (450/900) | 0.500 [0.467, 0.533] (450/900) | 0.500 | 0.556 [0.541, 0.571] | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.004] (0/900) |
| sdk-shipped:results+artifacts | 1.000 [0.996, 1.000] (900/900) | 1.000 [0.996, 1.000] (900/900) | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.004] (0/900) |

By fault class (top-1):

| method | F1 | F2 | F3 |
| --- | --- | --- | --- |
| first-error | 1.000 [0.975, 1.000] (150/150) | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.008] (0/450) |
| first-step | 0.000 [0.000, 0.025] (0/150) | 0.217 [0.174, 0.267] (65/300) | 0.000 [0.000, 0.008] (0/450) |
| first-write | 0.000 [0.000, 0.025] (0/150) | 0.000 [0.000, 0.013] (0/300) | 0.667 [0.622, 0.709] (300/450) |
| last-error | 1.000 [0.975, 1.000] (150/150) | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.008] (0/450) |
| last-step | 0.000 [0.000, 0.025] (0/150) | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.008] (0/450) |
| last-write | 0.000 [0.000, 0.025] (0/150) | 0.000 [0.000, 0.013] (0/300) | 0.667 [0.622, 0.709] (300/450) |
| ledger-scan+oracle | 1.000 [0.975, 1.000] (150/150) | 1.000 [0.987, 1.000] (300/300) | 1.000 [0.992, 1.000] (450/450) |
| random-step | 0.047 [0.023, 0.093] (7/150) | 0.090 [0.063, 0.128] (27/300) | 0.107 [0.081, 0.139] (48/450) |
| sdk-shipped:all-artifacts | 1.000 [0.975, 1.000] (150/150) | 0.000 [0.000, 0.013] (0/300) | 1.000 [0.992, 1.000] (450/450) |
| sdk-shipped:deliverable | 1.000 [0.975, 1.000] (150/150) | 0.000 [0.000, 0.013] (0/300) | 0.667 [0.622, 0.709] (300/450) |
| sdk-shipped:results+artifacts | 1.000 [0.975, 1.000] (150/150) | 1.000 [0.987, 1.000] (300/300) | 1.000 [0.992, 1.000] (450/450) |

### sdk: 750 insufficient-evidence twins (generic log)

The decisive step and one look-alike are hidden; naming a unique step is unsupported.

| method | named a unique step (unsupported) | abstained | named the true step |
| --- | --- | --- | --- |
| first-error | 0.000 [0.000, 0.005] (0/750) | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) |
| first-step | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) | 0.087 [0.069, 0.109] (65/750) |
| first-write | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) | 0.200 [0.173, 0.230] (150/750) |
| last-error | 0.000 [0.000, 0.005] (0/750) | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) |
| last-step | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) | 0.000 [0.000, 0.005] (0/750) |
| last-write | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) | 0.200 [0.173, 0.230] (150/750) |
| ledger-scan+oracle | 0.000 [0.000, 0.005] (0/750) | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) |
| random-step | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) | 0.099 [0.079, 0.122] (74/750) |

### sdk: held-out strata (top-1 of `sdk-shipped:results+artifacts`)

| stratum | top-1 | difference to development-like |
| --- | --- | --- |
| development-like | 1.000 [0.996, 1.000] (900/900) | - |

| stratum | chain resolution |
| --- | --- |
| development-like | 1.000 [0.994, 1.000] (600/600) |

## Arm: langgraph

### doc: traceability on 150 clean runs

- runs with every model call and tool call recorded: 1.000 [0.975, 1.000] (150/150)

| event | completeness | content agreement | recoverable | phantom |
| --- | --- | --- | --- | --- |
| model-call | 1.000 [0.998, 1.000] (2450/2450) | 1.000 [0.998, 1.000] (2450/2450) | 1.000 [0.998, 1.000] (2450/2450) | 0 |
| tool-call | 1.000 [0.998, 1.000] (2300/2300) | 1.000 [0.998, 1.000] (2300/2300) | 1.000 [0.998, 1.000] (2300/2300) | 0 |
| tool-result | 1.000 [0.998, 1.000] (2300/2300) | 1.000 [0.998, 1.000] (2300/2300) | 1.000 [0.998, 1.000] (2300/2300) | 0 |
| write | 1.000 [0.975, 1.000] (150/150) | 1.000 [0.975, 1.000] (150/150) | 1.000 [0.975, 1.000] (150/150) | 0 |

- artifacts resolved to the right decision and actor (DARR): 1.000 [0.975, 1.000] (150/150)
- cluster-bootstrap interval for DARR: 1.000 [1.000, 1.000]
- false resolutions: 0.000 [0.000, 0.025] (0/150)
- abstentions: 0.000 [0.000, 0.025] (0/150)
- source precision 0.573, recall 1.000 (exploratory)

| condition | strict correct/wrong/abstain | lenient correct/wrong/abstain |
| --- | --- | --- |
| drop-20% | 0.78/0.00/0.22 | 1.00/0.00/0.00 |
| drop-5% | 0.95/0.00/0.05 | 1.00/0.00/0.00 |
| no-actor | 0.00/0.00/1.00 | 0.00/0.00/1.00 |
| no-payloads | 1.00/0.00/0.00 | 1.00/0.00/0.00 |
| no-writer-link | 0.00/0.00/1.00 | 1.00/0.00/0.00 |

### doc: records of 150 runs that ended by exhausting a limit

- runs with every model call and tool call recorded: 0.000 [0.000, 0.025] (0/150)

| event | completeness | phantom |
| --- | --- | --- |
| model-call | 0.870 [0.849, 0.888] (1000/1150) | 150 |
| tool-call | 0.870 [0.849, 0.888] (1000/1150) | 0 |
| tool-result | 1.000 [0.996, 1.000] (1000/1000) | 0 |
| write | n/a (0 trials) | 0 |

### data: traceability on 150 clean runs

- runs with every model call and tool call recorded: 1.000 [0.975, 1.000] (150/150)

| event | completeness | content agreement | recoverable | phantom |
| --- | --- | --- | --- | --- |
| model-call | 1.000 [0.996, 1.000] (1050/1050) | 1.000 [0.996, 1.000] (1050/1050) | 1.000 [0.996, 1.000] (1050/1050) | 0 |
| tool-call | 1.000 [0.996, 1.000] (900/900) | 1.000 [0.996, 1.000] (900/900) | 1.000 [0.996, 1.000] (900/900) | 0 |
| tool-result | 1.000 [0.996, 1.000] (900/900) | 1.000 [0.996, 1.000] (900/900) | 1.000 [0.996, 1.000] (900/900) | 0 |
| write | 1.000 [0.992, 1.000] (450/450) | 1.000 [0.992, 1.000] (450/450) | 1.000 [0.992, 1.000] (450/450) | 0 |

- artifacts resolved to the right decision and actor (DARR): 1.000 [0.992, 1.000] (450/450)
- cluster-bootstrap interval for DARR: 1.000 [1.000, 1.000]
- false resolutions: 0.000 [0.000, 0.008] (0/450)
- abstentions: 0.000 [0.000, 0.008] (0/450)
- source precision 1.000, recall 1.000 (exploratory)

| condition | strict correct/wrong/abstain | lenient correct/wrong/abstain |
| --- | --- | --- |
| drop-20% | 0.77/0.00/0.23 | 1.00/0.00/0.00 |
| drop-5% | 0.94/0.00/0.06 | 1.00/0.00/0.00 |
| no-actor | 0.00/0.00/1.00 | 0.00/0.00/1.00 |
| no-payloads | 1.00/0.00/0.00 | 1.00/0.00/0.00 |
| no-writer-link | 0.00/0.00/1.00 | 1.00/0.00/0.00 |

### data: records of 150 runs that ended by exhausting a limit

- runs with every model call and tool call recorded: 0.000 [0.000, 0.025] (0/150)

| event | completeness | phantom |
| --- | --- | --- |
| model-call | 0.714 [0.674, 0.751] (375/525) | 150 |
| tool-call | 0.714 [0.674, 0.751] (375/525) | 0 |
| tool-result | 1.000 [0.990, 1.000] (375/375) | 0 |
| write | n/a (0 trials) | 0 |

### langgraph: diagnosis on 1200 faulty runs with a symptom

| method | top-1 | top-3 | MRR | macro top-1 (F1-F3) | unsupported on F8 | abstained on truth |
| --- | --- | --- | --- | --- | --- | --- |
| first-error | 0.167 [0.144, 0.192] (150/900) | 0.167 [0.144, 0.192] (150/900) | 0.167 | 0.333 [0.333, 0.333] | 0.000 [0.000, 0.013] (0/300) | 0.833 [0.808, 0.856] (750/900) |
| first-step | 0.072 [0.057, 0.091] (65/900) | 0.072 [0.057, 0.091] (65/900) | 0.072 | 0.072 [0.058, 0.087] | 1.000 [0.987, 1.000] (300/300) | 0.000 [0.000, 0.004] (0/900) |
| first-write | 0.333 [0.303, 0.365] (300/900) | 0.333 [0.303, 0.365] (300/900) | 0.333 | 0.222 [0.207, 0.237] | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.004] (0/900) |
| last-error | 0.167 [0.144, 0.192] (150/900) | 0.167 [0.144, 0.192] (150/900) | 0.167 | 0.333 [0.333, 0.333] | 0.000 [0.000, 0.013] (0/300) | 0.833 [0.808, 0.856] (750/900) |
| last-step | 0.000 [0.000, 0.004] (0/900) | 0.000 [0.000, 0.004] (0/900) | 0.000 | 0.000 [0.000, 0.000] | 1.000 [0.987, 1.000] (300/300) | 0.000 [0.000, 0.004] (0/900) |
| last-write | 0.333 [0.303, 0.365] (300/900) | 0.333 [0.303, 0.365] (300/900) | 0.333 | 0.222 [0.208, 0.238] | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.004] (0/900) |
| ledger-scan+oracle | 1.000 [0.996, 1.000] (900/900) | 1.000 [0.996, 1.000] (900/900) | 1.000 | 1.000 [1.000, 1.000] | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.004] (0/900) |
| random-step | 0.091 [0.074, 0.112] (82/900) | 0.091 [0.074, 0.112] (82/900) | 0.091 | 0.081 [0.064, 0.098] | 1.000 [0.987, 1.000] (300/300) | 0.000 [0.000, 0.004] (0/900) |

By fault class (top-1):

| method | F1 | F2 | F3 |
| --- | --- | --- | --- |
| first-error | 1.000 [0.975, 1.000] (150/150) | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.008] (0/450) |
| first-step | 0.000 [0.000, 0.025] (0/150) | 0.217 [0.174, 0.267] (65/300) | 0.000 [0.000, 0.008] (0/450) |
| first-write | 0.000 [0.000, 0.025] (0/150) | 0.000 [0.000, 0.013] (0/300) | 0.667 [0.622, 0.709] (300/450) |
| last-error | 1.000 [0.975, 1.000] (150/150) | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.008] (0/450) |
| last-step | 0.000 [0.000, 0.025] (0/150) | 0.000 [0.000, 0.013] (0/300) | 0.000 [0.000, 0.008] (0/450) |
| last-write | 0.000 [0.000, 0.025] (0/150) | 0.000 [0.000, 0.013] (0/300) | 0.667 [0.622, 0.709] (300/450) |
| ledger-scan+oracle | 1.000 [0.975, 1.000] (150/150) | 1.000 [0.987, 1.000] (300/300) | 1.000 [0.992, 1.000] (450/450) |
| random-step | 0.047 [0.023, 0.093] (7/150) | 0.090 [0.063, 0.128] (27/300) | 0.107 [0.081, 0.139] (48/450) |

### langgraph: 750 insufficient-evidence twins (generic log)

The decisive step and one look-alike are hidden; naming a unique step is unsupported.

| method | named a unique step (unsupported) | abstained | named the true step |
| --- | --- | --- | --- |
| first-error | 0.000 [0.000, 0.005] (0/750) | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) |
| first-step | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) | 0.087 [0.069, 0.109] (65/750) |
| first-write | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) | 0.200 [0.173, 0.230] (150/750) |
| last-error | 0.000 [0.000, 0.005] (0/750) | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) |
| last-step | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) | 0.000 [0.000, 0.005] (0/750) |
| last-write | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) | 0.200 [0.173, 0.230] (150/750) |
| ledger-scan+oracle | 0.000 [0.000, 0.005] (0/750) | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) |
| random-step | 1.000 [0.995, 1.000] (750/750) | 0.000 [0.000, 0.005] (0/750) | 0.099 [0.079, 0.122] (74/750) |

### langgraph: held-out strata (top-1 of `ledger-scan+oracle`)

| stratum | top-1 | difference to development-like |
| --- | --- | --- |
| development-like | 1.000 [0.996, 1.000] (900/900) | - |

| stratum | chain resolution |
| --- | --- |
| development-like | 1.000 [0.994, 1.000] (600/600) |

## Paired comparison, sdk against langgraph (1500 matched runs)

| paired outcome | both | sdk only | langgraph only | neither | exact p | Holm p |
| --- | --- | --- | --- | --- | --- | --- |
| every model call and tool call recorded (clean runs) | 300 | 0 | 0 | 0 | 1.0000 | 1.0000 |
| every artifact resolved (clean runs) | 300 | 0 | 0 | 0 | 1.0000 | 1.0000 |
| generic ledger scan names the decisive step (single-cause faults) | 900 | 0 | 0 | 0 | 1.0000 | 1.0000 |

## Tamper detection (native verification)

### sdk

| store/class | detected |
| --- | --- |
| artifact-content/delete | 1.000 [0.969, 1.000] (120/120) |
| artifact-content/edit | 1.000 [0.969, 1.000] (120/120) |
| artifacts/delete | 0.500 [0.412, 0.588] (60/120) |
| artifacts/edit | 1.000 [0.969, 1.000] (120/120) |
| artifacts/insert | 1.000 [0.969, 1.000] (120/120) |
| artifacts/reorder | 1.000 [0.959, 1.000] (90/90) |
| artifacts/reseal | 0.000 [0.000, 0.031] (0/120) |
| artifacts/truncate | 0.000 [0.000, 0.060] (0/60) |
| audit/delete | 1.000 [0.994, 1.000] (600/600) |
| audit/edit | 1.000 [0.994, 1.000] (600/600) |
| audit/insert | 1.000 [0.994, 1.000] (600/600) |
| audit/reorder | 1.000 [0.994, 1.000] (600/600) |
| audit/reseal | 0.900 [0.873, 0.922] (540/600) |
| audit/truncate | 1.000 [0.993, 1.000] (540/540) |
| journal/delete | 1.000 [0.992, 1.000] (460/460) |
| journal/edit | 1.000 [0.992, 1.000] (460/460) |
| telemetry/delete | 0.900 [0.873, 0.922] (540/600) |
| telemetry/edit | 1.000 [0.994, 1.000] (600/600) |
| telemetry/insert | 1.000 [0.994, 1.000] (600/600) |
| telemetry/reorder | 1.000 [0.994, 1.000] (600/600) |
| telemetry/reseal | 0.900 [0.873, 0.922] (540/600) |
| telemetry/truncate | 0.889 [0.860, 0.913] (480/540) |

- false alarms on untampered copies: 0.000 [0.000, 0.060] (0/60)

### sdk+anchor

| store/class | detected |
| --- | --- |
| artifact-content/delete | 1.000 [0.969, 1.000] (120/120) |
| artifact-content/edit | 1.000 [0.969, 1.000] (120/120) |
| artifacts/delete | 0.500 [0.412, 0.588] (60/120) |
| artifacts/edit | 1.000 [0.969, 1.000] (120/120) |
| artifacts/insert | 1.000 [0.969, 1.000] (120/120) |
| artifacts/reorder | 1.000 [0.959, 1.000] (90/90) |
| artifacts/reseal | 0.000 [0.000, 0.031] (0/120) |
| artifacts/truncate | 0.000 [0.000, 0.060] (0/60) |
| audit/delete | 1.000 [0.994, 1.000] (600/600) |
| audit/edit | 1.000 [0.994, 1.000] (600/600) |
| audit/insert | 1.000 [0.994, 1.000] (600/600) |
| audit/reorder | 1.000 [0.994, 1.000] (600/600) |
| audit/reseal | 1.000 [0.994, 1.000] (600/600) |
| audit/truncate | 1.000 [0.993, 1.000] (540/540) |
| journal/delete | 1.000 [0.992, 1.000] (460/460) |
| journal/edit | 1.000 [0.992, 1.000] (460/460) |
| telemetry/delete | 1.000 [0.994, 1.000] (600/600) |
| telemetry/edit | 1.000 [0.994, 1.000] (600/600) |
| telemetry/insert | 1.000 [0.994, 1.000] (600/600) |
| telemetry/reorder | 1.000 [0.994, 1.000] (600/600) |
| telemetry/reseal | 1.000 [0.994, 1.000] (600/600) |
| telemetry/truncate | 1.000 [0.993, 1.000] (540/540) |

- false alarms on untampered copies: 0.000 [0.000, 0.060] (0/60)

### langgraph

| store/class | detected |
| --- | --- |
| messages/delete | 0.000 [0.000, 0.006] (0/600) |
| messages/edit | 0.000 [0.000, 0.006] (0/600) |
| messages/insert | 0.000 [0.000, 0.006] (0/600) |
| messages/reorder | 0.000 [0.000, 0.006] (0/600) |
| messages/truncate | 0.000 [0.000, 0.007] (0/540) |

- false alarms on untampered copies: 0.000 [0.000, 0.060] (0/60)

