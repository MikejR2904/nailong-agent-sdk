import base64
import os

import pytest

from nailong_agent_sdk.agent.runtime import AgentRuntimeServices
from nailong_agent_sdk.agent.task_files import (
    MAX_LISTING_LIMIT,
    MAX_READ_BYTES,
    list_task_files,
    read_task_file,
)
from nailong_agent_sdk.agent.task_runner import AgentTaskRunner, TaskOptionError
from nailong_agent_sdk.foundations.identifiers import file_safe_name


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    (root / "out").mkdir(parents=True)
    (root / "out" / "design.md").write_bytes(b"# Design\n\nbody\n")
    (root / "out" / "notes.txt").write_text("n" * 10, "utf-8")
    (root / "a.txt").write_text("alpha", "utf-8")
    (root / "b.txt").write_text("beta", "utf-8")
    return root


def names(listing):
    return [entry.name for entry in listing.entries]


def test_a_listing_names_each_entry_with_its_kind_size_and_modification_time(workspace):
    listing = list_task_files(workspace)
    assert listing.path == "." and names(listing) == ["a.txt", "b.txt", "out"]
    by_name = {entry.name: entry for entry in listing.entries}
    assert (by_name["a.txt"].kind, by_name["a.txt"].size) == ("file", 5)
    assert (by_name["out"].kind, by_name["out"].size) == ("directory", None)
    assert by_name["a.txt"].path == "a.txt" and by_name["out"].path == "out"
    assert by_name["a.txt"].modified_at_utc.endswith("+00:00")
    assert (listing.total, listing.offset, listing.next_offset) == (3, 0, None)


def test_a_subdirectory_listing_uses_workspace_relative_paths(workspace):
    listing = list_task_files(workspace, "out")
    assert listing.path == "out" and names(listing) == ["design.md", "notes.txt"]
    assert [entry.path for entry in listing.entries] == ["out/design.md", "out/notes.txt"]


def test_a_large_directory_is_listed_one_bounded_page_at_a_time(workspace):
    for index in range(25):
        (workspace / "many" / f"f{index:02d}.txt").parent.mkdir(exist_ok=True)
        (workspace / "many" / f"f{index:02d}.txt").write_text("x", "utf-8")
    seen = []
    offset = 0
    while offset is not None:
        page = list_task_files(workspace, "many", offset=offset, limit=10)
        assert page.total == 25 and len(page.entries) <= 10
        seen.extend(names(page))
        offset = page.next_offset
    assert seen == [f"f{index:02d}.txt" for index in range(25)]
    beyond = list_task_files(workspace, "many", offset=25, limit=10)
    assert beyond.entries == [] and beyond.next_offset is None


def test_listing_limits_and_offsets_are_checked_by_name(workspace):
    for bad in (0, MAX_LISTING_LIMIT + 1):
        with pytest.raises(
            ValueError, match=rf"limit must be from 1 to {MAX_LISTING_LIMIT}, got {bad}"
        ):
            list_task_files(workspace, limit=bad)
    with pytest.raises(ValueError, match="offset must be at least 0, got -1"):
        list_task_files(workspace, offset=-1)


def test_a_file_cannot_be_listed_and_a_missing_path_is_named(workspace):
    with pytest.raises(ValueError, match='Task file path "a.txt" is a file, not a directory'):
        list_task_files(workspace, "a.txt")
    with pytest.raises(ValueError, match='Task file path "nowhere" does not exist'):
        list_task_files(workspace, "nowhere")


@pytest.mark.parametrize(
    "bad",
    [
        "../outside",
        "out/../../outside",
        "/etc/passwd",
        "\\windows",
        "C:\\Windows",
        "C:drive",
        "",
        "  ",
    ],
)
def test_paths_that_leave_the_workspace_or_are_not_relative_are_refused(workspace, bad):
    with pytest.raises(ValueError) as listed:
        list_task_files(workspace, bad)
    with pytest.raises(ValueError) as read:
        read_task_file(workspace, bad)
    for raised in (listed, read):
        assert f'Task file path "{bad}"' in str(raised.value)
        assert "workspace" in str(raised.value)


def test_credential_files_and_sdk_internal_state_are_neither_listed_nor_readable(workspace):
    (workspace / ".env").write_text("TOKEN=secret", "utf-8")
    (workspace / "key.pem").write_text("-----BEGIN", "utf-8")
    (workspace / ".ssh").mkdir()
    (workspace / ".ssh" / "config").write_text("Host x", "utf-8")
    (workspace / ".agent-artifacts").mkdir()
    (workspace / ".agent-artifacts" / "m.json").write_text("{}", "utf-8")
    assert names(list_task_files(workspace)) == ["a.txt", "b.txt", "out"]
    for denied in (".env", "key.pem", ".ssh/config"):
        with pytest.raises(ValueError, match="matches the built-in sensitive-path pattern"):
            read_task_file(workspace, denied)
    with pytest.raises(ValueError, match="sensitive-path pattern"):
        list_task_files(workspace, ".ssh")
    with pytest.raises(
        ValueError, match='Task file path ".agent-artifacts/m.json" is SDK-internal'
    ):
        read_task_file(workspace, ".agent-artifacts/m.json")
    with pytest.raises(ValueError, match='Task file path ".agent-artifacts" is SDK-internal'):
        list_task_files(workspace, ".agent-artifacts")


def test_a_symlink_that_points_outside_the_workspace_is_hidden_and_refused(workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", "utf-8")
    link = workspace / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("this platform or account cannot create symbolic links")
    assert "link.txt" not in names(list_task_files(workspace))
    with pytest.raises(ValueError, match='Task file path "link.txt" escapes the workspace'):
        read_task_file(workspace, "link.txt")


def test_a_file_is_read_in_pages_that_add_up_to_the_file(workspace):
    text = "".join(f"line {index}\n" for index in range(200))
    (workspace / "long.txt").write_bytes(text.encode("utf-8"))
    collected, offset, pages = "", 0, 0
    while offset is not None:
        chunk = read_task_file(workspace, "long.txt", offset=offset, max_bytes=300)
        assert (
            chunk.encoding == "utf-8"
            and chunk.size == len(text.encode())
            and chunk.offset == offset
        )
        assert chunk.bytes_returned == len(chunk.content.encode()) <= 300
        collected += chunk.content
        offset = chunk.next_offset
        pages += 1
    assert collected == text and pages > 4


def test_a_page_never_splits_a_multibyte_character(workspace):
    text = "héllo wörld — ünïcode ✓ " * 40
    (workspace / "utf8.txt").write_bytes(text.encode("utf-8"))
    collected, offset = "", 0
    while offset is not None:
        chunk = read_task_file(workspace, "utf8.txt", offset=offset, max_bytes=37)
        collected += chunk.content
        offset = chunk.next_offset
    assert collected == text


def test_binary_content_is_read_as_base64_and_refused_as_utf8(workspace):
    data = bytes(range(256)) * 3
    (workspace / "blob.bin").write_bytes(data)
    with pytest.raises(ValueError) as raised:
        read_task_file(workspace, "blob.bin")
    assert 'Task file path "blob.bin" is not valid UTF-8 at byte 128' in str(raised.value)
    assert 'request encoding="base64"' in str(raised.value)
    pieces, offset = b"", 0
    while offset is not None:
        chunk = read_task_file(
            workspace, "blob.bin", offset=offset, max_bytes=100, encoding="base64"
        )
        assert chunk.encoding == "base64"
        pieces += base64.b64decode(chunk.content)
        offset = chunk.next_offset
    assert pieces == data


def test_read_bounds_and_positions_are_checked_by_name(workspace):
    for bad in (0, MAX_READ_BYTES + 1):
        with pytest.raises(
            ValueError, match=rf"max_bytes must be from 1 to {MAX_READ_BYTES}, got {bad}"
        ):
            read_task_file(workspace, "a.txt", max_bytes=bad)
    with pytest.raises(ValueError, match="offset must be at least 0, got -3"):
        read_task_file(workspace, "a.txt", offset=-3)
    with pytest.raises(ValueError, match=r'offset 6 is beyond the end of "a.txt" \(5 bytes\)'):
        read_task_file(workspace, "a.txt", offset=6)
    at_end = read_task_file(workspace, "a.txt", offset=5)
    assert at_end.content == "" and at_end.next_offset is None and at_end.bytes_returned == 0
    with pytest.raises(ValueError, match='Task file path "out" is a directory'):
        read_task_file(workspace, "out")
    with pytest.raises(ValueError, match='Task file path "nowhere.txt" does not exist'):
        read_task_file(workspace, "nowhere.txt")
    with pytest.raises(ValueError, match='encoding must be "utf-8" or "base64", got "latin-1"'):
        read_task_file(workspace, "a.txt", encoding="latin-1")


def test_a_whole_small_file_comes_back_in_one_page(workspace):
    chunk = read_task_file(workspace, "out/design.md")
    assert chunk.content == "# Design\n\nbody\n" and chunk.next_offset is None
    assert (chunk.path, chunk.offset, chunk.size) == ("out/design.md", 0, 15)


def runner(tmp_path):
    return AgentTaskRunner(AgentRuntimeServices.open(tmp_path / "service"))


def test_a_task_workspace_is_found_from_its_id_and_never_created_by_looking(tmp_path):
    subject = runner(tmp_path)
    root = subject._services.run_root
    with pytest.raises(TaskOptionError) as missing:
        subject.workspace_for("t1")
    assert str(missing.value) == (
        'Agent task "t1" has no workspace directory "workspaces/t1": nothing has been written '
        "there yet."
    )
    assert not (root / "workspaces").exists()
    (root / "workspaces" / "t1").mkdir(parents=True)
    assert subject.workspace_for("t1") == (root / "workspaces" / "t1").resolve()
    unsafe = file_safe_name("job:1")
    (root / "workspaces" / unsafe).mkdir()
    assert subject.workspace_for("job:1") == (root / "workspaces" / unsafe).resolve()


def test_a_custom_workspace_is_judged_exactly_as_the_run_options_judge_it(tmp_path):
    subject = runner(tmp_path)
    root = subject._services.run_root
    with pytest.raises(TaskOptionError, match='has no workspace directory "jobs/alpha"'):
        subject.workspace_for("t1", "jobs/alpha")
    (root / "jobs" / "alpha").mkdir(parents=True)
    assert subject.workspace_for("t1", "jobs/alpha") == (root / "jobs" / "alpha").resolve()
    for bad in ("../escape", "/absolute", "C:\absolute", " ", ".", "jobs/..", ".agent-runs"):
        with pytest.raises(TaskOptionError, match="workspace"):
            subject.workspace_for("t1", bad)


def test_a_page_too_small_for_one_character_says_how_big_it_must_be(workspace):
    text = chr(0xE9) + chr(0x20AC) + chr(0x1D11E)
    (workspace / "wide.txt").write_bytes(text.encode("utf-8"))
    with pytest.raises(ValueError) as raised:
        read_task_file(workspace, "wide.txt", max_bytes=1)
    assert str(raised.value) == (
        "max_bytes 1 cannot hold the UTF-8 character at byte 0; use at least 4."
    )
    pages, offset = [], 0
    while offset is not None:
        chunk = read_task_file(workspace, "wide.txt", offset=offset, max_bytes=4)
        pages.append(chunk.content)
        offset = chunk.next_offset
    assert pages == [chr(0xE9), chr(0x20AC), chr(0x1D11E)]


def test_a_file_that_ends_inside_a_character_is_invalid_not_paged_forever(workspace):
    (workspace / "cut.txt").write_bytes(b"ok" + chr(0x20AC).encode("utf-8")[:2])
    with pytest.raises(ValueError) as raised:
        read_task_file(workspace, "cut.txt")
    assert 'Task file path "cut.txt" is not valid UTF-8 at byte 2' in str(raised.value)
    first = read_task_file(workspace, "cut.txt", max_bytes=3)
    assert first.content == "ok" and first.next_offset == 2
    with pytest.raises(ValueError, match="is not valid UTF-8 at byte 2"):
        read_task_file(workspace, "cut.txt", offset=2, max_bytes=3)
    raw = read_task_file(workspace, "cut.txt", encoding="base64")
    assert base64.b64decode(raw.content) == b"ok\xe2\x82"


def test_a_directory_with_more_entries_than_the_scan_limit_is_refused(workspace, monkeypatch):
    from nailong_agent_sdk.agent import task_files

    (workspace / "crowded").mkdir()
    for index in range(5):
        (workspace / "crowded" / f"f{index}.txt").write_bytes(b"x")
    monkeypatch.setattr(task_files, "MAX_DIRECTORY_ENTRIES", 3)
    with pytest.raises(ValueError) as raised:
        list_task_files(workspace, "crowded")
    assert str(raised.value) == (
        'Task file path "crowded" holds more than 3 entries; list a narrower directory.'
    )
    assert names(list_task_files(workspace, "out")) == ["design.md", "notes.txt"]
