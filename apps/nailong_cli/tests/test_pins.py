from __future__ import annotations

import pytest

from nailong_cli.pins import (
    KEPT_HEADING,
    MAX_PIN_CHARS,
    Pins,
    kept_from,
    pin_id,
    standing_rules,
    with_kept,
)
from tests.conftest import call, final, run_cli
from tests.test_workflow import queued


@pytest.fixture
def pins(tmp_path):
    return Pins(tmp_path / "proj", tmp_path / "home")


def test_a_pin_is_one_bullet_with_a_stable_id_in_a_plain_file(pins):
    pin = pins.add("Never push to main without a review.")
    assert pin.scope == "project" and pin.id == pin_id("never push to main  without a review.")
    text = pins.path("project").read_text(encoding="utf-8")
    assert text.endswith("- Never push to main without a review.\n") and text.startswith(
        "# Pinned rules"
    )
    assert [p.text for p in pins.listing()] == ["Never push to main without a review."]


def test_a_file_edited_by_hand_is_read_and_both_scopes_are_listed_user_first(pins):
    pins.path("user").parent.mkdir(parents=True)
    pins.path("user").write_text(
        "- Answer in British English.\n* Keep replies short.\n", encoding="utf-8"
    )
    pins.add("Run tests before saying it works.")
    got = [(p.scope, p.text) for p in pins.listing()]
    assert got == [
        ("user", "Answer in British English."),
        ("user", "Keep replies short."),
        ("project", "Run tests before saying it works."),
    ]
    assert pins.text().splitlines()[1:] == [f"- {text}" for _, text in got]


def test_limits_duplicates_and_unknown_ids_are_refused_by_name(pins):
    pins.add("Rule one is here.")
    with pytest.raises(ValueError, match="already pinned"):
        pins.add("  rule ONE is   here. ")
    with pytest.raises(ValueError, match=f"limit is {MAX_PIN_CHARS}"):
        pins.add("x" * (MAX_PIN_CHARS + 1))
    with pytest.raises(ValueError, match="cannot be empty"):
        pins.add("   ")
    with pytest.raises(ValueError, match='scope must be "project" or "user"'):
        pins.add("ok rule", "team")
    with pytest.raises(ValueError, match='There is no pin "p-000000"'):
        pins.remove("p-000000")
    for number in range(39):
        pins.add(f"Rule number {number} applies.")
    with pytest.raises(ValueError, match="already 40 project pins"):
        pins.add("one more")


def test_the_total_size_is_capped_so_pins_can_never_be_cut(pins):
    for number in range(10):
        pins.add(f"{number} " + "w" * 495)
    with pytest.raises(ValueError, match="pass 5000 characters in all"):
        pins.add("y" * 100)


def test_removing_a_pin_removes_only_that_line(pins):
    first = pins.add("Keep this one.")
    second = pins.add("Drop this one.")
    pins.remove(second.id)
    assert [p.id for p in pins.listing()] == [first.id]
    assert "Drop this one" not in pins.path("project").read_text(encoding="utf-8")


def test_standing_rules_are_found_in_the_users_words_and_questions_and_pins_are_skipped():
    pinned = Pins.__new__(Pins)
    found = standing_rules(
        [
            "Please fix the bug. Always use tabs in Makefiles. Thanks!",
            "Should we never deploy on Fridays? Our policy is no deploys on Fridays.",
            "From now on, answer in French.\nrandom text",
        ],
        [],
    )
    assert found == [
        "Always use tabs in Makefiles.",
        "Our policy is no deploys on Fridays.",
        "From now on, answer in French.",
    ]
    from nailong_cli.pins import Pin

    skip = [Pin("p-1", "project", "always use tabs in makefiles.")]
    assert "Always use tabs in Makefiles." not in standing_rules(
        ["Always use tabs in Makefiles."], skip
    )
    assert pinned is not None


def test_the_kept_section_round_trips_and_never_grows_without_bound():
    text = with_kept("A summary.", ["Always A.", "Never B."])
    assert text.count(KEPT_HEADING) == 1 and kept_from(text) == ["Always A.", "Never B."]
    again = with_kept(text, [*kept_from(text), "Always A.", "Always C."])
    assert kept_from(again) == ["Always A.", "Never B.", "Always C."]
    assert with_kept("Only summary.", []) == "Only summary."
    many = with_kept("s", [f"Always rule {n}." for n in range(50)])
    assert len(kept_from(many)) == 20 and kept_from(many)[-1] == "Always rule 49."


def prompt_text(call_):
    return call_.prompt.model_dump()["sections"]


def instructions(call_):
    for section in call_.prompt.model_dump()["sections"]:
        if section["kind"] == "instructions":
            return section["value"]["text"]
    return ""


def test_remember_pins_a_rule_at_the_top_of_the_very_next_request_and_forget_removes_it(
    project, home
):
    _, out, err, setup = run_cli(
        project,
        home,
        [],
        final("a"),
        final("b"),
        lines=(
            "/remember Never delete files without asking.\nHello\n/memory pins\n"
            "/forget p-xxxxxx\nthrow away\n/exit\n"
        ),
        interactive=True,
    )
    assert "pinned p-" in out and "top of every request" in out
    first = instructions(setup.model.calls[0])
    assert "Pinned rules." in first and "- Never delete files without asking." in first
    assert first.index("Pinned rules.") < first.index("Never delete") < len(first)
    assert first.startswith(first.split("\n")[0]) and "Pinned rules." in first[:3000]
    assert 'There is no pin "p-xxxxxx"' in out + err


def test_a_user_pin_applies_in_every_project_and_forget_unpins(project, home):
    run_cli(
        project,
        home,
        [],
        lines="/remember --user Reply in British English.\n/exit\n",
        interactive=True,
    )
    assert "Reply in British English." in (home / ".config" / "nailong" / "pinned.md").read_text(
        encoding="utf-8"
    )
    pin_line = (home / ".config" / "nailong" / "pinned.md").read_text(encoding="utf-8")
    ident = pin_id("Reply in British English.")
    assert pin_line
    _, out, _, setup = run_cli(
        project, home, [], final("ok"), lines=f"Hi\n/forget {ident}\n/exit\n", interactive=True
    )
    assert "Reply in British English." in instructions(setup.model.calls[0])
    assert f"unpinned {ident}" in out


def test_the_overview_says_what_is_always_loaded_and_what_is_only_looked_up(project, home):
    (project / "AGENTS.md").write_text("Use pytest.\n", encoding="utf-8")
    _, out, err, _ = run_cli(
        project,
        home,
        [],
        final("hi"),
        lines=(
            "Hello\n/remember Always run the linter.\n/memory\n/memory show\n/memory bad\n/exit\n"
        ),
        interactive=True,
    )
    assert "Always in the prompt (put first, never summarised): 1 pinned rule(s)" in out
    assert "Always run the linter." in out and "project:" in out and "AGENTS.md" in out
    assert "Conversation (recent turns only; older ones only if summarised):" in out
    assert "Looked up on demand (in the prompt only when the agent searches):" in out
    assert "wiki: 0 page(s), 0 stale" in out and "knowledge base: 0 source(s)" in out
    assert "Pinned rules." in out and "Notes from AGENTS.md" in out
    assert "Usage: /memory" in out + err


def test_compacting_keeps_standing_rules_word_for_word_and_says_how_to_pin_them(project, home):
    build = queued(final("ok one"), final("Summary: the user is fixing a bug."), final("ok two"))
    _, out, _, setup = run_cli(
        project,
        home,
        [],
        build=build,
        lines=(
            "Fix the parser. Always use tabs in Makefiles. Should we never ship Fridays?\n"
            "/compact\nNext question\n/exit\n"
        ),
        interactive=True,
    )
    assert "1 sentence(s) that read as standing rules were kept word for word" in out
    assert "Always use tabs in Makefiles." in out and "/remember pins one for good" in out
    history = str(setup.model.calls[2].task.input)
    assert "Summary: the user is fixing a bug." in history
    assert KEPT_HEADING in history and "Always use tabs in Makefiles." in history
    assert "never ship Fridays" not in history.split(KEPT_HEADING)[1]


def test_a_second_compaction_still_has_the_rule_even_if_the_model_forgets_it(project, home):
    build = queued(
        final("a"),
        final("First summary."),
        final("b"),
        final("Second summary, no rule mentioned."),
        final("c"),
    )
    _, _, _, setup = run_cli(
        project,
        home,
        [],
        build=build,
        lines="Always use tabs in Makefiles.\n/compact\nMore work\n/compact\nAnd more\n/exit\n",
        interactive=True,
    )
    history = str(setup.model.calls[4].task.input)
    assert "Second summary, no rule mentioned." in history
    assert "Always use tabs in Makefiles." in history and KEPT_HEADING in history


def test_a_rule_already_pinned_is_not_kept_twice(project, home):
    build = queued(final("a"), final("Summary."))
    _, out, _, _ = run_cli(
        project,
        home,
        [],
        build=build,
        lines=(
            "/remember Always use tabs in Makefiles.\nAlways use tabs in Makefiles.\n"
            "/compact\n/exit\n"
        ),
        interactive=True,
    )
    assert "standing rules" not in out


def test_the_agent_can_pin_only_with_approval_and_not_in_planning_mode(project, home):
    _, out, _, setup = run_cli(
        project,
        home,
        [],
        call("memory_pin", rule="Use UTC for all timestamps."),
        final("done"),
        lines="Remember that we use UTC\ny\n/memory pins\n/exit\n",
        interactive=True,
    )
    assert "Approve memory_pin?" in out and "Use UTC for all timestamps." in out
    _, denied, _, _ = run_cli(
        project,
        home,
        [],
        call("memory_pin", rule="Delete everything on Fridays."),
        final("done"),
        lines="Remember it\nn\n/exit\n",
        interactive=True,
    )
    assert "was declined" in denied
    pinned = project / ".nailong" / "pinned.md"
    assert "Delete everything on Fridays." not in pinned.read_text(encoding="utf-8")
    _, _, _, planning = run_cli(
        project,
        home,
        ["--permission-mode", "plan"],
        final(),
        lines="Plan\n/exit\n",
        interactive=True,
    )
    from tests.conftest import tool_names

    assert "memory_pin" not in tool_names(planning.model.calls[0])
