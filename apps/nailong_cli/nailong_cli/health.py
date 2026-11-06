"""Is this project's agent state sound? One report from the files, without starting a model.

It reads the event logs, the plan journals, the wiki, the discoveries, the knowledge base and the
claims, and says what is broken (a log whose chain does not verify), what needs
attention (unfinished plans, stale pages, a discovery that conflicts) and how much of what the
agents know is backed by evidence. `nailong --health` prints it and exits 1 when something is
broken, so a scheduler can run it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .claims import ArtifactClaims
from .discoveries import DiscoveryStore
from .events import EventError, check, read_events
from .knowledge import KnowledgeBase
from .memory_refs import MemoryIndex
from .taskstate import PlanJournal
from .wiki import Wiki

WARN_BYTES = {"events": 200_000_000, "workspace": 2_000_000_000}


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    attention: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return "broken" if self.problems else "attention" if self.attention else "ok"

    def as_json(self) -> str:
        return json.dumps(
            {
                "status": self.status,
                "problems": self.problems,
                "attention": self.attention,
                "metrics": self.metrics,
            },
            indent=2,
        )

    def lines(self) -> list[str]:
        out = [f"health: {self.status}"]
        out += [f"  BROKEN  {item}" for item in self.problems]
        out += [f"  attention  {item}" for item in self.attention]
        for group, values in self.metrics.items():
            shown = ", ".join(f"{key} {value}" for key, value in values.items())
            out.append(f"  {group}: {shown}")
        return out


def _size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _percent(part: int, whole: int) -> str:
    return "n/a" if whole == 0 else f"{100 * part // whole}%"


def coverage(index: MemoryIndex, wiki: Wiki, discoveries: DiscoveryStore) -> dict[str, Any]:
    """How much of what the project remembers is backed by evidence that still holds."""
    pages = wiki.names()
    cited = [name for name in pages if wiki.source_status(name)]
    stale = wiki.stale_pages()
    citations = [item for name in pages for item in wiki.source_status(name)]
    current = [item for item in citations if item["status"] == "current"]
    rows = discoveries.listing("all")
    accepted = [row for row in rows if row["status"] == "accepted"]
    quoted = 0
    total_evidence = 0
    for row in rows:
        for item in discoveries.get(row["id"])["evidence"]:
            total_evidence += 1
            quoted += bool(item["quote"])
    return {
        "wiki pages": len(pages),
        "pages with sources": _percent(len(cited), len(pages)),
        "pages stale": len(stale),
        "citations current": _percent(len(current), len(citations)),
        "discoveries": len(rows),
        "accepted": len(accepted),
        "accepted but stale": sum(row["stale"] for row in accepted),
        "evidence with a verified quote": _percent(quoted, total_evidence),
    }


def collect(project: Path) -> Report:
    report = Report()
    root = project / ".nailong"
    wiki = Wiki(root / "wiki", project)
    discoveries = DiscoveryStore(root / "discoveries", project)
    knowledge = KnowledgeBase(root / "knowledge", {"project": project}, root / "workspace")
    index = MemoryIndex(project, wiki, discoveries, knowledge)
    wiki.attach(index)

    logs = sorted((root / "events").glob("run-*.jsonl")) if (root / "events").is_dir() else []
    open_total = 0
    for path in logs:
        try:
            problems = check(read_events(path), running=path.with_suffix(".pid").is_file())
        except EventError as error:
            report.problems.append(f"{path.name}: {error}")
            continue
        report.problems += [
            f"{path.name}: {item}" for item in problems if "never ended" not in item
        ]
        open_total += sum("never ended" in item for item in problems)
    if open_total:
        report.attention.append(
            f"{open_total} event span(s) never ended in finished runs; /events check lists them "
            "and the next start closes them"
        )
    report.metrics["events"] = {"logs": len(logs), "bytes": _size(root / "events")}

    plans = PlanJournal(root / "tasks")
    unfinished = plans.unfinished()
    for plan in unfinished:
        report.attention.append(f"plan {plan.plan_id} is unfinished: /plan resume")
    report.metrics["plans"] = {"total": len(plans.ids()), "unfinished": len(unfinished)}

    stale = index.stale_report()
    for kind, lines in stale.items():
        report.attention += [f"stale {kind}: {line}" for line in lines]
    for row in discoveries.listing("open"):
        if row["conflicts_with"]:
            report.attention.append(f"discovery {row['id']} conflicts with {row['conflicts_with']}")
    try:
        report.metrics["memory"] = coverage(index, wiki, discoveries)
    except ValueError as error:
        report.problems.append(f"memory: {error}")

    held = ArtifactClaims(root / "claims", project).holders()
    report.metrics["claims"] = {"files held": len(held)}
    sizes = {name: _size(root / name) for name in ("events", "workspace")}
    for name, limit in WARN_BYTES.items():
        if sizes[name] > limit:
            report.attention.append(
                f"{name} state is {sizes[name] // 1_000_000} MB (limit {limit // 1_000_000})"
            )
    report.metrics["storage"] = {name: f"{value // 1000} KB" for name, value in sizes.items()}
    return report
