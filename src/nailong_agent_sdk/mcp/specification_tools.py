# Copyright (c) 2026 David Michael Indraputra

"""MCP tool for specification preprocessing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ._shared import McpContext, _validation_errors

if TYPE_CHECKING:
    from mcp.server import MCPServer


def register_specification_tools(server: MCPServer, ctx: McpContext) -> None:
    @ctx.tool(server, "process_specification_manifest", exclusive=False)
    def process_specification_manifest(
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
