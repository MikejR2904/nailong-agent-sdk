from __future__ import annotations

import re

from nailong_agent_sdk.starter import discover_skills

from nailong_cli.skills import BUILTIN_SKILLS, TOOL_NAMES
from tests.conftest import final, run_cli, tool_names

EXPECTED = {
    "analyze-spreadsheet",
    "code-review",
    "incident-notes",
    "kb-answer",
    "paper-summary",
    "release-notes",
    "server-health-check",
    "wiki-ingest",
}


def skills():
    return {item.name: item for item in discover_skills([("built-in", BUILTIN_SKILLS)])}


def test_the_library_ships_these_skills_and_every_one_parses():
    found = skills()
    assert set(found) == EXPECTED
    for item in found.values():
        assert item.description and len(item.body) > 200 and item.allowed_tools


def test_every_tool_a_skill_is_allowed_to_use_is_a_real_tool(project, home):
    _, _, _, setup = run_cli(
        project, home, ["--web"], final(), lines="Hi\n/exit\n", interactive=True
    )
    offered = set(tool_names(setup.model.calls[0]))
    for item in skills().values():
        for entry in item.allowed_tools:
            name = re.match(r"[A-Za-z_][\w]*", entry).group(0)
            name = TOOL_NAMES.get(name.lower(), name)
            assert name in offered, f"{item.name} allows {name}, which no session offers"


def test_the_skills_name_only_tools_that_exist_in_their_own_steps():
    known = {
        "fetch_document", "read_document", "search_document", "interpret_page", "read_file",
        "wiki_search",
        "wiki_read", "wiki_write", "notes_write", "notes_read", "knowledge_search",
        "run_command", "shell_open", "shell_run", "shell_close", "grep", "glob",
    }  # fmt: skip
    for item in skills().values():
        for name in re.findall(r"`([a-z_]+)`", item.body):
            if "_" in name and name not in known:
                assert name in {"ssh_hosts", "no_text_layer"}, f"{item.name} mentions {name}"


def test_the_server_check_is_manual_only_and_the_others_are_offered_to_the_model(project, home):
    found = skills()
    assert found["server-health-check"].manual_only
    assert not any(
        item.manual_only for name, item in found.items() if name != "server-health-check"
    )
    _, _, _, setup = run_cli(project, home, [], final(), lines="Hi\n/exit\n", interactive=True)
    text = str(setup.model.calls[0].prompt.model_dump()["sections"])
    assert "paper-summary" in text and "server-health-check" not in text


def test_a_built_in_can_be_run_by_name_and_a_project_skill_of_the_same_name_replaces_it(
    project, home
):
    _, out, _, setup = run_cli(
        project,
        home,
        [],
        final(),
        lines="/paper-summary paper.pdf\n/skills\n/exit\n",
        interactive=True,
    )
    task = setup.model.calls[0].task.instructions
    assert "Summarise the document at: paper.pdf" in task and "interpret_page" in task
    assert "built-in: " in out
    own = project / ".nailong" / "skills" / "code-review"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: Our own review\n---\nReview our way.",
        encoding="utf-8",
    )
    _, listing, _, _ = run_cli(project, home, [], lines="/skills\n/exit\n", interactive=True)
    assert "/code-review  Our own review" in listing
