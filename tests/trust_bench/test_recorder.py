import json

from trust_bench.recorder import BoundaryRecorder, WorkspaceWatcher, load_recorded, verify_recorded
from trust_bench.schema import Event, Kind, canonical, digest_bytes, digest_of


def sample_events():
    return [
        Event(Kind.MODEL_CALL, agent_id="a", model_index=0, digest="d0", content="{}"),
        Event(Kind.TOOL_CALL, agent_id="a", model_index=0, call_id="c1", name="glob"),
        Event(Kind.TOOL_RESULT, agent_id="a", call_id="c1", status="succeeded"),
        Event(Kind.WRITE, agent_id="a", call_id="c2", path="out.json", digest="w0"),
    ]


def test_each_event_kind_is_keyed_by_what_identifies_it():
    model, call, result, write = sample_events()
    assert model.key == ("M", "a", 0)
    assert call.key == ("C", "c1") and result.key == ("R", "c1")
    assert write.key == ("W", "out.json", "w0")


def test_a_result_and_its_call_share_an_id_but_not_a_key():
    _, call, result, _ = sample_events()
    assert call.call_id == result.call_id and call.key != result.key


def test_events_survive_a_dictionary_round_trip():
    for event in sample_events():
        assert Event.from_dict(event.as_dict()) == event


def test_canonical_form_ignores_key_order_and_digests_agree():
    assert canonical({"b": 1, "a": [2, 3]}) == canonical({"a": [2, 3], "b": 1})
    assert digest_of({"b": 1, "a": 2}) == digest_of({"a": 2, "b": 1})
    assert digest_of({"a": 1}) != digest_of({"a": 2})
    assert digest_bytes(b"x") == digest_bytes(b"x") != digest_bytes(b"y")


def test_the_recorder_writes_a_chain_that_verifies_and_loads_back(tmp_path):
    path = tmp_path / "truth.jsonl"
    recorder = BoundaryRecorder(path)
    for event in sample_events():
        recorder.record(event)
    recorder.close()
    assert verify_recorded(path)
    assert load_recorded(path) == sample_events()
    assert recorder.events == sample_events()


def test_editing_a_recorded_line_breaks_the_chain(tmp_path):
    path = tmp_path / "truth.jsonl"
    recorder = BoundaryRecorder(path)
    for event in sample_events():
        recorder.record(event)
    recorder.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    body = json.loads(lines[1])
    body["name"] = "forged"
    lines[1] = json.dumps(body, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert not verify_recorded(path)


def test_deleting_or_reordering_recorded_lines_breaks_the_chain(tmp_path):
    path = tmp_path / "truth.jsonl"
    recorder = BoundaryRecorder(path)
    for event in sample_events():
        recorder.record(event)
    recorder.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2], lines[3]]) + "\n", encoding="utf-8")
    assert not verify_recorded(path)
    path.write_text("\n".join([lines[1], lines[0], lines[2], lines[3]]) + "\n", encoding="utf-8")
    assert not verify_recorded(path)


def test_a_chain_cut_short_still_verifies_which_is_why_the_head_is_kept_elsewhere(tmp_path):
    path = tmp_path / "truth.jsonl"
    recorder = BoundaryRecorder(path)
    for event in sample_events():
        recorder.record(event)
    recorder.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    assert verify_recorded(path) and len(load_recorded(path)) == 2


def test_the_watcher_reports_new_and_changed_files_once(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "a.txt").write_text("a", encoding="utf-8")
    watcher = WorkspaceWatcher(tmp_path)
    assert watcher.changes() == []
    (tmp_path / "out.json").write_text("1", encoding="utf-8")
    assert watcher.changes() == [("out.json", digest_bytes(b"1"))]
    assert watcher.changes() == []
    (tmp_path / "out.json").write_text("2", encoding="utf-8")
    assert watcher.changes() == [("out.json", digest_bytes(b"2"))]


def test_the_watcher_ignores_hidden_directories_the_system_keeps_its_records_in(tmp_path):
    watcher = WorkspaceWatcher(tmp_path)
    (tmp_path / ".agent-artifacts").mkdir()
    (tmp_path / ".agent-artifacts" / "log.jsonl").write_text("x", encoding="utf-8")
    assert watcher.changes() == []


def test_the_watcher_tolerates_a_workspace_that_does_not_exist_yet(tmp_path):
    watcher = WorkspaceWatcher(tmp_path / "later")
    assert watcher.changes() == []
    (tmp_path / "later").mkdir()
    (tmp_path / "later" / "f.txt").write_text("f", encoding="utf-8")
    assert [path for path, _ in watcher.changes()] == ["f.txt"]
