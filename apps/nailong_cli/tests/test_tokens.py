from __future__ import annotations

from tests.conftest import final, run_cli


def test_tokens_lists_the_parts_of_the_next_request_and_the_biggest_tools(project, home):
    code, out, _, _ = run_cli(project, home, [], lines="/tokens\n/exit\n", interactive=True)
    assert code == 0
    assert "instructions" in out and "tool definitions (" in out and "session history" in out
    assert "Largest tool definitions:" in out
    assert "No provider-counted usage yet" in out


def test_autocompact_is_on_unless_the_settings_turn_it_off(project, home):
    _, out, _, _ = run_cli(
        project, home, [], final(), lines="Hi\n/tokens\n/exit\n", interactive=True
    )
    assert "autocompact is off" not in out
