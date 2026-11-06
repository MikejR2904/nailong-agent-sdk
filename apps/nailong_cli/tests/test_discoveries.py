from __future__ import annotations

import threading

import pytest

from nailong_cli.discoveries import DiscoveryStore
from tests.conftest import call, final, run_cli, tool_names

BYPASS = ["--permission-mode", "bypass", "--allow-unsandboxed"]


@pytest.fixture
def store(tmp_path):
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "src" / "auth.py").write_text("TOKEN_TTL_MINUTES = 15\n", encoding="utf-8")
    (project / "src" / "docs.md").write_text(
        "Tokens expire after fifteen minutes.\n", encoding="utf-8"
    )
    return DiscoveryStore(project / ".nailong" / "discoveries", project), project


def evidence(path="src/auth.py", quote="TOKEN_TTL_MINUTES = 15"):
    return [{"path": path, "quote": quote, "location": "line 1"}]


def test_a_discovery_is_published_with_owner_and_evidence_and_is_not_yet_knowledge(store):
    discoveries, _ = store
    made = discoveries.publish(
        "auth.token-ttl", "Tokens last 15 minutes.", evidence(), owner="sub-scout"
    )
    assert made == {"id": "d-0001", "status": "proposed", "conflicts_with": []}
    found = discoveries.get("d-0001")
    assert found["owner"] == "sub-scout" and found["status"] == "proposed" and not found["stale"]
    assert found["evidence"][0]["status"] == "current" and len(found["evidence"][0]["sha256"]) == 64
    assert discoveries.projection() == ""


def test_evidence_is_required_real_inside_the_project_and_quotes_must_be_genuine(store):
    discoveries, project = store
    (project / ".env").write_text("K=v", encoding="utf-8")
    for items, fragment in (
        ([], "needs at least one piece of evidence"),
        ([{"path": "../x.py"}], "not a path inside the project"),
        ([{"path": ".env"}], "credential-style path"),
        ([{"path": "src/none.py"}], "is not a file in the project"),
        ([{"path": "src/auth.py", "quote": "TTL = 99"}], "The quote is not in"),
    ):
        with pytest.raises(ValueError, match=fragment):
            discoveries.publish("topic", "claim", items)
    with pytest.raises(ValueError, match="at most 5 files"):
        discoveries.publish("topic", "claim", [{"path": "src/auth.py"}] * 6)
    with pytest.raises(ValueError, match="Topic"):
        discoveries.publish("Bad Topic!", "claim", evidence())
    assert discoveries.listing("all") == []


def test_the_same_claim_twice_is_reported_as_a_duplicate_not_published_again(store):
    discoveries, _ = store
    discoveries.publish("auth.ttl", "Tokens last 15 minutes.", evidence())
    again = discoveries.publish("auth.ttl", "tokens  last 15 MINUTES.", evidence(), owner="other")
    assert again["duplicate"] is True and again["id"] == "d-0001"
    assert len(discoveries.listing("all")) == 1


def test_different_claims_on_one_topic_are_a_conflict_that_blocks_acceptance(store):
    discoveries, _ = store
    discoveries.publish("auth.ttl", "Tokens last 15 minutes.", evidence(), owner="a")
    second = discoveries.publish(
        "auth.ttl", "Tokens last an hour.", evidence("src/docs.md", ""), owner="b"
    )
    assert second["conflicts_with"] == ["d-0001"]
    assert discoveries.get("d-0001")["conflicts_with"] == ["d-0002"]
    with pytest.raises(ValueError, match=r"conflicts with \['d-0002'\].*supersedes"):
        discoveries.accept("d-0001", "looks right")
    done = discoveries.accept("d-0001", "code is the source of truth", ["d-0002"])
    assert done == {"id": "d-0001", "status": "accepted", "superseded": ["d-0002"]}
    assert discoveries.get("d-0002")["status"] == "superseded"
    assert discoveries.get("d-0001")["conflicts_with"] == []
    with pytest.raises(ValueError, match="is superseded, not proposed"):
        discoveries.accept("d-0002", "late")
    assert "auth.ttl: Tokens last 15 minutes. [d-0001]" in discoveries.projection()


def test_superseding_something_that_is_not_in_conflict_is_refused(store):
    discoveries, _ = store
    discoveries.publish("a.one", "Claim one.", evidence())
    discoveries.publish("b.two", "Claim two.", evidence())
    with pytest.raises(ValueError, match=r"conflicts with nothing.*got \['d-0002'\]"):
        discoveries.accept("d-0001", "n", ["d-0002"])


def test_a_discovery_turns_stale_when_its_evidence_changes_and_cannot_be_accepted(store):
    discoveries, project = store
    discoveries.publish("auth.ttl", "Tokens last 15 minutes.", evidence())
    discoveries.accept("d-0001", "verified")
    assert "d-0001" in discoveries.projection()
    (project / "src" / "auth.py").write_text("TOKEN_TTL_MINUTES = 60\n", encoding="utf-8")
    assert discoveries.get("d-0001")["stale"] is True
    assert discoveries.projection() == ""
    discoveries.publish(
        "auth.ttl.new", "Tokens last 60 minutes.", evidence(quote="TOKEN_TTL_MINUTES = 60")
    )
    (project / "src" / "auth.py").write_text("TOKEN_TTL_MINUTES = 90\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"rest on files that changed|rests on files that changed"):
        discoveries.accept("d-0002", "n")
    (project / "src" / "auth.py").unlink()
    assert discoveries.get("d-0002")["evidence"][0]["status"] == "missing"


def test_a_stale_duplicate_can_be_published_again_from_the_new_text(store):
    discoveries, project = store
    discoveries.publish("auth.ttl", "Tokens last 15 minutes.", evidence())
    (project / "src" / "auth.py").write_text(
        "TOKEN_TTL_MINUTES = 15  # confirmed\n", encoding="utf-8"
    )
    again = discoveries.publish("auth.ttl", "Tokens last 15 minutes.", evidence())
    assert again["id"] == "d-0002" and not again.get("duplicate")


def test_rejecting_records_who_and_why_and_removes_it_from_the_open_list(store):
    discoveries, _ = store
    discoveries.publish("auth.ttl", "Tokens last 15 minutes.", evidence())
    discoveries.reject("d-0001", "measured differently", by="main")
    assert discoveries.listing("open") == []
    assert discoveries.get("d-0001")["history"][0]["reason"] == "measured differently"
    with pytest.raises(ValueError, match="already rejected"):
        discoveries.reject("d-0001", "again")
    with pytest.raises(ValueError, match='There is no discovery "d-0009"'):
        discoveries.get("d-0009")


def test_many_agents_publishing_at_once_lose_nothing_and_get_distinct_ids(store):
    discoveries, _ = store
    barrier = threading.Barrier(8)
    ids: list[str] = []

    def publish(number: int) -> None:
        barrier.wait()
        ids.append(
            discoveries.publish(
                f"topic.n{number}", f"Claim {number}.", evidence(), owner=f"sub-{number}"
            )["id"]
        )

    threads = [threading.Thread(target=publish, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(ids) == [f"d-{n:04d}" for n in range(1, 9)]
    assert {row["owner"] for row in discoveries.listing("all")} == {f"sub-{n}" for n in range(8)}


def test_a_crash_that_cut_the_last_journal_line_is_survived_but_damage_elsewhere_is_named(store):
    discoveries, _ = store
    discoveries.publish("a.one", "Claim one.", evidence())
    journal = discoveries._journal()
    with journal.open("a", encoding="utf-8") as handle:
        handle.write('{"event":"publi')
    assert len(discoveries.listing("all")) == 1
    journal.write_text('{broken\n{"event":"x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Line 1 of .* is not JSON"):
        discoveries.listing("all")


def test_the_agent_publishes_a_person_approves_and_the_next_session_is_told(project, home):
    (project / "src").mkdir()
    (project / "src" / "auth.py").write_text("TOKEN_TTL_MINUTES = 15\n", encoding="utf-8")
    code, out, err, setup = run_cli(
        project,
        home,
        [],
        call(
            "discovery_publish",
            topic="auth.ttl",
            claim="Tokens last 15 minutes.",
            evidence=[{"path": "src/auth.py", "quote": "TOKEN_TTL_MINUTES = 15"}],
        ),
        call("discovery_accept", id="d-0001", note="checked the code"),
        final("done"),
        lines=(
            "Look into auth\ny\n/discoveries\n/discoveries accepted\n/discoveries d-0001\n/exit\n"
        ),
        interactive=True,
    )
    assert code == 0, err
    assert "Approve discovery_accept?" in out
    assert "d-0001 [accepted] auth.ttl" in out and "src/auth.py (current)" in out
    names = tool_names(setup.model.calls[0])
    assert {"discovery_publish", "discovery_list", "discovery_read", "discovery_accept"} <= set(
        names
    )
    _, _, _, later = run_cli(project, home, [], final(), lines="Hello\n/exit\n", interactive=True)
    sections = str(later.model.calls[0].prompt.model_dump()["sections"])
    assert (
        "Accepted project knowledge" in sections and "Tokens last 15 minutes. [d-0001]" in sections
    )


def test_planning_mode_may_read_discoveries_but_not_publish_or_decide(project, home):
    _, _, _, setup = run_cli(
        project,
        home,
        ["--permission-mode", "plan"],
        final(),
        lines="Plan\n/exit\n",
        interactive=True,
    )
    names = set(tool_names(setup.model.calls[0]))
    assert {"discovery_list", "discovery_read"} <= names
    assert not names & {"discovery_publish", "discovery_accept", "discovery_reject"}


def test_a_denied_acceptance_leaves_the_discovery_proposed(project, home):
    (project / "a.py").write_text("X = 1\n", encoding="utf-8")
    code, out, err, _ = run_cli(
        project,
        home,
        [],
        call("discovery_publish", topic="a.x", claim="X is one.", evidence=[{"path": "a.py"}]),
        call("discovery_accept", id="d-0001", note="n"),
        final("done"),
        lines="Go\nn\n/discoveries accepted\n/discoveries\n/exit\n",
        interactive=True,
    )
    assert code == 0, err
    assert "No accepted discoveries." in out and "d-0001 [proposed] a.x" in out
