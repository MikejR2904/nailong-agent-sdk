from __future__ import annotations

import pytest
import yaml

from nailong_cli import ci
from nailong_cli.setup import CHOICES
from tests.conftest import run_cli, write_user_config
from tests.test_sdk_features import BASE


def test_the_github_workflow_is_valid_and_runs_only_for_trusted_commenters():
    workflow = yaml.safe_load(ci.GITHUB)
    assert workflow["jobs"]["nailong"]["runs-on"] == "ubuntu-latest"
    condition = workflow["jobs"]["nailong"]["if"]
    assert (
        "OWNER" in condition and "COLLABORATOR" in condition and "author_association" in condition
    )
    assert "--permission-mode plan" in ci.GITHUB


def test_the_comment_text_reaches_the_shell_only_through_an_environment_variable():
    workflow = yaml.safe_load(ci.GITHUB)
    for step in workflow["jobs"]["nailong"]["steps"]:
        script = step.get("run", "")
        assert "github.event.comment" not in script and "github.event.inputs" not in script
    run_step = next(
        s for s in workflow["jobs"]["nailong"]["steps"] if s.get("name") == "Run nailong"
    )
    assert "comment.body" in run_step["env"]["REQUEST"]


def test_the_gitlab_job_is_valid_and_manual_only():
    job = yaml.safe_load(ci.GITLAB)["nailong"]
    assert job["rules"][0]["if"].endswith('"web"')
    assert "--permission-mode plan" in job["script"][1]


def test_ci_prints_a_template_and_write_saves_it_once(project, home):
    _, out, err, _ = run_cli(
        project,
        home,
        [],
        lines=(
            "/ci github\n/ci github write\n/ci github write\n/ci gitlab write\n/ci\n/ci nope\n"
            "/exit\n"
        ),
        interactive=True,
    )
    assert (
        "name: nailong" in out
        and "wrote" in out
        and "Usage: /ci github|github-review|gitlab" in out
    )
    assert (project / ".github" / "workflows" / "nailong.yml").is_file()
    assert (project / ".gitlab-ci-nailong.yml").is_file()
    assert "already exists" in err


def test_an_unknown_service_is_named():
    with pytest.raises(ValueError, match='Unknown service "bitbucket"'):
        ci.template("bitbucket")


def test_the_service_menu_lists_the_common_hosted_providers():
    labels = " ".join(choice.label for choice in CHOICES)
    for name in ("Groq", "Together", "Fireworks", "DeepSeek", "Mistral", "Cerebras"):
        assert name in labels
    from nailong_agent_sdk.starter.models import OPENAI_COMPATIBLE_BASE_URLS

    for choice in CHOICES:
        if choice.base_url:
            assert choice.base_url in OPENAI_COMPATIBLE_BASE_URLS


def test_connect_replaces_the_settings_and_switches_the_model_now(project, home):
    write_user_config(home, BASE)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    _, out, err, _ = run_cli(
        project,
        home,
        [],
        lines="/connect\n2\n1\nvendor/new-model\ntyped-key\n/model\n/exit\n",
        interactive=True,
    )
    assert "now using vendor/new-model (openai-compatible)" in out, err


def test_connect_can_be_declined_without_changing_anything(project, home):
    write_user_config(home, BASE)
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    _, _, err, _ = run_cli(project, home, [], lines="/connect\n1\n/exit\n", interactive=True)
    assert "was left as it is" in err
