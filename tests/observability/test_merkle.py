import hashlib

import pytest

from nailong_agent_sdk.observability.merkle import (
    MerkleAccumulator,
    MerkleTree,
    empty_root,
    hash_children,
    hash_leaf,
    merkle_root,
    split_point,
    verify_consistency,
    verify_inclusion,
)

CT_LEAVES = [
    b"",
    b"\x00",
    b"\x10",
    bytes.fromhex("2021"),
    bytes.fromhex("3031"),
    bytes.fromhex("40414243"),
    bytes.fromhex("5051525354555657"),
    bytes.fromhex("606162636465666768696a6b6c6d6e6f"),
]
CT_ROOTS = {
    1: "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
    2: "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",
    3: "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77",
    4: "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
    5: "4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4",
    6: "76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef",
    7: "ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c",
    8: "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328",
}
LEAVES = [bytes([index]) * 3 for index in range(41)]


def test_the_roots_match_the_published_certificate_transparency_test_vectors():
    assert empty_root().hex() == hashlib.sha256(b"").hexdigest()
    tree = MerkleTree(CT_LEAVES)
    for size, expected in CT_ROOTS.items():
        assert tree.root(size).hex() == expected
        assert merkle_root(CT_LEAVES[:size]).hex() == expected


def test_leaves_and_nodes_are_domain_separated():
    leaf = hash_leaf(b"ab")
    assert leaf != hashlib.sha256(b"ab").digest()
    assert hash_children(hash_leaf(b"a"), hash_leaf(b"b")) != leaf


def test_the_split_point_is_the_largest_power_of_two_below_the_size():
    assert [split_point(size) for size in (2, 3, 4, 5, 8, 9, 17)] == [1, 2, 2, 4, 4, 8, 16]
    with pytest.raises(ValueError, match="needs at least 2"):
        split_point(1)


def test_the_accumulator_equals_the_tree_root_after_every_append():
    accumulator, tree = MerkleAccumulator(), MerkleTree(LEAVES)
    assert accumulator.root() == empty_root()
    for size, item in enumerate(LEAVES, start=1):
        accumulator.append(item)
        assert accumulator.size == size and accumulator.root() == tree.root(size)


def test_every_inclusion_proof_of_every_prefix_verifies():
    tree = MerkleTree(LEAVES)
    for size in range(1, len(LEAVES) + 1):
        for index in range(size):
            proof = tree.inclusion_proof(index, size)
            assert verify_inclusion(LEAVES[index], index, size, proof, tree.root(size))


def test_every_consistency_proof_between_every_pair_of_sizes_verifies():
    tree = MerkleTree(LEAVES)
    for second in range(1, len(LEAVES) + 1):
        for first in range(1, second + 1):
            proof = tree.consistency_proof(first, second)
            assert verify_consistency(first, second, tree.root(first), tree.root(second), proof)


def test_proof_sizes_are_logarithmic():
    tree = MerkleTree([index.to_bytes(4, "big") for index in range(1024)])
    assert len(tree.inclusion_proof(500)) == 10
    assert len(tree.consistency_proof(300, 1024)) <= 2 * 10


def test_a_wrong_leaf_index_size_or_proof_fails_inclusion():
    tree = MerkleTree(LEAVES)
    proof, root = tree.inclusion_proof(7), tree.root()
    size = len(LEAVES)
    assert verify_inclusion(LEAVES[7], 7, size, proof, root)
    assert not verify_inclusion(LEAVES[8], 7, size, proof, root)
    assert not verify_inclusion(LEAVES[7], 6, size, proof, root)
    assert not verify_inclusion(LEAVES[7], 7, size, proof[:-1], root)
    assert not verify_inclusion(LEAVES[7], 7, size, [*proof, proof[0]], root)
    assert not verify_inclusion(LEAVES[7], 7, size, [bytes(32), *proof[1:]], root)
    assert not verify_inclusion(LEAVES[7], size, size, proof, root)


def test_a_rewritten_history_has_no_valid_consistency_proof():
    honest = MerkleTree(LEAVES)
    forged = MerkleTree([LEAVES[0], b"forged", *LEAVES[2:]])
    first, second = 20, len(LEAVES)
    proof = forged.consistency_proof(first, second)
    assert not verify_consistency(first, second, honest.root(first), forged.root(second), proof)
    assert not verify_consistency(first, second, honest.root(first), honest.root(second), proof)
    assert verify_consistency(first, second, forged.root(first), forged.root(second), proof)


def test_consistency_edge_cases():
    tree = MerkleTree(LEAVES)
    assert verify_consistency(5, 5, tree.root(5), tree.root(5), [])
    assert not verify_consistency(5, 5, tree.root(5), tree.root(6), [])
    assert not verify_consistency(5, 5, tree.root(5), tree.root(5), [bytes(32)])
    assert verify_consistency(0, 9, empty_root(), tree.root(9), [])
    assert not verify_consistency(9, 5, tree.root(9), tree.root(5), [])
    assert not verify_consistency(3, 9, tree.root(3), tree.root(9), [])


def test_queries_outside_the_tree_name_the_range():
    tree = MerkleTree(LEAVES[:4])
    with pytest.raises(ValueError, match="Leaf index 4 is outside a tree of 4 leaves"):
        tree.inclusion_proof(4)
    with pytest.raises(ValueError, match="1 <= first <= second, got first=0 and second=4"):
        tree.consistency_proof(0)
    with pytest.raises(ValueError, match="Tree size 5 is outside the 4 leaves held"):
        tree.root(5)
