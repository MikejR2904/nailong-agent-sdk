import json

import pytest
from dfl_bench.instances import generate_instance, replay
from dfl_bench.ledger import LedgerExecutor, write_outcomes
from dfl_bench.oracle import MEASURED


def test_ledger_serves_recorded_outcomes_and_labels_them_measured(tmp_path):
    instance = generate_instance("L", "aligned", 0, 1)
    ids = replay(instance).ids
    path = tmp_path / "ledger.jsonl"
    rows = [
        {"instance_id": instance.instance_id, "selection": ids[:3], "trial": 0, "outcome": 1.0},
        {"instance_id": instance.instance_id, "selection": ids[:3], "trial": 1, "outcome": 0.0},
    ]
    assert write_outcomes(path, rows) == 2
    executor = LedgerExecutor(path)
    assert executor.evidence_kind == MEASURED
    assert executor.run(instance, reversed(ids[:3]), 0) == 1.0
    assert executor.run(instance, ids[:3], 1) == 0.0


def test_ledger_never_fabricates_a_missing_outcome(tmp_path):
    instance = generate_instance("L", "aligned", 0, 1)
    path = tmp_path / "ledger.jsonl"
    path.write_text("")
    with pytest.raises(KeyError, match="never fabricates outcomes"):
        LedgerExecutor(path).run(instance, ["episode-1"], 0)
    with pytest.raises(FileNotFoundError, match="does not exist"):
        LedgerExecutor(tmp_path / "absent.jsonl")


def test_ledger_rejects_malformed_out_of_range_and_conflicting_rows(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text("not json\n")
    with pytest.raises(ValueError, match="line 1 is malformed"):
        LedgerExecutor(path)
    row = {"instance_id": "i", "selection": ["a"], "trial": 0, "outcome": 1.5}
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="outside"):
        LedgerExecutor(path)
    first = {**row, "outcome": 1.0}
    path.write_text(json.dumps(first) + "\n" + json.dumps({**first, "outcome": 0.0}) + "\n")
    with pytest.raises(ValueError, match="different outcome"):
        LedgerExecutor(path)
    with pytest.raises(ValueError, match="outside"):
        write_outcomes(tmp_path / "w.jsonl", [row])
