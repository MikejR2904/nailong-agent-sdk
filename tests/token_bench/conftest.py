import sys
from pathlib import Path

for name in ("dfl", "token_economy"):
    path = Path(__file__).resolve().parents[2] / "benchmarks" / name
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

collect_ignore = (
    []
    if (Path(__file__).resolve().parents[2] / "benchmarks" / "token_economy").is_dir()
    else ["test_harness.py"]
)
