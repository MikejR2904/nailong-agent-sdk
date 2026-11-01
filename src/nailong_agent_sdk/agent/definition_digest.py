# Copyright (c) 2026 David Michael Indraputra

"""Digests of what an agent was configured to be, recorded with every run it makes."""

from __future__ import annotations

from typing import Any

from ..foundations.contracts import AgentDefinition
from ..foundations.jcs import jcs_hash

DIGEST_BASIS = "rfc8785-jcs-sha256"


def definition_digests(definition: AgentDefinition) -> dict[str, Any]:
    dump = definition.model_dump(mode="json")
    return {
        "digest_basis": DIGEST_BASIS,
        "definition_sha256": jcs_hash(dump),
        "instructions_version": definition.instructions.version,
        "instructions_sha256": jcs_hash(dump["instructions"]),
        "tools_sha256": jcs_hash(dump["tools"]),
        "tool_names": sorted(tool.name for tool in definition.tools),
        "input_schema_sha256": jcs_hash(dump["input_schema"]),
        "output_schema_sha256": jcs_hash(dump["output_schema"]),
        "model_binding_sha256": jcs_hash(dump["model_binding"]),
    }
