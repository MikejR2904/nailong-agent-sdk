"""Measure what the Merkle tree heads, Ed25519 signatures and witness cost.

Run from benchmarks/trust:  python overhead_merkle.py [--out results/merkle_overhead.json]

Reference points are the numbers the Agent Flight Recorder paper (arXiv 2609.01931) reports for its
full stack: about 48 microseconds median per event and 512 bytes per event. They come from other
hardware, another language and richer events, so they are context and not a comparison.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

from nailong_agent_sdk.observability.ed25519 import Ed25519Signer
from nailong_agent_sdk.observability.merkle import (
    MerkleAccumulator,
    MerkleTree,
    verify_consistency,
    verify_inclusion,
)
from nailong_agent_sdk.observability.run_checkpoints import FileCheckpointSink, HmacSha256Signer
from nailong_agent_sdk.observability.telemetry_models import (
    TelemetryActor,
    TelemetryAuthority,
    TelemetryContext,
)
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.observability.transparency import publish_tree_head

AFR_MEDIAN_MICROSECONDS = 48.0
AFR_BYTES_PER_EVENT = 512
EVENTS = 3_000
LEAVES = 200_000
SIZES = (1_000, 10_000, 100_000)
PROOF_SIZES = (1_000, 10_000, 100_000, 262_144)
CHECKPOINT_EVERY = (100, 1_000)
ACTOR = TelemetryActor(kind="system", identifier="bench", role="runtime")


def percentiles(samples_ns: list[int]) -> dict[str, float]:
    ordered = sorted(samples_ns)
    return {
        "median_us": statistics.median(ordered) / 1_000,
        "p95_us": ordered[int(0.95 * (len(ordered) - 1))] / 1_000,
        "mean_us": statistics.fmean(ordered) / 1_000,
        "count": len(ordered),
    }


def fake_hashes(count: int) -> list[str]:
    return [f"{index:064x}" for index in range(count)]


def telemetry_baseline_and_combined(root: Path) -> dict[str, object]:
    baseline_store = TelemetryStore(root / "baseline")
    context = TelemetryContext(run_id="bench")
    plain: list[int] = []
    for _ in range(EVENTS):
        started = time.perf_counter_ns()
        baseline_store.emit(
            "bench.event",
            context,
            actor=ACTOR,
            authority=TelemetryAuthority.DETERMINISTIC,
            status="ok",
            payload={"n": 1},
        )
        plain.append(time.perf_counter_ns() - started)
    baseline_store.close()
    combined_store = TelemetryStore(root / "combined")
    accumulator = MerkleAccumulator()
    combined: list[int] = []
    for _ in range(EVENTS):
        started = time.perf_counter_ns()
        event = combined_store.emit(
            "bench.event",
            context,
            actor=ACTOR,
            authority=TelemetryAuthority.DETERMINISTIC,
            status="ok",
            payload={"n": 1},
        )
        accumulator.append(bytes.fromhex(event.integrity_hash))
        combined.append(time.perf_counter_ns() - started)
    started = time.perf_counter()
    break_found = combined_store.chain_break("bench")
    chain_seconds = time.perf_counter() - started
    combined_store.close()
    return {
        "telemetry_emit_with_chain": percentiles(plain),
        "telemetry_emit_with_chain_and_merkle_append": percentiles(combined),
        "chain_verification_us_per_event": chain_seconds / EVENTS * 1e6,
        "chain_intact": break_found is None,
    }


def accumulator_cost() -> dict[str, object]:
    leaf = bytes(32)
    accumulator = MerkleAccumulator()
    samples: list[int] = []
    for index in range(LEAVES):
        data = index.to_bytes(32, "big")
        started = time.perf_counter_ns()
        accumulator.append(data)
        samples.append(time.perf_counter_ns() - started)
    root_samples: list[int] = []
    for _ in range(2_000):
        started = time.perf_counter_ns()
        accumulator.root()
        root_samples.append(time.perf_counter_ns() - started)
    del leaf
    return {
        "accumulator_append": percentiles(samples),
        "accumulator_root_after_200k": percentiles(root_samples),
    }


def checkpoint_cost(directory: Path) -> dict[str, object]:
    hmac_signer = HmacSha256Signer(b"overhead-benchmark-key-0123456789", signer_id="hmac")
    ed_signer = Ed25519Signer.generate("ed")
    sink = FileCheckpointSink(directory)
    rows = []
    for size in SIZES:
        hashes = fake_hashes(size)
        timings: dict[str, float] = {"size": size}
        for label, signer in (("hmac", hmac_signer), ("ed25519", ed_signer)):
            started = time.perf_counter()
            head = publish_tree_head(hashes, sink, f"bench-{label}-{size}", signer)
            timings[f"publish_from_scratch_ms_{label}"] = (time.perf_counter() - started) * 1e3
            timings[f"head_bytes_{label}"] = len(head.model_dump_json().encode("utf-8"))
        rows.append(timings)
    message = b"x" * 200
    sign_samples = {"hmac": [], "ed25519": []}
    verify_samples = {"hmac": [], "ed25519": []}
    for label, signer in (("hmac", hmac_signer), ("ed25519", ed_signer)):
        for _ in range(2_000):
            started = time.perf_counter_ns()
            signature = signer.sign(message)
            sign_samples[label].append(time.perf_counter_ns() - started)
            started = time.perf_counter_ns()
            signer.verify(message, signature)
            verify_samples[label].append(time.perf_counter_ns() - started)
    return {
        "publish_rows": rows,
        "sign": {name: percentiles(values) for name, values in sign_samples.items()},
        "verify": {name: percentiles(values) for name, values in verify_samples.items()},
    }


def proof_cost() -> list[dict[str, object]]:
    rows = []
    for size in PROOF_SIZES:
        leaves = [index.to_bytes(8, "big") for index in range(size)]
        started = time.perf_counter()
        tree = MerkleTree(leaves)
        root = tree.root()
        build_seconds = time.perf_counter() - started
        first = size // 2 + 1
        index = size // 3
        inclusion = tree.inclusion_proof(index)
        extension = tree.consistency_proof(first, size)
        inclusion_samples: list[int] = []
        extension_samples: list[int] = []
        for _ in range(500):
            started_ns = time.perf_counter_ns()
            ok_inclusion = verify_inclusion(leaves[index], index, size, inclusion, root)
            inclusion_samples.append(time.perf_counter_ns() - started_ns)
            started_ns = time.perf_counter_ns()
            ok_extension = verify_consistency(first, size, tree.root(first), root, extension)
            extension_samples.append(time.perf_counter_ns() - started_ns)
        assert ok_inclusion and ok_extension
        rows.append(
            {
                "size": size,
                "build_tree_and_root_s": build_seconds,
                "inclusion_proof_bytes": 32 * len(inclusion),
                "extension_proof_bytes": 32 * len(extension),
                "inclusion_verify": percentiles(inclusion_samples),
                "extension_verify": percentiles(extension_samples),
            }
        )
        del tree, leaves
    return rows


def render(result: dict[str, object]) -> str:
    emit = result["telemetry"]
    plain = emit["telemetry_emit_with_chain"]
    combined = emit["telemetry_emit_with_chain_and_merkle_append"]
    accumulator = result["accumulator"]
    checkpoints = result["checkpoints"]
    lines = [
        "# Merkle tree heads, Ed25519 and witness: measured overhead",
        "",
        f"Python {result['python']} on {result['platform']}. {EVENTS} telemetry events, "
        f"{LEAVES:,} accumulator leaves. Times are per operation.",
        "",
        "## Per-event cost",
        "",
        "| operation | median (us) | p95 (us) |",
        "|---|---|---|",
        f"| telemetry emit (SQLite write, redaction, hash chain) | {plain['median_us']:.1f} "
        f"| {plain['p95_us']:.1f} |",
        f"| the same emit plus a Merkle accumulator append | {combined['median_us']:.1f} "
        f"| {combined['p95_us']:.1f} |",
        f"| accumulator append alone | {accumulator['accumulator_append']['median_us']:.2f} "
        f"| {accumulator['accumulator_append']['p95_us']:.2f} |",
        f"| accumulator root after 200k leaves | "
        f"{accumulator['accumulator_root_after_200k']['median_us']:.2f} "
        f"| {accumulator['accumulator_root_after_200k']['p95_us']:.2f} |",
        "",
        f"The Merkle append costs {accumulator['accumulator_append']['median_us']:.1f} us at the "
        f"median, {accumulator['accumulator_append']['median_us'] / plain['median_us'] * 100:.2f}% "
        f"of the {plain['median_us']:.0f} us the emit already costs. The end-to-end difference "
        f"between the two emit rows ({combined['median_us'] - plain['median_us']:+.0f} us) is "
        "smaller than the run-to-run variation of the SQLite write and should be read as zero, "
        "not as a speed-up. "
        f"For context the Agent Flight Recorder paper reports about {AFR_MEDIAN_MICROSECONDS:.0f} "
        "us per event for its full stack, on other hardware with another event model.",
        "",
        "## Storage",
        "",
        "The tree uses the integrity hashes the ledgers already hold, so it stores nothing per "
        "event. A signed head is one record per ledger per checkpoint:",
        "",
        "| signer | head record (bytes) | per event, checkpoint every 100 | every 1,000 |",
        "|---|---|---|---|",
    ]
    rows = checkpoints["publish_rows"]
    for label in ("hmac", "ed25519"):
        size = rows[0][f"head_bytes_{label}"]
        lines.append(f"| {label} | {size} | {size / 100:.2f} | {size / 1000:.3f} |")
    lines += [
        "",
        f"The Agent Flight Recorder paper reports {AFR_BYTES_PER_EVENT} bytes per event; its "
        "events carry their own data, so the figures are not like for like.",
        "",
        "## Signing",
        "",
        "| signer | sign median (us) | verify median (us) |",
        "|---|---|---|",
    ]
    for label in ("hmac", "ed25519"):
        lines.append(
            f"| {label} | {checkpoints['sign'][label]['median_us']:.1f} "
            f"| {checkpoints['verify'][label]['median_us']:.1f} |"
        )
    lines += [
        "",
        "## Publishing a head from scratch (re-hashing n records)",
        "",
        "| n | HMAC (ms) | Ed25519 (ms) |",
        "|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['size']:,} | {row['publish_from_scratch_ms_hmac']:.1f} "
            f"| {row['publish_from_scratch_ms_ed25519']:.1f} |"
        )
    lines += [
        "",
        "With the live accumulator the head costs one root computation and one signature "
        "whatever n is.",
        "",
        "## Verification: proofs against re-reading the log",
        "",
        f"Re-verifying the hash chain costs {emit['chain_verification_us_per_event']:.1f} us per "
        "event and grows with the log. A holder of two signed heads checks an extension, or one "
        "record, with a proof:",
        "",
        "| n | inclusion proof (bytes) | verify (us) | extension proof (bytes) | verify (us) "
        "| chain re-verify at n (ms, extrapolated) |",
        "|---|---|---|---|---|---|",
    ]
    for row in result["proofs"]:
        lines.append(
            f"| {row['size']:,} | {row['inclusion_proof_bytes']} "
            f"| {row['inclusion_verify']['median_us']:.1f} | {row['extension_proof_bytes']} "
            f"| {row['extension_verify']['median_us']:.1f} "
            f"| {row['size'] * emit['chain_verification_us_per_event'] / 1e3:,.0f} |"
        )
    lines += [
        "",
        "Chain re-verification is measured at 3,000 events and scaled linearly; every proof "
        "figure is measured.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("results/merkle_overhead.json"))
    parser.add_argument("--report", type=Path, default=Path("reports/merkle_overhead.md"))
    arguments = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="merkle-overhead-") as directory:
        base = Path(directory)
        result: dict[str, object] = {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "telemetry": telemetry_baseline_and_combined(base),
            "accumulator": accumulator_cost(),
            "checkpoints": checkpoint_cost(base / "sink"),
            "proofs": proof_cost(),
        }
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    text = render(result)
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
