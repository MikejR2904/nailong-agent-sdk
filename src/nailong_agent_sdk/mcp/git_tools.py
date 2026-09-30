# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for local Git repository inspection and specification version locking."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..specifications.gate_models import (
    DependencyGraph,
    GapReport,
    UnifiedSpecification,
    VersionMetadata,
)
from ..specifications.git_models import GitApproval
from ._shared import McpContext, _validation_errors

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_git_tools(server: MCPServer, ctx: McpContext) -> None:
    @server.tool(name="get_git_repository_state", structured_output=True)
    async def get_git_repository_state(repository_path: str) -> dict[str, Any]:
        """Inspect a runtime-root-contained local Git repository without mutation."""

        try:
            return {
                "ok": True,
                "repository": ctx.repository_for(repository_path).state().model_dump(mode="json"),
            }
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="classify_specification_version", structured_output=True)
    async def classify_specification_version(
        repository_path: str,
        version: str,
        specification: dict[str, Any],
        dependency_graph: dict[str, Any],
    ) -> dict[str, Any]:
        """Classify from persisted structured snapshots, not changed path names."""

        try:
            classification = ctx.versioning.classify(
                ctx.repository_for(repository_path),
                version,
                UnifiedSpecification.model_validate(specification),
                DependencyGraph.model_validate(dependency_graph),
            )
            return {"ok": True, "classification": classification.model_dump(mode="json")}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="create_specification_git_lock", structured_output=True)
    async def create_specification_git_lock(
        repository_path: str,
        specification: dict[str, Any],
        dependency_graph: dict[str, Any],
        gap_report: dict[str, Any],
        metadata: dict[str, Any],
        approval: dict[str, Any],
    ) -> dict[str, Any]:
        """Create an approval-gated annotated local specification tag and lock record."""

        try:
            record = ctx.versioning.create_lock(
                ctx.repository_for(repository_path),
                UnifiedSpecification.model_validate(specification),
                DependencyGraph.model_validate(dependency_graph),
                GapReport.model_validate(gap_report),
                VersionMetadata.model_validate(metadata),
                GitApproval.model_validate(approval),
            )
            return {"ok": True, "lock": record.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="create_variant_worktree", structured_output=True)
    async def create_variant_worktree(
        repository_path: str,
        name: str,
        branch: str,
        base_ref: str,
        specification_tag: str,
        purpose: str,
        approval: dict[str, Any],
    ) -> dict[str, Any]:
        """Create an approved local variant worktree rooted under the runtime ledger."""

        try:
            record = ctx.versioning.create_variant_worktree(
                ctx.repository_for(repository_path),
                name=name,
                branch=branch,
                base_ref=base_ref,
                specification_tag=specification_tag,
                purpose=purpose,
                approval=GitApproval.model_validate(approval),
            )
            return {"ok": True, "worktree": record.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}
