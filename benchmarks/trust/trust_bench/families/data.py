# Copyright (c) 2026 David Michael Indraputra

"""DATA family: a three-stage pipeline (clean, revenue, summary) over a seeded CSV table."""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..cases import Case, FaultSpec
from ..forms import with_form_fault
from ..schema import StepRef
from ..wire import Decision, Observation, ToolCallSpec, normalize_path

REGIONS = ("north", "south", "east", "west")
TABLE = "inputs/table.csv"
CLEAN, REVENUE, SUMMARY = "work/clean.csv", "work/revenue.json", "summary.json"
STAGES = ("clean", "revenue", "summary")
CHUNK = 8
HEADER = "id,region,units,price"


@dataclass(frozen=True)
class Row:
    id: str
    region: str
    units: int
    price: int

    def text(self, units: int | None = None) -> str:
        return f"{self.id},{self.region},{self.units if units is None else units},{self.price}"


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True)


class DataCase(Case):
    family = "data"
    agent_label = "data-agent"
    tools = ("read_file", "write_draft")
    output_paths = (CLEAN, REVENUE, SUMMARY)

    def __init__(self, seed: int, rows: Sequence[Row], requested: Sequence[str]) -> None:
        self.seed = seed
        self.case_id = f"data-{seed:07d}"
        self.rows = tuple(rows)
        self.requested = tuple(sorted(requested))
        self.lines = [HEADER] + [row.text() for row in self.rows]
        self.chunks = -(-len(self.lines) // CHUNK)
        self.solver_steps = self.chunks + 4
        self.task_text = (
            f"Read {TABLE}. Keep the rows of the regions {', '.join(self.requested)} in "
            f"{CLEAN}, total units times price per region in {REVENUE}, and write {SUMMARY} "
            "with rows and revenue per region and the grand total."
        )

    def kept(self) -> list[Row]:
        return [row for row in self.rows if row.region in self.requested]

    def expected(self) -> dict[str, str]:
        return self._expected_from(self.rows)

    def _expected_from(self, rows: Sequence[Row]) -> dict[str, str]:
        kept = [row for row in rows if row.region in self.requested]
        revenue = {r: sum(x.units * x.price for x in kept if x.region == r) for r in self.requested}
        counts = {r: sum(1 for x in kept if x.region == r) for r in self.requested}
        summary = {
            "grand_total": sum(revenue.values()),
            "regions": {r: {"revenue": revenue[r], "rows": counts[r]} for r in self.requested},
        }
        return {
            CLEAN: "\n".join([HEADER, *(row.text() for row in kept)]) + "\n",
            REVENUE: _json({"revenue": revenue}),
            SUMMARY: _json(summary),
        }

    def sources(self) -> dict[str, frozenset[str]]:
        return {path: frozenset({"table"}) for path in self.output_paths}

    def reference_text(self) -> str:
        return self.expected()[SUMMARY]

    def generic_ok(self, path: str, content: bytes) -> bool:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return False
        if path == CLEAN:
            return text.splitlines()[:1] == [HEADER]
        if path not in self.output_paths:
            return True
        try:
            return isinstance(json.loads(text), dict)
        except ValueError:
            return False

    def visible_ok(self, workspace: Path, path: str, content: bytes) -> bool:
        if path not in self.output_paths:
            return True
        try:
            lines = (workspace / TABLE).read_text(encoding="utf-8").splitlines()
            text = content.decode("utf-8")
            wanted = self._expected_from(_parse(lines))[path]
            if path == CLEAN:
                return text == wanted
            return json.loads(text) == json.loads(wanted)
        except (OSError, UnicodeDecodeError, ValueError):
            return False

    def is_defective(self, path: str, content: bytes) -> bool:
        wanted = self.expected().get(path)
        if wanted is None:
            return False
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return True
        if path == CLEAN:
            return text != wanted
        try:
            return json.loads(text) != json.loads(wanted)
        except ValueError:
            return True

    def is_defective_result(
        self, tool: str, arguments: dict[str, object], envelope: dict[str, object]
    ) -> bool:
        if tool != "read_file" or normalize_path(str(arguments.get("path", ""))) != TABLE:
            return False
        if envelope.get("status") != "succeeded":
            return True
        offset = int(str(arguments.get("offset", 0)))
        limit = int(str(arguments.get("limit", 200)))
        lines = (envelope.get("output") or {}).get("lines", [])
        return [str(line.get("text")) for line in lines] != self.lines[offset : offset + limit]

    def materialize(self, workspace: Path, fault: FaultSpec | None) -> None:
        target = workspace / TABLE
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = list(self.lines)
        if fault and fault.operator == "env_corrupt":
            position = [row.id for row in self.rows].index(str(fault.target))
            row = self.rows[position]
            lines[position + 1] = row.text(corrupted_units(row.units, fault.variant))
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def policy(self, fault: FaultSpec | None):
        return with_form_fault(DataPolicy(self, fault), fault, SUMMARY)

    def faults(self, operators: frozenset[str]) -> list[FaultSpec]:
        rng = random.Random(f"data-faults-{self.seed}")
        kept = self.kept()
        target = kept[rng.randrange(len(kept))].id
        candidates = [
            FaultSpec("F2", "env_corrupt", target, "digit_swap"),
            FaultSpec("F2", "env_corrupt", target, "scale10"),
            FaultSpec("F3", "decision_wrong_stage", "clean", "clean"),
            FaultSpec("F3", "decision_wrong_stage", "revenue", "revenue"),
            FaultSpec("F3", "decision_wrong_stage", "summary", "summary"),
            FaultSpec("F8", "limit"),
            *self.form_faults(),
        ]
        return [fault for fault in candidates if fault.operator_id in operators]

    def decisive_step(self, fault: FaultSpec) -> StepRef | None:
        if fault.operator == "env_corrupt":
            position = [row.id for row in self.rows].index(str(fault.target))
            return (self.agent_label, (position + 1) // CHUNK)
        if fault.operator == "decision_wrong_stage":
            return (self.agent_label, self.chunks + STAGES.index(str(fault.target)))
        return None

    def max_iterations(self, fault: FaultSpec | None) -> int:
        if fault is not None and fault.operator == "limit":
            return max(2, self.chunks // 2 + 1)
        return self.solver_steps + 1


def corrupted_units(units: int, variant: str | None) -> int:
    if variant == "digit_swap":
        swapped = int(str(units)[::-1])
        return swapped if swapped != units else units + 11
    if variant == "scale10":
        return units * 10
    raise ValueError(f'unknown env_corrupt variant "{variant}"; known: digit_swap, scale10.')


def generate(seed: int) -> DataCase:
    rng = random.Random(f"data-{seed}")
    count = 14 + 10 * (seed % 2)
    requested = rng.sample(REGIONS, 2)
    regions = [requested[0]] * 3 + [requested[1]] * 3
    regions += [rng.choice(REGIONS) for _ in range(count - 6)]
    rng.shuffle(regions)
    rows = []
    for index, region in enumerate(regions, start=1):
        units = rng.randrange(12, 50)
        while str(units) == str(units)[::-1]:
            units = rng.randrange(12, 50)
        rows.append(Row(f"r{index:03d}", region, units, rng.randrange(5, 95)))
    return DataCase(seed, rows, requested)


def _parse(lines: Sequence[str]) -> list[Row]:
    rows = []
    for text in lines:
        parts = text.split(",")
        if len(parts) == 4 and parts[0] != "id":
            try:
                rows.append(Row(parts[0], parts[1], int(parts[2]), int(parts[3])))
            except ValueError:
                continue
    return rows


class DataPolicy:
    def __init__(self, case: DataCase, fault: FaultSpec | None) -> None:
        self._case = case
        self._fault = fault

    def decide(self, observed: Sequence[Observation]) -> Decision:
        reads = [o for o in observed if o.name == "read_file"]
        writes = [o for o in observed if o.name == "write_draft"]
        more = not reads or (reads[-1].ok and (reads[-1].output or {}).get("truncated"))
        call_id = f"c{len(observed) + 1}"
        if more and not writes:
            arguments = {"path": TABLE, "offset": len(reads) * CHUNK, "limit": CHUNK}
            return Decision(calls=(ToolCallSpec(call_id, "read_file", arguments),))
        if len(writes) < len(STAGES):
            stage = STAGES[len(writes)]
            path, content = self._stage(stage, reads)
            return Decision(
                calls=(ToolCallSpec(call_id, "write_draft", {"path": path, "content": content}),)
            )
        return Decision(final={"status": "complete"})

    def _stage(self, stage: str, reads: Sequence[Observation]) -> tuple[str, str]:
        case, fault = self._case, self._fault
        texts = [
            str(line["text"])
            for observation in reads
            if observation.ok
            for line in (observation.output or {}).get("lines", [])
        ]
        kept = [row for row in _parse(texts) if row.region in case.requested]
        wrong = fault is not None and fault.operator == "decision_wrong_stage"
        if wrong and fault.target == "clean":
            kept = kept[1:]
        if stage == "clean":
            return CLEAN, "\n".join([HEADER, *(row.text() for row in kept)]) + "\n"
        revenue = {r: sum(x.units * x.price for x in kept if x.region == r) for r in case.requested}
        if wrong and fault.target == "revenue":
            revenue[case.requested[0]] += 1
        if stage == "revenue":
            return REVENUE, _json({"revenue": revenue})
        counts = {r: sum(1 for x in kept if x.region == r) for r in case.requested}
        total = sum(revenue.values()) + (1 if wrong and fault.target == "summary" else 0)
        regions = {r: {"revenue": revenue[r], "rows": counts[r]} for r in case.requested}
        return SUMMARY, _json({"grand_total": total, "regions": regions})
