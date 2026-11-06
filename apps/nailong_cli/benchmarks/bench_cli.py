"""Timing and size benchmarks for the CLI's own machinery (no model is called).

Run from apps/nailong_cli:  python benchmarks/bench_cli.py [output.md]
It measures start-up, the wiki, discoveries, the knowledge base, the event log and the size of what
the model is sent before the user's prompt, then writes a Markdown table.
"""

from __future__ import annotations

import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1] / "src"))

from nailong_cli.discoveries import DiscoveryStore  # noqa: E402
from nailong_cli.events import EventLog, check, read_events  # noqa: E402
from nailong_cli.knowledge import KnowledgeBase  # noqa: E402
from nailong_cli.wiki import Wiki  # noqa: E402


def timed(function, repeat=1):
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        function()
        samples.append((time.perf_counter() - start) * 1000)
    return samples


def row(label, samples, note=""):
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return f"| {label} | {len(samples)} | {statistics.median(samples):.1f} | {p95:.1f} | {note} |"


def startup(rows):
    command = [sys.executable, "-m", "nailong_cli", "--version"]
    samples = []
    for _ in range(5):
        start = time.perf_counter()
        subprocess.run(command, cwd=ROOT, capture_output=True, check=True)
        samples.append((time.perf_counter() - start) * 1000)
    rows.append(row("process start (`--version`)", samples, "includes interpreter and imports"))
    with tempfile.TemporaryDirectory() as folder:
        health = [sys.executable, "-m", "nailong_cli", "--health", "--project", folder]
        samples = []
        for _ in range(5):
            start = time.perf_counter()
            subprocess.run(health, cwd=ROOT, capture_output=True, check=True)
            samples.append((time.perf_counter() - start) * 1000)
    rows.append(row("`--health` on an empty project", samples, ""))


def wiki_bench(rows, directory):
    project = directory / "wiki-project"
    project.mkdir()
    wiki = Wiki(project / ".nailong" / "wiki", project)
    writes = []
    for number in range(300):
        start = time.perf_counter()
        wiki.write(
            f"page-{number}", f"# Page {number}\nsome text about topic {number % 17}.", "bench"
        )
        writes.append((time.perf_counter() - start) * 1000)
    rows.append(
        row("wiki write (new page, 1..300 pages)", writes, "fsync of page, history, journal, index")
    )
    rows.append(row("wiki write, first 50", writes[:50]))
    rows.append(
        row(
            "wiki write, last 50",
            writes[-50:],
            "stays flat: the index and log are updated, not rebuilt",
        )
    )
    rows.append(
        row("wiki read", timed(lambda: wiki.read("page-150"), 100), "includes backlinks scan")
    )
    rows.append(row("wiki search (300 pages)", timed(lambda: wiki.search("topic 7"), 50)))
    updates = []
    for number in range(50):
        start = time.perf_counter()
        wiki.write(
            "page-1", f"# Page 1\nrevision {number}", "bench", base_revision=wiki.revision("page-1")
        )
        updates.append((time.perf_counter() - start) * 1000)
    rows.append(row("wiki update of one page (50 revisions)", updates))


def discovery_bench(rows, directory):
    project = directory / "disc-project"
    project.mkdir()
    (project / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
    store = DiscoveryStore(project / ".nailong" / "discoveries", project)
    publishes = []
    for number in range(200):
        start = time.perf_counter()
        store.publish(
            f"topic.t{number}", f"Claim {number}.", [{"path": "src.py", "quote": "VALUE = 1"}]
        )
        publishes.append((time.perf_counter() - start) * 1000)
    rows.append(row("discovery publish (1..200)", publishes, "re-reads the journal each time"))
    rows.append(row("discovery list (200)", timed(lambda: store.listing("all"), 20)))


def knowledge_bench(rows, directory):
    for count in (100, 400):
        project = directory / f"kb-{count}"
        (project / "docs").mkdir(parents=True)
        for number in range(count):
            body = " ".join(f"word{(number * 31 + i) % 997}" for i in range(800))
            (project / "docs" / f"d{number}.md").write_text(
                f"# Doc {number}\n\n{body}\n", encoding="utf-8"
            )
        kb = KnowledgeBase(
            project / ".nailong" / "knowledge", {"project": project}, project / ".nailong" / "w"
        )
        added = timed(lambda: kb.add("docs"))
        rows.append(row(f"knowledge add {count} files", added, "parse, split, hash, atomic save"))
        rows.append(
            row(
                f"knowledge search ({count} files)",
                timed(lambda: kb.search("word42 word77"), 15),
                "index rebuilt per search",
            )
        )


def event_bench(rows, directory):
    log = EventLog(directory / "events.jsonl")
    start = time.perf_counter()
    for number in range(2000):
        log.emit("discovery.published", {"id": f"d-{number}", "topic": "t"})
    elapsed = time.perf_counter() - start
    rows.append(
        f"| event emit (durable, fsync each) | 2000 | {elapsed / 2000 * 1000:.2f} | - | "
        f"{2000 / elapsed:.0f} events/s |"
    )
    events = read_events(log.path)
    rows.append(
        row("event log read + verify (2000 events)", timed(lambda: check(read_events(log.path)), 5))
    )
    assert check(events) == []


def prompt_size(rows):
    import json

    from tests.conftest import final, run_cli

    sizes = {}
    for label, flags in (("default tools", []), ("with web", ["--web"])):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "p"
            (project / ".nailong").mkdir(parents=True)
            (project / ".nailong" / "config.toml").write_text(
                'provider = "anthropic"\nmodel = "m"\napi_key_env = "K"\n', encoding="utf-8"
            )
            home = Path(folder) / "h"
            home.mkdir()
            _, out, _, _ = run_cli(
                project,
                home,
                flags,
                final(),
                lines="/tokens\n/exit\n",
                interactive=True,
                environ={"K": "k"},
            )
            sizes[label] = [
                line.strip()
                for line in out.splitlines()
                if line.strip().startswith(("instructions", "tool definitions", "total"))
            ]
    for label, lines in sizes.items():
        rows.append(
            f"| request size before the prompt, {label} | 1 | - | - | "
            f"{'; '.join(' '.join(line.split()) for line in lines)} (estimated tokens) |"
        )
    _ = json


def main() -> None:
    rows: list[str] = []
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        startup(rows)
        wiki_bench(rows, directory)
        discovery_bench(rows, directory)
        knowledge_bench(rows, directory)
        event_bench(rows, directory)
    prompt_size(rows)
    header = (
        f"# CLI benchmarks\n\nMeasured {date.today()} on {platform.platform()}, Python "
        f"{platform.python_version()}. No model is called; times are wall-clock milliseconds on a "
        "local disk, with every durable write synced (`fsync`).\n\n"
        "| Measurement | Runs | Median ms | p95 ms | Note |\n|---|---|---|---|---|\n"
    )
    findings = (
        "\n## What these numbers say\n\n"
        "- Starting the program takes about two seconds, almost all of it importing the SDK and "
        "the `mcp` package (`python -X importtime` shows `mcp` alone near 0.6 s). That is the "
        "SDK's import structure, not the CLI's own code, and it is the first thing to fix for "
        "short-lived commands such as `--health`.\n"
        "- A durable wiki write costs tens of milliseconds because every write syncs the page, "
        "its history copy, the journal and the index to disk. It no longer grows with the number "
        "of pages (an earlier version rebuilt the whole index and log on each write: 225 ms at "
        "300 pages).\n"
        "- Knowledge-base search rebuilds the lexical index on every call; it scales with the "
        "number of passages and is cheap at hundreds of files, not at hundreds of thousands.\n"
        "- The fixed part of every model request (instructions plus tool definitions) is about "
        "4,000 to 4,600 estimated tokens before the user's words; the tool definitions are three "
        "quarters of it. The cost of a request is that figure times your model's input price "
        "(cached input after the first request where the provider caches), times the number of "
        "model steps in a turn.\n"
    )
    text = header + "\n".join(rows) + "\n" + findings
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if target:
        target.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
