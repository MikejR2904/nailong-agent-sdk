from __future__ import annotations

import json

import pytest
import yaml

from nailong_cli import ci, ci_review
from nailong_cli.ci_review import ReviewError, build_review, commentable_lines

DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 import os
-old = 1
+new = 2
+extra = 3
 print(new)
diff --git a/gone.py b/gone.py
--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-x
"""


def test_only_lines_shown_by_the_diff_can_take_a_comment():
    found = commentable_lines(DIFF)
    assert found["app.py"] == {1, 2, 3, 4}
    assert "gone.py" not in found


def test_comments_on_diff_lines_go_inline_and_the_rest_are_listed_in_the_summary():
    answer = "Sure:\n" + json.dumps(
        {
            "summary": "Mostly fine.",
            "comments": [
                {"path": "app.py", "line": 3, "body": "extra is unused"},
                {"path": "app.py", "line": 99, "body": "not in the diff"},
                {"path": "other.py", "line": 1, "body": "unknown file"},
                {"path": "app.py", "line": 2, "body": ""},
            ],
        }
    )
    review = build_review(answer, DIFF)
    assert review["event"] == "COMMENT"
    assert review["comments"] == [
        {"path": "app.py", "line": 3, "side": "RIGHT", "body": "extra is unused"}
    ]
    assert "Mostly fine." in review["body"] and "`app.py:99`" in review["body"]
    assert "`other.py:1`" in review["body"]


def test_a_reply_without_json_is_refused_by_name():
    with pytest.raises(ReviewError, match="holds no JSON object"):
        build_review("looks good to me", DIFF)
    with pytest.raises(ReviewError, match="not valid JSON"):
        build_review("{nope}", DIFF)


def test_the_review_is_posted_through_gh_with_the_payload_on_stdin(monkeypatch):
    calls = []

    class Done:
        returncode = 0
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs["input"]))
        return Done()

    monkeypatch.setattr(ci_review.subprocess, "run", fake_run)
    ci_review.post({"event": "COMMENT", "body": "x", "comments": []}, "owner/repo", "12")
    assert calls[0][0] == ["gh", "api", "repos/owner/repo/pulls/12/reviews", "--input", "-"]
    assert json.loads(calls[0][1])["event"] == "COMMENT"
    with pytest.raises(ReviewError, match="not usable"):
        ci_review.post({}, "bad repo", "12")


def test_the_workflow_is_valid_yaml_skips_forks_and_never_runs_the_agent_with_edits():
    workflow = yaml.safe_load(ci.REVIEW)
    job = workflow["jobs"]["review"]
    assert "head.repo.full_name == github.repository" in job["if"]
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "write"}
    script = "\n".join(step.get("run", "") for step in job["steps"])
    assert "--permission-mode plan" in script and "nailong_cli.ci_review" in script
    assert ci.template("github-review")[1].name == "nailong-review.yml"
