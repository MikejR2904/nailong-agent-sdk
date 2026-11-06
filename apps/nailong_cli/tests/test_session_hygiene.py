from __future__ import annotations

import re

from tests.conftest import final, run_cli


def first_session(project, home) -> str:
    _, out, _, _ = run_cli(
        project, home, [], final("one"), lines="Hello\n/exit\n", interactive=True
    )
    return re.search(r"session (session-\w+)", out).group(1)


def sessions_listed(out: str) -> list[str]:
    return re.findall(r"^[> ]*[ *] (session-[0-9a-f]{8})", out, flags=re.MULTILINE)


def test_resuming_inside_the_cli_does_not_leave_the_empty_session_it_started_with(project, home):
    old = first_session(project, home)
    _, out, _, _ = run_cli(
        project, home, [], final("x"), lines=f"/resume {old}\n/sessions\n/exit\n", interactive=True
    )
    assert sessions_listed(out.split("resumed session")[1]) == [old]


def test_quitting_without_a_message_leaves_no_session_behind(project, home):
    old = first_session(project, home)
    run_cli(project, home, [], final("x"), lines="/exit\n", interactive=True)
    _, out, _, _ = run_cli(
        project, home, ["-r", old], final("x"), lines="/sessions\n/exit\n", interactive=True
    )
    assert sessions_listed(out) == [old]


def test_new_replaces_an_unused_session_instead_of_stacking_empty_ones(project, home):
    old = first_session(project, home)
    _, out, _, _ = run_cli(
        project,
        home,
        ["-r", old],
        final("x"),
        lines="/new\n/new\n/sessions\n/exit\n",
        interactive=True,
    )
    assert len(sessions_listed(out.split("started session")[-1])) == 2


def test_continue_picks_the_latest_session_that_has_turns_not_an_empty_one(project, home):
    old = first_session(project, home)
    from nailong_cli.session_hygiene import discard_if_unused  # noqa: F401

    _, out, _, _ = run_cli(
        project, home, ["-c"], final("x"), lines="/sessions\n/exit\n", interactive=True
    )
    assert f"* {old}" in out


def test_rename_keeps_the_turns_under_a_new_name_and_removes_the_old_one(project, home):
    old = first_session(project, home)
    _, out, _, _ = run_cli(
        project,
        home,
        ["-r", old],
        final("x"),
        lines="/rename thesis\n/sessions\n/exit\n",
        interactive=True,
    )
    assert "Renamed" in out and "(1 turn(s) kept)" in out
    listed = out.split("kept).")[1]
    assert "thesis" in listed and old not in listed
    _, again, _, _ = run_cli(
        project, home, ["-r", "thesis"], final("x"), lines="/sessions\n/exit\n", interactive=True
    )
    assert "* thesis" in again


def test_resume_with_no_id_lets_you_choose_from_the_sessions_that_have_turns(project, home):
    old = first_session(project, home)
    _, out, _, _ = run_cli(
        project, home, ["-r"], final("x"), lines="1\n/sessions\n/exit\n", interactive=True
    )
    assert f"1. {old}" in out and f"* {old}" in out


def test_resume_with_no_id_and_no_terminal_names_the_sessions(project, home):
    old = first_session(project, home)
    code, _, err, _ = run_cli(project, home, ["-r"], final("x"), lines="")
    assert code != 0 and old in err
