# Copyright (c) 2026 David Michael Indraputra

"""Signed Merkle tree heads over a run's ledgers, with inclusion and extension proofs.

The telemetry and audit ledgers chain each record to the one before. A verifier holding a signed
head (size and Merkle root of the record hashes) can later check, without reading the whole log,
that one record is in the log (inclusion proof) and that a newer head extends an older one
(extension proof). Checking the log itself against a head detects a truncation, a rewrite and a
reseal. Heads are kept in a ``CheckpointSink`` outside the run root and signed with any
``CheckpointSigner``; an asymmetric signer lets a verifier hold no secret.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, ValidationError

from ..foundations.contracts import StrictModel
from ..foundations.errors import AgentSdkError
from ..foundations.hashing import canonical_json
from .audit_log import AuditTranscriptStore
from .merkle import MerkleAccumulator, MerkleTree, verify_consistency, verify_inclusion
from .run_checkpoints import CheckpointSigner, CheckpointSink
from .telemetry_store import TelemetryStore

TREE_HEAD_SCHEMA = "agent-sdk-merkle-tree-head-v1"
STORES = ("telemetry", "audit")


def tree_head_name(run_id: str, store: str) -> str:
    return f"{run_id}.{store}.merkle"


class TreeHeadVerdict(StrEnum):
    MATCHES = "matches"
    ADVANCED = "advanced"
    TRUNCATED = "truncated"
    DIVERGED = "diverged"
    CHAIN_BROKEN = "chain-broken"
    SIGNATURE_INVALID = "signature-invalid"
    MISBOUND = "misbound"
    NO_HEAD = "no-head"
    UNREADABLE = "unreadable"


class MerkleTreeHead(StrictModel):
    schema_version: str = TREE_HEAD_SCHEMA
    name: str = Field(min_length=1)
    size: int = Field(ge=0)
    root: str = Field(min_length=64, max_length=64)
    published_at_utc: str
    signer_id: str
    signature: str = ""

    def signed_message(self) -> bytes:
        unsigned = self.model_copy(update={"signature": ""}).model_dump(mode="json")
        return canonical_json(unsigned).encode("utf-8")


class TreeHeadVerification(StrictModel):
    verdict: TreeHeadVerdict
    heads: int = Field(default=0, ge=0)
    published_size: int = Field(default=0, ge=0)
    current_size: int = Field(default=0, ge=0)
    detail: str

    @property
    def ok(self) -> bool:
        return self.verdict in {TreeHeadVerdict.MATCHES, TreeHeadVerdict.ADVANCED}


class ConsistencyProof(StrictModel):
    first_size: int = Field(ge=1)
    second_size: int = Field(ge=1)
    hashes: list[str] = Field(default_factory=list)


class InclusionProof(StrictModel):
    index: int = Field(ge=0)
    size: int = Field(ge=1)
    hashes: list[str] = Field(default_factory=list)


class ProofCheck(StrictModel):
    ok: bool
    detail: str


def leaf_data(record_hash: str) -> bytes:
    try:
        return bytes.fromhex(record_hash)
    except ValueError as error:
        raise ValueError(
            f"A record hash must be hexadecimal, got {record_hash[:20]!r}: {error}"
        ) from error


def run_hashes(
    telemetry: TelemetryStore, audit: AuditTranscriptStore, run_id: str
) -> dict[str, list[str]]:
    return {
        "telemetry": [event.integrity_hash for event in telemetry.iter_events(run_id)],
        "audit": [entry.integrity_hash for entry in audit.iter_entries(run_id)],
    }


def sign_tree_head(
    size: int,
    root: bytes,
    sink: CheckpointSink,
    name: str,
    signer: CheckpointSigner,
    *,
    now: datetime | None = None,
) -> MerkleTreeHead:
    draft = MerkleTreeHead(
        name=name,
        size=size,
        root=root.hex(),
        published_at_utc=(now or datetime.now(UTC)).isoformat(),
        signer_id=signer.signer_id,
    )
    head = draft.model_copy(update={"signature": signer.sign(draft.signed_message())})
    sink.append(name, head.model_dump_json())
    return head


def publish_tree_head(
    hashes: Sequence[str],
    sink: CheckpointSink,
    name: str,
    signer: CheckpointSigner,
    *,
    now: datetime | None = None,
) -> MerkleTreeHead:
    accumulator = MerkleAccumulator()
    for item in hashes:
        accumulator.append(leaf_data(item))
    return sign_tree_head(accumulator.size, accumulator.root(), sink, name, signer, now=now)


def publish_run_tree_heads(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    sink: CheckpointSink,
    signer: CheckpointSigner,
    *,
    now: datetime | None = None,
) -> dict[str, MerkleTreeHead]:
    telemetry_break = telemetry.chain_break(run_id)
    audit_break = audit.chain_break(run_id)
    for label, broken in (("telemetry", telemetry_break), ("audit", audit_break)):
        if broken is not None:
            raise AgentSdkError(
                "TREE_HEAD_CHAIN_BROKEN",
                f'The {label} chain of run "{run_id}" is broken at sequence {broken.sequence} '
                f"({broken.kind}): {broken.message} A broken ledger is never signed.",
                {"run_id": run_id, "store": label},
            )
    hashes = run_hashes(telemetry, audit, run_id)
    return {
        store: publish_tree_head(
            hashes[store], sink, tree_head_name(run_id, store), signer, now=now
        )
        for store in STORES
    }


def verify_tree_heads(
    hashes: Sequence[str],
    sink: CheckpointSink,
    name: str,
    verifier: CheckpointSigner,
) -> TreeHeadVerification:
    try:
        lines = sink.read_all(name)
    except (OSError, AgentSdkError) as error:
        return _verdict(
            TreeHeadVerdict.UNREADABLE,
            f'The tree heads of "{name}" cannot be read at {sink.location(name)}: '
            f"{type(error).__name__}: {error}",
        )
    if not lines:
        return _verdict(
            TreeHeadVerdict.NO_HEAD,
            f'No tree head of "{name}" exists at {sink.location(name)}, so the ledger is '
            "tamper-evident only: its tail and its whole chain can be rewritten undetected.",
        )
    heads: list[MerkleTreeHead] = []
    for number, line in enumerate(lines, start=1):
        try:
            head = MerkleTreeHead.model_validate_json(line)
        except (ValueError, ValidationError) as error:
            return _verdict(
                TreeHeadVerdict.UNREADABLE,
                f'Tree head {number} of "{name}" is not a valid record: '
                f"{type(error).__name__}: {error}",
                heads=len(heads),
            )
        if head.schema_version != TREE_HEAD_SCHEMA or not verifier.verify(
            head.signed_message(), head.signature
        ):
            return _verdict(
                TreeHeadVerdict.SIGNATURE_INVALID,
                f'Tree head {number} of "{name}" does not carry a valid signature from signer '
                f'"{verifier.signer_id}": it was edited or signed with another key.',
                heads=len(heads),
            )
        mismatch = _binding_problem(head, name, verifier)
        if mismatch is not None:
            return _verdict(
                TreeHeadVerdict.MISBOUND,
                f'Tree head {number} of "{name}" is validly signed but {mismatch}: it '
                "was copied from another ledger or signer.",
                heads=len(heads),
            )
        heads.append(head)
    tree = MerkleTree([leaf_data(item) for item in hashes])
    current = tree.size
    for number, head in enumerate(heads, start=1):
        counts = {"heads": len(heads), "published_size": head.size, "current_size": current}
        if current < head.size:
            return _verdict(
                TreeHeadVerdict.TRUNCATED,
                f"Tree head {number} covers {head.size} records but the ledger holds {current}: "
                f"{head.size - current} newest records were removed.",
                **counts,
            )
        if tree.root(head.size).hex() != head.root:
            return _verdict(
                TreeHeadVerdict.DIVERGED,
                f"The first {head.size} records of the ledger no longer hash to the root "
                f"{head.root[:12]} of tree head {number}: the ledger was rewritten or resealed "
                "after it was published.",
                **counts,
            )
    newest = max(heads, key=lambda item: item.size)
    counts = {"heads": len(heads), "published_size": newest.size, "current_size": current}
    if current == newest.size:
        return _verdict(
            TreeHeadVerdict.MATCHES,
            f"The ledger holds exactly the {current} published records.",
            **counts,
        )
    return _verdict(
        TreeHeadVerdict.ADVANCED,
        f"The ledger extends the published {newest.size} records by {current - newest.size}; "
        "publish a new head to cover them.",
        **counts,
    )


def verify_run_tree_heads(
    telemetry: TelemetryStore,
    audit: AuditTranscriptStore,
    run_id: str,
    sink: CheckpointSink,
    verifier: CheckpointSigner,
) -> dict[str, TreeHeadVerification]:
    chains = {"telemetry": telemetry.chain_break(run_id), "audit": audit.chain_break(run_id)}
    hashes = run_hashes(telemetry, audit, run_id)
    results: dict[str, TreeHeadVerification] = {}
    for store in STORES:
        outcome = verify_tree_heads(hashes[store], sink, tree_head_name(run_id, store), verifier)
        broken = chains[store]
        if broken is not None and outcome.ok:
            outcome = _verdict(
                TreeHeadVerdict.CHAIN_BROKEN,
                f'The {store} chain of run "{run_id}" is broken at sequence {broken.sequence} '
                f"({broken.kind}): {broken.message}",
                heads=outcome.heads,
                published_size=outcome.published_size,
                current_size=outcome.current_size,
            )
        results[store] = outcome
    return results


def prove_extension(
    hashes: Sequence[str], first_size: int, second_size: int | None = None
) -> ConsistencyProof:
    tree = MerkleTree([leaf_data(item) for item in hashes])
    second = tree.size if second_size is None else second_size
    proof = tree.consistency_proof(first_size, second)
    return ConsistencyProof(
        first_size=first_size, second_size=second, hashes=[item.hex() for item in proof]
    )


def verify_extension(
    first: MerkleTreeHead,
    second: MerkleTreeHead,
    proof: ConsistencyProof,
    verifier: CheckpointSigner,
) -> ProofCheck:
    for label, head in (("older", first), ("newer", second)):
        if not verifier.verify(head.signed_message(), head.signature):
            return ProofCheck(
                ok=False,
                detail=f"The {label} head does not carry a valid signature from signer "
                f'"{verifier.signer_id}".',
            )
    if first.name != second.name:
        return ProofCheck(
            ok=False,
            detail=f'The older head is of "{first.name}" but the newer is of '
            f'"{second.name}"; an extension is only defined within one ledger.',
        )
    if (proof.first_size, proof.second_size) != (first.size, second.size):
        return ProofCheck(
            ok=False,
            detail=f"The proof is for sizes {proof.first_size} and {proof.second_size} but the "
            f"heads are {first.size} and {second.size}.",
        )
    try:
        steps = [bytes.fromhex(item) for item in proof.hashes]
        good = verify_consistency(
            first.size, second.size, bytes.fromhex(first.root), bytes.fromhex(second.root), steps
        )
    except ValueError as error:
        return ProofCheck(ok=False, detail=f"The proof or a root is not hexadecimal: {error}")
    if good:
        return ProofCheck(
            ok=True,
            detail=f"The newer head ({second.size} records) extends the older ({first.size}); "
            "no record before the older size was changed.",
        )
    return ProofCheck(
        ok=False,
        detail=f"The newer head ({second.size} records) does not extend the older "
        f"({first.size}): the log was rewritten or forked between them.",
    )


def prove_inclusion(hashes: Sequence[str], index: int, size: int | None = None) -> InclusionProof:
    tree = MerkleTree([leaf_data(item) for item in hashes])
    count = tree.size if size is None else size
    path = tree.inclusion_proof(index, count)
    return InclusionProof(index=index, size=count, hashes=[item.hex() for item in path])


def verify_event_inclusion(
    record_hash: str,
    proof: InclusionProof,
    head: MerkleTreeHead,
    verifier: CheckpointSigner,
) -> ProofCheck:
    if not verifier.verify(head.signed_message(), head.signature):
        return ProofCheck(
            ok=False,
            detail=f'The head does not carry a valid signature from signer "{verifier.signer_id}".',
        )
    if proof.size != head.size:
        return ProofCheck(
            ok=False,
            detail=f"The proof is for a tree of {proof.size} records but the head covers "
            f"{head.size}.",
        )
    try:
        good = verify_inclusion(
            leaf_data(record_hash),
            proof.index,
            proof.size,
            [bytes.fromhex(item) for item in proof.hashes],
            bytes.fromhex(head.root),
        )
    except ValueError as error:
        return ProofCheck(ok=False, detail=f"The record hash, proof or root is invalid: {error}")
    if good:
        return ProofCheck(
            ok=True,
            detail=f"Record {proof.index} is in the {head.size} records covered by the head.",
        )
    return ProofCheck(
        ok=False,
        detail=f"Record {proof.index} with hash {record_hash[:12]} is not in the "
        f"{head.size} records covered by the head.",
    )


def _binding_problem(head: MerkleTreeHead, name: str, verifier: CheckpointSigner) -> str | None:
    if head.name != name:
        return f'names the ledger "{head.name}" instead of "{name}"'
    if head.signer_id != verifier.signer_id:
        return f'names signer "{head.signer_id}" instead of "{verifier.signer_id}"'
    return None


def _verdict(kind: TreeHeadVerdict, detail: str, **counts: int) -> TreeHeadVerification:
    return TreeHeadVerification(verdict=kind, detail=detail, **counts)
