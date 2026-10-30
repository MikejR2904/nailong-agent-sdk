# Copyright (c) 2026 David Michael Indraputra

"""Developer validation helpers for local SDK contract files.

These utilities are deterministic wrappers around the package's Pydantic
contracts. They intentionally do not execute models, tools, or user callbacks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..foundations.contracts import AgentDefinition, ScopedAgentTask, StrictModel
from ..specifications.documents import SpecificationManifest
from ..state.planning import Plan


class ValidationIssue(StrictModel):
    """One contract validation issue with a stable source path."""

    path: str
    message: str
    error_type: str


class ValidationReport(StrictModel):
    """Validation result for one local SDK artifact."""

    schema_version: str = "agent-sdk-validation-report-v1"
    artifact_type: str
    path: str
    valid: bool
    issues: list[ValidationIssue] = []


_CONTRACTS: dict[str, type[StrictModel]] = {
    "agent-definition": AgentDefinition,
    "scoped-task": ScopedAgentTask,
    "plan": Plan,
    "specification-manifest": SpecificationManifest,
}


def supported_contract_types() -> tuple[str, ...]:
    """Return contract type names accepted by ``validate_contract_file``."""

    return tuple(sorted(_CONTRACTS))


def validate_contract_file(path: Path, artifact_type: str) -> ValidationReport:
    """Validate a JSON/YAML file against one SDK contract without side effects."""

    if artifact_type not in _CONTRACTS:
        raise ValueError(
            f"Unsupported artifact type {artifact_type!r}; "
            f"expected one of {supported_contract_types()}."
        )
    payload = _load_structured_file(path)
    contract = _CONTRACTS[artifact_type]
    try:
        contract.model_validate(payload)
    except ValidationError as error:
        return ValidationReport(
            artifact_type=artifact_type,
            path=str(path),
            valid=False,
            issues=[
                ValidationIssue(
                    path=".".join(str(item) for item in issue["loc"]),
                    message=str(issue["msg"]),
                    error_type=str(issue["type"]),
                )
                for issue in error.errors()
            ],
        )
    return ValidationReport(artifact_type=artifact_type, path=str(path), valid=True)


def _load_structured_file(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f'Contract file "{path}" does not exist.')
    suffix = path.suffix.lower()
    if suffix not in {".json", ".yaml", ".yml"}:
        raise ValueError(
            f'Contract file "{path}" must use .json, .yaml, or .yml, got "{path.suffix}".'
        )
    text = path.read_text(encoding="utf-8-sig")
    try:
        return json.loads(text) if suffix == ".json" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as error:
        kind = "JSON" if suffix == ".json" else "YAML"
        raise ValueError(f'Contract file "{path}" is not valid {kind}: {error}') from error
