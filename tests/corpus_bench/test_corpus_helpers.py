import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from tests.support.paths import REPO_ROOT

BENCHMARK = str(REPO_ROOT / "benchmarks" / "trust")
if BENCHMARK not in sys.path:
    sys.path.insert(0, BENCHMARK)

from corpus import pipeline  # noqa: E402
from corpus.evaluate import eligible, wilson  # noqa: E402
from corpus.llm import json_object  # noqa: E402
from corpus.methods import Prediction, node_of  # noqa: E402

from nailong_agent_sdk.tools.artifacts import ArtifactStore  # noqa: E402

SEEDS = (1, 2, 3, 4, 5, 6)


def good_files(seed):
    expected = pipeline.truth(seed)
    return {
        "sales.csv": pipeline.sales_csv(pipeline.sales(seed)),
        "facts.json": json.dumps(expected["per_region"]),
        "analysis.md": f"TOTAL: {expected['total']}\nTOP: {expected['top']}\n",
        "summary.txt": f"TOTAL={expected['total']};TOP={expected['top']}\n",
    }


def test_the_top_region_is_unique_and_the_truth_adds_up():
    for seed in SEEDS:
        expected = pipeline.truth(seed)
        ordered = sorted(expected["per_region"].values(), reverse=True)
        assert ordered[0] > ordered[1]
        assert expected["total"] == sum(expected["per_region"].values())


def test_every_correct_file_passes_and_every_planted_corruption_is_a_defect():
    for seed in SEEDS:
        files = good_files(seed)
        for path in ("facts.json", "analysis.md", "summary.txt"):
            assert not pipeline.artifact_defective(path, files[path], seed)
            assert pipeline.artifact_defective(
                path, pipeline.corrupt(path, files[path], seed), seed
            )
        assert pipeline.corrupt("sales.csv", files["sales.csv"], seed) != files["sales.csv"]
        assert pipeline.region_totals(pipeline.sales(seed)) == pipeline.truth(seed)["per_region"]


def test_a_malformed_file_is_a_defect_rather_than_an_error():
    assert pipeline.artifact_defective("facts.json", "not json", 1)
    assert pipeline.artifact_defective("analysis.md", "nothing here", 1)
    assert pipeline.artifact_defective("summary.txt", "", 1)


def context(call_id, node):
    return SimpleNamespace(
        call=SimpleNamespace(id=call_id, arguments={}),
        agent_identity=f"node-{node}",
        task=SimpleNamespace(id=f"node-{node}"),
        iteration=1,
    )


def executor_for(tmp_path, kind, node, seed=1):
    store = ArtifactStore(tmp_path)
    case = pipeline.Case("t", "m", kind, node, seed, "dev")
    return store, pipeline.NodeExecutor(store, case, node)


def test_a_read_fault_returns_altered_content_and_leaves_the_store_alone(tmp_path):
    store, executor = executor_for(tmp_path, "read", "B")
    original = good_files(1)["facts.json"]
    record = store.write_text("facts.json", original, manifest={"agent_run_id": "node-A"})
    result = executor.read(record.artifact_id, context("c1", "B"))
    assert result.output["content"] != original
    assert store.read_bytes(store.history("facts.json")[-1].sha256).decode() == original
    assert executor.fault_call == {"tool": "read_artifact", "call_id": "c1"}


def test_a_read_at_another_node_or_of_another_file_is_not_altered(tmp_path):
    store, executor = executor_for(tmp_path, "read", "B")
    other = pipeline.NodeExecutor(store, executor.case, "A")
    record = store.write_text("facts.json", "{}", manifest={})
    assert other.read(record.artifact_id, context("c", "A")).output["content"] == "{}"
    assert other.fault_call is None


def test_a_write_fault_stores_altered_content_and_reports_success(tmp_path):
    store, executor = executor_for(tmp_path, "write", "A")
    intended = good_files(1)["facts.json"]
    result = executor.write("facts.json", intended, context("w1", "A"))
    stored = store.read_bytes(store.history("facts.json")[-1].sha256).decode()
    assert result.status == "succeeded" and stored != intended
    assert executor.fault_call == {"tool": "write_draft", "call_id": "w1"}


def test_an_unknown_artifact_id_is_a_failed_read(tmp_path):
    _, executor = executor_for(tmp_path, "none", "-")
    result = asyncio.run(
        executor.execute(
            pipeline.TOOLS[0],
            SimpleNamespace(
                call=SimpleNamespace(id="c", arguments={"artifact_id": "sha256:0"}),
                agent_identity="x",
                task=SimpleNamespace(id="x"),
                iteration=1,
            ),
        )
    )
    assert result.status == "failed" and "No artifact has the id" in result.error


def test_scoring_helpers():
    assert node_of("node-B") == "B" and node_of("other") is None and node_of(None) is None
    assert Prediction("A", "c").node == "A"
    assert wilson(0, 0) == (0.0, 1.0)
    low, high = wilson(34, 68)
    assert 0.38 < low < 0.5 < high < 0.62
    assert json_object('noise {"agent": "A", "step": 2} tail') == {"agent": "A", "step": 2}
    assert json_object("no object") is None and json_object("{broken") is None
    label = {"kind": "read", "pipeline_complete": True, "outcome_failed": True, "fault_call": {}}
    assert not eligible(label)
    label["fault_call"] = {"node": "A", "call_id": "c"}
    assert eligible(label) and not eligible({**label, "kind": "none"})
    assert not eligible({**label, "pipeline_complete": False})
    assert not eligible({**label, "outcome_failed": False})


def test_the_planted_cells_cover_both_kinds_at_every_node():
    assert set(pipeline.FAULT_TARGET_FILE) == {
        (kind, node) for kind in ("read", "write") for node in pipeline.NODES
    }


def test_the_corpus_has_the_planned_cases():
    from corpus.build import cases

    everything = cases()
    assert len(everything) == 126
    assert {c.split for c in everything} == {"dev", "test"}
    assert len({c.case_id for c in everything}) == 126
    with pytest.raises(StopIteration):
        next(c for c in everything if c.split == "dev" and c.seed in (3, 4, 5, 6))
