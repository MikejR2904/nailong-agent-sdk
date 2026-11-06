import pytest

from nailong_agent_sdk.starter import (
    discover_skills,
    load_skill_definition,
    render_skill,
    skill_context_of,
    skill_file_text,
)

HEADER = "---\nname: deploy\ndescription: Deploy the app to staging\n---\n"


def make(root, name, text, extra=None):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    for relative, content in (extra or {}).items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return folder / "SKILL.md"


def test_a_skill_folder_gives_its_header_fields_body_and_bundled_files(tmp_path):
    path = make(
        tmp_path,
        "deploy",
        "---\nname: deploy\ndescription: Deploy the app to staging\n"
        "allowed-tools: Bash(git status:*) Read\ndisable-model-invocation: true\n"
        'argument-hint: "[env]"\n---\nRun the script.\n',
        {"scripts/go.sh": "echo go", "notes.md": "n"},
    )
    skill = load_skill_definition(path, "project")
    assert (skill.name, skill.description) == ("deploy", "Deploy the app to staging")
    assert skill.manual_only and skill.argument_hint == "[env]" and skill.source == "project"
    assert skill.allowed_tools == ("Bash(git status:*)", "Read")
    assert skill.files == ("notes.md", "scripts/go.sh") and skill.body.startswith("Run the script")


def test_a_list_in_the_header_may_use_brackets_or_dashes(tmp_path):
    inline = make(
        tmp_path, "a", "---\nname: a\ndescription: d\nallowed-tools: [read_file, grep]\n---\nx"
    )
    dashed = make(
        tmp_path,
        "b",
        "---\nname: b\ndescription: d\nallowed-tools:\n  - read_file\n  - glob\n---\nx",
    )
    assert load_skill_definition(inline, "p").allowed_tools == ("read_file", "grep")
    assert load_skill_definition(dashed, "p").allowed_tools == ("read_file", "glob")


def test_a_single_markdown_file_is_still_a_skill_with_its_first_line_as_description(tmp_path):
    flat = tmp_path / "notes-style.md"
    flat.write_text("Summarise a pull request\n\nSteps follow.", encoding="utf-8")
    skill = load_skill_definition(flat, "user")
    assert skill.name == "notes-style" and skill.description == "Summarise a pull request"
    assert skill.directory is None and skill.files == ()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("---\nname: deploy\n---\nbody", 'needs a "description:" line'),
        ("---\nname: other\ndescription: d\n---\nbody", 'name "other" must match its folder'),
        ("---\nname: Deploy\ndescription: d\n---\nbody", "must match its folder"),
        ("---\nname: deploy\ndescription: d\nbody", "never closes it"),
        ("---\nnot a pair\n---\nbody", 'must look like "key: value"'),
        ("   \n", "is empty"),
    ],
)
def test_a_bad_skill_names_the_file_and_the_problem(tmp_path, text, message):
    path = make(tmp_path, "deploy", text)
    with pytest.raises(ValueError, match=message) as caught:
        load_skill_definition(path, "project")
    assert str(path) in str(caught.value)


def test_a_folder_name_that_is_not_a_valid_skill_name_is_refused(tmp_path):
    path = make(tmp_path, "Bad_Name", "---\ndescription: d\n---\nbody")
    with pytest.raises(ValueError, match="lowercase letters"):
        load_skill_definition(path, "project")


def test_the_codex_sidecar_switches_off_implicit_use(tmp_path):
    path = make(
        tmp_path,
        "deploy",
        HEADER + "body",
        {"agents/openai.yaml": "policy:\n  allow_implicit_invocation: false\n"},
    )
    assert load_skill_definition(path, "project").manual_only is True


def test_later_roots_override_earlier_ones_and_both_layouts_are_found(tmp_path):
    user, project = tmp_path / "user", tmp_path / "project"
    make(user, "deploy", HEADER + "user version")
    user.joinpath("flat.md").write_text("A flat skill", encoding="utf-8")
    make(project, "deploy", HEADER + "project version")
    found = {item.name: item for item in discover_skills([("user", user), ("project", project)])}
    assert sorted(found) == ["deploy", "flat"]
    assert found["deploy"].source == "project" and "project version" in found["deploy"].body
    assert discover_skills([("none", tmp_path / "missing")]) == []


def test_rendering_fills_arguments_and_lists_the_bundled_files(tmp_path):
    path = make(
        tmp_path,
        "deploy",
        HEADER + "Deploy $1 with flags: $ARGUMENTS",
        {"scripts/go.sh": "echo"},
    )
    skill = load_skill_definition(path, "project")
    text = render_skill(skill, 'staging "--dry run"')
    assert 'Deploy staging with flags: staging "--dry run"' in text
    assert f"Skill directory: {skill.directory}" in text and "- scripts/go.sh" in text
    plain = load_skill_definition(
        make(tmp_path, "noargs", HEADER.replace("deploy", "noargs") + "Do it."), "p"
    )
    assert render_skill(plain, "now").endswith(
        "ARGUMENTS: now"
    ) or "ARGUMENTS: now" in render_skill(plain, "now")


def test_a_bundled_file_is_read_by_relative_path_and_cannot_escape_the_folder(tmp_path):
    path = make(tmp_path, "deploy", HEADER + "body", {"scripts/go.sh": "echo go"})
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    skill = load_skill_definition(path, "project")
    assert skill_file_text(skill, "scripts/go.sh") == "echo go"
    with pytest.raises(ValueError, match="has no bundled file"):
        skill_file_text(skill, "../secret.txt")
    flat = tmp_path / "flat.md"
    flat.write_text("One file", encoding="utf-8")
    with pytest.raises(ValueError, match="single file"):
        skill_file_text(load_skill_definition(flat, "p"), "x")


def test_the_run_context_carries_the_rendered_text_and_a_version_that_follows_it(tmp_path):
    skill = load_skill_definition(make(tmp_path, "deploy", HEADER + "Use $ARGUMENTS."), "project")
    first, second = skill_context_of(skill, "a"), skill_context_of(skill, "b")
    assert first.id == "deploy" and "Use a." in first.content and first.version != second.version
