import json
import shutil

from nailong_agent_sdk.foundations.hashing import canonical_hash
from nailong_agent_sdk.tools import artifacts as artifact_module
from nailong_agent_sdk.tools.artifacts import ArtifactStore, ArtifactVersion, HistoryCheck
from tests.support.diagnosis_runs import fingerprints


def log_path(root):
    return root / ".agent-artifacts" / "history" / "writes.jsonl"


def log_lines(root):
    return log_path(root).read_text("utf-8").splitlines()


def put_lines(root, lines):
    log_path(root).write_text("".join(f"{line}\n" for line in lines), "utf-8")


def written(root, count=5):
    store = ArtifactStore(root)
    for index in range(count):
        store.write_text(
            "a.txt" if index % 2 == 0 else "b.txt",
            f"v{index}\n",
            manifest={"agent_identity": f"agent-{index}", "tool_call_id": f"c{index}"},
        )
    return store


def reseal(lines, start):
    sealed = list(lines)
    previous = json.loads(sealed[start - 1])["integrity_hash"] if start else None
    for index in range(start, len(sealed)):
        body = json.loads(sealed[index])
        body.pop("integrity_hash")
        body["previous_hash"] = previous
        previous = canonical_hash(body)
        sealed[index] = json.dumps({**body, "integrity_hash": previous}, separators=(",", ":"))
    return sealed


def test_every_write_is_chained_to_the_one_before_it_and_the_log_verifies(tmp_path):
    written(tmp_path)
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    records = [json.loads(line) for line in log_lines(tmp_path)]
    assert isinstance(check, HistoryCheck)
    assert check.ok and check.records == check.chained == 5 and check.broken_at is None
    assert check.head == records[-1]["integrity_hash"] and check.reason is None
    assert records[0]["previous_hash"] is None
    assert [r["previous_hash"] for r in records[1:]] == [r["integrity_hash"] for r in records[:-1]]


def test_an_empty_or_absent_log_verifies_with_nothing_to_chain(tmp_path):
    check = ArtifactStore(tmp_path / "none", read_only=True).verify_history()
    assert check.ok and check.records == check.chained == 0 and check.head is None


def test_editing_a_record_is_caught_at_that_line_with_the_records_before_it_counted(tmp_path):
    written(tmp_path)
    lines = log_lines(tmp_path)
    forged = json.loads(lines[2])
    forged["manifest"]["tool_call_id"] = "forged"
    lines[2] = json.dumps(forged, separators=(",", ":"))
    put_lines(tmp_path, lines)
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert not check.ok and check.broken_at == 3 and check.records == check.chained == 2
    assert check.reason == (
        'Line 3 of the artifact write log "writes.jsonl" has an integrity_hash that does not '
        "match its content."
    )
    assert check.head == json.loads(lines[1])["integrity_hash"]


def test_deleting_a_record_from_the_middle_leaves_a_gap_in_the_sequence(tmp_path):
    written(tmp_path)
    lines = log_lines(tmp_path)
    del lines[2]
    put_lines(tmp_path, lines)
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert not check.ok and check.broken_at == 3
    assert "has write_sequence 4 after 2" in check.reason


def test_swapping_two_records_is_caught_at_the_first_of_them(tmp_path):
    written(tmp_path)
    lines = log_lines(tmp_path)
    lines[1], lines[2] = lines[2], lines[1]
    put_lines(tmp_path, lines)
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert not check.ok and check.broken_at == 2
    assert "has write_sequence 3 after 1" in check.reason


def test_inserting_a_copy_of_a_record_repeats_a_sequence_number(tmp_path):
    written(tmp_path)
    lines = log_lines(tmp_path)
    lines.insert(2, lines[1])
    put_lines(tmp_path, lines)
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert not check.ok and check.broken_at == 3
    assert "has write_sequence 2 after 2" in check.reason


def test_a_forged_record_in_the_right_place_with_the_wrong_link_is_caught(tmp_path):
    written(tmp_path)
    lines = log_lines(tmp_path)
    forged = json.loads(lines[2])
    forged["previous_hash"] = "0" * 64
    forged.pop("integrity_hash")
    forged["integrity_hash"] = canonical_hash(forged)
    lines[2] = json.dumps(forged, separators=(",", ":"))
    put_lines(tmp_path, lines)
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert not check.ok and check.broken_at == 3
    assert (
        "has previous_hash '" in check.reason
        and "not the hash of the record before it" in check.reason
    )


def test_a_log_cut_short_still_verifies_so_the_head_must_be_kept_outside(tmp_path):
    written(tmp_path)
    head = ArtifactStore(tmp_path, read_only=True).verify_history().head
    put_lines(tmp_path, log_lines(tmp_path)[:3])
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert check.ok and check.records == 3 and check.head != head


def test_a_tail_rewritten_and_resealed_verifies_but_its_head_changes(tmp_path):
    written(tmp_path)
    head = ArtifactStore(tmp_path, read_only=True).verify_history().head
    lines = log_lines(tmp_path)
    forged = json.loads(lines[2])
    forged["manifest"]["tool_call_id"] = "forged"
    forged.pop("integrity_hash")
    lines[2] = json.dumps({**forged, "integrity_hash": "x"}, separators=(",", ":"))
    put_lines(tmp_path, reseal(lines, 2))
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert check.ok and check.records == 5 and check.head != head


def test_a_log_from_before_the_chain_verifies_and_new_writes_chain_from_nothing(tmp_path):
    written(tmp_path, 3)
    bare = []
    for line in log_lines(tmp_path):
        record = json.loads(line)
        record.pop("previous_hash")
        record.pop("integrity_hash")
        bare.append(json.dumps(record, separators=(",", ":")))
    put_lines(tmp_path, bare)
    old = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert old.ok and old.records == 3 and old.chained == 0 and old.head is None
    ArtifactStore(tmp_path).write_text("a.txt", "new\n")
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert check.ok and check.records == 4 and check.chained == 1
    assert json.loads(log_lines(tmp_path)[3])["previous_hash"] is None


def test_an_unchained_record_after_chained_ones_is_caught(tmp_path):
    written(tmp_path, 3)
    lines = log_lines(tmp_path)
    bare = json.loads(lines[2])
    bare.pop("previous_hash")
    bare.pop("integrity_hash")
    lines[2] = json.dumps(bare, separators=(",", ":"))
    put_lines(tmp_path, lines)
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert not check.ok and check.broken_at == 3
    assert "has no integrity_hash although the records before it are chained" in check.reason


def test_a_line_that_is_not_a_record_is_named(tmp_path):
    written(tmp_path, 2)
    lines = log_lines(tmp_path)
    put_lines(tmp_path, [*lines, "not json"])
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert (
        not check.ok
        and check.broken_at == 3
        and "is not a write record: JSONDecodeError" in check.reason
    )
    put_lines(tmp_path, [*lines, "[1]"])
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert check.reason.endswith("is not a write record: a JSON object was expected.")


def test_a_write_cut_short_at_the_end_of_the_log_is_not_counted(tmp_path):
    written(tmp_path, 2)
    with log_path(tmp_path).open("ab") as stream:
        stream.write(b'{"write_sequence":3,"path_key":"a.txt","occ')
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert check.ok and check.records == 2


def test_another_process_continues_the_chain_from_the_last_record_on_disk(tmp_path):
    written(tmp_path, 2)
    artifact_module._TAILS.clear()
    ArtifactStore(tmp_path).write_text("a.txt", "from another process\n")
    check = ArtifactStore(tmp_path, read_only=True).verify_history()
    assert check.ok and check.records == check.chained == 3


def test_a_store_written_before_the_log_existed_is_adopted_as_a_chain(tmp_path):
    root = tmp_path / "run"
    store = ArtifactStore(root)
    for index in range(3):
        store.write_text("old.txt", f"v{index}\n", manifest={"n": index})
    shutil.rmtree(root / ".agent-artifacts" / "history")
    ArtifactStore(root).write_text("old.txt", "v3\n", manifest={"n": 3})
    check = ArtifactStore(root, read_only=True).verify_history()
    assert check.ok and check.records == check.chained == 4


def test_the_history_a_caller_reads_carries_no_chain_fields(tmp_path):
    written(tmp_path, 3)
    version = ArtifactStore(tmp_path, read_only=True).history("a.txt")[0]
    assert isinstance(version, ArtifactVersion)
    assert (
        "integrity_hash" not in version.model_dump() and "previous_hash" not in version.model_dump()
    )


def test_verifying_a_history_changes_no_file(tmp_path):
    written(tmp_path)
    before = fingerprints(tmp_path)
    ArtifactStore(tmp_path, read_only=True).verify_history()
    assert fingerprints(tmp_path) == before
