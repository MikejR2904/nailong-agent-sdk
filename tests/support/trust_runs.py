from functools import cache

from trust_bench.evaluate import FAMILIES, evaluate_job
from trust_bench.splits import DEV_OPERATORS, TEST_ONLY_OPERATORS

ALL_OPERATORS = DEV_OPERATORS | TEST_ONLY_OPERATORS


@cache
def evaluated(family, seed, arm, operator_id=None):
    return evaluate_job((family, seed, arm, operator_id, "test", ALL_OPERATORS))


def fault_ids(family, seed):
    return [fault.operator_id for fault in FAMILIES[family](seed).faults(ALL_OPERATORS)]
