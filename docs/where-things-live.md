# Where things live

Start from the symptom, open the file named, and you are in the right place. Each file has one job.

## Conventions

- One responsibility per module. A package folder (`recorder/`, `evidence_profile/`) groups files that share one subject.
- An error code's prefix names the module that raises it (`RECORDER_*` comes from `observability/recorder/`). `tests/test_error_code_ownership.py` fails when a module raises another module's prefix.
- Each verdict has one producer. The check that decides it is the only place to change it.
- Every definition is described in `docs/reference/`; `tests/test_reference_docs.py` keeps that in step with the code.

## A bug: where does it come from?

| Symptom | Look in |
| --- | --- |
| An audit or telemetry chain does not verify | `observability/audit_log.py`, `observability/telemetry_store.py` |
| Hashes differ between writer and verifier | `foundations/jcs.py` (canonical form), then the ledger's schema tag |
| A signature is rejected, or a key is "unknown" or "revoked" | `observability/keyring.py`, `observability/signed_records.py` |
| A signer or HMAC/Ed25519 mismatch | `observability/signers.py`, `observability/ed25519.py` |
| A tree head or inclusion proof fails | `observability/merkle.py`, `observability/transparency.py` |
| A run-head checkpoint is missing or does not match | `observability/run_checkpoints.py`, `agent/run_checkpointer.py` |
| Checkpoints are not written where expected | `observability/sinks.py` |
| The last telemetry event and last audit entry disagree | `observability/terminal_link.py` |
| A timestamp is rejected | `observability/timestamp.py` |
| Event times run backwards | `observability/clock_check.py` |
| A payload was cut short | `observability/payload_bounds.py` |
| A witness refuses or does not answer | `observability/witness.py` |
| The recorder refuses a request or an append | `observability/recorder/protocol.py` (wire format), `core.py` (what it accepts) |
| The recorder cannot be reached, TLS fails, a read-back is cut | `recorder/client.py`, `recorder/tls.py`, `recorder/server.py` |
| The recorder and a host ledger disagree | `recorder/compare.py`, `recorder/mirror.py` |
| One slow recorder stalls other runs | `recorder/client.py` (connection pool), `recorder/core.py` (per-run gates) |
| A profile fails to load or build | `agent/evidence_profile/settings.py`, `build.py` |
| A profile verdict is wrong | `agent/evidence_profile/checks.py`, one function per check |
| The profile object behaves oddly | `agent/evidence_profile/profile.py` |

## A feature: where does it go?

| You want to | Add it to |
| --- | --- |
| Verify one more thing about a finished run | a function in `agent/evidence_profile/checks.py`, then add it to `CHECKS` |
| Add a setting to the evidence profile | `agent/evidence_profile/settings.py`, then read it in `build.py` |
| Keep checkpoints somewhere new | a class beside `FileCheckpointSink` in `observability/sinks.py` |
| Sign with a new algorithm | `observability/signers.py`; resolve its keys in `keyring.py` |
| Add a recorder command | handler in `recorder/server.py`, logic in `recorder/core.py`, call in `recorder/client.py`, wire names in `recorder/protocol.py` |
| Add a new kind of ledger record | the ledger's module, then `observability/signed_records.py` so it is checked the same way |

When you add a file, add its entry under `docs/reference/` and run the documentation tests.
