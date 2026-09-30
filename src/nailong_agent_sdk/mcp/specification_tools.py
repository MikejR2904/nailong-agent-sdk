# Copyright (c) 2026 David Michael Indraputra

"""MCP tools for specification preprocessing, task-scoped selection, and Gate 1."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..memory.context_selection import DesignStage, TaskAwareContextSelector
from ..specifications.documents import DocumentTree, SpecificationCategory
from ..specifications.gate_models import (
    DependencyGraph,
    GapReport,
    SemanticGapFinding,
    UnifiedSpecification,
    VersionMetadata,
)
from ._shared import McpContext, _validation_errors

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_specification_tools(server: MCPServer, ctx: McpContext) -> None:
    @server.tool(name="process_specification_manifest", structured_output=True)
    async def process_specification_manifest(
        manifest_path: str = "specification-manifest.yaml",
    ) -> dict[str, Any]:
        """Parse manifest sources into persisted ordered source-referenced document trees."""

        try:
            manifest = ctx.preprocessor.load_manifest(manifest_path)
            trees = ctx.preprocessor.process_manifest(manifest)
            paths = [
                str(ctx.preprocessor.persist_tree(tree).relative_to(ctx.specification_root))
                for tree in trees
            ]
            return {
                "ok": True,
                "manifest": manifest.model_dump(mode="json"),
                "trees": [tree.model_dump(mode="json") for tree in trees],
                "processed_paths": paths,
            }
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="select_task_context", structured_output=True)
    async def select_task_context(
        trees: list[dict[str, Any]],
        stage: str,
        task_text: str,
        scope_pointers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Prune source trees by design stage before matching task scope and keywords."""

        try:
            selected = TaskAwareContextSelector().select(
                [DocumentTree.model_validate(tree) for tree in trees],
                DesignStage(stage),
                task_text,
                scope_pointers or (),
            )
            return {"ok": True, "selection": selected.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="validate_gate_one", structured_output=True)
    async def validate_gate_one(
        specification: dict[str, Any],
        required_categories: list[str],
        semantic_findings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run Gate 1 checks and source-bound admission of host-proposed semantic gaps."""

        try:
            graph, report = ctx.specification_gate.validate(
                UnifiedSpecification.model_validate(specification),
                required_categories={SpecificationCategory(item) for item in required_categories},
                semantic_findings=[
                    SemanticGapFinding.model_validate(item) for item in semantic_findings or []
                ],
            )
            return {
                "ok": True,
                "dependency_graph": graph.model_dump(mode="json"),
                "gap_report": report.model_dump(mode="json"),
                "summary": report.summary(),
            }
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}

    @server.tool(name="soft_lock_specification", structured_output=True)
    async def soft_lock_specification(
        specification: dict[str, Any],
        dependency_graph: dict[str, Any],
        gap_report: dict[str, Any],
        metadata: dict[str, Any],
        user_approved: bool,
        proceed_with_gaps: bool = False,
        plans: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Persist Gate 1 handover artifacts only after explicit designer approval."""

        try:
            parsed_spec = UnifiedSpecification.model_validate(specification)
            parsed_graph = DependencyGraph.model_validate(dependency_graph)
            parsed_report = GapReport.model_validate(gap_report)
            parsed_metadata = VersionMetadata.model_validate(metadata)
            decision = ctx.specification_gate.soft_lock(
                parsed_spec,
                parsed_report,
                parsed_metadata,
                user_approved=user_approved,
                proceed_with_gaps=proceed_with_gaps,
            )
            if decision.accepted and decision.metadata is not None:
                ctx.gate_store.persist(
                    parsed_spec, parsed_graph, parsed_report, decision.metadata, plans or []
                )
            return {"ok": True, "decision": decision.model_dump(mode="json")}
        except ValidationError as error:
            return {"ok": False, "errors": _validation_errors(error)}
        except Exception as error:
            return {"ok": False, "errors": [{"message": str(error), "type": type(error).__name__}]}
