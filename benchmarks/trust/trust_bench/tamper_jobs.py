# Copyright (c) 2026 David Michael Indraputra

"""Run the tamper battery for one clean case on one arm; aggregate trials into detection rates."""

from __future__ import annotations

import tempfile
import warnings
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import stats
from .evaluate import FAMILIES

ARMS = ("sdk", "sdk+anchor", "sdk-default", "langgraph")


def tamper_job(job: tuple[str, int, str]) -> dict[str, Any]:
    family, seed, arm = job
    warnings.simplefilter("ignore")
    case = FAMILIES[family](seed)
    with tempfile.TemporaryDirectory(prefix="trustbench_tamper_") as directory:
        base = Path(directory)
        if arm in {"sdk", "sdk+anchor", "sdk-default"}:
            from .systems.nailong import run_sdk
            from .systems.nailong_tamper import tamper_sdk

            run = run_sdk(
                case,
                None,
                base / "run",
                anchored=arm == "sdk+anchor",
                default_anchor=arm == "sdk-default",
            )
            report = tamper_sdk(run, base / "work")
        elif arm == "langgraph":
            from .systems.langgraph_arm import run_langgraph
            from .systems.langgraph_tamper import tamper_langgraph

            run = run_langgraph(case, None, base / "run")
            report = tamper_langgraph(run, base / "work")
        else:
            raise ValueError(f'unknown tamper arm "{arm}"; known: {", ".join(ARMS)}.')
    return {
        "case_id": case.case_id,
        "family": family,
        "arm": arm,
        "trials": [[t.store, t.klass, t.position, t.detected] for t in report.trials],
        "false_alarms": report.false_alarms,
        "pristine_checks": report.pristine_checks,
    }


def tamper_table(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cells: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    alarms = checks = 0
    for result in results:
        alarms += len(result["false_alarms"])
        checks += result["pristine_checks"]
        for store, klass, _position, detected in result["trials"]:
            cell = cells[(store, klass)]
            cell[0] += int(detected)
            cell[1] += 1
    table = {
        f"{store}/{klass}": stats.proportion(hit, total)
        for (store, klass), (hit, total) in sorted(cells.items())
    }
    return {"cells": table, "false_alarms": stats.proportion(alarms, checks)}
