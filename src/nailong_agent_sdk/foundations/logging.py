# Copyright (c) 2026 David Michael Indraputra

"""Opt-in stdlib logging as a fallback net outside the structured telemetry path.

The SDK's primary observability record is the hash-chained telemetry store and
audit log; this module exists only for the narrow cases outside that path —
a host-supplied callback raising, best-effort cleanup failing — where there
would otherwise be no trace at all. A ``NullHandler`` is attached to the SDK's
root logger so an embedding host sees nothing unless it explicitly attaches
its own handler to this namespace, per the standard library's own guidance
for libraries.
"""

from __future__ import annotations

import logging

_ROOT_LOGGER_NAME = "nailong_agent_sdk"
logging.getLogger(_ROOT_LOGGER_NAME).addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    """Return a logger namespaced under the SDK's root logger."""

    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")
