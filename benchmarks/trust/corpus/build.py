"""Build the corpus: run every case, keep its ledgers and write one label row per case.

python -m corpus.build --out results/corpus --workers 6
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from corpus.pipeline import NODES, Case, run_case

MODELS = (
    "accounts/fireworks/models/glm-5p3-flash",
    "accounts/fireworks/models/deepseek-v4p1-flash",
    "accounts/fireworks/models/minimax-m3p1-flash",
)
DEV_SEEDS = (1, 2)
TEST_SEEDS = (3, 4, 5, 6)
KINDS = ("read", "write")


def short(model: str) -> str:
    return model.rsplit("/", 1)[-1]


def cases() -> list[Case]:
    found: list[Case] = []
    for model in MODELS:
        for split, seeds in (("dev", DEV_SEEDS), ("test", TEST_SEEDS)):
            for seed in seeds:
                for kind in KINDS:
                    for node in NODES:
                        name = f"{short(model)}-{kind}-{node}-s{seed}"
                        found.append(Case(name, model, kind, node, seed, split))
                found.append(Case(f"{short(model)}-none-s{seed}", model, "none", "-", seed, split))
    return found


def api_key() -> str:
    from trust_bench.v2.live import api_key as key

    return key()


def one(case: Case, out: Path) -> dict[str, Any]:
    directory = out / case.case_id
    marker = directory / "label.json"
    if marker.is_file():
        return json.loads(marker.read_text("utf-8"))
    started = time.time()
    try:
        row = run_case(case, api_key(), directory)
    except Exception as error:
        row = {"case_id": case.case_id, "error": f"{type(error).__name__}: {str(error)[:300]}"}
    row["seconds"] = round(time.time() - started, 1)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(row, indent=1, sort_keys=True), encoding="utf-8")
    return row


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("results/corpus"))
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--split", default="")
    arguments = parser.parse_args(argv)
    chosen = [c for c in cases() if not arguments.split or c.split == arguments.split]
    if arguments.limit:
        chosen = chosen[: arguments.limit]
    arguments.out.mkdir(parents=True, exist_ok=True)
    done = 0
    with ProcessPoolExecutor(max_workers=arguments.workers) as pool:
        futures = {pool.submit(one, case, arguments.out): case for case in chosen}
        for future in as_completed(futures):
            row = future.result()
            done += 1
            state = row.get("error") or (
                f"complete={row['pipeline_complete']} failed={row['outcome_failed']}"
            )
            print(f"[{done}/{len(chosen)}] {row['case_id']}: {state}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
