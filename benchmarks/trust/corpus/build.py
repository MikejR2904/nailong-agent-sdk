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
NATURAL_DEV_SEEDS = (*range(1001, 1007), *range(2001, 2007))
NATURAL_TEST_SEEDS = (*range(1101, 1113), *range(2101, 2113))
DERIVED_DEV_SEEDS = tuple(range(3001, 3007))
DERIVED_TEST_SEEDS = tuple(range(3101, 3113))
PLANTED_DERIVED_DEV_SEEDS = (3201, 3202)
PLANTED_DERIVED_TEST_SEEDS = (3301, 3302, 3303, 3304)
DEV_SEEDS = (1, 2)
TEST_SEEDS = (3, 4, 5, 6)
KINDS = ("read", "write")


def short(model: str) -> str:
    return model.rsplit("/", 1)[-1]


def cases(dev_seeds=DEV_SEEDS, test_seeds=TEST_SEEDS) -> list[Case]:
    found: list[Case] = []
    for model in MODELS:
        for split, seeds in (("dev", dev_seeds), ("test", test_seeds)):
            for seed in seeds:
                for kind in KINDS:
                    for node in NODES:
                        name = f"{short(model)}-{kind}-{node}-s{seed}"
                        found.append(Case(name, model, kind, node, seed, split))
                found.append(Case(f"{short(model)}-none-s{seed}", model, "none", "-", seed, split))
    return found


def natural_cases(dev_seeds=NATURAL_DEV_SEEDS, test_seeds=NATURAL_TEST_SEEDS) -> list[Case]:
    found: list[Case] = []
    for model in MODELS:
        for split, seeds in (("dev", dev_seeds), ("test", test_seeds)):
            for seed in seeds:
                found.append(
                    Case(f"{short(model)}-natural-s{seed}", model, "natural", "-", seed, split)
                )
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
    parser.add_argument("--out", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--split", default="")
    parser.add_argument(
        "--set",
        dest="which",
        default="planted",
        choices=("planted", "natural", "derived", "planted-derived"),
    )
    arguments = parser.parse_args(argv)
    if arguments.which == "derived":
        pool = natural_cases(DERIVED_DEV_SEEDS, DERIVED_TEST_SEEDS)
    elif arguments.which == "planted-derived":
        pool = cases(PLANTED_DERIVED_DEV_SEEDS, PLANTED_DERIVED_TEST_SEEDS)
    elif arguments.which == "natural":
        pool = natural_cases()
    else:
        pool = cases()
    out = arguments.out or Path(
        {
            "planted": "results/corpus",
            "natural": "results/corpus_natural",
            "planted-derived": "results/corpus_planted_derived",
        }.get(arguments.which, "results/corpus_derived")
    )
    chosen = [c for c in pool if not arguments.split or c.split == arguments.split]
    if arguments.limit:
        chosen = chosen[: arguments.limit]
    out.mkdir(parents=True, exist_ok=True)
    done = 0
    with ProcessPoolExecutor(max_workers=arguments.workers) as pool:
        futures = {pool.submit(one, case, out): case for case in chosen}
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
