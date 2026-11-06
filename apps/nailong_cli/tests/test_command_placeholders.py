from __future__ import annotations

from pathlib import Path

from nailong_cli.user_commands import UserCommand


def render(body: str, arguments: str) -> str:
    return UserCommand("t", body, Path("t.md")).render(arguments)


def test_text_you_pass_is_not_searched_for_placeholders_again():
    assert render("Do $1 then $ARGUMENTS", '"$2" b') == 'Do $2 then "$2" b'


def test_missing_positions_are_empty_and_arguments_keeps_everything():
    assert render("[$1][$2] all=$ARGUMENTS", "one") == "[one][] all=one"
