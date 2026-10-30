# Trust Profile v2: evidence and authority architecture, development split

Commit `8b4917f`. Pre-registration: `PREREGISTRATION_V2.md` (v1 and its results unchanged).

This report keeps three tiers apart. **A pass here is not a claim that the SDK is trustworthy**: it is a statement about the injected fault classes, the seeds and the scripted solvers named in the pre-registration, and section 6 of that file lists what no battery covers.

## Card

| id | claim | estimate | verdict |
|---|---|---|---|
| V2.1a | with the anchor every class on every store is detected | 14 of 22 cells pass; too few trials for 0.98: artifact-content/delete 120/120, artifact-content/edit 120/120, artifacts/delete 120/120, artifacts/edit 120/120, artifacts/insert 120/120, artifacts/reorder 90/90, artifacts/reseal 120/120, artifacts/truncate 60/60 | NOT RUN |
| V2.1b | an untouched run raises no alarm | 0/120; exact upper 0.0247; fewer than the 149 trials a 0.02 bound needs | NOT RUN |
| V2.1c | (expectation) without the anchor the SDK is tamper-evident only | cells with undetected tampering: artifacts/delete, artifacts/reseal, artifacts/truncate, audit/reseal, telemetry/delete, telemetry/reseal, telemetry/truncate | held |
| V2.2a | after an abrupt exit no verifier raises an alarm | 0/300; exact upper 0.0099 | PASS |
| V2.2b | checkpoints and anchors read as matches or advanced | 0/300; exact upper 0.0099 | PASS |
| V2.2c | a writer can resume and every chain still verifies | 0/300; exact upper 0.0099 | PASS |
| V2.2d | cutting past the newest checkpoint is detected | 635/635; exact lower 0.9953 | PASS |
| V2.2e | (expectation) cutting back to the newest checkpoint is not detected | 358/358 = 1.0000 undetected | held |
| V2.2f | every class on the run record is detected with the anchor | failing classes: none; false alarms 0/1000; exact upper 0.0030; harness errors 0 | PASS |
| V2.2g | (expectation) reseal, truncate and result pass the store's own check | reseal 1.0000, result 1.0000, truncate 1.0000 | held |
| V2.3a | no violation in any scenario | failing scenarios: none | PASS |
| V2.3b | every denial, consumption and revocation has its event | 0/1350; exact upper 0.0022 | PASS |
| V2.3c | (instrument check) the battery can fail | custom-executor control: 150/150 seeds with a violation; single-use-race control: 150/150 seeds with a violation; foreign control: 150/150 seeds with a violation | PASS |
| V2.4a | the origin is found when the records contain it | 1200/1200; Wilson lower 0.9968 | PASS |
| V2.4b | it never names an origin the records do not contain | 0/300; exact upper 0.0099 | PASS |
| V2.4c | ambiguity is reported, not resolved | 0/150; exact upper 0.0198 | PASS |
| V2.4d | relations are labelled by what supports them | 0 causal, 0 inferred without the option, 0 inferred for a near-copy; 150/150 exact copies marked inferred; 0/1500; exact upper 0.0020 | PASS |
| V2.4e | a chain through an unrecorded path ends with a statement of what was not seen | 150/150; harness errors 0 | PASS |

Rows marked *held* or *did not hold* are expectations that describe a limit of the design; they have no pass or fail.

## Tier 1: source-level properties

pytest exit status 0. A passing unit test is source-level evidence for the inputs it covers; it is not a rate and is not a statement about code it does not exercise.

| property | tests passed | failing |
|---|---|---|
| approval consumption is atomic across threads and processes | 1/1 | none |
| a revoked approval authorizes nothing | 1/1 | none |
| an approval of another run or node is refused | 1/1 | none |
| an authorizer on the agent decides before a custom executor | 10/10 | none |
| every grant, consumption, revocation, expiry and denial is an event | 12/12 | none |
| approver-only revocation over MCP, bound to the request hash | 4/4 | none |
| an agent cannot grant or revoke its own approvals | 8/8 | none |
| the artifact log is anchored by a signed record outside the workspace | 15/15 | none |
| the graph run record is anchored | 7/7 | none |
| periodic checkpoints and graph-node anchoring | 4/4 | none |
| torn audit and checkpoint tails are read as ending before them and repaired | 7/7 | none |
| lineage separates observed relations from inferred ones | 14/14 | none |
| an OTLP export with a ledger sends each record once; bounds follow the unit | 8/8 | none |
| graph edges cannot be changed in place and the index follows a switch | 7/7 | none |

## Tier 2: fault injection

### V2.1 Tamper battery with anchors

### Detection by cell, arm `langgraph`

| cell | detected | exact lower |
|---|---|---|
| messages/delete | 0/600 | 0.0000 |
| messages/edit | 0/600 | 0.0000 |
| messages/insert | 0/600 | 0.0000 |
| messages/reorder | 0/600 | 0.0000 |
| messages/truncate | 0/540 | 0.0000 |

### Detection by cell, arm `sdk`

| cell | detected | exact lower |
|---|---|---|
| artifact-content/delete | 120/120 | 0.9753 |
| artifact-content/edit | 120/120 | 0.9753 |
| artifacts/delete | 60/120 | 0.4214 |
| artifacts/edit | 120/120 | 0.9753 |
| artifacts/insert | 120/120 | 0.9753 |
| artifacts/reorder | 90/90 | 0.9673 |
| artifacts/reseal | 0/120 | 0.0000 |
| artifacts/truncate | 0/60 | 0.0000 |
| audit/delete | 600/600 | 0.9950 |
| audit/edit | 600/600 | 0.9950 |
| audit/insert | 600/600 | 0.9950 |
| audit/reorder | 600/600 | 0.9950 |
| audit/reseal | 540/600 | 0.8775 |
| audit/truncate | 540/540 | 0.9945 |
| journal/delete | 460/460 | 0.9935 |
| journal/edit | 460/460 | 0.9935 |
| telemetry/delete | 540/600 | 0.8775 |
| telemetry/edit | 600/600 | 0.9950 |
| telemetry/insert | 600/600 | 0.9950 |
| telemetry/reorder | 600/600 | 0.9950 |
| telemetry/reseal | 540/600 | 0.8775 |
| telemetry/truncate | 480/540 | 0.8641 |

### Detection by cell, arm `sdk+anchor`

| cell | detected | exact lower |
|---|---|---|
| artifact-content/delete | 120/120 | 0.9753 |
| artifact-content/edit | 120/120 | 0.9753 |
| artifacts/delete | 120/120 | 0.9753 |
| artifacts/edit | 120/120 | 0.9753 |
| artifacts/insert | 120/120 | 0.9753 |
| artifacts/reorder | 90/90 | 0.9673 |
| artifacts/reseal | 120/120 | 0.9753 |
| artifacts/truncate | 60/60 | 0.9513 |
| audit/delete | 600/600 | 0.9950 |
| audit/edit | 600/600 | 0.9950 |
| audit/insert | 600/600 | 0.9950 |
| audit/reorder | 600/600 | 0.9950 |
| audit/reseal | 600/600 | 0.9950 |
| audit/truncate | 540/540 | 0.9945 |
| journal/delete | 460/460 | 0.9935 |
| journal/edit | 460/460 | 0.9935 |
| telemetry/delete | 600/600 | 0.9950 |
| telemetry/edit | 600/600 | 0.9950 |
| telemetry/insert | 600/600 | 0.9950 |
| telemetry/reorder | 600/600 | 0.9950 |
| telemetry/reseal | 600/600 | 0.9950 |
| telemetry/truncate | 540/540 | 0.9945 |


### V2.2 Crash recovery

300 trials (239 ended by the injected crash, 61 finished before it); 300 had a checkpoint when they ended; 635 cut attempts past a checkpoint.

| store | cut past the newest checkpoint: detected |
|---|---|
| artifacts | 40/40 |
| audit | 295/295 |
| telemetry | 300/300 |

### V2.2 Anchored graph-run record

| class | anchor detects | exact lower | the store's own check detects |
|---|---|---|---|
| delete | 200/200 | 0.9851 | 200/200 |
| edit | 200/200 | 0.9851 | 200/200 |
| reseal | 200/200 | 0.9851 | 0/200 |
| result | 200/200 | 0.9851 | 0/200 |
| truncate | 200/200 | 0.9851 | 0/200 |

### V2.3 Authority boundary under adversarial conditions

| scenario | calls attempted | calls that ran | trials with a violation | harness errors | verdict |
|---|---|---|---|---|---|
| bound-arguments | 552 | 0 | 0/150; exact upper 0.0198 | 0 | PASS |
| custom-executor | 354 | 0 | 0/150; exact upper 0.0198 | 0 | PASS |
| delegation | 363 | 0 | 0/150; exact upper 0.0198 | 0 | PASS |
| foreign | 453 | 0 | 0/150; exact upper 0.0198 | 0 | PASS |
| malformed | 1179 | 0 | 0/150; exact upper 0.0198 | 0 | PASS |
| retry-storm | 1869 | 0 | 0/150; exact upper 0.0198 | 0 | PASS |
| revoked | 1082 | 0 | 0/150; exact upper 0.0198 | 0 | PASS |
| single-use-race | 1329 | 150 | 0/150; exact upper 0.0198 | 0 | PASS |
| state-change | 453 | 0 | 0/150; exact upper 0.0198 | 0 | PASS |

### V2.4 Lineage on synthetic chains

| chain kind | origin kind as built |
|---|---|
| ambiguous | 150/150 |
| artifact-chain | 150/150 |
| near-copy | 150/150 |
| own-decision | 150/150 |
| process-origin | 150/150 |
| search-origin | 150/150 |
| stored-result | 150/150 |
| unrecorded | 150/150 |
| verbatim-copy | 150/150 |
| web-origin | 150/150 |

## Tier 3: real-agent evidence

### Committed live-model ledgers, verified read-only (V2.5)

44 runs in 7 committed scenarios were verified read-only on copies; 0 rows report a break or an error. These ledgers were written by live models before several of the changes above; a break is listed with its reason and is not called a defect until it has been investigated.

| scenario | run | events/records | telemetry or artifact chain | audit chain | terminal link |
|---|---|---|---|---|---|
| house_fence_scenario | orchestration-1 | 5 | intact | intact | absent |
| house_fence_scenario | run-1 | 2 | intact | intact | absent |
| house_fence_scenario | fence | 27 | intact | intact | absent |
| house_fence_scenario | house | 55 | intact | intact | absent |
| house_fence_scenario | plan | 16 | intact | intact | absent |
| house_fence_scenario | controller-1 | 3 | intact | intact | absent |
| house_fence_scenario | (artifact write log) | 0 | intact |  |  |
| monopoly_game | monopoly-run | 1401 | intact | intact | absent |
| monopoly_game | (artifact write log) | 0 | intact |  |  |
| pdf_document_test | pdf-doc-tfla | 170 | intact | intact | absent |
| pdf_document_test | pdf-doc-speculative-decoding | 40 | intact | intact | absent |
| pdf_document_test | (artifact write log) | 0 | intact |  |  |
| research_tools_test | research-speculative-decoding | 56 | intact | intact | absent |
| research_tools_test | research-singapore-news | 32 | intact | intact | absent |
| research_tools_test | (artifact write log) | 0 | intact |  |  |
| tool_capability_survey | tool-survey | 172 | intact | intact | absent |
| tool_capability_survey | (artifact write log) | 0 | intact |  |  |
| university_live_scenario | orchestration-2 | 5 | intact | intact | absent |
| university_live_scenario | run-1 | 2 | intact | intact | absent |
| university_live_scenario | plan_medicine | 39 | intact | intact | absent |
| university_live_scenario | plan_arts | 55 | intact | intact | absent |
| university_live_scenario | plan_business | 39 | intact | intact | absent |
| university_live_scenario | plan_computing | 39 | intact | intact | absent |
| university_live_scenario | plan_law | 39 | intact | intact | absent |
| university_live_scenario | plan_engineering | 39 | intact | intact | absent |
| university_live_scenario | survey_law | 25 | intact | intact | absent |
| university_live_scenario | survey_arts | 24 | intact | intact | absent |
| university_live_scenario | survey_medicine | 25 | intact | intact | absent |
| university_live_scenario | survey_engineering | 24 | intact | intact | absent |
| university_live_scenario | survey_computing | 24 | intact | intact | absent |
| university_live_scenario | survey_business | 24 | intact | intact | absent |
| university_live_scenario | controller-1 | 3 | intact | intact | absent |
| university_live_scenario | orchestration-1 | 1 | intact | intact | absent |
| university_live_scenario | (artifact write log) | 0 | intact |  |  |
| university_scenario | orchestration-2 | 5 | intact | intact | absent |
| university_scenario | run-1 | 2 | intact | intact | absent |
| university_scenario | plan_business | 39 | intact | intact | absent |
| university_scenario | plan_law | 39 | intact | intact | absent |
| university_scenario | plan_engineering | 39 | intact | intact | absent |
| university_scenario | plan_medicine | 39 | intact | intact | absent |
| university_scenario | plan_arts | 39 | intact | intact | absent |
| university_scenario | plan_computing | 55 | intact | intact | absent |
| university_scenario | survey_computing | 16 | intact | intact | absent |
| university_scenario | survey_engineering | 16 | intact | intact | absent |
| university_scenario | survey_medicine | 16 | intact | intact | absent |
| university_scenario | survey_arts | 16 | intact | intact | absent |
| university_scenario | survey_law | 16 | intact | intact | absent |
| university_scenario | survey_business | 16 | intact | intact | absent |
| university_scenario | controller-1 | 3 | intact | intact | absent |
| university_scenario | orchestration-1 | 1 | intact | intact | absent |
| university_scenario | (artifact write log) | 0 | intact |  |  |

### New live-model runs

**NOT RUN.** No battery calls a model, and a live-model run needs the owner's permission. Nothing in tiers 1 and 2 shows how a live model behaves under these controls.

## v1 regression

See `reports\v2_dev_v1_regression.md`.

## What remains open

* Anchors are independent only while the signing key and the anchor sink are outside the writer's reach; an attacker who rewrites both the evidence and its anchor is not resisted.
* Writes after the newest checkpoint or anchor are unprotected until the next one (V2.2e), and a power loss can drop what was not synced.
* A host that supplies a `ToolExecutor` without an authorizer, a registered command that does something outside the SDK's tools, and files changed outside `ArtifactStore` are not governed or recorded by the SDK.
* Lineage follows only the retrievals the journal recorded; an exact textual match is a hint.
* The v1 lock and the test-split confirmatory run are not made; a live-model run is not made.
