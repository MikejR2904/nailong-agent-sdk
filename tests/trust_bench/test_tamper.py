import pytest
from trust_bench.evaluate import FAMILIES
from trust_bench.gates import gate_link_only_chain, gate_sufficiency_arithmetic, run_gates
from trust_bench.systems.langgraph_arm import run_langgraph
from trust_bench.systems.langgraph_tamper import tamper_langgraph
from trust_bench.systems.nailong import run_sdk
from trust_bench.systems.nailong_tamper import tamper_sdk
from trust_bench.tamper import CLASSES, LinkOnlyChain, positions, tamper_lines


def lines(count=6):
    return [f"line-{i}" for i in range(count)]


def forge(line):
    return line + "-forged"


def test_positions_cover_the_ends_and_stay_inside_the_record():
    grid = positions(1000)
    assert len(grid) == 10 and grid[0] == 0 and grid[-1] == 999
    assert positions(3) == [0, 1, 2] and positions(1) == [0] and positions(0) == []


def test_positions_refuse_a_grid_too_small_to_span_the_record():
    with pytest.raises(ValueError, match="at least 2 wanted positions, got 1"):
        positions(10, 1)


def test_each_tamper_class_changes_the_record_the_way_it_is_named():
    base = lines()
    assert tamper_lines(base, "edit", 2, forge)[2] == "line-2-forged"
    assert tamper_lines(base, "delete", 2, forge) == [*base[:2], *base[3:]]
    swapped = tamper_lines(base, "reorder", 2, forge)
    assert swapped[2:4] == ["line-3", "line-2"]
    inserted = tamper_lines(base, "insert", 2, forge)
    assert inserted[3] == "line-2-forged" and len(inserted) == len(base) + 1
    assert tamper_lines(base, "truncate", 3, forge) == base[:3]
    assert base == lines()


def test_reordering_the_last_entry_swaps_it_with_its_predecessor():
    assert tamper_lines(lines(3), "reorder", 2, forge)[1:] == ["line-2", "line-1"]


def test_a_class_that_cannot_apply_at_a_position_is_skipped_not_faked():
    assert tamper_lines(lines(), "truncate", 0, forge) is None
    assert tamper_lines(lines(), "reseal", 2, forge, None) is None
    assert tamper_lines(["only"], "reorder", 0, forge) is None


def test_an_unknown_tamper_class_names_the_known_ones():
    with pytest.raises(ValueError, match='unknown tamper class "bend"; known: edit, delete'):
        tamper_lines(lines(), "bend", 0, forge)


def test_the_link_only_chain_is_blind_to_exactly_what_the_published_chain_is():
    chain = LinkOnlyChain(100)
    assert LinkOnlyChain.verify(chain.lines)
    grid = positions(100)
    edits = [
        not LinkOnlyChain.verify(
            tamper_lines(chain.lines, "edit", p, LinkOnlyChain.mutate, LinkOnlyChain.reseal)
        )
        for p in grid
    ]
    assert edits.count(True) == len(grid) - 1
    reseals = [
        LinkOnlyChain.verify(
            tamper_lines(chain.lines, "reseal", p, LinkOnlyChain.mutate, LinkOnlyChain.reseal)
        )
        for p in grid
    ]
    assert all(reseals)


def test_the_reproduction_gates_pass_and_name_their_published_source():
    gates = run_gates()
    assert [g.passed for g in gates] == [True, True]
    assert "arXiv 2609.01931" in gates[0].source and "arXiv 2606.20634" in gates[1].source
    assert gate_link_only_chain().observed["fork"] == 0.0
    assert gate_sufficiency_arithmetic().observed["trace-present:overclaim"] == 0.75


def detections(report):
    cells = {}
    for trial in report.trials:
        cell = cells.setdefault((trial.store, trial.klass), [0, 0])
        cell[0] += int(trial.detected)
        cell[1] += 1
    return {key: hit / total for key, (hit, total) in cells.items()}


@pytest.fixture(scope="module")
def sdk_reports(tmp_path_factory):
    out = {}
    for anchored in (False, True):
        base = tmp_path_factory.mktemp("anchored" if anchored else "plain")
        run = run_sdk(FAMILIES["data"](1), None, base / "run", anchored=anchored)
        out[anchored] = tamper_sdk(run, base / "work", wanted=3)
    return out


def test_the_untampered_sdk_record_raises_no_alarm(sdk_reports):
    for report in sdk_reports.values():
        assert report.pristine_checks == 1 and report.false_alarms == []


def test_the_sdk_chains_detect_every_edit_delete_reorder_insert_and_truncation(sdk_reports):
    rates = detections(sdk_reports[False])
    for store in ("audit", "telemetry"):
        for klass in ("edit", "reorder", "insert"):
            assert rates[(store, klass)] == 1.0, (store, klass)
    assert rates[("audit", "delete")] == 1.0 and rates[("audit", "truncate")] == 1.0


def test_a_resealed_tail_is_caught_only_with_an_outside_anchor(sdk_reports):
    plain, anchored = detections(sdk_reports[False]), detections(sdk_reports[True])
    assert plain[("audit", "reseal")] < 1.0 or plain[("telemetry", "reseal")] < 1.0
    for key, value in anchored.items():
        if key[0] in {"audit", "telemetry"}:
            assert value == 1.0, key


def test_edited_or_deleted_artifact_content_is_detected_by_its_content_hash(sdk_reports):
    rates = detections(sdk_reports[False])
    assert rates[("artifact-content", "edit")] == 1.0
    assert rates[("artifact-content", "delete")] == 1.0


def test_the_artifact_write_history_catches_edits_but_not_a_cut_tail_or_a_resealed_one(
    sdk_reports,
):
    rates = detections(sdk_reports[True])
    for klass in ("edit", "reorder", "insert"):
        assert rates[("artifacts", klass)] == 1.0, klass
    assert 0.0 < rates[("artifacts", "delete")] < 1.0
    assert rates[("artifacts", "truncate")] == 0.0 and rates[("artifacts", "reseal")] == 0.0


def test_langgraph_has_no_check_beyond_the_record_still_loading(tmp_path):
    run = run_langgraph(FAMILIES["data"](1), None, tmp_path / "run")
    report = tamper_langgraph(run, tmp_path / "work", wanted=3)
    assert report.false_alarms == [] and report.trials
    assert all(not trial.detected for trial in report.trials)
    assert {trial.klass for trial in report.trials} <= set(CLASSES)
