# Copyright (c) 2026 David Michael Indraputra

"""The durable run record and its canonical integrity hash."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import Field

from ..foundations.contracts import StrictModel
from .planning import PlanValidationReport


class RunRecord(StrictModel):
    schema_version: str = "agent-run-v1"
    run_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    graph: dict[str, Any]
    plan_validation: PlanValidationReport
    cancelled: bool = False
    run_hash: str = Field(min_length=1)
    history_entry_count: int = Field(default=0, ge=0)
    history_integrity_hash: str | None = None


def _hash_run(
    run_id: str,
    plan_id: str,
    graph: dict[str, Any],
    validation: PlanValidationReport,
    cancelled: bool,
) -> str:
    payload = {
        "run_id": run_id,
        "plan_id": plan_id,
        "graph": graph,
        "plan_validation": validation.model_dump(mode="json"),
        "cancelled": cancelled,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
