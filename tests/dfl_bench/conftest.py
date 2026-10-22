import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[2] / "benchmarks" / "dfl"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))


@pytest.fixture
def usable_instances():
    from dfl_bench.instances import generate_instance
    from dfl_bench.selection import is_usable

    def make(count=1, regime="aligned", seed=5):
        found, index = [], 0
        while len(found) < count:
            instance = generate_instance(f"C{seed}", regime, index, seed)
            index += 1
            if is_usable(instance):
                found.append(instance)
        return found

    return make
