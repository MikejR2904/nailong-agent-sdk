import sys
from pathlib import Path

for name in ("dfl", "token_economy"):
    path = Path(__file__).resolve().parents[2] / "benchmarks" / name
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
