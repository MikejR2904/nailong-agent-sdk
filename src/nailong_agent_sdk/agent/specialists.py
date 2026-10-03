# Copyright (c) 2026 David Michael Indraputra

"""Source-grounded BaseAgent specializations for the RTL-to-GDSII stages.

RTLWorker follows the contract the framework defines explicitly (systems-design
framework, updated PDF, p. 66). The synthesis, physical-design and timing
sign-off workers use the harness's registered process tools (``run_yosys``,
``run_openroad``, ``run_opensta``): each writes only declared drafts, runs its
stage tool through a named command template, and reports content-addressed
artifacts plus deterministic checks. Acceptance stays with verification gates.
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


def _read_spec_tool() -> ToolDefinition:
    return ToolDefinition(
        name="read_spec",
        description="Read only an authorized locked specification pointer or source span.",
        input_schema=_object_schema(["pointer"], {"pointer": {"type": "string"}}),
        episode_kind=EpisodeKind.EXPLORATORY,
    )


def _read_artifact_tool() -> ToolDefinition:
    return ToolDefinition(
        name="read_artifact",
        description="Read a content-addressed authorized artifact.",
        input_schema=_object_schema(["artifact_id"], {"artifact_id": {"type": "string"}}),
        episode_kind=EpisodeKind.EXPLORATORY,
    )


def _write_draft_tool(what: str) -> ToolDefinition:
    return ToolDefinition(
        name="write_draft",
        description=f"Write one {what} at a declared path only.",
        input_schema=_object_schema(
            ["path", "content"], {"path": {"type": "string"}, "content": {"type": "string"}}
        ),
        episode_kind=EpisodeKind.ACTION,
    )


def _process_tool(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema={"type": "object", "additionalProperties": False},
        episode_kind=EpisodeKind.ACTION,
        requires_manifest=True,
    )


_ARTIFACT_ID = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
_CHECKS = {"type": "array", "items": {"type": "object"}}


def _stage_worker(
    *,
    identity: str,
    version: str,
    instructions: str,
    tools: list[ToolDefinition],
    outputs: dict[str, object],
    model_binding: ModelBinding,
) -> AgentDefinition:
    return AgentDefinition(
        identity=identity,
        instructions=VersionedInstructions(version=version, text=instructions),
        input_schema={"type": "object"},
        output_schema=_object_schema(
            ["status", *outputs], {"status": {"const": "complete"}, **outputs}
        ),
        tools=tools,
        model_binding=model_binding,
        memory_scope=MemoryScope.TASK_SCOPED,
        termination_policy=TerminationPolicy(
            max_iterations=12,
            status_field="status",
            escalation=EscalationTarget.CONTROLLER,
        ),
        verification_gate_id="status-is-complete",
    )


def synthesis_worker_definition(model_binding: ModelBinding) -> AgentDefinition:
    """Synthesize one scoped, already-linted RTL artifact to a gate-level netlist."""

    return _stage_worker(
        identity="Synthesize one scoped RTL artifact into a gate-level netlist.",
        version="synthesis-worker-v1",
        instructions=(
            "Execute exactly one synthesis PlanTask. Read the locked specification and the "
            "authorized RTL artifact, write the synthesis script and constraints only at "
            "declared paths, then run the registered Yosys template. Report the netlist "
            "artifact and every check Yosys produced; never alter the locked interface."
        ),
        tools=[
            _read_spec_tool(),
            _read_artifact_tool(),
            _write_draft_tool("synthesis script or constraint file"),
            _process_tool("run_yosys", "Run the registered Yosys synthesis template."),
        ],
        outputs={"netlist_artifact_id": _ARTIFACT_ID, "checks": _CHECKS},
        model_binding=model_binding,
    )


def physical_design_worker_definition(model_binding: ModelBinding) -> AgentDefinition:
    """Floorplan, place and route one scoped netlist with OpenROAD."""

    return _stage_worker(
        identity="Place and route one scoped netlist into a layout.",
        version="physical-design-worker-v1",
        instructions=(
            "Execute exactly one physical-design PlanTask. Read the locked specification and "
            "the authorized netlist, write floorplan and flow scripts only at declared paths, "
            "then run the registered OpenROAD template. Report the layout artifact and every "
            "check (DRC, congestion, utilization) the run produced."
        ),
        tools=[
            _read_spec_tool(),
            _read_artifact_tool(),
            _write_draft_tool("floorplan or physical-design flow script"),
            _process_tool("run_openroad", "Run the registered OpenROAD flow template."),
        ],
        outputs={"layout_artifact_id": _ARTIFACT_ID, "checks": _CHECKS},
        model_binding=model_binding,
    )


def timing_signoff_worker_definition(model_binding: ModelBinding) -> AgentDefinition:
    """Run static timing analysis on one scoped layout or netlist with OpenSTA."""

    return _stage_worker(
        identity="Sign off timing for one scoped design with static timing analysis.",
        version="timing-signoff-worker-v1",
        instructions=(
            "Execute exactly one timing sign-off PlanTask. Read the locked timing "
            "constraints and the authorized design artifact, run the registered OpenSTA "
            "template, and report worst and total negative slack exactly as the tool "
            "printed them. Set timing_met only from those numbers."
        ),
        tools=[
            _read_spec_tool(),
            _read_artifact_tool(),
            _process_tool("run_opensta", "Run the registered OpenSTA analysis template."),
        ],
        outputs={
            "timing_report_artifact_id": _ARTIFACT_ID,
            "worst_negative_slack_ns": {"type": "number"},
            "total_negative_slack_ns": {"type": "number"},
            "timing_met": {"type": "boolean"},
            "checks": _CHECKS,
        },
        model_binding=model_binding,
    )
