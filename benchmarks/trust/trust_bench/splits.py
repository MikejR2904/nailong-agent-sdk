# Copyright (c) 2026 David Michael Indraputra

"""Development and test splits: disjoint seeds, a held-out family, held-out fault operators."""

from __future__ import annotations

from enum import StrEnum

DEV_SEEDS = range(1, 1_000)
TEST_SEEDS = range(1_000_000, 2_000_000)
HELD_OUT_FAMILIES = frozenset({"plan"})
DEV_OPERATORS = frozenset(
    {
        "env_unreadable",
        "env_corrupt:digit_swap",
        "decision_wrong_total:off_by_one",
        "decision_wrong_stage:clean",
        "decision_wrong_stage:summary",
        "limit",
    }
)
TEST_ONLY_OPERATORS = frozenset(
    {
        "env_corrupt:scale10",
        "decision_wrong_total:sign_flip",
        "decision_wrong_stage:revenue",
        "decision_wrong_node:budget",
        "decision_wrong_node:actuals",
        "decision_wrong_node:variance",
    }
)


FORM_OPERATORS = frozenset({"decision_wrong_form:truncate"})


class Split(StrEnum):
    DEV = "dev"
    TEST = "test"


def seeds_for(split: Split) -> range:
    return DEV_SEEDS if split is Split.DEV else TEST_SEEDS


def operators_for(split: Split) -> frozenset[str]:
    return DEV_OPERATORS if split is Split.DEV else DEV_OPERATORS | TEST_ONLY_OPERATORS


def families_for(split: Split, available: frozenset[str]) -> frozenset[str]:
    return available - HELD_OUT_FAMILIES if split is Split.DEV else available
