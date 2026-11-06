"""Findings that agents publish with evidence, and the ones a person accepts as project knowledge.

An agent never overwrites shared truth. It publishes a discovery: a claim about a topic, with the
project files (and, when it can, the exact words) that support it. Two discoveries on one topic
that say different things are shown as a conflict, not resolved silently. Accepting one is a
decision a person approves, and it must name the conflicting ones it replaces. When a file a
discovery rests on changes or goes, the discovery reads as stale and cannot be accepted until it is
published again from the new text. Everything is a line in an append-only journal.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from nailong_agent_sdk.starter import FunctionTool, tool
from nailong_agent_sdk.tools.policy import sensitive_pattern_for

from .documents import DOCUMENT_SUFFIXES, DocumentReader
from .storage import append_durably, exclusive

MAX_CLAIM_CHARS = 1_000
MAX_EVIDENCE = 5
MAX_QUOTE_CHARS = 300
MAX_PROJECTION_CHARS = 2_000
LOCK_WAIT_SECONDS = 10.0
LOCK_STALE_SECONDS = 60.0
_TOPIC = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,63}$")


def _words(text: str) -> str:
    return " ".join(text.split()).casefold()


class DiscoveryStore:
    def __init__(self, directory: Path, project: Path) -> None:
        self._directory = directory
        self._project = project.resolve()
        self._reader = DocumentReader({"project": self._project}, self._project / ".nailong")
        self.events: Any = None

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.events is not None:
            self.events.emit(kind, payload)

    def _journal(self) -> Path:
        return self._directory / "journal.jsonl"

    def _events(self) -> list[dict[str, Any]]:
        path = self._journal()
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        events = []
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                if number == len(lines):
                    continue
                raise ValueError(
                    f"Line {number} of {path} is not JSON and is not the last line, so a crash "
                    "did not cut it off; the journal needs repair by hand."
                ) from None
        return events

    def _states(self) -> dict[str, dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for event in self._events():
            kind, ident = str(event.get("event")), str(event.get("id"))
            if kind == "published":
                states[ident] = {**event, "status": "proposed", "history": []}
            elif ident in states:
                state = states[ident]
                state["history"].append(
                    {k: v for k, v in event.items() if k not in {"event", "id"}} | {"event": kind}
                )
                state["status"] = {
                    "accepted": "accepted",
                    "rejected": "rejected",
                    "superseded": "superseded",
                }.get(kind, state["status"])
        return states

    def _evidence_status(self, item: dict[str, Any]) -> str:
        target = (self._project / item["path"]).resolve()
        if not target.is_file():
            return "missing"
        return (
            "current"
            if hashlib.sha256(target.read_bytes()).hexdigest() == item["sha256"]
            else "changed"
        )

    def _describe(self, state: dict[str, Any], states: dict[str, dict[str, Any]]) -> dict[str, Any]:
        evidence = [{**item, "status": self._evidence_status(item)} for item in state["evidence"]]
        live = {"proposed", "accepted"}
        conflicts = sorted(
            other["id"]
            for other in states.values()
            if other["id"] != state["id"]
            and other["status"] in live
            and state["status"] in live
            and other["topic"] == state["topic"]
            and _words(other["claim"]) != _words(state["claim"])
        )
        return {
            "id": state["id"],
            "topic": state["topic"],
            "claim": state["claim"],
            "owner": state["owner"],
            "published_at": state["at"],
            "status": state["status"],
            "scope": "authoritative" if state["status"] == "accepted" else "shared",
            "stale": any(item["status"] != "current" for item in evidence),
            "conflicts_with": conflicts,
            "evidence": evidence,
            "history": state["history"],
        }

    def _text_of(self, target: Path) -> str:
        if target.suffix.lower() in DOCUMENT_SUFFIXES:
            _, units, _ = self._reader.units(target)
            return "\n".join(read() for read in units)
        return target.read_text(encoding="utf-8", errors="replace")

    def _evidence(self, items: list[dict[str, Any]]) -> list[dict[str, str]]:
        if not items:
            raise ValueError("A discovery needs at least one piece of evidence: a project file.")
        if len(items) > MAX_EVIDENCE:
            raise ValueError(
                f"A discovery can cite at most {MAX_EVIDENCE} files; got {len(items)}."
            )
        checked = []
        for item in items:
            relative = str(item.get("path") or "")
            target = (self._project / relative).resolve()
            if not relative or not target.is_relative_to(self._project):
                raise ValueError(f'Evidence "{relative}" is not a path inside the project.')
            if sensitive_pattern_for(target) is not None:
                raise ValueError(f'Evidence "{relative}" is a credential-style path.')
            if not target.is_file():
                raise ValueError(f'Evidence "{relative}" is not a file in the project.')
            quote = str(item.get("quote") or "").strip()
            if quote:
                if len(quote) > MAX_QUOTE_CHARS:
                    raise ValueError(f"A quote can be at most {MAX_QUOTE_CHARS} characters.")
                if _words(quote) not in _words(self._text_of(target)):
                    raise ValueError(
                        f'The quote is not in "{relative}". Quote the file\'s own words, or leave '
                        "the quote out and name the location."
                    )
            checked.append(
                {
                    "path": Path(relative).as_posix(),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "quote": quote,
                    "location": str(item.get("location") or "")[:80],
                }
            )
        return checked

    def publish(
        self, topic: str, claim: str, evidence: list[dict[str, Any]], owner: str = "main"
    ) -> dict[str, Any]:
        name = topic.strip().lower()
        if not _TOPIC.match(name):
            raise ValueError(
                f'Topic "{topic}" must be lowercase letters, digits, ".", "_", "-" or "/", up to '
                "64 characters, for example auth.token-lifetime."
            )
        text = " ".join(claim.split())
        if not text or len(text) > MAX_CLAIM_CHARS:
            raise ValueError(f"The claim must be 1 to {MAX_CLAIM_CHARS} characters.")
        checked = self._evidence(evidence)
        self._directory.mkdir(parents=True, exist_ok=True)
        with exclusive(
            self._directory / ".lock",
            wait=LOCK_WAIT_SECONDS,
            stale=LOCK_STALE_SECONDS,
            what="discoveries",
        ):
            states = self._states()
            for state in states.values():
                if (
                    state["topic"] == name
                    and state["status"] in {"proposed", "accepted"}
                    and _words(state["claim"]) == _words(text)
                    and not self._describe(state, states)["stale"]
                ):
                    existing = self._describe(state, states)
                    return {
                        "id": existing["id"],
                        "duplicate": True,
                        "status": existing["status"],
                        "conflicts_with": existing["conflicts_with"],
                    }
            ident = f"d-{len(states) + 1:04d}"
            event = {
                "event": "published",
                "id": ident,
                "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "topic": name,
                "claim": text,
                "owner": owner,
                "evidence": checked,
            }
            append_durably(self._journal(), json.dumps(event, separators=(",", ":")))
            states[ident] = {**event, "status": "proposed", "history": []}
            described = self._describe(states[ident], states)
        self._emit("discovery.published", {"id": ident, "topic": name, "owner": owner})
        return {"id": ident, "status": "proposed", "conflicts_with": described["conflicts_with"]}

    def get(self, ident: str) -> dict[str, Any]:
        states = self._states()
        if ident not in states:
            raise ValueError(f'There is no discovery "{ident}"; ids: {sorted(states)}.')
        return self._describe(states[ident], states)

    def listing(self, status: str = "open") -> list[dict[str, Any]]:
        if status not in {"open", "accepted", "rejected", "all"}:
            raise ValueError('status must be "open", "accepted", "rejected" or "all".')
        states = self._states()
        wanted = {
            "open": {"proposed"},
            "accepted": {"accepted"},
            "rejected": {"rejected", "superseded"},
        }
        rows = []
        for state in states.values():
            if status != "all" and state["status"] not in wanted[status]:
                continue
            described = self._describe(state, states)
            rows.append(
                {
                    "id": described["id"],
                    "topic": described["topic"],
                    "claim": described["claim"][:160],
                    "owner": described["owner"],
                    "status": described["status"],
                    "scope": described["scope"],
                    "stale": described["stale"],
                    "conflicts_with": described["conflicts_with"],
                }
            )
        return rows

    def accept(
        self, ident: str, note: str, supersedes: list[str] | None = None, by: str = "user"
    ) -> dict[str, Any]:
        replaced = sorted(set(supersedes or []))
        with exclusive(
            self._directory / ".lock",
            wait=LOCK_WAIT_SECONDS,
            stale=LOCK_STALE_SECONDS,
            what="discoveries",
        ):
            current = self.get(ident)
            if current["status"] != "proposed":
                raise ValueError(f"Discovery {ident} is {current['status']}, not proposed.")
            if current["stale"]:
                changed = [
                    f"{e['path']} ({e['status']})"
                    for e in current["evidence"]
                    if e["status"] != "current"
                ]
                raise ValueError(
                    f"Discovery {ident} rests on files that changed: {', '.join(changed)}. "
                    "Check it against the new text and publish it again."
                )
            conflicts = current["conflicts_with"]
            if set(conflicts) != set(replaced):
                raise ValueError(
                    f"Discovery {ident} conflicts with {conflicts or 'nothing'}; accepting it "
                    f"must name exactly those in supersedes (got {replaced}). Decide which "
                    "claim is right first."
                )
            stamp = datetime.now().astimezone().isoformat(timespec="seconds")
            append_durably(
                self._journal(),
                json.dumps(
                    {"event": "accepted", "id": ident, "at": stamp, "by": by,
                     "note": " ".join(note.split())[:300], "supersedes": replaced},
                    separators=(",", ":"),
                ),
            )  # fmt: skip
            for other in replaced:
                append_durably(
                    self._journal(),
                    json.dumps(
                        {"event": "superseded", "id": other, "at": stamp, "by_id": ident},
                        separators=(",", ":"),
                    ),
                )
        self._emit("discovery.accepted", {"id": ident, "by": by, "superseded": replaced})
        return {"id": ident, "status": "accepted", "superseded": replaced}

    def reject(self, ident: str, reason: str, by: str = "user") -> dict[str, Any]:
        with exclusive(
            self._directory / ".lock",
            wait=LOCK_WAIT_SECONDS,
            stale=LOCK_STALE_SECONDS,
            what="discoveries",
        ):
            current = self.get(ident)
            if current["status"] not in {"proposed", "accepted"}:
                raise ValueError(f"Discovery {ident} is already {current['status']}.")
            append_durably(
                self._journal(),
                json.dumps(
                    {"event": "rejected", "id": ident,
                     "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                     "by": by, "reason": " ".join(reason.split())[:300]},
                    separators=(",", ":"),
                ),
            )  # fmt: skip
        self._emit("discovery.rejected", {"id": ident, "by": by})
        return {"id": ident, "status": "rejected"}

    def projection(self, limit: int = MAX_PROJECTION_CHARS) -> str:
        lines = []
        states = self._states()
        for state in states.values():
            if state["status"] != "accepted":
                continue
            described = self._describe(state, states)
            if described["stale"]:
                continue
            lines.append(f"- {described['topic']}: {described['claim']} [{described['id']}]")
        text = "\n".join(lines)
        return (
            text
            if len(text) <= limit
            else text[:limit].rsplit("\n", 1)[0] + "\n- (more: discovery_list)"
        )

    def tools(
        self, author: str = "main", *, can_decide: bool = False, can_publish: bool = True
    ) -> list[FunctionTool]:
        def discovery_list(status: str = "open") -> list[dict[str, Any]]:
            """List discoveries: open (proposed), accepted, rejected or all, with stale and
            conflict flags."""
            return self.listing(status)

        def discovery_read(id: str) -> dict[str, Any]:
            """Read one discovery with its evidence, whether that evidence changed, and what it
            conflicts with."""
            return self.get(id)

        tools = [tool(discovery_list, read_only=True), tool(discovery_read, read_only=True)]
        if can_publish:

            def discovery_publish(
                topic: str, claim: str, evidence: list[dict[str, str]]
            ) -> dict[str, Any]:
                """Publish a finding for others to build on. evidence is a list of {"path":
                project file, "quote": its exact words (optional), "location": where (optional)}.
                It does not become project knowledge until a person accepts it; a different claim
                on the same topic is reported as a conflict."""
                return self.publish(topic, claim, evidence, owner=author)

            tools.append(tool(discovery_publish))
        if can_decide:

            def discovery_accept(
                id: str, note: str, supersedes: list[str] | None = None
            ) -> dict[str, Any]:
                """Make a discovery accepted project knowledge. Name in supersedes every
                discovery it conflicts with. A person approves this."""
                return self.accept(id, note, supersedes, by=author)

            def discovery_reject(id: str, reason: str) -> dict[str, Any]:
                """Reject a discovery that is wrong or no longer holds, and say why."""
                return self.reject(id, reason, by=author)

            tools.append(tool(discovery_accept, needs_approval=True))
            tools.append(tool(discovery_reject, needs_approval=True))
        return tools
