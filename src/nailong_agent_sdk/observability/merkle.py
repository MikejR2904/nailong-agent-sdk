# Copyright (c) 2026 David Michael Indraputra

"""Merkle tree hashing with inclusion and consistency proofs, as in RFC 6962 and RFC 9162."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"
DIGEST_BYTES = 32


def hash_leaf(data: bytes) -> bytes:
    return hashlib.sha256(LEAF_PREFIX + data).digest()


def hash_children(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(NODE_PREFIX + left + right).digest()


def empty_root() -> bytes:
    return hashlib.sha256(b"").digest()


def split_point(size: int) -> int:
    if size < 2:
        raise ValueError(f"A tree of {size} leaves has no split point; it needs at least 2.")
    return 1 << (size - 1).bit_length() - 1


class MerkleTree:
    """A tree over a fixed list of leaves that answers roots, inclusion and consistency queries."""

    def __init__(self, leaves: Sequence[bytes]) -> None:
        self._leaf_hashes = [hash_leaf(item) for item in leaves]
        self._memo: dict[tuple[int, int], bytes] = {}

    @classmethod
    def from_leaf_hashes(cls, leaf_hashes: Sequence[bytes]) -> MerkleTree:
        tree = cls(())
        tree._leaf_hashes = list(leaf_hashes)
        return tree

    @property
    def size(self) -> int:
        return len(self._leaf_hashes)

    def root(self, size: int | None = None) -> bytes:
        count = self.size if size is None else size
        self._require_size(count)
        return empty_root() if count == 0 else self._range_root(0, count)

    def inclusion_proof(self, index: int, size: int | None = None) -> list[bytes]:
        count = self.size if size is None else size
        self._require_size(count)
        if not 0 <= index < count:
            raise ValueError(
                f"Leaf index {index} is outside a tree of {count} leaves; "
                f"valid indexes are 0 to {count - 1}."
            )
        return self._path(index, 0, count)

    def consistency_proof(self, first: int, second: int | None = None) -> list[bytes]:
        count = self.size if second is None else second
        self._require_size(count)
        if not 1 <= first <= count:
            raise ValueError(
                f"A consistency proof needs 1 <= first <= second, got first={first} and "
                f"second={count}."
            )
        if first == count:
            return []
        return self._subproof(first, 0, count, True)

    def _require_size(self, count: int) -> None:
        if not 0 <= count <= self.size:
            raise ValueError(f"Tree size {count} is outside the {self.size} leaves held.")

    def _range_root(self, low: int, high: int) -> bytes:
        key = (low, high)
        found = self._memo.get(key)
        if found is not None:
            return found
        width = high - low
        if width == 1:
            result = self._leaf_hashes[low]
        else:
            middle = low + split_point(width)
            result = hash_children(self._range_root(low, middle), self._range_root(middle, high))
        self._memo[key] = result
        return result

    def _path(self, index: int, low: int, high: int) -> list[bytes]:
        proof: list[bytes] = []
        while high - low > 1:
            middle = low + split_point(high - low)
            if index < middle:
                proof.append(self._range_root(middle, high))
                high = middle
            else:
                proof.append(self._range_root(low, middle))
                low = middle
        proof.reverse()
        return proof

    def _subproof(self, first: int, low: int, high: int, complete: bool) -> list[bytes]:
        steps: list[bytes] = []
        while True:
            width = high - low
            if first == width:
                if not complete:
                    steps.append(self._range_root(low, high))
                break
            middle = low + split_point(width)
            if first <= middle - low:
                steps.append(self._range_root(middle, high))
                high = middle
            else:
                steps.append(self._range_root(low, middle))
                first -= middle - low
                low = middle
                complete = False
        steps.reverse()
        return steps


class MerkleAccumulator:
    """Append-only root maintenance in O(log n) space and amortised O(1) hashes per leaf."""

    def __init__(self) -> None:
        self._frontier: list[tuple[int, bytes]] = []
        self._size = 0

    @property
    def size(self) -> int:
        return self._size

    def append(self, data: bytes) -> None:
        width, node = 1, hash_leaf(data)
        while self._frontier and self._frontier[-1][0] == width:
            _, left = self._frontier.pop()
            node = hash_children(left, node)
            width *= 2
        self._frontier.append((width, node))
        self._size += 1

    def root(self) -> bytes:
        if not self._frontier:
            return empty_root()
        result = self._frontier[-1][1]
        for _, left in reversed(self._frontier[:-1]):
            result = hash_children(left, result)
        return result


def merkle_root(leaves: Sequence[bytes]) -> bytes:
    accumulator = MerkleAccumulator()
    for item in leaves:
        accumulator.append(item)
    return accumulator.root()


def verify_inclusion(
    leaf_data: bytes, index: int, size: int, proof: Sequence[bytes], root: bytes
) -> bool:
    if not 0 <= index < size:
        return False
    position, last = index, size - 1
    running = hash_leaf(leaf_data)
    for sibling in proof:
        if last == 0:
            return False
        if position & 1 or position == last:
            running = hash_children(sibling, running)
            if not position & 1:
                while not position & 1 and position != 0:
                    position >>= 1
                    last >>= 1
        else:
            running = hash_children(running, sibling)
        position >>= 1
        last >>= 1
    return last == 0 and running == root


def verify_consistency(
    first: int,
    second: int,
    first_root: bytes,
    second_root: bytes,
    proof: Sequence[bytes],
) -> bool:
    if first < 0 or second < first:
        return False
    if first == second:
        return not proof and first_root == second_root
    if first == 0:
        return not proof and first_root == empty_root()
    steps = list(proof)
    if first & (first - 1) == 0:
        steps.insert(0, first_root)
    if not steps:
        return False
    position, last = first - 1, second - 1
    while position & 1:
        position >>= 1
        last >>= 1
    old = new = steps[0]
    for sibling in steps[1:]:
        if last == 0:
            return False
        if position & 1 or position == last:
            old = hash_children(sibling, old)
            new = hash_children(sibling, new)
            if not position & 1:
                while not position & 1 and position != 0:
                    position >>= 1
                    last >>= 1
        else:
            new = hash_children(new, sibling)
        position >>= 1
        last >>= 1
    return last == 0 and old == first_root and new == second_root
