# Evidence and observability: standards mapping and limits

Written 2026-11-01 against the code at that date. It states, requirement by requirement, what
`nailong_agent_sdk` implements, what it implements only in part and what it does not. It is a
working map for engineers, not a compliance claim, an audit or legal advice.

## How to read this

* **Yes**: implemented and covered by a test.
* **Partly**: implemented with a stated difference, or only when an option is switched on.
* **No**: not implemented.
* **Unverified**: I did not read the code that would decide it.

**Source quality.** No finished standard for agent logging exists. The IETF text below is an
individual Internet-Draft ("Agent Audit Trail", revision 06) that calls itself work in progress. The
EU harmonised logging standards (prEN 18229-1, ISO/IEC 24970) are drafts. The OpenTelemetry GenAI
conventions are marked experimental. The NIST and OWASP rows rest on secondary summaries, not the
primary text, and the EU application dates differ between sources (check Article 113). Treat every
row as "my reading of a moving target".

## What the evidence is, and who it defends against

The SDK keeps two ledgers per run (audit entries and telemetry events), each a hash chain. Everything
else is optional evidence kept where the agent cannot rewrite it. Each piece answers one attack:

| Piece | Defends against | Does not defend against |
|---|---|---|
| Hash chain (`-v2` records hash RFC 8785 JCS bytes) | An edit or deletion in the middle of a ledger | Truncating the newest records, or rewriting the whole chain |
| Signed run-head checkpoint (HMAC or Ed25519) | Truncation and rewrite after the checkpoint | Anything between checkpoints; a thief of the signing key |
| Signed Merkle tree heads, inclusion and extension proofs | A log that changed history between two heads | A writer who shows two verifiers different heads (no gossip or cosigning) |
| Out-of-process recorder (own key, own copy) | A host process that edits or deletes its own ledger after sending | An attacker with the recorder's account or disk; anything sent before the recorder was attached |
| Remote append-only witness | Rewriting checkpoints kept beside the run | A witness operator who colludes |
| RFC 3161 timestamp on a checkpoint or head | Backdating past the authority's attestation; a revoked key's late signatures | Records between checkpoints; an authority whose certificate was revoked (not checked) |
| Key ring with windows and revocation | Use of a retired or revoked key, once the time is trusted | Backdating without a trusted timestamp |
| Signed tombstones | A deleted run explained away by a forged retention record | Runs pruned before signing was on |
| Recorder that checks record text and stamps its own receipt time | A host that sends text which does not match the hash it announced; a record held back or dated in the future | A host that never sends a record at all |
| Recorder-signed attestation (`final` on the closing seal) | A forged or truncated ledger when the host's own key cannot be trusted; a run that ended without the host closing it | Anything the host did not send before it was compromised |
| Per-run scoped tokens, TLS and client certificates | A leaked token writing into another run; an eavesdropper or an impostor recorder | A host that holds the token for its own run and lies in it |
| Evidence profile | Leaving one of the above switched off by accident | A compromised host (see below) |

## Against a hostile host

Suppose the machine that runs the agent is controlled by an adversary. Software cannot make that
machine honest; it can make what the machine already said hard to unsay. The protection is a
*prefix* property: every record the host sent to the recorder before the compromise, and the
recorder accepted, is evidence the host can no longer change, and the recorder's signature (not a key
on the host) says so. What it can still do:

* **Stop reporting.** A host that sends nothing leaves no trace of what it did next. What is
  detectable is the silence: a run the recorder never saw closed (`not-final`), records in the
  local ledger the recorder never received (`recorder` fails under `require_final_attestation`), and
  records whose claimed time is far from the recorder's receipt time.
* **Lie in new records.** Everything after the compromise is as true as the host chooses.
* **Take its own run's token.** A scoped token opens one run, so the damage is that run, and the
  recorder refuses a record whose text does not match its announced hash.

To get this, run the recorder on another machine or account, over TLS with client certificates,
holding the only signing key; put the profile on the host with no `signing_key_file`, tokens in files
with owner-only permissions, and `require_final_attestation`. The operator's own machine, the
recorder's disk and the recorder's key are then the trust base. The recorder is a single point; a
second independent witness and trusted timestamps reduce, and do not remove, that dependence.

## IETF draft-sharif-agent-audit-trail-06

| Section | Requirement | Status | Where / difference |
|---|---|---|---|
| 3.1 | `record_id` UUIDv4 | Partly | Telemetry events have `event_id`; audit entries are identified by run and `sequence` |
| 3.1 | `timestamp` RFC 3339 UTC | Yes | `occurred_at_utc` |
| 3.1 | `agent_id`, `session_id` | Yes | `agent_id` in the telemetry context; the run id is the session |
| 3.1 | `agent_version` SemVer | Partly | Instruction version plus `definition_sha256` (`agent/definition_digest.py`) |
| 3.1 | `action_type` from a registry | Partly | `event_type`, an open vocabulary |
| 3.1 | `outcome` closed set | Partly | `status` is free text |
| 3.1 | `trust_level` L0 to L4 | No | No such concept |
| 3.1 | `parent_record_id`, `prev_hash` | Partly | `sequence` and `previous_hash`. The hash is over the record's content with its own hash blanked, not "the previous record's JCS bytes", so a verifier written to the draft will not reproduce it |
| 3.1 | `record_phase` pre/post | Partly | `tool-requested` (with `arguments_sha256`) precedes `tool-result`; no field says which a record is |
| 3.3 | Timestamps non-decreasing | Partly | A caller-supplied `occurred_at_utc` is stored as given and still verifies; the evidence profile reports where times run backwards (fails only with `require_ordered_time`), and the recorder adds its own receipt time |
| 3.3 | Records at most 64 KB | Partly | Payloads over 8 KiB keep identity fields, lengths and digests plus a preview |
| 4 | Pre-execution record of state-changing actions | Partly | Intent is recorded before a tool runs, but arguments are stored only after it ran (as a digest before). No risk classification |
| 5 | Independent recorder, own key, distinct OS principal | Partly | Separate process, own key, signed attestations, TLS with client certificates and per-run tokens (`observability/recorder.py`, `observability/attestation.py`); the same OS account unless the operator arranges otherwise |
| 5.3 | Consequential actions default to L2+ | No | |
| 6.1 | Chain with SHA-256 over RFC 8785 | Yes | `foundations/jcs.py`, `-v2` schemas; `-v1` records still verify |
| 6.2 | Per-record signature (ES256, ML-DSA-65) | No | Only checkpoints and heads are signed; Ed25519 and HMAC only; no post-quantum |
| 6.3 | Verifier checks: links, signatures, gaps | Yes | `chain_break`, `verify_run_head_checkpoints`, `verify_terminal_link` |
| 6.3 | Completeness when no `session_end` is "inconclusive" | Partly | The recorder's attestation is `final` only when the host closed the run; `not-final` is a distinct verdict. The local terminal link still reports `absent` |
| 6.4 | Merkle batches, externally anchored | Yes | RFC 6962/9162 trees (`observability/merkle.py`), witness, timestamps |
| 7 | Registered action types and fields | Partly | Tool, model, lifecycle and error events exist under other names |
| 8 | Genesis, session end, heartbeat, orphan detection | Partly | `run-started` carries the configuration digest; `terminal-link` and the recorder's final seal close a run; an unclosed run is detectable afterwards (`not-final`); no live heartbeat or monitor |
| 9 | Retention, per-record signed tombstones | Partly | Whole-run tombstones, signed when the pass is given a signer; no per-record erasure; retention minimums are not enforced |
| 10 | JSONL, Syslog, CSV export | Partly | The ledgers are JSONL and OTLP export exists; no Syslog or CSV |
| 12 | Store hashes, not raw input and output | No | Redacted, bounded payloads are stored |
| 13 | Decision reproducibility | No | Hosted models are at best reconstructable |

## EU AI Act, Article 12 (and 26(6))

| Requirement | Status | Notes |
|---|---|---|
| Automatic recording of events over the system's lifetime | Yes | Always on for `BaseAgent` with stores |
| Traceability of functioning | Yes | Ledgers, failure diagnosis and attribution tools |
| Identify situations that present a risk or a substantial modification | Partly | Failed, blocked and watchdog events; configuration digests expose a changed definition. No classifier |
| Support post-market monitoring | Partly | Metrics and OTLP export |
| Support operational oversight by deployers | Partly | Approvals record who decided and under which authority |
| Keep logs at least six months (deployers) | No | Nothing prunes unless the caller runs retention; no enforced minimum |
| Conform to a recognised standard | No | None is final |

## NIST SP 800-53 AU family (my reading)

| Control | Status | Notes |
|---|---|---|
| AU-3 content of records | Partly | Actor, authority, time, status, run and trace ids; no source address |
| AU-8 time stamps | Partly | Local clock; optional RFC 3161 on checkpoints and heads |
| AU-9 protection of audit information | Partly | Cryptographic protection and separate storage when configured; access control is the operating system's |
| AU-10 non-repudiation | Partly | Asymmetric signatures with key ids, rotation and revocation; no per-record signature, and human identity is a credential name |
| AU-11 retention | Partly | Caller-chosen policy |
| AU-12 generation | Yes | |

## OpenTelemetry GenAI conventions (experimental)

| Item | Status | Notes |
|---|---|---|
| `invoke_agent` span | Yes | `gen_ai.agent.name`, `gen_ai.agent.id`, `gen_ai.agent.version`, `nailong.agent.definition_sha256` |
| `chat` span | Partly | `gen_ai.provider.name`, `gen_ai.request.model`; response model and usage attributes not found |
| `execute_tool` span | Partly | `gen_ai.tool.name`, `gen_ai.tool.call.id`; tool type not found |
| `create_agent` span, GenAI metrics, content-capture events | Unverified | `observability/otlp.py` was not read in full |

## OWASP agentic Top 10, ASI08 (repudiation and untraceability)

The point is a record the agent could not have written differently. That holds only when the
recorder runs under an account the agent cannot use, the signing key is out of its reach, and the
evidence profile is on. By default the checkpoint key lives under `~/.nailong-agent-sdk`, in the
same account as the run.

## Protocol standards

| Standard | Status | Notes |
|---|---|---|
| RFC 6962 / 9162 Merkle trees and proofs | Yes | The published certificate-transparency roots, and every inclusion and consistency proof between every pair of sizes up to a bound |
| RFC 8785 JSON canonicalization | Yes | Number, key-order and structure cases written from the RFC's examples; no second implementation was compared. Integers beyond ±2^53−1 are written exactly |
| RFC 3161 time-stamp protocol | Partly | Client and verifier. Not checked: certificate revocation, signing-certificate attribute, policy OID, RSA-PSS; requests use SHA-256 only |
| RFC 9943 SCITT | No | Not a goal; a checkpoint with a witness and a timestamp is a lighter relative |

## Limitations to design around

1. **Host compromise.** Root on the writer's host reads the signing key and the recorder tokens.
2. **No gossip.** Nothing compares what different verifiers were shown, so a writer could present
   different heads to different parties.
3. **Granularity.** Stamps and signatures cover checkpoints and heads, not each record. A tail
   written after the newest checkpoint is covered only by the recorder's copy.
4. **Revocation relies on time.** A key's window is checked against the time the record claims
   unless a trusted timestamp replaces it. Without timestamps a thief can backdate.
5. **Recorder transport.** TLS with client certificates is available; without it, tokens and every
   record cross the socket unencrypted, and the profile refuses a non-loopback recorder unless
   `allow_remote_plaintext` is set.
6. **Adoption and recovery.** A recorder attached to a run that already has records answers
   `RECORDER_GAP` until `backfill()` sends the history. Tombstones written before signing was
   switched on fail a profile that requires signatures (`truncated`), because they cannot be
   re-signed without rewriting the log.
7. **Cost.** About 290 audit appends per second with an fsync each; a synchronous recorder adds a
   round trip per record and, when unreachable, up to two timeouts per record. The recorder keeps
   every record in memory.
8. **Large payloads.** Over 8 KiB, an audit payload keeps its short fields, lengths and SHA-256
   digests plus a preview; the rest is in the tool-result journal, which retention can delete.
9. **Redaction is pattern-based.** A secret in a shape no pattern knows is stored.
10. **Clocks.** Caller-supplied times are accepted by the stores. The profile reports regressions
    and compares them with the recorder's clock; two clocks that disagree by more than the tolerance
    look like an attack.
11. **Scope.** Evidence shows what happened, not whether the model's answer was right.
12. **Unknown versions.** A ledger written by a newer schema is refused by name, not read.
13. **Errors that are not specific.** A telemetry payload nested deeper than the serializer allows
    fails with "Circular reference detected (depth exceeded)".

## The strongest configuration

```json
{
  "signing_key_file": "/secure/writer.key",
  "keyring_file": "/etc/nailong/keyring.json",
  "witness": {"url": "https://witness.example/", "append_token_variable": "WITNESS_APPEND",
              "read_token_variable": "WITNESS_READ"},
  "recorder": {"host": "127.0.0.1", "port": 7600, "append_token_variable": "REC_APPEND",
               "read_token_variable": "REC_READ", "public_key_file": "/etc/nailong/recorder.pub"},
  "timestamp_authority_url": "https://tsa.example/",
  "trusted_roots": ["/etc/nailong/tsa-root.pem"],
  "require_recorder": true,
  "require_timestamps": true
}
```

Check a run with `nailong-agent-sdk-dev verify-run-evidence <run_root> <run_id> profile.json`. Run
the recorder as another operating-system user, keep its key file readable by that user only, and
rotate keys with `KeyRing.rotated`.
