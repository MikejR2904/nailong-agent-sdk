from __future__ import annotations

import pytest

from nailong_cli.apply_patch import apply_planned, parse_patch, patch_paths, plan_patch
from nailong_cli.rules import PermissionRules, Rule
from tests.conftest import call, final, run_cli

BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]


def wrap(body):
    return "*** Begin Patch\n" + body + "\n*** End Patch"


def run(project, body, protected=()):
    planned = plan_patch(project, parse_patch(wrap(body)), protected)
    apply_planned(planned)
    return planned


def test_a_patch_adds_updates_and_deletes_files_in_one_step(tmp_path):
    (tmp_path / "keep.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    (tmp_path / "old.txt").write_text("gone\n", encoding="utf-8")
    run(
        tmp_path,
        "*** Add File: new/hello.txt\n+hello\n+world\n"
        "*** Update File: keep.py\n@@\n a = 1\n-b = 2\n+b = 20\n c = 3\n"
        "*** Delete File: old.txt",
    )
    assert (tmp_path / "new" / "hello.txt").read_text(encoding="utf-8") == "hello\nworld\n"
    assert (tmp_path / "keep.py").read_text(encoding="utf-8") == "a = 1\nb = 20\nc = 3\n"
    assert not (tmp_path / "old.txt").exists()


def test_several_hunks_apply_in_order_and_a_move_renames_the_file(tmp_path):
    (tmp_path / "m.py").write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
    run(
        tmp_path,
        "*** Update File: m.py\n*** Move to: pkg/n.py\n@@\n-one\n+ONE\n two\n"
        "@@ later\n four\n-five\n+FIVE",
    )
    assert not (tmp_path / "m.py").exists()
    assert (tmp_path / "pkg" / "n.py").read_text(
        encoding="utf-8"
    ) == "ONE\ntwo\nthree\nfour\nFIVE\n"


def test_windows_line_endings_and_a_missing_final_newline_are_kept(tmp_path):
    (tmp_path / "w.txt").write_bytes(b"a\r\nb\r\nc")
    run(tmp_path, "*** Update File: w.txt\n a\n-b\n+B\n c")
    assert (tmp_path / "w.txt").read_bytes() == b"a\r\nB\r\nc"


def test_trailing_whitespace_differences_still_match(tmp_path):
    (tmp_path / "t.txt").write_text("x = 1   \ny = 2\n", encoding="utf-8")
    run(tmp_path, "*** Update File: t.txt\n-x = 1\n+x = 9\n y = 2")
    assert (tmp_path / "t.txt").read_text(encoding="utf-8").startswith("x = 9")


def test_a_hunk_that_does_not_match_names_the_file_and_nothing_changes(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"Hunk 1 of b.txt does not match.*'wrong'"):
        run(
            tmp_path,
            "*** Update File: a.txt\n-alpha\n+ALPHA\n*** Update File: b.txt\n-wrong\n+x",
        )
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "alpha\n"


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("*** Add File: a.txt\nno plus", "must start with [+]"),
        ("*** Update File: a.txt", "has no changes"),
        ("junk line", "expected a [*]{3} file header"),
        ("*** Update File: a.txt\n?bad", "must start with a space, [+] or -"),
        ("*** Delete File: a.txt\n+x", "takes no further lines"),
    ],
)
def test_malformed_patches_say_which_line_is_wrong(tmp_path, body, fragment):
    with pytest.raises(ValueError, match=fragment):
        parse_patch(wrap(body))


def test_the_begin_and_end_lines_are_required():
    with pytest.raises(ValueError, match="must start with"):
        parse_patch("*** Add File: a\n+x\n*** End Patch")
    with pytest.raises(ValueError, match="must end with"):
        parse_patch("*** Begin Patch\n*** Add File: a\n+x")
    with pytest.raises(ValueError, match="no file operations"):
        parse_patch("*** Begin Patch\n*** End Patch")


def test_paths_must_stay_inside_the_project_and_away_from_protected_and_credential_files(tmp_path):
    for body, fragment in [
        ("*** Add File: ../escape.txt\n+x", "leaves the project"),
        ("*** Add File: /abs.txt\n+x", "must be relative|leaves the project"),
        ("*** Add File: .nailong/config.toml\n+x", "which the agent may not change"),
        ("*** Add File: .env\n+SECRET=1", "denied credential path"),
    ]:
        with pytest.raises(ValueError, match=fragment):
            run(tmp_path, body, protected=(".nailong",))


def test_adding_a_file_that_exists_or_changing_one_that_does_not_is_refused(tmp_path):
    (tmp_path / "here.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        run(tmp_path, "*** Add File: here.txt\n+y")
    with pytest.raises(ValueError, match="does not exist"):
        run(tmp_path, "*** Update File: nowhere.txt\n-a\n+b")
    with pytest.raises(ValueError, match="does not exist"):
        run(tmp_path, "*** Delete File: nowhere.txt")


def test_paths_are_listed_from_the_patch_text():
    text = wrap("*** Add File: a.txt\n+x\n*** Update File: b.py\n*** Move to: c.py\n-1\n+2")
    assert patch_paths(text) == ["a.txt", "b.py", "c.py"]


def test_a_deny_or_ask_rule_on_any_file_in_the_patch_decides_for_the_whole_patch():
    rules = PermissionRules(
        [Rule("deny", ("apply_patch",), "*.lock"), Rule("ask", ("apply_patch",), "secrets/*")]
    )
    assert rules.decide("apply_patch", "src/a.py\npoetry.lock").action == "deny"
    assert rules.decide("apply_patch", "src/a.py\nsecrets/x").action == "ask"
    assert rules.decide("apply_patch", "src/a.py") is None


def test_the_agent_can_patch_files_and_rewind_undoes_it(project, home):
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    patch = wrap("*** Update File: app.py\n-x = 1\n+x = 2\n*** Add File: extra.txt\n+hi")
    code, out, err, _ = run_cli(
        project,
        home,
        BYPASS,
        call("apply_patch", patch=patch),
        final("done"),
        lines="Patch it\n/rewind 1\ny\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    assert (project / "app.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (project / "extra.txt").exists()


def test_accept_edits_mode_lets_a_patch_through_but_default_mode_asks(project, home):
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    patch = wrap("*** Update File: app.py\n-x = 1\n+x = 3")
    run_cli(
        project,
        home,
        ["--permission-mode", "accept-edits", "-p", "go"],
        call("apply_patch", patch=patch),
        final("done"),
    )
    assert (project / "app.py").read_text(encoding="utf-8") == "x = 3\n"
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    _, out, err, _ = run_cli(
        project, home, ["-p", "go"], call("apply_patch", patch=patch), final("done")
    )
    assert "Refused apply_patch" in out + err
    assert (project / "app.py").read_text(encoding="utf-8") == "x = 1\n"
