# Copyright (c) 2026 David Michael Indraputra

"""Stable public-API catalogue generation for SDK consumers and maintainers.

The tool discovers only symbols intentionally listed in ``nailong_agent_sdk.__all__``.
It is therefore a documentation/compatibility aid, not a reflection-based capability
discovery mechanism for agent execution.
"""

from __future__ import annotations

import importlib
import inspect
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ..foundations.atomic_io import replace_atomic, unique_temporary_path
from ..foundations.contracts import StrictModel


class PublicApiSymbol(StrictModel):
    """One exported symbol and its stable classification."""

    name: str
    kind: str
    module: str
    documentation: str | None = None


class PublicApiCatalog(StrictModel):
    """Versioned inventory of the package's explicitly supported imports."""

    schema_version: str = "agent-sdk-public-api-v1"
    package_name: str
    package_version: str
    symbols: list[PublicApiSymbol]


def build_public_api_catalog() -> PublicApiCatalog:
    """Return a deterministic inventory of the ``nailong_agent_sdk`` public API."""

    package = importlib.import_module("nailong_agent_sdk")
    symbols = [_describe_symbol(name, getattr(package, name)) for name in sorted(package.__all__)]
    return PublicApiCatalog(
        package_name="nailong-agent-sdk",
        package_version=_package_version(),
        symbols=symbols,
    )


def write_public_api_catalog(path: Path) -> PublicApiCatalog:
    """Write the catalogue as canonical JSON and return the generated object."""

    catalog = build_public_api_catalog()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = unique_temporary_path(path)
    temporary.write_text(
        json.dumps(catalog.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    replace_atomic(temporary, path)
    return catalog


def _describe_symbol(name: str, value: object) -> PublicApiSymbol:
    if inspect.isclass(value):
        kind = "class"
    elif inspect.isfunction(value):
        kind = "function"
    elif inspect.ismodule(value):
        kind = "module"
    else:
        kind = type(value).__name__
    documentation = inspect.getdoc(value)
    return PublicApiSymbol(
        name=name,
        kind=kind,
        module=str(getattr(value, "__module__", "nailong_agent_sdk")),
        documentation=documentation.splitlines()[0] if documentation else None,
    )


def _package_version() -> str:
    try:
        return version("nailong-agent-sdk")
    except PackageNotFoundError:
        return "uninstalled-source-tree"
