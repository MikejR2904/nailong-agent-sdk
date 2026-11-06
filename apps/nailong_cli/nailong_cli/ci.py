"""/ci: a workflow that runs nailong from GitHub issue comments or a GitLab pipeline."""

from __future__ import annotations

from pathlib import Path

GITHUB_FILE = Path(".github") / "workflows" / "nailong.yml"
GITLAB_FILE = Path(".gitlab-ci-nailong.yml")
REVIEW_FILE = Path(".github") / "workflows" / "nailong-review.yml"

GITHUB = """\
# Answers "/nailong <request>" comments from people with write access, read-only (plan mode).
# Needs the model settings committed in .nailong/config.toml and the key as a repository secret.
name: nailong
on:
  issue_comment:
    types: [created]
  workflow_dispatch:
    inputs:
      prompt:
        description: What should nailong look at?
        required: true
permissions:
  contents: read
  issues: write
  pull-requests: write
jobs:
  nailong:
    if: >-
      github.event_name == 'workflow_dispatch' ||
      (startsWith(github.event.comment.body, '/nailong') &&
       contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'),
                github.event.comment.author_association))
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install nailong-cli
      - name: Run nailong
        env:
          REQUEST: ${{ github.event.inputs.prompt || github.event.comment.body }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
        run: |
          request="${REQUEST#/nailong}"
          nailong -p "$request" --permission-mode plan --output text > answer.txt
      - name: Reply on the issue or pull request
        if: github.event_name == 'issue_comment'
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh issue comment "${{ github.event.issue.number }}" --body-file answer.txt
"""

REVIEW = """\
# Reviews pull requests from this repository (not forks) and posts one COMMENT review with inline
# comments on lines the diff shows. The agent runs read-only; it never approves or requests changes.
# Needs the model settings committed in .nailong/config.toml and the key as a repository secret.
name: nailong-review
on:
  pull_request:
    types: [opened, synchronize, ready_for_review]
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    if: >-
      github.event.pull_request.head.repo.full_name == github.repository &&
      !github.event.pull_request.draft
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install nailong-cli
      - name: Review the change
        env:
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          BASE_REF: ${{ github.base_ref }}
        run: |
          git diff "origin/${BASE_REF}...HEAD" | head -c 400000 > pr.diff
          prompt="$(python -c 'from nailong_cli.ci_review import PROMPT; print(PROMPT)')"
          nailong -p "$prompt" --permission-mode plan --output text > answer.txt
      - name: Post the review
        env:
          GH_TOKEN: ${{ github.token }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: python -m nailong_cli.ci_review answer.txt pr.diff
"""

GITLAB = """\
# Include this from .gitlab-ci.yml:  include: .gitlab-ci-nailong.yml
# Run it from CI/CD > Pipelines > Run pipeline, setting NAILONG_PROMPT. Read-only (plan mode).
# Needs the model settings committed in .nailong/config.toml and the key as a CI/CD variable.
nailong:
  image: python:3.13
  rules:
    - if: $NAILONG_PROMPT && $CI_PIPELINE_SOURCE == "web"
  script:
    - pip install nailong-cli
    - nailong -p "$NAILONG_PROMPT" --permission-mode plan --output text | tee nailong-answer.txt
  artifacts:
    paths: [nailong-answer.txt]
"""


def template(kind: str) -> tuple[str, Path]:
    if kind == "github":
        return GITHUB, GITHUB_FILE
    if kind == "gitlab":
        return GITLAB, GITLAB_FILE
    if kind == "github-review":
        return REVIEW, REVIEW_FILE
    raise ValueError(f'Unknown service "{kind}"; choose github, github-review or gitlab.')


def write_template(project: Path, kind: str) -> Path:
    text, relative = template(kind)
    target = project / relative
    if target.exists():
        raise ValueError(f"{target} already exists; remove it first to write a fresh one.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
