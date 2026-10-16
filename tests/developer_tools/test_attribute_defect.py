import json
import time

import pytest

from nailong_agent_sdk.developer_tools.attribute_defect import (
    MAX_LATER_WRITERS,
    MAX_LISTED_PROBES,
    attribute_defect,
    content_check,
)
from nailong_agent_sdk.foundations.suffix_search import SuffixStrategy
from nailong_agent_sdk.tools.artifacts import ArtifactStore
from tests.support.artifact_logs import long_history
from tests.support.developer_tool_runs import cli
from tests.support.diagnosis_runs import fingerprints

BUG = "total = 41\nBUG\n"


def has_bug(content, version):
    return b"BUG" in content


def written(root, texts, *, writers=None):
    store = ArtifactStore(root)
    for index, text in enumerate(texts, start=1):
        writer = (writers or {}).get(index, f"agent-{index}")
        store.write_text(
            "report.txt",
            text,
            manifest={
                "agent_identity": writer,
                "agent_run_id": f"run-of-{writer}",
                "node_id": f"node:{writer}",
                "tool": "write_draft",
                "tool_call_id": f"c{index}",
                "iteration": index,
            },
        )
    return store


def clean(count):
    return [f"clean {index}\n" for index in range(count)]


def test_the_write_that_introduced_a_defect_and_its_agent_are_named(tmp_path):
    texts = clean(6) + [BUG] + [f"{BUG}more {index}\n" for index in range(5)]
    written(tmp_path, texts, writers={7: "bob"})
    result = attribute_defect(tmp_path, "report.txt", has_bug, check="contains BUG")
    assert not result.ok and result.versions == 12 and not result.born_defective
    assert result.introduced_by.sequence == 7 and result.introduced_by.agent_identity == "bob"
    assert result.introduced_by.node_id == "node:bob" and result.introduced_by.tool_call_id == "c7"
    assert result.introduced_by.agent_run_id == "run-of-bob" and result.introduced_by.iteration == 7
    assert result.last_clean.sequence == 6
    assert [item.sequence for item in result.later_writers] == [8, 9, 10, 11, 12]
    assert "since version 7 of 12" in result.summary and 'agent "bob"' in result.summary
    assert result.evaluations == len(result.probes) <= 9


def test_scanning_gives_the_same_answer_and_probes_every_defective_version(tmp_path):
    texts = clean(6) + [BUG] + [f"{BUG}more {index}\n" for index in range(5)]
    written(tmp_path, texts)
    gallop = attribute_defect(tmp_path, "report.txt", has_bug)
    scan = attribute_defect(tmp_path, "report.txt", has_bug, strategy="scan")
    assert scan.introduced_by.sequence == gallop.introduced_by.sequence == 7
    assert scan.evaluations == 7 and gallop.evaluations < scan.evaluations


def test_a_clean_latest_version_costs_one_evaluation_and_names_nobody(tmp_path):
    written(tmp_path, [BUG] + clean(4))
    result = attribute_defect(tmp_path, "report.txt", has_bug)
    assert result.ok and result.introduced_by is None and result.evaluations == 1
    assert result.last_clean.sequence == 5
    assert result.summary.startswith('The latest version (5) of "report.txt" passes the check')


def test_a_defect_present_from_the_first_write_is_that_writers(tmp_path):
    written(tmp_path, [BUG] + [f"{BUG}{index}\n" for index in range(6)], writers={1: "alice"})
    result = attribute_defect(tmp_path, "report.txt", has_bug)
    assert result.born_defective and result.last_clean is None
    assert result.introduced_by.sequence == 1 and result.introduced_by.agent_identity == "alice"
    assert "no clean version exists" in result.summary


def test_a_defect_that_was_fixed_and_broken_again_is_attributed_to_the_last_break(tmp_path):
    pattern = [False, False, True, True, False, False, False, True, True, True]
    written(tmp_path, [BUG if bad else f"fine {index}\n" for index, bad in enumerate(pattern)])
    for strategy in ("gallop", "scan"):
        result = attribute_defect(tmp_path, "report.txt", has_bug, strategy=strategy)
        assert result.introduced_by.sequence == 8 and result.last_clean.sequence == 7


def test_identical_contents_are_evaluated_once(tmp_path):
    written(tmp_path, ["same\n", BUG, "same\n", BUG, "same\n", BUG, BUG])
    calls = []

    def counting(content, version):
        calls.append(version.sequence)
        return has_bug(content, version)

    result = attribute_defect(tmp_path, "report.txt", counting, strategy="scan")
    assert result.evaluations == len(calls) == 2
    assert len(result.probes) > result.evaluations
    assert result.introduced_by.sequence == 6


def test_the_check_receives_the_bytes_and_the_version_it_is_judging(tmp_path):
    written(tmp_path, ["one\n", "two\n"])
    seen = []

    def check(content, version):
        seen.append((content, version.sequence, version.manifest["tool"]))
        return False

    attribute_defect(tmp_path, "report.txt", check)
    assert seen == [(b"two\n", 2, "write_draft")]


def test_later_writers_are_listed_up_to_a_bound_and_the_rest_counted(tmp_path):
    texts = clean(2) + [BUG] * (MAX_LATER_WRITERS + 8)
    written(tmp_path, [f"{text}{index}" for index, text in enumerate(texts)])
    result = attribute_defect(tmp_path, "report.txt", has_bug)
    assert len(result.later_writers) == MAX_LATER_WRITERS and result.later_writers_omitted == 7


def test_a_write_that_recorded_no_writer_says_so(tmp_path):
    store = ArtifactStore(tmp_path)
    store.write_text("report.txt", "fine\n")
    store.write_text("report.txt", BUG)
    result = attribute_defect(tmp_path, "report.txt", has_bug)
    assert result.introduced_by.agent_identity is None
    assert "the write recorded no writer" in result.summary


def test_a_path_with_no_recorded_write_names_the_paths_that_have_one(tmp_path):
    written(tmp_path, ["a\n"])
    with pytest.raises(
        ValueError, match=r'No write of "ghost.txt" is recorded under .*\[\'report.txt\'\]'
    ):
        attribute_defect(tmp_path, "ghost.txt", has_bug)


def test_a_missing_root_a_bad_strategy_and_a_lost_blob_are_named(tmp_path):
    with pytest.raises(ValueError, match="does not exist or is not a directory"):
        attribute_defect(tmp_path / "typo", "report.txt", has_bug)
    store = written(tmp_path, ["a\n", "b\n"])
    with pytest.raises(
        ValueError, match=r"strategy must be one of \['gallop', 'scan'\], got \"fast\""
    ):
        attribute_defect(tmp_path, "report.txt", has_bug, strategy="fast")
    newest = store.history("report.txt")[-1]
    (tmp_path / ".agent-artifacts" / "content" / newest.sha256).unlink()
    with pytest.raises(ValueError, match="is not stored under"):
        attribute_defect(tmp_path, "report.txt", has_bug)


def test_attributing_changes_no_file(tmp_path):
    written(tmp_path, clean(3) + [BUG, BUG])
    before = fingerprints(tmp_path)
    attribute_defect(tmp_path, "report.txt", has_bug)
    assert fingerprints(tmp_path) == before


def test_the_built_in_checks_are_exactly_one_of_four_and_describe_themselves(tmp_path):
    version = ArtifactStore(tmp_path).write_text("a.txt", "x")
    digest = version.sha256
    contains, description = content_check(contains="BUG")
    assert contains(b"a BUG here", None) and not contains(b"fine", None)
    assert description == 'content contains "BUG"'
    lacks, _ = content_check(lacks="OK")
    assert lacks(b"nothing", None) and not lacks(b"all OK", None)
    matches, description = content_check(matches=r"total = \d+")
    assert matches(b"total = 41", None) and not matches(b"total = x", None)
    assert description == r"content matches /total = \d+/"
    same, _ = content_check(sha256=digest)
    record = ArtifactStore(tmp_path, read_only=True).history("a.txt")[0]
    assert same(b"x", record) and not same(b"y", record.model_copy(update={"sha256": "0" * 64}))
    with pytest.raises(ValueError, match=r"exactly one of .* got \[\]"):
        content_check()
    with pytest.raises(ValueError, match=r"got \['contains', 'lacks'\]"):
        content_check(contains="a", lacks="b")
    with pytest.raises(ValueError, match='matches pattern "\\(" is not a valid regular expression'):
        content_check(matches="(")


def test_a_history_of_thousands_of_writes_is_searched_in_a_few_dozen_probes(tmp_path):
    clean_text, bug_text = "all good\n", "BUG\n"
    long_history(tmp_path, "big.txt", {1: clean_text, 2_001: bug_text}, count=3_000)
    started = time.perf_counter()
    result = attribute_defect(tmp_path, "big.txt", has_bug)
    elapsed = time.perf_counter() - started
    assert result.versions == 3_000 and result.introduced_by.sequence == 2_001
    assert len(result.probes) <= 2 * 11 + 3
    assert result.evaluations == 2
    assert elapsed < 2.0, f"{elapsed:.2f}s"
    scanned = attribute_defect(tmp_path, "big.txt", has_bug, strategy=SuffixStrategy.SCAN)
    assert scanned.introduced_by.sequence == 2_001 and scanned.evaluations == 2
    assert len(scanned.probes) == MAX_LISTED_PROBES


def test_the_command_prints_the_attribution_and_exits_by_the_verdict(tmp_path):
    written(tmp_path, clean(3) + [BUG, BUG], writers={4: "carol"})
    found = cli("attribute-defect", tmp_path, "report.txt", "--contains", "BUG")
    assert found.returncode == 1
    printed = json.loads(found.stdout)
    assert printed["introduced_by"]["agent_identity"] == "carol" and printed["ok"] is False
    assert printed["check"] == 'content contains "BUG"' and printed["strategy"] == "gallop"
    fine = cli("attribute-defect", tmp_path, "report.txt", "--lacks", "clean", "--strategy", "scan")
    assert fine.returncode == 1 and json.loads(fine.stdout)["strategy"] == "scan"
    passing = cli("attribute-defect", tmp_path, "report.txt", "--matches", "^never$")
    assert passing.returncode == 0 and json.loads(passing.stdout)["ok"] is True
    unknown = cli("attribute-defect", tmp_path, "ghost.txt", "--contains", "x")
    assert unknown.returncode == 3 and 'No write of "ghost.txt"' in unknown.stderr
    usage = cli("attribute-defect", tmp_path, "report.txt", "--contains", "a", "--lacks", "b")
    assert usage.returncode == 2
    invalid = cli("attribute-defect", tmp_path, "report.txt", "--matches", "(")
    assert invalid.returncode == 3 and "not a valid regular expression" in invalid.stderr
