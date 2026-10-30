"""Three real agents over a shared artifact store, with a planted tool fault at a known call.

A extracts per-region totals from a sales table, B turns them into a grand total and a top region,
C writes the headline. A tool-level fault (a read that returns altered content, or a write that
stores altered content) is planted at one node. The agents are real models; the fault is the only
label. The ledgers, the tool-result journal and the artifact history are the SDK's own.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nailong_agent_sdk.agent.openai_compatible import (
    OpenAICompatibleAgentModel,
    OpenAICompatibleEndpoint,
)
from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.foundations.contracts import (
    AgentDefinition,
    EpisodeKind,
    EscalationTarget,
    MemoryScope,
    ModelBinding,
    ScopedAgentTask,
    TaskScope,
    TerminationPolicy,
    ToolDefinition,
    ToolExecutionResult,
    VersionedInstructions,
)
from nailong_agent_sdk.tools.artifacts import ArtifactStore
from nailong_agent_sdk.tools.tools import ToolInvocationContext

BASE_URL = "https://api.fireworks.ai/inference/v1"
NODES = ("A", "B", "C")
REGIONS = ("north", "south", "east")
HARD_REGIONS = ("north", "south", "east", "west", "central")
HARD_FROM_SEED = 1000
TOTALS_FROM_SEED = 2000
DERIVED_FROM_SEED = 3000
MAX_ITERATIONS = 8
MAX_TOKENS = 2_000
HARD_MAX_TOKENS = 16_000
COMPLETE = {
    "type": "object",
    "properties": {"status": {"const": "complete"}},
    "required": ["status"],
    "additionalProperties": True,
}
FAULT_TARGET_FILE = {
    ("read", "A"): "sales.csv",
    ("write", "A"): "facts.json",
    ("read", "B"): "facts.json",
    ("write", "B"): "analysis.md",
    ("read", "C"): "analysis.md",
    ("write", "C"): "summary.txt",
}
OUTPUT_FILE = {"A": "facts.json", "B": "analysis.md", "C": "summary.txt"}
INPUT_FILE = {"A": "sales.csv", "B": "facts.json", "C": "analysis.md"}
FINAL = '{"type": "final", "output": {"status": "complete"}}'
INSTRUCTIONS = {
    "A": (
        "Read the sales table (artifact id {artifact_id}) with read_artifact. Add up the amount "
        "column for each region and write the three totals to facts.json with write_draft as a "
        'JSON object such as {{"north": 100, "south": 200, "east": 300}}. When it is written, '
        "finish with a final reply of exactly {FINAL}"
    ),
    "B": (
        "Read the facts (artifact id {artifact_id}) with read_artifact. Work out the grand total "
        "over the three regions and which region has the largest total. Write analysis.md with "
        "write_draft: one line 'TOTAL: <number>', one line 'TOP: <region>', and one line per "
        "region with its total. When it is written, finish with a final reply of exactly {FINAL}"
    ),
    "C": (
        "Read the analysis (artifact id {artifact_id}) with read_artifact. Write summary.txt "
        "with write_draft containing exactly one line of the form TOTAL=<number>;TOP=<region> "
        "taken from the analysis. When it is written, finish with a final reply of exactly {FINAL}"
    ),
}


HARD_INSTRUCTIONS = {
    **INSTRUCTIONS,
    "A": (
        "Read the sales table (artifact id {artifact_id}) with read_artifact. Add up the amount "
        "column for each of the five regions and write the five totals to facts.json with "
        "write_draft as a JSON object mapping each region name to its total, such as "
        '{{"north": 100, "south": 200}}. When it is written, finish with a final reply of '
        "exactly {FINAL}"
    ),
    "B": (
        "Read the facts (artifact id {artifact_id}) with read_artifact. Work out the grand total "
        "over all the regions and which region has the largest total. Write analysis.md with "
        "write_draft: one line 'TOTAL: <number>', one line 'TOP: <region>', and one line per "
        "region with its total. When it is written, finish with a final reply of exactly {FINAL}"
    ),
}


DERIVED_INSTRUCTIONS = {
    **HARD_INSTRUCTIONS,
    "B": (
        "Read the facts (artifact id {artifact_id}) with read_artifact. Work out the grand total "
        "over all the regions, which region has the largest total, the margin (the largest total "
        "minus the second largest total) and the average (the grand total divided by the number "
        "of regions, rounded down to a whole number). Write analysis.md with write_draft: one "
        "line 'TOTAL: <number>', one line 'TOP: <region>', one line 'MARGIN: <number>', one line "
        "'AVERAGE: <number>', and one line per region with its total. When it is written, finish "
        "with a final reply of exactly {FINAL}"
    ),
    "C": (
        "Read the analysis (artifact id {artifact_id}) with read_artifact. Write summary.txt "
        "with write_draft containing exactly one line of the form "
        "TOTAL=<number>;TOP=<region>;MARGIN=<number> taken from the analysis. When it is "
        "written, finish with a final reply of exactly {FINAL}"
    ),
}
FILE_SPEC = (
    "- facts.json: a JSON object with the sum of the amount column for each region\n"
    "- analysis.md: a line 'TOTAL: <grand total of all amounts>', a line 'TOP: <region with the\n"
    "  largest total>' and one line per region with its total\n"
    "- summary.txt: the single line TOTAL=<grand total>;TOP=<top region>"
)
DERIVED_FILE_SPEC = (
    "- facts.json: a JSON object with the sum of the amount column for each region\n"
    "- analysis.md: lines 'TOTAL: <grand total>', 'TOP: <region with the largest total>',\n"
    "  'MARGIN: <largest total minus second largest total>', 'AVERAGE: <grand total divided by\n"
    "  the number of regions, rounded down>' and one line per region with its total\n"
    "- summary.txt: the single line TOTAL=<grand total>;TOP=<top region>;MARGIN=<margin>"
)


def derived(seed: int) -> bool:
    return seed >= DERIVED_FROM_SEED


def file_spec(seed: int) -> str:
    return DERIVED_FILE_SPEC if derived(seed) else FILE_SPEC


def instructions_for(node: str, seed: int) -> str:
    if derived(seed):
        return DERIVED_INSTRUCTIONS[node]
    return (HARD_INSTRUCTIONS if hard(seed) else INSTRUCTIONS)[node]


def tool(name: str, description: str, properties: dict[str, Any]) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        episode_kind=EpisodeKind.ACTION,
    )


TOOLS = [
    tool("read_artifact", "Read an artifact by id.", {"artifact_id": {"type": "string"}}),
    tool(
        "write_draft",
        "Write a file in the shared workspace.",
        {"path": {"type": "string"}, "content": {"type": "string"}},
    ),
]


@dataclass(frozen=True)
class Case:
    case_id: str
    model: str
    kind: str
    node: str
    seed: int
    split: str

    @property
    def fault(self) -> tuple[str, str] | None:
        return (self.kind, self.node) if self.kind in {"read", "write"} else None


def hard(seed: int) -> bool:
    return seed >= HARD_FROM_SEED


def regions_for(seed: int) -> tuple[str, ...]:
    return HARD_REGIONS if hard(seed) else REGIONS


def sales(seed: int) -> list[tuple[str, str, int]]:
    rng = random.Random(f"sales:{seed}")
    if TOTALS_FROM_SEED <= seed < DERIVED_FROM_SEED:
        return one_row_per_region(seed, rng)
    months, low, high = (6, 10_000, 99_999) if hard(seed) else (4, 120, 980)
    while True:
        rows = [
            (f"2026-{month:02d}", region, rng.randint(low, high))
            for month in range(1, months + 1)
            for region in regions_for(seed)
        ]
        totals = region_totals(rows, seed)
        ordered = sorted(totals.values(), reverse=True)
        if ordered[0] != ordered[1]:
            return rows


def one_row_per_region(seed: int, rng: random.Random) -> list[tuple[str, str, int]]:
    while True:
        rows = [("2026-01", region, rng.randint(100_000, 999_999)) for region in regions_for(seed)]
        ordered = sorted((amount for _, _, amount in rows), reverse=True)
        if ordered[0] != ordered[1]:
            return rows


def region_totals(rows: list[tuple[str, str, int]], seed: int = 0) -> dict[str, int]:
    totals = dict.fromkeys(regions_for(seed), 0)
    for _, region, amount in rows:
        totals[region] += amount
    return totals


def sales_csv(rows: list[tuple[str, str, int]]) -> str:
    return "month,region,amount\n" + "".join(f"{m},{r},{a}\n" for m, r, a in rows)


def truth(seed: int) -> dict[str, Any]:
    totals = region_totals(sales(seed), seed)
    top = max(totals, key=lambda region: totals[region])
    found = {"per_region": totals, "total": sum(totals.values()), "top": top}
    if derived(seed):
        ordered = sorted(totals.values(), reverse=True)
        found["margin"] = ordered[0] - ordered[1]
        found["average"] = found["total"] // len(totals)
    return found


def answer_line(seed: int) -> str:
    expected = truth(seed)
    line = f"TOTAL={expected['total']};TOP={expected['top']}"
    return f"{line};MARGIN={expected['margin']}" if derived(seed) else line


def corrupt(path: str, content: str, seed: int) -> str:
    rng = random.Random(f"fault:{path}:{seed}")
    delta = rng.choice([-1, 1]) * rng.randint(37, 211)
    if path == "sales.csv":
        lines = content.splitlines()
        index = rng.randint(1, len(lines) - 1)
        month, region, amount = lines[index].split(",")
        lines[index] = f"{month},{region},{max(1, int(amount) + delta)}"
        return "\n".join(lines) + "\n"
    if path == "facts.json":
        data = json.loads(content)
        region = rng.choice(sorted(data))
        data[region] = int(data[region]) + delta
        return json.dumps(data)
    if path == "analysis.md":
        return re.sub(
            r"TOTAL:\s*(\d+)",
            lambda match: f"TOTAL: {max(1, int(match.group(1)) + delta)}",
            content,
            count=1,
        )
    return re.sub(
        r"TOTAL=(\d+)",
        lambda match: f"TOTAL={max(1, int(match.group(1)) + delta)}",
        content,
        count=1,
    )


def _number(text: str, pattern: str) -> int | None:
    found = re.search(pattern, text)
    return int(found.group(1)) if found else None


def artifact_defective(path: str, content: str, seed: int) -> bool:
    expected = truth(seed)
    try:
        if path == "facts.json":
            return {k: int(v) for k, v in json.loads(content).items()} != expected["per_region"]
        if path == "analysis.md":
            top = re.search(r"TOP:\s*(\w+)", content)
            wrong = (
                _number(content, r"TOTAL:\s*(\d+)") != expected["total"]
                or top is None
                or top.group(1).lower() != expected["top"]
            )
            if derived(seed):
                wrong = (
                    wrong
                    or _number(content, r"MARGIN:\s*(\d+)") != expected["margin"]
                    or _number(content, r"AVERAGE:\s*(\d+)") != expected["average"]
                )
            return wrong
        if path == "summary.txt":
            return content.strip() != answer_line(seed)
    except (ValueError, TypeError, AttributeError):
        return True
    return False


def natural_label(workspace: Path, seed: int) -> dict[str, Any] | None:
    store = ArtifactStore(workspace, read_only=True)
    for node in NODES:
        history = store.history(OUTPUT_FILE[node])
        if not history:
            return None
        latest = history[-1]
        content = store.read_bytes(latest.sha256).decode("utf-8")
        if artifact_defective(OUTPUT_FILE[node], content, seed):
            return {
                "node": node,
                "call_id": latest.manifest.get("tool_call_id"),
                "tool": "write_draft",
            }
    return None


def outcome_failed(workspace: Path, seed: int) -> bool:
    store = ArtifactStore(workspace, read_only=True)
    history = store.history("summary.txt") if (workspace / ".agent-artifacts").is_dir() else []
    if not history:
        return True
    return artifact_defective("summary.txt", store.read_bytes(history[-1].sha256).decode(), seed)


class NodeExecutor:
    def __init__(self, store: ArtifactStore, case: Case, node: str) -> None:
        self.store, self.case, self.node = store, case, node
        self.fault = case.fault if case.fault and case.fault[1] == node else None
        self.fault_call: dict[str, Any] | None = None
        self.last_write: str | None = None

    async def execute(self, tool_def: ToolDefinition, context: ToolInvocationContext) -> Any:
        arguments = context.call.arguments
        if tool_def.name == "read_artifact":
            return self.read(str(arguments.get("artifact_id", "")), context)
        if tool_def.name == "write_draft":
            return self.write(
                str(arguments.get("path", "")), str(arguments.get("content", "")), context
            )
        return ToolExecutionResult(status="failed", error=f'Unknown tool "{tool_def.name}".')

    def read(self, artifact_id: str, context: ToolInvocationContext) -> ToolExecutionResult:
        versions = [
            item
            for path in self.store.paths()
            for item in self.store.history(path)
            if item.artifact_id == artifact_id
        ]
        if not versions:
            return ToolExecutionResult(
                status="failed", error=f'No artifact has the id "{artifact_id}".'
            )
        version = versions[-1]
        content = self.store.read_bytes(version.sha256).decode("utf-8")
        if (
            self.fault
            and self.fault[0] == "read"
            and version.relative_path == FAULT_TARGET_FILE[self.fault]
        ):
            altered = corrupt(version.relative_path, content, self.case.seed)
            self.fault_call = {"tool": "read_artifact", "call_id": context.call.id}
            content = altered
        return ToolExecutionResult(
            status="succeeded",
            output={
                "artifact_id": version.artifact_id,
                "path": version.relative_path,
                "content": content,
            },
        )

    def write(self, path: str, content: str, context: ToolInvocationContext) -> ToolExecutionResult:
        stored = content
        if self.fault and self.fault[0] == "write" and path == FAULT_TARGET_FILE[self.fault]:
            stored = corrupt(path, content, self.case.seed)
            self.fault_call = {"tool": "write_draft", "call_id": context.call.id}
        record = self.store.write_text(
            path,
            stored,
            manifest={
                "agent_identity": context.agent_identity,
                "agent_run_id": context.task.id,
                "tool": "write_draft",
                "tool_call_id": context.call.id,
                "iteration": context.iteration,
            },
        )
        self.last_write = path
        return ToolExecutionResult(
            status="succeeded", output={"artifact_id": record.artifact_id, "path": path}
        )


def definition(node: str, model: str, max_tokens: int = MAX_TOKENS) -> AgentDefinition:
    return AgentDefinition(
        identity=f"node-{node}",
        instructions=VersionedInstructions(version="v1", text=f"You are agent {node}."),
        input_schema={"type": "object"},
        tools=TOOLS,
        model_binding=ModelBinding(
            provider="fireworks",
            model=model,
            parameters={"max_tokens": max_tokens, "temperature": 0.2},
        ),
        output_schema=COMPLETE,
        memory_scope=MemoryScope.TASK_SCOPED,
        termination_policy=TerminationPolicy(
            max_iterations=MAX_ITERATIONS, status_field="status", escalation=EscalationTarget.NONE
        ),
    )


def run_case(case: Case, key: str, directory: Path) -> dict[str, Any]:
    if directory.exists():
        shutil.rmtree(directory)
    service, workspace = directory / "service", directory / "workspace"
    services = AgentRuntimeServices.open(service, checkpointer=None)
    store = ArtifactStore(workspace)
    setup = store.write_text(
        "sales.csv",
        sales_csv(sales(case.seed)),
        manifest={"agent_identity": "setup", "agent_run_id": "setup", "tool": "setup"},
    )
    handoff = setup.artifact_id
    statuses: dict[str, str] = {}
    fault_call: dict[str, Any] | None = None
    started_ok = True
    try:
        for node in NODES:
            executor = NodeExecutor(store, case, node)
            model = OpenAICompatibleAgentModel(
                OpenAICompatibleEndpoint(base_url=BASE_URL, api_key=key, timeout_seconds=90.0),
                provider="fireworks",
                model=case.model,
            )
            subject = services.create_agent(
                definition(node, case.model, HARD_MAX_TOKENS if hard(case.seed) else MAX_TOKENS),
                model,
                tool_executor=executor,
                require_tool_authorization=False,
            )
            task = ScopedAgentTask(
                id=f"node-{node}",
                input={},
                scope=TaskScope(label="pipeline"),
                locked_interface={},
                instructions=INSTRUCTIONS[node].format(
                    artifact_id=handoff, FINAL=FINAL.replace("{{", "{").replace("}}", "}")
                ),
                acceptance_criteria=[f"{OUTPUT_FILE[node]} is written"],
            )
            try:
                result = asyncio.run(subject.run(task))
                statuses[node] = result.status.value
            except Exception as error:
                statuses[node] = f"exception:{type(error).__name__}"
            if executor.fault_call is not None:
                fault_call = {**executor.fault_call, "node": node}
            written = store.history(OUTPUT_FILE[node])
            if statuses[node] != "completed" or not written:
                started_ok = False
                break
            handoff = written[-1].artifact_id
    finally:
        services.audit_logs.close()
        services.telemetry.close()
    failed = outcome_failed(workspace, case.seed)
    if case.kind == "natural" and started_ok and failed:
        fault_call = natural_label(workspace, case.seed)
    return {
        "case_id": case.case_id,
        "model": case.model,
        "kind": case.kind,
        "node": case.node,
        "seed": case.seed,
        "split": case.split,
        "statuses": statuses,
        "pipeline_complete": started_ok,
        "outcome_failed": failed,
        "fault_call": fault_call,
        "expected": truth(case.seed),
    }
