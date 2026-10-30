# Copyright (c) 2026 David Michael Indraputra

"""The installed package name and version, read from distribution metadata once."""

from __future__ import annotations

from functools import cache
from importlib.metadata import PackageNotFoundError, version

PACKAGE_NAME = "nailong-agent-sdk"  #  ensure a match with pyproject.toml and setup.cfg


@cache  #  cache to avoid looking up unnecessarily
def package_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "0+unknown"


def http_user_agent(role: str) -> str:
    # This is used by webtools and MCP server to identify themselves consistently.
    return f"{PACKAGE_NAME}/{package_version()} {role}"
