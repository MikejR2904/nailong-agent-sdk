# Copyright (c) 2026 David Michael Indraputra

"""Source-grounded BaseAgent specializations.

Only RTLWorker receives a concrete tool contract because the framework defines
that contract explicitly (systems-design framework, updated PDF, p. 66).
Other role factories are intentionally declarative placeholders.
"""

from __future__ import annotations

from ..foundations.contracts import (
    AgentDefinition,
    EpisodeKind,
    EscalationTarget,
    MemoryScope,
    ModelBinding,
    TerminationPolicy,
    ToolDefinition,
    VersionedInstructions,
)


def _object_schema(required: list[str], properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }


def planning_agent_definition(model_binding: ModelBinding) -> AgentDefinition:
    """Return the bounded planner role; plan validity remains deterministic."""

    return AgentDefinition(
        identity="Produce one typed PlanTask DAG from the locked specification.",
        instructions=VersionedInstructions(
            version="planning-agent-v1",
            text=(
                "Copy locked scopes and interfaces verbatim, cite every signal role, and emit "
                "dependency proofs. The deterministic plan validator decides acceptance."
            ),
        ),
        input_schema={"type": "object"},
        output_schema=_object_schema(
            ["status", "plan"], {"status": {"const": "complete"}, "plan": {"type": "object"}}
        ),
        tools=[
            ToolDefinition(
                name="read_spec",
                description="Read an authorized locked specification span.",
                input_schema=_object_schema(["pointer"], {"pointer": {"type": "string"}}),
                episode_kind=EpisodeKind.EXPLORATORY,
            )
        ],
        model_binding=model_binding,
        memory_scope=MemoryScope.TASK_SCOPED,
        termination_policy=TerminationPolicy(
            max_iterations=6,
            status_field="status",
            escalation=EscalationTarget.CONTROLLER,
        ),
        verification_gate_id=None,
    )


def rtl_worker_definition(model_binding: ModelBinding) -> AgentDefinition:
    """Return the exact first RTLWorker capability boundary defined by the framework."""

    empty_object = {"type": "object", "additionalProperties": False}
    return AgentDefinition(
        identity=(
            "Generate or repair one scoped RTL artifact without changing the locked interface."
        ),
        instructions=VersionedInstructions(
            version="rtl-worker-v1",
            text=(
                "Execute exactly one RTL PlanTask. Preserve the verbatim locked interface. "
                "Write only declared drafts and report deterministic local checks."
            ),
        ),
        input_schema={"type": "object"},
        output_schema=_object_schema(
            ["status", "draft_artifact_id", "checks", "locked_interface_hash"],
            {
                "status": {"const": "complete"},
                "draft_artifact_id": {"type": "string", "minLength": 1},
                "checks": {"type": "array", "items": {"type": "object"}},
                "locked_interface_hash": {"type": "string", "minLength": 1},
            },
        ),
        tools=[
            ToolDefinition(
                name="read_spec",
                description="Read only an authorized locked specification pointer or source span.",
                input_schema=_object_schema(["pointer"], {"pointer": {"type": "string"}}),
                episode_kind=EpisodeKind.EXPLORATORY,
            ),
            ToolDefinition(
                name="read_artifact",
                description="Read a content-addressed authorized artifact.",
                input_schema=_object_schema(["artifact_id"], {"artifact_id": {"type": "string"}}),
                episode_kind=EpisodeKind.EXPLORATORY,
            ),
            ToolDefinition(
                name="write_draft",
                description="Write one RTL draft at a declared path only.",
                input_schema=_object_schema(
                    ["path", "content"],
                    {"path": {"type": "string"}, "content": {"type": "string"}},
                ),
                episode_kind=EpisodeKind.ACTION,
            ),
            ToolDefinition(
                name="run_verilator",
                description="Run the registered Verilator lint template over declared inputs.",
                input_schema=empty_object,
                episode_kind=EpisodeKind.ACTION,
                requires_manifest=True,
            ),
            ToolDefinition(
                name="run_yosys",
                description=(
                    "Run the registered Yosys parse or elaborate template over declared inputs."
                ),
                input_schema=empty_object,
                episode_kind=EpisodeKind.ACTION,
                requires_manifest=True,
            ),
            ToolDefinition(
                name="diff_declared_artifacts",
                description="Diff authorized base and draft artifact IDs.",
                input_schema=_object_schema(
                    ["base_artifact_id", "draft_artifact_id"],
                    {
                        "base_artifact_id": {"type": "string"},
                        "draft_artifact_id": {"type": "string"},
                    },
                ),
                episode_kind=EpisodeKind.EXPLORATORY,
            ),
        ],
        model_binding=model_binding,
        memory_scope=MemoryScope.TASK_SCOPED,
        termination_policy=TerminationPolicy(
            max_iterations=10,
            status_field="status",
            escalation=EscalationTarget.CONTROLLER,
        ),
        verification_gate_id="validate-rtl-task-result",
    )


def placeholder_specialist_definition(role: str, model_binding: ModelBinding) -> AgentDefinition:
    """Expose a role boundary without inventing unsupported EDA tool semantics."""

    return AgentDefinition(
        identity=f"Execute one scoped {role} task through declared capabilities only.",
        instructions=VersionedInstructions(
            version="placeholder-specialist-v1",
            text=(
                "This role has no stage-specific tool contract yet. Return blocked when required "
                "capabilities or an approved stage contract are absent."
            ),
        ),
        input_schema={"type": "object"},
        output_schema=_object_schema(
            ["status", "reason"], {"status": {"type": "string"}, "reason": {"type": "string"}}
        ),
        model_binding=model_binding,
        memory_scope=MemoryScope.TASK_SCOPED,
        termination_policy=TerminationPolicy(
            max_iterations=1,
            status_field="status",
            escalation=EscalationTarget.CONTROLLER,
        ),
    )
