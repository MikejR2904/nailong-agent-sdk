# Copyright (c) 2026 David Michael Indraputra

"""Start the recorder: ``python -m nailong_agent_sdk.observability.recorder``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
