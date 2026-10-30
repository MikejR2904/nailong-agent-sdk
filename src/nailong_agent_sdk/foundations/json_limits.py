# Copyright (c) 2026 David Michael Indraputra

"""Bounds on untrusted JSON-like payloads that must stay serializable by pydantic.
The limit by pydantic is 200 levels deep, but 64 is kept for a defensive bound reason.
Real-world payloads rarely exceed a few dozen levels; 64 is safe yet practical.
"""

from __future__ import annotations

from typing import Any

MAX_JSON_DEPTH = 64


def assert_json_depth(value: Any, label: str, limit: int = MAX_JSON_DEPTH) -> Any:
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children: Any = item.values()
        elif isinstance(item, list | tuple):
            children = item
        else:
            continue
        if depth > limit:
            raise ValueError(f"{label} nests more than {limit} levels deep; the limit is {limit}.")
        stack.extend((child, depth + 1) for child in children)
    return value
