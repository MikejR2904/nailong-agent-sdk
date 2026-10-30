# Copyright (c) 2026 David Michael Indraputra

"""PLAN family: two ingest agents feed a variance agent, a three-node plan held out of dev."""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..cases import Case, FaultSpec
from ..forms import with_form_fault
from ..nodes import NodeSpec
from ..schema import StepRef
from ..wire import Decision, Observation, ToolCallSpec, normalize_path

DEPARTMENTS = ("ops", "sales", "legal", "research")
BUDGET_TABLE, ACTUALS_TABLE = "inputs/budget.csv", "inputs/actuals.csv"
BUDGET_OUT, ACTUALS_OUT, REPORT = "work/budget.json", "work/actuals.json", "report.json"
INGEST_BUDGET, INGEST_ACTUALS, VARIANCE = "ingest-budget", "ingest-actuals", "variance"
HEADER = "id,dept,amount"
CHUNK = 8
UNREADABLE = b"\xff\xfe\x00\x80not-utf8"
TABLES = {INGEST_BUDGET: BUDGET_TABLE, INGEST_ACTUALS: ACTUALS_TABLE}
OUTPUTS = {INGEST_BUDGET: BUDGET_OUT, INGEST_ACTUALS: ACTUALS_OUT, VARIANCE: REPORT}
PREFIXES = {INGEST_BUDGET: "ib", INGEST_ACTUALS: "ia", VARIANCE: "va"}
WRONG_NODES = {"budget": INGEST_BUDGET, "actuals": INGEST_ACTUALS, "variance": VARIANCE}


@dataclass(frozen=True)
class Row:
    id: str
    dept: str
    amount: int

    def text(self, amount: int | None = None) -> str:
        return f"{self.id},{self.dept},{self.amount if amount is None else amount}"


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True)


class PlanCase(Case):
    family = "plan"
    agent_label = VARIANCE
    tools = ("read_file", "write_draft")
    output_paths = (BUDGET_OUT, ACTUALS_OUT, REPORT)

    def __init__(
        self,
        seed: int,
        budget: Sequence[Row],
        actuals: Sequence[Row],
        requested: Sequence[str],
    ) -> None:
        self.seed = seed
        self.case_id = f"plan-{seed:07d}"
        self.tables = {INGEST_BUDGET: tuple(budget), INGEST_ACTUALS: tuple(actuals)}
        self.requested = tuple(sorted(requested))
        self.lines = {
            label: [HEADER, *(row.text() for row in rows)] for label, rows in self.tables.items()
        }
        self.chunks = {label: -(-len(lines) // CHUNK) for label, lines in self.lines.items()}
        self.solver_steps = max(self.chunks.values()) + 3
        depts = ", ".join(self.requested)
        self.task_text = (
            f"Total the budget and the actual spend of the departments {depts}, and report the "
            f"variance (actual minus budget) per department and overall in {REPORT}."
        )

    def nodes(self) -> tuple[NodeSpec, ...]:
        depts = ", ".join(self.requested)
        return (
            NodeSpec(
                INGEST_BUDGET,
                self.tools,
                (BUDGET_OUT,),
                (),
                f"Read {BUDGET_TABLE} and write {BUDGET_OUT} with the budget total of each of "
                f"the departments {depts}.",
            ),
            NodeSpec(
                INGEST_ACTUALS,
                self.tools,
                (ACTUALS_OUT,),
                (),
                f"Read {ACTUALS_TABLE} and write {ACTUALS_OUT} with the actual total of each of "
                f"the departments {depts}.",
            ),
            NodeSpec(
                VARIANCE,
                self.tools,
                (REPORT,),
                (INGEST_BUDGET, INGEST_ACTUALS),
                f"Read {BUDGET_OUT} and {ACTUALS_OUT} and write {REPORT} with the variance "
                "(actual minus budget) per department and overall.",
            ),
        )

    def totals(self, label: str) -> dict[str, int]:
        return self._totals_of(self.tables[label])

    def _totals_of(self, rows: Sequence[Row]) -> dict[str, int]:
        return {d: sum(r.amount for r in rows if r.dept == d) for d in self.requested}

    def _report_from(self, budget: dict[str, int], actuals: dict[str, int]) -> str:
        variance = {d: actuals.get(d, 0) - budget.get(d, 0) for d in self.requested}
        return _json({"overall": sum(variance.values()), "variance": variance})

    def expected(self) -> dict[str, str]:
        budget, actuals = self.totals(INGEST_BUDGET), self.totals(INGEST_ACTUALS)
        return {
            BUDGET_OUT: _json({"totals": budget}),
            ACTUALS_OUT: _json({"totals": actuals}),
            REPORT: self._report_from(budget, actuals),
        }

    def reference_text(self) -> str:
        return self.expected()[REPORT]

    def generic_ok(self, path: str, content: bytes) -> bool:
        if path not in self.output_paths:
            return True
        try:
            return isinstance(json.loads(content.decode("utf-8")), dict)
        except (UnicodeDecodeError, ValueError):
            return False

    def visible_ok(self, workspace: Path, path: str, content: bytes) -> bool:
        if path not in self.output_paths:
            return True
        try:
            wanted = self._wanted_in(workspace, path)
            return json.loads(content.decode("utf-8")) == json.loads(wanted)
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
            return False

    def _wanted_in(self, workspace: Path, path: str) -> str:
        if path != REPORT:
            table = BUDGET_TABLE if path == BUDGET_OUT else ACTUALS_TABLE
            return _json({"totals": self._totals_of(_parse(_readable_lines(workspace / table)))})
        loaded = {}
        for out in (BUDGET_OUT, ACTUALS_OUT):
            body = json.loads((workspace / out).read_text(encoding="utf-8"))
            loaded[out] = {str(k): int(v) for k, v in body["totals"].items()}
        return self._report_from(loaded[BUDGET_OUT], loaded[ACTUALS_OUT])

    def sources(self) -> dict[str, frozenset[str]]:
        return {
            BUDGET_OUT: frozenset({"budget"}),
            ACTUALS_OUT: frozenset({"actuals"}),
            REPORT: frozenset({"budget", "actuals"}),
        }

    def is_defective(self, path: str, content: bytes) -> bool:
        wanted = self.expected().get(path)
        if wanted is None:
            return False
        try:
            return json.loads(content.decode("utf-8")) != json.loads(wanted)
        except (UnicodeDecodeError, ValueError):
            return True

    def is_defective_result(
        self, tool: str, arguments: dict[str, object], envelope: dict[str, object]
    ) -> bool:
        if tool != "read_file":
            return False
        path = normalize_path(str(arguments.get("path", "")))
        if path not in {BUDGET_TABLE, ACTUALS_TABLE, BUDGET_OUT, ACTUALS_OUT}:
            return False
        if envelope.get("status") != "succeeded":
            return True
        lines = [str(line.get("text")) for line in (envelope.get("output") or {}).get("lines", [])]
        if path in {BUDGET_OUT, ACTUALS_OUT}:
            return "\n".join(lines) != self.expected()[path]
        label = INGEST_BUDGET if path == BUDGET_TABLE else INGEST_ACTUALS
        offset = int(str(arguments.get("offset", 0)))
        limit = int(str(arguments.get("limit", 200)))
        return lines != self.lines[label][offset : offset + limit]

    def materialize(self, workspace: Path, fault: FaultSpec | None) -> None:
        for label, table in TABLES.items():
            target = workspace / table
            target.parent.mkdir(parents=True, exist_ok=True)
            lines = list(self.lines[label])
            if fault and fault.operator == "env_unreadable" and fault.target == label:
                target.write_bytes(UNREADABLE)
                continue
            if fault and fault.operator == "env_corrupt" and fault.target == label:
                index = int(str(fault.row))
                row = self.tables[label][index]
                lines[index + 1] = row.text(corrupted_amount(row.amount, fault.variant))
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def policy(self, fault: FaultSpec | None):
        raise NotImplementedError("PLAN is a multi-node case; ask for node_policy(label, fault).")

    def node_policy(self, label: str, fault: FaultSpec | None):
        if label == VARIANCE:
            return with_form_fault(VariancePolicy(self, fault), fault, REPORT)
        return IngestPolicy(self, label, fault)

    def faults(self, operators: frozenset[str]) -> list[FaultSpec]:
        rng = random.Random(f"plan-faults-{self.seed}")
        label = rng.choice((INGEST_BUDGET, INGEST_ACTUALS))
        rows = [index for index, row in enumerate(self.tables[label]) if row.dept in self.requested]
        index = rows[rng.randrange(len(rows))]
        candidates = [
            FaultSpec("F1", "env_unreadable", INGEST_ACTUALS),
            FaultSpec("F2", "env_corrupt", label, "digit_swap", row=index),
            FaultSpec("F2", "env_corrupt", label, "scale10", row=index),
            FaultSpec("F3", "decision_wrong_node", "budget", "budget"),
            FaultSpec("F3", "decision_wrong_node", "actuals", "actuals"),
            FaultSpec("F3", "decision_wrong_node", "variance", "variance"),
            FaultSpec("F8", "limit", INGEST_ACTUALS),
            *self.form_faults(),
        ]
        return [fault for fault in candidates if fault.operator_id in operators]

    def decisive_step(self, fault: FaultSpec) -> StepRef | None:
        if fault.operator == "env_unreadable":
            return (str(fault.target), 0)
        if fault.operator == "env_corrupt":
            return (str(fault.target), (int(str(fault.row)) + 1) // CHUNK)
        if fault.operator == "decision_wrong_node":
            label = WRONG_NODES[str(fault.target)]
            return (label, 1 if label == VARIANCE else self.chunks[label])
        return None

    def max_iterations(self, fault: FaultSpec | None) -> int:
        return self.solver_steps + 1

    def node_max_iterations(self, label: str, fault: FaultSpec | None) -> int:
        if fault is not None and fault.operator == "limit" and fault.target == label:
            return max(2, self.chunks[label] // 2 + 1)
        if label == VARIANCE:
            return 4
        return self.chunks[label] + 3


def corrupted_amount(amount: int, variant: str | None) -> int:
    if variant == "digit_swap":
        swapped = int(str(amount)[::-1])
        return swapped if swapped != amount else amount + 11
    if variant == "scale10":
        return amount * 10
    raise ValueError(f'unknown env_corrupt variant "{variant}"; known: digit_swap, scale10.')


def generate(seed: int) -> PlanCase:
    rng = random.Random(f"plan-{seed}")
    count = 14 + 10 * (seed % 2)
    requested = rng.sample(DEPARTMENTS, 2)

    def table(prefix: str) -> list[Row]:
        depts = [requested[0]] * 3 + [requested[1]] * 3
        depts += [rng.choice(DEPARTMENTS) for _ in range(count - 6)]
        rng.shuffle(depts)
        rows = []
        for index, dept in enumerate(depts, start=1):
            amount = rng.randrange(120, 980)
            while str(amount) == str(amount)[::-1]:
                amount = rng.randrange(120, 980)
            rows.append(Row(f"{prefix}{index:03d}", dept, amount))
        return rows

    return PlanCase(seed, table("b"), table("a"), requested)


def _readable_lines(target: Path) -> list[str]:
    try:
        return target.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def _parse(lines: Sequence[str]) -> list[Row]:
    rows = []
    for text in lines:
        parts = text.split(",")
        if len(parts) == 3 and parts[0] != "id":
            try:
                rows.append(Row(parts[0], parts[1], int(parts[2])))
            except ValueError:
                continue
    return rows


class IngestPolicy:
    def __init__(self, case: PlanCase, label: str, fault: FaultSpec | None) -> None:
        self._case = case
        self._label = label
        self._fault = fault
        self._prefix = PREFIXES[label]

    def decide(self, observed: Sequence[Observation]) -> Decision:
        reads = [o for o in observed if o.name == "read_file"]
        writes = [o for o in observed if o.name == "write_draft"]
        more = not reads or (reads[-1].ok and (reads[-1].output or {}).get("truncated"))
        call_id = f"{self._prefix}-c{len(observed) + 1}"
        if more and not writes:
            arguments = {
                "path": TABLES[self._label],
                "offset": len(reads) * CHUNK,
                "limit": CHUNK,
            }
            return Decision(calls=(ToolCallSpec(call_id, "read_file", arguments),))
        if not writes:
            arguments = {"path": OUTPUTS[self._label], "content": self._totals(reads)}
            return Decision(calls=(ToolCallSpec(call_id, "write_draft", arguments),))
        return Decision(final={"status": "complete"})

    def _totals(self, reads: Sequence[Observation]) -> str:
        texts = [
            str(line["text"])
            for observation in reads
            if observation.ok
            for line in (observation.output or {}).get("lines", [])
        ]
        rows = _parse(texts)
        totals = {d: sum(r.amount for r in rows if r.dept == d) for d in self._case.requested}
        fault = self._fault
        if (
            fault is not None
            and fault.operator == "decision_wrong_node"
            and WRONG_NODES[str(fault.target)] == self._label
        ):
            totals[self._case.requested[0]] += 1
        return _json({"totals": totals})


class VariancePolicy:
    def __init__(self, case: PlanCase, fault: FaultSpec | None) -> None:
        self._case = case
        self._fault = fault
        self._prefix = PREFIXES[VARIANCE]

    def decide(self, observed: Sequence[Observation]) -> Decision:
        if not observed:
            return Decision(
                calls=(
                    ToolCallSpec(f"{self._prefix}-c1", "read_file", {"path": BUDGET_OUT}),
                    ToolCallSpec(f"{self._prefix}-c2", "read_file", {"path": ACTUALS_OUT}),
                )
            )
        if len(observed) == 2:
            arguments = {"path": REPORT, "content": self._report(observed)}
            return Decision(calls=(ToolCallSpec(f"{self._prefix}-c3", "write_draft", arguments),))
        return Decision(final={"status": "complete"})

    def _report(self, reads: Sequence[Observation]) -> str:
        loaded: dict[str, dict[str, int]] = {}
        for observation in reads:
            path = normalize_path(str(observation.arguments.get("path")))
            lines = (observation.output or {}).get("lines", []) if observation.ok else []
            try:
                body = json.loads("\n".join(str(line["text"]) for line in lines))
                loaded[path] = {str(k): int(v) for k, v in body["totals"].items()}
            except (ValueError, KeyError, TypeError):
                loaded[path] = {}
        budget, actuals = loaded.get(BUDGET_OUT, {}), loaded.get(ACTUALS_OUT, {})
        variance = {d: actuals.get(d, 0) - budget.get(d, 0) for d in self._case.requested}
        overall = sum(variance.values())
        fault = self._fault
        if (
            fault is not None
            and fault.operator == "decision_wrong_node"
            and WRONG_NODES[str(fault.target)] == VARIANCE
        ):
            overall += 1
        return _json({"overall": overall, "variance": variance})
