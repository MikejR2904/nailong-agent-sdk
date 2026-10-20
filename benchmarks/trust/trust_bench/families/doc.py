# Copyright (c) 2026 David Michael Indraputra

"""DOC family: total amounts per requested vendor from seeded records, report as JSON."""

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

VENDORS = ("Acme", "Birch", "Cobalt", "Delta")
REPORT = "report.json"
UNREADABLE = b"\xff\xfe\x00\x80not-utf8"
WORDS = (
    "invoice",
    "shipment",
    "pallet",
    "carrier",
    "customs",
    "ledger",
    "audit",
    "quarter",
    "supplier",
    "contract",
    "freight",
    "inspection",
    "approval",
    "discount",
    "balance",
)
MEMO_CHARS = 700
LARGE_RECORDS = 24


@dataclass(frozen=True)
class Record:
    id: str
    vendor: str
    amount: int
    memo: str = ""

    def text(self, amount: int | None = None) -> str:
        body: dict[str, object] = {
            "amount": self.amount if amount is None else amount,
            "id": self.id,
            "vendor": self.vendor,
        }
        if self.memo:
            body["memo"] = self.memo
        return json.dumps(body)


class DocCase(Case):
    family = "doc"
    agent_label = "doc-agent"
    tools = ("glob", "read_file", "write_draft")
    output_paths = (REPORT,)

    def __init__(self, seed: int, records: Sequence[Record], requested: Sequence[str]) -> None:
        self.seed = seed
        self.case_id = f"doc-{seed:07d}"
        self.records = tuple(sorted(records, key=lambda item: item.id))
        self.requested = tuple(sorted(requested))
        self.solver_steps = len(self.records) + 3
        self.scale = "large" if any(item.memo for item in self.records) else "small"
        self.task_text = (
            f"Read every record under inputs/ and write {REPORT} with, for each of the vendors "
            f"{', '.join(self.requested)}, the ids of its records and the total of their amounts."
        )

    def expected(self) -> dict[str, dict[str, object]]:
        return self._expected_from(self.records)

    def _expected_from(self, records: Sequence[Record]) -> dict[str, dict[str, object]]:
        report: dict[str, dict[str, object]] = {}
        for vendor in self.requested:
            mine = [item for item in records if item.vendor == vendor]
            report[vendor] = {
                "records": sorted(item.id for item in mine),
                "total": sum(i.amount for i in mine),
            }
        body: dict[str, object] = {"vendors": report}
        memos = {i.id: i.memo for i in records if i.vendor in self.requested and i.memo}
        if memos:
            body["memos"] = memos
        return body

    def reference_text(self) -> str:
        return json.dumps(self.expected(), sort_keys=True)

    def generic_ok(self, path: str, content: bytes) -> bool:
        if path != REPORT:
            return True
        try:
            return isinstance(json.loads(content.decode("utf-8")), dict)
        except (UnicodeDecodeError, ValueError):
            return False

    def visible_ok(self, workspace: Path, path: str, content: bytes) -> bool:
        if path != REPORT:
            return True
        try:
            return json.loads(content.decode("utf-8")) == self._expected_from(
                _workspace_records(workspace)
            )
        except (UnicodeDecodeError, ValueError):
            return False

    def sources(self) -> dict[str, frozenset[str]]:
        return {REPORT: frozenset(i.id for i in self.records if i.vendor in self.requested)}

    def is_defective(self, path: str, content: bytes) -> bool:
        if path != REPORT:
            return False
        try:
            return json.loads(content.decode("utf-8")) != self.expected()
        except (UnicodeDecodeError, ValueError):
            return True

    def is_defective_result(
        self, tool: str, arguments: dict[str, object], envelope: dict[str, object]
    ) -> bool:
        if tool != "read_file":
            return False
        if envelope.get("status") != "succeeded":
            return True
        stem = (
            normalize_path(str(arguments.get("path", ""))).rsplit("/", 1)[-1].removesuffix(".json")
        )
        original = next((item for item in self.records if item.id == stem), None)
        if original is None:
            return False
        lines = (envelope.get("output") or {}).get("lines", [])
        return "\n".join(str(line.get("text")) for line in lines) != original.text()

    def materialize(self, workspace: Path, fault: FaultSpec | None) -> None:
        inputs = workspace / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        for item in self.records:
            target = inputs / f"{item.id}.json"
            if fault and fault.operator == "env_unreadable" and fault.target == item.id:
                target.write_bytes(UNREADABLE)
            elif fault and fault.operator == "env_corrupt" and fault.target == item.id:
                target.write_text(
                    item.text(corrupted_amount(item.amount, fault.variant)), encoding="utf-8"
                )
            else:
                target.write_text(item.text(), encoding="utf-8")

    def policy(self, fault: FaultSpec | None):
        return with_form_fault(DocPolicy(self, fault), fault, REPORT)

    def faults(self, operators: frozenset[str]) -> list[FaultSpec]:
        rng = random.Random(f"doc-faults-{self.seed}")
        mine = [item for item in self.records if item.vendor in self.requested]
        target = mine[rng.randrange(len(mine))].id
        candidates = [
            FaultSpec("F1", "env_unreadable", target),
            FaultSpec("F2", "env_corrupt", target, "digit_swap"),
            FaultSpec("F2", "env_corrupt", target, "scale10"),
            FaultSpec("F3", "decision_wrong_total", self.requested[0], "off_by_one"),
            FaultSpec("F3", "decision_wrong_total", self.requested[0], "sign_flip"),
            FaultSpec("F8", "limit"),
            *self.form_faults(),
        ]
        return [fault for fault in candidates if fault.operator_id in operators]

    def decisive_step(self, fault: FaultSpec) -> StepRef | None:
        if fault.operator in {"env_unreadable", "env_corrupt"}:
            position = [item.id for item in self.records].index(str(fault.target))
            return (self.agent_label, 1 + position)
        if fault.operator == "decision_wrong_total":
            return (self.agent_label, 1 + len(self.records))
        return None

    def max_iterations(self, fault: FaultSpec | None) -> int:
        if fault is not None and fault.operator == "limit":
            return max(2, len(self.records) // 2)
        return self.solver_steps + 1


def _workspace_records(workspace: Path) -> list[Record]:
    found = []
    for target in sorted((workspace / "inputs").glob("*.json")):
        try:
            body = json.loads(target.read_text(encoding="utf-8"))
            found.append(
                Record(
                    str(body["id"]),
                    str(body["vendor"]),
                    int(body["amount"]),
                    str(body.get("memo", "")),
                )
            )
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
            continue
    return found


def corrupted_amount(amount: int, variant: str | None) -> int:
    if variant == "digit_swap":
        swapped = int(str(amount)[::-1])
        return swapped if swapped != amount else amount + 11
    if variant == "scale10":
        return amount * 10
    raise ValueError(f'unknown env_corrupt variant "{variant}"; known: digit_swap, scale10.')


def generate(seed: int) -> DocCase:
    rng = random.Random(f"doc-{seed}")
    large = seed % 3 == 0
    count = LARGE_RECORDS if large else 6 + 4 * (seed % 2)
    requested = rng.sample(VENDORS, 2)
    other = [name for name in VENDORS if name not in requested]
    guaranteed = 3 if large else 2
    vendors = [requested[0]] * guaranteed + [requested[1]] * guaranteed
    vendors += [
        rng.choice(VENDORS if rng.random() < 0.5 else other) for _ in range(count - 2 * guaranteed)
    ]
    rng.shuffle(vendors)
    records = []
    for index, vendor in enumerate(vendors, start=1):
        amount = rng.randrange(20, 980)
        while str(amount) == str(amount)[::-1]:
            amount = rng.randrange(20, 980)
        memo = ""
        if large:
            memo = " ".join(rng.choice(WORDS) for _ in range(MEMO_CHARS // 8))[:MEMO_CHARS]
        records.append(Record(f"rec-{index:03d}", vendor, amount, memo))
    return DocCase(seed, records, requested)


class DocPolicy:
    def __init__(self, case: DocCase, fault: FaultSpec | None) -> None:
        self._case = case
        self._fault = fault

    def decide(self, observed: Sequence[Observation]) -> Decision:
        if not observed:
            return Decision(calls=(ToolCallSpec("c1", "glob", {"pattern": "inputs/*.json"}),))
        paths = sorted(
            normalize_path(str(p)) for p in (observed[0].output or {}).get("matches", [])
        )
        reads = observed[1 : 1 + len(paths)]
        if len(reads) < len(paths):
            index = len(reads)
            return Decision(
                calls=(ToolCallSpec(f"c{index + 2}", "read_file", {"path": paths[index]}),)
            )
        if len(observed) == 1 + len(paths):
            call_id = f"c{len(paths) + 2}"
            arguments = {"path": REPORT, "content": self._report(reads)}
            return Decision(calls=(ToolCallSpec(call_id, "write_draft", arguments),))
        return Decision(final={"status": "complete"})

    def _report(self, reads: Sequence[Observation]) -> str:
        found: list[dict[str, object]] = []
        for observation in reads:
            if not observation.ok:
                continue
            lines = (observation.output or {}).get("lines", [])
            try:
                found.append(json.loads("\n".join(str(line["text"]) for line in lines)))
            except (ValueError, KeyError, TypeError):
                continue
        report: dict[str, dict[str, object]] = {}
        for vendor in self._case.requested:
            mine = [item for item in found if item.get("vendor") == vendor]
            total = sum(int(str(item["amount"])) for item in mine)
            report[vendor] = {"records": sorted(str(item["id"]) for item in mine), "total": total}
        fault = self._fault
        if fault is not None and fault.operator == "decision_wrong_total":
            current = int(str(report[str(fault.target)]["total"]))
            report[str(fault.target)]["total"] = (
                current + 1 if fault.variant == "off_by_one" else -current
            )
        body: dict[str, object] = {"vendors": report}
        memos = {
            str(item["id"]): str(item["memo"])
            for item in found
            if item.get("vendor") in self._case.requested and item.get("memo")
        }
        if memos:
            body["memos"] = memos
        return json.dumps(body, sort_keys=True)
