# Copyright (c) 2026 David Michael Indraputra

"""Typed declarations for the governed core tool set."""

from __future__ import annotations

from typing import Any

from ...foundations.contracts import EpisodeKind, ToolConcurrency, ToolDefinition
from ...state.elastic import ELASTIC_REQUEST_TOOL_NAME, MAX_ELASTIC_DEPENDENCIES


def core_tool_definitions() -> list[ToolDefinition]:
    """Return typed declarations that a consumer may opt into in AgentDefinition.tools."""

    def definition(
        name: str, description: str, schema: dict[str, Any], *, write: bool = False
    ) -> ToolDefinition:
        return ToolDefinition(
            name=name,
            description=description,
            input_schema=schema,
            episode_kind=EpisodeKind.ACTION if write else EpisodeKind.EXPLORATORY,
            concurrency=ToolConcurrency.SERIAL if write else ToolConcurrency.PARALLEL_SAFE,
        )

    text_path = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }
    return [
        definition(
            "read_file",
            "Read a bounded UTF-8 file below the governed run root.",
            {
                **text_path,
                "properties": {
                    **text_path["properties"],
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                },
            },
        ),
        definition(
            "glob",
            "List bounded run-root paths matched by a relative glob.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        ),
        definition(
            "grep",
            "Search bounded UTF-8 files below the run root using a regex.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "file_glob": {"type": "string"},
                    "case_sensitive": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        ),
        definition(
            "write_draft",
            "Write a declared draft output path.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            write=True,
        ),
        definition(
            "edit_draft",
            "Apply an exact bounded replacement to a declared draft.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            write=True,
        ),
        definition(
            "read_artifact",
            "Read an authorized immutable artifact.",
            {
                "type": "object",
                "properties": {"artifact_id": {"type": "string"}},
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
        ),
        definition(
            "grep_artifact",
            "Find literal text in an authorized immutable artifact.",
            {
                "type": "object",
                "properties": {"artifact_id": {"type": "string"}, "needle": {"type": "string"}},
                "required": ["artifact_id", "needle"],
                "additionalProperties": False,
            },
        ),
        definition(
            "diff_declared_artifacts",
            "Diff two content-addressed artifact IDs.",
            {
                "type": "object",
                "properties": {
                    "base_artifact_id": {"type": "string"},
                    "draft_artifact_id": {"type": "string"},
                    "draft_occurrence_id": {"type": "string"},
                },
                "required": ["base_artifact_id", "draft_artifact_id"],
                "additionalProperties": False,
            },
        ),
        definition(
            "get_tool_result",
            "Read a bounded opaque tool-result handle.",
            {
                "type": "object",
                "properties": {
                    "handle_id": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 64, "maximum": 20000},
                },
                "required": ["handle_id"],
                "additionalProperties": False,
            },
        ),
        definition(
            "web_fetch",
            "Fetch bounded public HTTP(S) content marked as untrusted evidence. "
            "A PDF URL (content-type application/pdf) is parsed and returned "
            "as many consecutive pages starting at 'page' as fit in max_chars "
            "(usually several pages per call, not just one): the response "
            "includes total_pages, pages_included (which pages this call's "
            "content actually covers), next_page (set only if pages remain), "
            "and images_found (any images on those pages, each with an "
            "image_ref). If next_page is set, call again with page=next_page "
            "to continue - do not assume one call covers an entire multi-page "
            "PDF.",
            {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 500, "maximum": 20000},
                    "page": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10000,
                        "description": (
                            "1-indexed page to start reading from, for a PDF "
                            "URL only. Ignored for non-PDF content. Defaults "
                            "to 1; pass the previous response's next_page to "
                            "continue reading the same document."
                        ),
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        ),
        definition(
            "render_pdf_page",
            "Render one page of a PDF URL as an image, capturing vector "
            "graphics, embedded raster images, and text exactly as a viewer "
            "would display them composited together - unlike web_fetch's "
            "text extraction, this also captures diagrams drawn with vector "
            "lines/boxes/arrows that are not stored as separate image "
            "objects. Returns image_ref, total_pages, and the rendered "
            "page's byte_count; pass image_ref to interpret_pdf_image to "
            "actually see what the page shows.",
            {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "page": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10000,
                        "description": "1-indexed page to render. Defaults to 1.",
                    },
                    "scale": {
                        "type": "number",
                        "minimum": 1.0,
                        "maximum": 4.0,
                        "description": (
                            "Rendering scale relative to the PDF's own point "
                            "size (1.0 = 72 DPI). Higher values give a "
                            "sharper, larger image at the cost of more "
                            "bytes. Defaults to 2.0."
                        ),
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        ),
        definition(
            "web_search",
            "Search public web evidence; returned snippets are untrusted.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        definition(
            "sleep",
            "Pause a bounded number of seconds.",
            {
                "type": "object",
                "properties": {"seconds": {"type": "number", "minimum": 0, "maximum": 30}},
                "additionalProperties": False,
            },
        ),
        definition(
            "ask_human_question",
            "Ask the configured human responder a scoped question.",
            {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
                "additionalProperties": False,
            },
        ),
        definition(
            "brief",
            "Bound a textual summary without altering its claims.",
            {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 20, "maximum": 2000},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        ),
        definition(
            "notebook_edit",
            "Edit a declared notebook JSON output without executing it.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "cell_index": {"type": "integer", "minimum": 0, "maximum": 2000},
                    "new_source": {"type": "string"},
                    "mode": {"enum": ["replace", "append"]},
                },
                "required": ["path", "new_source"],
                "additionalProperties": False,
            },
            write=True,
        ),
        definition(
            "run_registered_command",
            "Execute one policy-approved named supervisor template; no raw command is accepted.",
            {
                "type": "object",
                "properties": {"template_name": {"type": "string"}},
                "required": ["template_name"],
                "additionalProperties": False,
            },
            write=True,
        ),
        definition(
            ELASTIC_REQUEST_TOOL_NAME,
            "Queue one extra exploration node for the controller. It runs after this task "
            "finishes, inherits this task's authority and cannot see more than this task can; "
            "a join node then resumes this task with every exploration result. A request that "
            "exceeds the plan's elastic capacity is refused here or held for a controller "
            "decision when this task ends.",
            {
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9]([A-Za-z0-9._-]{0,126}[A-Za-z0-9_-])?$",
                        "description": "Unique within this task; letters, digits, '.', '_', '-'.",
                    },
                    "scope": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2000,
                        "description": "The area the exploration is limited to.",
                    },
                    "instructions": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                        "maxItems": MAX_ELASTIC_DEPENDENCIES,
                        "description": "Nodes this task already depends on whose results the "
                        "exploration also needs.",
                    },
                    "routing_refs": {
                        "type": "object",
                        "properties": {
                            "requirement_ids": {"type": "array", "items": {"type": "string"}},
                            "signal_ids": {"type": "array", "items": {"type": "string"}},
                            "task_ids": {"type": "array", "items": {"type": "string"}},
                            "schema_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["request_id", "scope", "instructions", "reason"],
                "additionalProperties": False,
            },
            write=True,
        ),
    ]
