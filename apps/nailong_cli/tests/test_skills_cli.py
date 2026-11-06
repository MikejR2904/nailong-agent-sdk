from __future__ import annotations

from tests.conftest import call, final, instructions_text, run_cli


def make_skill(root, name, header, body="Do it.", files=None):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\n{header}---\n{body}", encoding="utf-8")
    for relative, text in (files or {}).items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return folder


def skills_dir(project):
    path = project / ".nailong" / "skills"
    path.mkdir(exist_ok=True)
    return path


def test_a_skill_runs_by_name_with_arguments_and_shows_its_folder(project, home):
    make_skill(
        skills_dir(project),
        "deploy",
        "description: Deploy to an environment\nargument-hint: [env]\n",
        "Deploy to $1.",
        {"scripts/go.sh": "echo go"},
    )
    code, out, _, setup = run_cli(
        project, home, [], final(), lines="/deploy staging\n/exit\n", interactive=True
    )
    task = setup.model.calls[0].task.instructions
    assert code == 0 and 'Run the skill "deploy" now' in task and "Deploy to staging." in task
    assert "scripts/go.sh" in task and "Skill directory:" in task


def test_the_dollar_form_runs_a_skill_and_an_unknown_dollar_word_is_just_text(project, home):
    make_skill(skills_dir(project), "deploy", "description: Deploy\n", "Deploy $ARGUMENTS.")
    _, _, _, setup = run_cli(
        project,
        home,
        [],
        final(),
        final(),
        lines="$deploy prod\n$unknown thing\n/exit\n",
        interactive=True,
    )
    assert "Deploy prod." in setup.model.calls[0].task.instructions
    assert setup.model.calls[1].task.instructions == "$unknown thing"


def test_a_manual_only_skill_is_hidden_from_the_model_but_runs_when_you_type_it(project, home):
    make_skill(
        skills_dir(project),
        "cut-version",
        "description: Cut a version\ndisable-model-invocation: true\n",
    )
    make_skill(skills_dir(project), "lint", "description: Lint the code\n")
    _, _, _, setup = run_cli(
        project, home, [], final(), final(), lines="Hello\n/cut-version\n/exit\n", interactive=True
    )
    first = setup.model.calls[0]
    text = instructions_text(first)
    assert "- lint: Lint the code" in text and "cut-version" not in text
    assert 'Run the skill "cut-version" now' in setup.model.calls[1].task.instructions


def test_the_model_can_load_a_skill_and_read_a_file_bundled_with_it(project, home):
    make_skill(
        skills_dir(project),
        "deploy",
        "description: Deploy\n",
        "See notes.md.",
        {"notes.md": "the bundled notes"},
    )
    _, out, _, _ = run_cli(
        project,
        home,
        [],
        call("use_skill", name="deploy", file="notes.md"),
        final("done"),
        lines="Deploy it\n/exit\n",
        interactive=True,
    )
    assert "the bundled notes" in out


def test_a_skill_that_lists_tools_blocks_every_other_tool_while_it_runs(project, home):
    make_skill(
        skills_dir(project), "readonly", "description: Look only\nallowed-tools: Read\n", "Look."
    )
    code, out, _, _ = run_cli(
        project,
        home,
        ["--permission-mode", "bypass", "--allow-unsandboxed"],
        call("write_file", path="x.txt", content="no"),
        final("done"),
        lines="/readonly\n/exit\n",
        interactive=True,
    )
    assert code == 0 and not (project / "x.txt").exists()
    assert 'The skill "readonly" allows only read_file' in out


def test_the_tool_limit_ends_with_the_skill_so_the_next_turn_is_unrestricted(project, home):
    make_skill(skills_dir(project), "readonly", "description: Look\nallowed-tools: Read\n", "Look.")
    code, out, _, _ = run_cli(
        project,
        home,
        ["--permission-mode", "bypass", "--allow-unsandboxed"],
        call("write_file", path="y.txt", content="yes"),
        final("done"),
        lines="/readonly\nWrite y\n/exit\n",
        interactive=True,
    )
    assert code == 0 and out.count('The skill "readonly" allows only') == 1
    assert (project / "y.txt").read_text(encoding="utf-8") == "yes"


def test_skills_from_claude_folders_are_found_and_the_project_one_wins(project, home):
    make_skill(home / ".claude" / "skills", "shared", "description: From claude\n", "claude copy")
    make_skill(skills_dir(project), "shared", "description: From project\n", "project copy")
    make_skill(home / ".claude" / "skills", "only-user", "description: User skill\n")
    _, out, _, _ = run_cli(project, home, [], lines="/skills\n/exit\n", interactive=True)
    assert "/shared  From project" in out and "/only-user  User skill" in out


def test_skills_new_creates_a_working_skill_and_install_copies_a_folder(project, home, tmp_path):
    source = make_skill(tmp_path / "elsewhere", "imported", "description: Imported skill\n", "Do.")
    _, out, err, setup = run_cli(
        project,
        home,
        [],
        final(),
        lines=(
            f"/skills new fresh-one\n/skills new Bad Name\n/skills install {source}\n"
            "/skills\n/exit\n"
        ),
        interactive=True,
    )
    assert (project / ".nailong" / "skills" / "fresh-one" / "SKILL.md").is_file()
    assert (project / ".nailong" / "skills" / "imported" / "SKILL.md").is_file()
    assert "/fresh-one" in out and "/imported  Imported skill" in out
    assert "Usage: /skills new <name>" in out


def test_a_bad_skill_file_stops_startup_naming_the_file(project, home):
    folder = skills_dir(project) / "broken"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: broken\n---\nbody", encoding="utf-8")
    code, _, err, _ = run_cli(project, home, [], lines="/exit\n", interactive=True)
    assert code != 0 and "SKILL.md" in err and 'needs a "description:" line' in err


def test_a_skill_the_model_loads_does_not_lock_its_other_tools(project, home):
    make_skill(skills_dir(project), "readonly", "description: Look\nallowed-tools: Read\n", "Look.")
    code, _, _, _ = run_cli(
        project,
        home,
        ["--permission-mode", "bypass", "--allow-unsandboxed"],
        call("use_skill", name="readonly"),
        call("write_file", path="z.txt", content="fine"),
        final("done"),
        lines="Go\n/exit\n",
        interactive=True,
    )
    assert code == 0 and (project / "z.txt").read_text(encoding="utf-8") == "fine"
