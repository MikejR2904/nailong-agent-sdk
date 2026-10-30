# Copyright (c) 2026 David Michael Indraputra

"""Lineage on synthetic chains with known ground truth (V2.4).

Every job builds one chain of runs in a scratch directory, with the artifact store and the
tool-result journal the SDK would have written, then asks ``attribute_lineage`` where the defect
came from and compares the answer with what the chain was built to contain.
"""

from __future__ import annotations

import random
import shutil
import tempfile
from pathlib import Path
from typing import Any

from nailong_agent_sdk.developer_tools.attribute_lineage import (
    FlowBasis,
    FlowRelationKind,
    InputKind,
    OriginKind,
    attribute_lineage,
)
from nailong_agent_sdk.foundations.contracts import ToolCall, ToolExecutionResult
from nailong_agent_sdk.foundations.hashing import canonical_json
from nailong_agent_sdk.memory.context_projection import FileToolResultJournal
from nailong_agent_sdk.tools.artifacts import ArtifactStore

DEFECT = "DEFECT"
KINDS = (
    "artifact-chain",
    "process-origin",
    "web-origin",
    "search-origin",
    "stored-result",
    "ambiguous",
    "own-decision",
    "unrecorded",
    "verbatim-copy",
    "near-copy",
)


def content_check(path: str, content: bytes) -> bool:
    return DEFECT.encode() in content


def result_check(tool: str, arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    return DEFECT in canonical_json(result)


def writer(run_id: str, call_id: str, iteration: int) -> dict[str, Any]:
    return {
        "agent_identity": f"agent-{run_id}",
        "agent_run_id": run_id,
        "tool": "write_draft",
        "tool_call_id": call_id,
        "iteration": iteration,
    }


def record(
    journal: FileToolResultJournal,
    run: str,
    call: str,
    name: str,
    arguments: dict[str, Any],
    output: dict[str, Any],
) -> str:
    return journal.record_for_run(
        run,
        ToolCall(id=call, name=name, arguments=arguments),
        ToolExecutionResult(status="succeeded", output=output),
    ).handle_id


def read_artifact(journal: FileToolResultJournal, run: str, call: str, version: Any) -> None:
    record(
        journal,
        run,
        call,
        "read_artifact",
        {"artifact_id": version.artifact_id},
        {"artifact_id": version.artifact_id, "content": "…"},
    )


def chain_downstream(
    store: ArtifactStore,
    journal: FileToolResultJournal,
    rng: random.Random,
    first_version: Any,
    depth: int,
    start: int,
) -> tuple[str, Any]:
    """Runs start..start+depth-1 each read the last version and write a derived one."""

    last = first_version
    run = ""
    for step in range(depth):
        run = f"r{start + step}"
        read_artifact(journal, run, f"{run}-read", last)
        if step < depth - 1:
            text = f"derived {rng.randrange(10**6)} from {DEFECT} input"
            record(
                journal,
                run,
                f"{run}-w",
                "write_draft",
                {"path": f"f{start + step}.txt", "content": text},
                {"ok": True},
            )
            last = store.write_text(
                f"f{start + step}.txt", text, manifest=writer(run, f"{run}-w", 2)
            )
    return run, last


def build(kind: str, rng: random.Random, root: Path) -> dict[str, Any]:
    store = ArtifactStore(root)
    journal = FileToolResultJournal(root)
    depth = rng.randint(2, 5)
    expect: dict[str, Any] = {"origin_kind": OriginKind.NONE, "ok": False, "infer": False}
    last_run = f"r{depth - 1}"
    if kind in {"artifact-chain", "own-decision"}:
        if kind == "artifact-chain":
            clean = store.write_text(
                "seed.txt", "clean input\n", manifest=writer("r0", "r0-seed", 1)
            )
            read_artifact(journal, "r0", "r0-read", clean)
        text = f"{DEFECT} chosen by the run"
        record(
            journal, "r0", "r0-w", "write_draft", {"path": "f0.txt", "content": text}, {"ok": True}
        )
        first = store.write_text("f0.txt", text, manifest=writer("r0", "r0-w", 3))
        last_run, _ = chain_downstream(store, journal, rng, first, depth, 1)
        expect.update(origin_kind=OriginKind.ARTIFACT_WRITE, run="r0", call="r0-w")
    elif kind in {"process-origin", "web-origin", "search-origin", "verbatim-copy", "near-copy"}:
        name, output, input_kind = {
            "process-origin": (
                "run_registered_command",
                {
                    "template_name": "gen",
                    "output": f"report {DEFECT} value",
                    "exit_kind": "succeeded",
                },
                InputKind.PROCESS_OUTPUT,
            ),
            "web-origin": (
                "web_fetch",
                {"url": "https://example.org/x", "text": f"{DEFECT} fact"},
                InputKind.WEB_FETCH,
            ),
            "search-origin": (
                "web_search",
                {"results": [{"snippet": f"{DEFECT} claim"}]},
                InputKind.WEB_SEARCH,
            ),
            "verbatim-copy": (
                "run_registered_command",
                {
                    "template_name": "gen",
                    "output": f"the exact line with {DEFECT} in it, long enough",
                    "exit_kind": "succeeded",
                },
                InputKind.PROCESS_OUTPUT,
            ),
            "near-copy": (
                "run_registered_command",
                {
                    "template_name": "gen",
                    "output": f"the exact line with {DEFECT} in it, long enough",
                    "exit_kind": "succeeded",
                },
                InputKind.PROCESS_OUTPUT,
            ),
        }[kind]
        arguments = (
            {"template_name": "gen"}
            if name == "run_registered_command"
            else ({"url": "https://example.org/x"} if name == "web_fetch" else {"query": "q"})
        )
        record(journal, "r0", "r0-in", name, arguments, output)
        written = f"summary mentioning {DEFECT}"
        if kind == "verbatim-copy":
            written = f"intro\nthe exact line with {DEFECT} in it, long enough\noutro"
        if kind == "near-copy":
            written = f"intro\nthe exact line with {DEFECT} in it, long enougH\noutro"
        record(
            journal,
            "r0",
            "r0-w",
            "write_draft",
            {"path": "f0.txt", "content": written},
            {"ok": True},
        )
        first = store.write_text("f0.txt", written, manifest=writer("r0", "r0-w", 2))
        last_run, _ = chain_downstream(store, journal, rng, first, depth, 1)
        expect.update(
            origin_kind=OriginKind.EXTERNAL_INPUT,
            input_kind=input_kind,
            call="r0-in",
            infer=kind in {"verbatim-copy", "near-copy"},
            verbatim=kind == "verbatim-copy",
        )
    elif kind == "stored-result":
        version = store.write_text(
            "data.txt", f"{DEFECT} total\n", manifest=writer("r0", "r0-w", 4)
        )
        handle = record(
            journal,
            "r1",
            "r1-read",
            "read_artifact",
            {"artifact_id": version.artifact_id},
            {"content": f"{DEFECT} total"},
        )
        record(
            journal,
            "r2",
            "r2-get",
            "get_tool_result",
            {"handle_id": handle},
            {"handle_id": handle, "content": "{}"},
        )
        last_run = "r2"
        expect.update(origin_kind=OriginKind.ARTIFACT_WRITE, run="r0", call="r0-w")
    elif kind == "ambiguous":
        store.write_text("a.txt", f"{DEFECT} twice\n", manifest=writer("r0", "r0-w", 1))
        again = store.write_text("b.txt", f"{DEFECT} twice\n", manifest=writer("r9", "r9-w", 1))
        read_artifact(journal, "r1", "r1-read", again)
        last_run = "r1"
        expect.update(origin_kind=OriginKind.NONE, ambiguous=True)
    else:
        text = f"{DEFECT} changed behind the store's back"
        (root / "f0.txt").write_text(text, "utf-8")
        record(
            journal,
            "r1",
            "r1-read",
            "read_file",
            {"path": "f0.txt"},
            {
                "path": "f0.txt",
                "offset": 0,
                "lines": [{"line": 1, "text": text}],
                "truncated": False,
            },
        )
        last_run = "r1"
        expect.update(ok=True, unrecorded=True)
    expect["last_run"] = last_run
    return expect


def lineage_job(job: tuple[str, int]) -> dict[str, Any]:
    kind, seed = job
    rng = random.Random(f"lineage:{kind}:{seed}")
    root = Path(tempfile.mkdtemp(prefix="lineage-"))
    try:
        expect = build(kind, rng, root)
        outcomes: dict[str, Any] = {"kind": kind, "seed": seed}
        for infer in (False, True):
            result = attribute_lineage(
                root,
                root,
                expect["last_run"],
                content_check,
                is_defective_result=result_check,
                infer_verbatim=infer,
                min_verbatim_chars=20,
            )
            label = "inferring" if infer else "plain"
            outcomes[label] = {
                "ok": result.ok,
                "origin_kind": result.origin_kind.value,
                "origin_run": result.origin.agent_run_id if result.origin else None,
                "origin_call": (
                    result.origin.tool_call_id
                    if result.origin
                    else (result.origin_input.tool_call_id if result.origin_input else None)
                ),
                "input_kind": result.origin_input.kind.value if result.origin_input else None,
                "causal": sum(1 for item in result.relations if item.causal),
                "inferred": sum(1 for item in result.relations if item.basis is FlowBasis.INFERRED),
                "containment": sum(
                    1
                    for item in result.relations
                    if item.kind is FlowRelationKind.VERBATIM_CONTAINMENT
                ),
                "summary_states_limit": "only the inputs the journal recorded" in result.summary,
            }
        truth = {
            key: (value.value if hasattr(value, "value") else value)
            for key, value in expect.items()
        }
        outcomes["truth"] = truth
        return outcomes
    except Exception as error:
        return {"kind": kind, "seed": seed, "error": f"{type(error).__name__}: {error}"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def lineage_key(job: tuple[str, int]) -> str:
    return f"lineage:{job[0]}:{job[1]}"


def enumerate_lineage_jobs(seeds: list[int]) -> list[tuple[str, int]]:
    return [(kind, seed) for kind in KINDS for seed in seeds]
