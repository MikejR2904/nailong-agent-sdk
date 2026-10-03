from __future__ import annotations

import sys
from pathlib import Path

# Make ``tests.support`` importable as ``support`` from any test module.
sys.path.insert(0, str(Path(__file__).parent))
