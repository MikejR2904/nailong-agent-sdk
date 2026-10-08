# Copyright (c) 2026 David Michael Indraputra

"""The installed package name and version, read from distribution metadata once."""

from __future__ import annotations

from functools import cache
from importlib.metadata import PackageNotFoundError, version

PACKAGE_NAME = "nailong-agent-sdk"


@cache
def package_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "0+unknown"


def http_user_agent(role: str) -> str:
    return f"{PACKAGE_NAME}/{package_version()} {role}"
