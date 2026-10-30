# Copyright (c) 2026 David Michael Indraputra

"""``python -m trust_bench`` entry point; run it from ``benchmarks/trust``."""

import sys

from .cli import entry

sys.exit(entry())
