# Merkle tree heads, Ed25519 and witness: measured overhead

Python 3.13.1 on Windows-11-10.0.26300-SP0. 3000 telemetry events, 200,000 accumulator leaves. Times are per operation.

## Per-event cost

| operation | median (us) | p95 (us) |
|---|---|---|
| telemetry emit (SQLite write, redaction, hash chain) | 2291.3 | 3293.1 |
| the same emit plus a Merkle accumulator append | 2277.9 | 3260.8 |
| accumulator append alone | 2.70 | 9.60 |
| accumulator root after 200k leaves | 9.00 | 9.30 |

The Merkle append costs 2.7 us at the median, 0.12% of the 2291 us the emit already costs. The end-to-end difference between the two emit rows (-13 us) is smaller than the run-to-run variation of the SQLite write and should be read as zero, not as a speed-up. For context the Agent Flight Recorder paper reports about 48 us per event for its full stack, on other hardware with another event model.

## Storage

The tree uses the integrity hashes the ledgers already hold, so it stores nothing per event. A signed head is one record per ledger per checkpoint:

| signer | head record (bytes) | per event, checkpoint every 100 | every 1,000 |
|---|---|---|---|
| hmac | 313 | 3.13 | 0.313 |
| ed25519 | 378 | 3.78 | 0.378 |

The Agent Flight Recorder paper reports 512 bytes per event; its events carry their own data, so the figures are not like for like.

## Signing

| signer | sign median (us) | verify median (us) |
|---|---|---|
| hmac | 5.9 | 6.3 |
| ed25519 | 84.1 | 240.7 |

## Publishing a head from scratch (re-hashing n records)

| n | HMAC (ms) | Ed25519 (ms) |
|---|---|---|
| 1,000 | 10.9 | 9.5 |
| 10,000 | 48.4 | 48.0 |
| 100,000 | 428.8 | 431.6 |

With the live accumulator the head costs one root computation and one signature whatever n is.

## Verification: proofs against re-reading the log

Re-verifying the hash chain costs 69.5 us per event and grows with the log. A holder of two signed heads checks an extension, or one record, with a proof:

| n | inclusion proof (bytes) | verify (us) | extension proof (bytes) | verify (us) | chain re-verify at n (ms, extrapolated) |
|---|---|---|---|---|---|
| 1,000 | 320 | 21.1 | 352 | 30.8 | 70 |
| 10,000 | 448 | 29.4 | 480 | 37.5 | 695 |
| 100,000 | 544 | 34.9 | 576 | 45.3 | 6,953 |
| 262,144 | 576 | 38.1 | 608 | 39.9 | 18,226 |

Chain re-verification is measured at 3,000 events and scaled linearly; every proof figure is measured.
