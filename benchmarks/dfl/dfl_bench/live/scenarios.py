"""Tool-call histories with ground-truth questions, for measured downstream evaluation.

A task is a realistic agent history (reads, a failed call, derived actions) followed by a three-part
question. Each answer is a value that sits in the full result of specific episodes; the generator
checks that the value is present in a retained episode's projected context and absent from its
compacted stub, so retention decides whether the question is answerable. The verifier is
deterministic string comparison against the generator's ground truth.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import product
from typing import Any

from nailong_agent_sdk.foundations.hashing import canonical_json
from nailong_agent_sdk.memory.context_projection import ContextProjectionPolicy
from nailong_agent_sdk.memory.episode_scoring import _episode_tokens

from ..instances import Instance, replay

FAMILIES = ("ledger", "deploy", "triage")
MAX_OUTPUT_CHARS = 900

WORDS = (
    "settlement adjustment remittance batch audit window carryover tolerance variance escalation "
    "threshold ledger posting cutoff schedule retry fallback quorum throttle backlog rollout "
    "canary drain probe cohort manifest rebuild shard replica failover latency saturation "
    "dispatch handoff rotation triage runbook mitigation postmortem severity customer vendor "
    "invoice credit refund dispute chargeback reserve forecast allocation reconcile snapshot "
    "tenant region cluster namespace artifact pipeline stage gate approval waiver exception"
).split()
FIRST = (
    "Ana Bruno Chen Dara Elif Farid Gita Hugo Ines Jonas Kavya Liam Maya Nikos Omar Priya".split()
)
LAST = (
    "Almeida Brandt Chua Dubois Erdem Fischer Gupta Haas Ito Jansen Khan Lopez Moreau Novak".split()
)
CURRENCIES = "EUR GBP JPY SGD CHF AUD CAD NOK".split()


@dataclass(frozen=True)
class LiveTask:
    instance: Instance
    family: str
    answer_keys: tuple[str, ...]
    expected: dict[str, str]
    fact_sources: dict[str, tuple[int, ...]]

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {key: {"type": ["string", "null"]} for key in self.answer_keys},
            "required": list(self.answer_keys),
            "additionalProperties": False,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "instance": self.instance.to_json(),
            "family": self.family,
            "answer_keys": list(self.answer_keys),
            "expected": self.expected,
            "fact_sources": {k: list(v) for k, v in self.fact_sources.items()},
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> LiveTask:
        return cls(
            instance=Instance.from_json(data["instance"]),
            family=data["family"],
            answer_keys=tuple(data["answer_keys"]),
            expected=dict(data["expected"]),
            fact_sources={k: tuple(v) for k, v in data["fact_sources"].items()},
        )


def normalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return str(value).strip().strip("\"'").strip().casefold()


def contains_value(text: str, value: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(normalize(value))}(?![a-z0-9])"
    return re.search(pattern, normalize(text)) is not None


def score_answer(answer: Mapping[str, Any] | None, expected: Mapping[str, str]) -> float:
    if not isinstance(answer, Mapping):
        return 0.0
    correct = sum(
        1 for key, value in expected.items() if normalize(answer.get(key)) == normalize(value)
    )
    return correct / len(expected)


class _Builder:
    def __init__(self, family: str, variant: int, index: int, seed: int) -> None:
        self.rng = random.Random(f"live/{seed}/{family}/{variant}/{index}")
        vocab_rng = random.Random(f"live-vocab/{family}/{variant}")
        self.vocabulary = vocab_rng.sample(WORDS, 28)
        self.prefix = vocab_rng.choice(
            ["acct", "wallet", "vault", "till", "fund", "pool", "desk", "book"]
            if family == "ledger"
            else ["svc", "app", "api", "job", "edge", "core", "web", "worker"]
            if family == "deploy"
            else ["tkt", "case", "req", "inc", "iss", "alert", "page", "task"]
        )
        self.steps: list[dict[str, Any]] = []
        self.sources: dict[str, list[int]] = {}

    def pad(self, low: int, high: int) -> str:
        target = self.rng.randint(low, high)
        words: list[str] = []
        while sum(len(w) + 1 for w in words) < target:
            words.append(self.rng.choice(self.vocabulary))
        return " ".join(words)[:target].rstrip()

    def entity(self) -> str:
        return f"{self.prefix}{self.rng.randrange(1000, 9999)}"

    def person(self) -> str:
        return f"{self.rng.choice(FIRST)} {self.rng.choice(LAST)}"

    def code(self) -> str:
        return f"E{self.rng.randrange(1000, 9999)}"

    def failure_text(self, subject: str, code: str) -> str:
        detail = self.pad(110, 150)
        return (
            f"{subject} did not complete after 3 retries; upstream reported: {detail}. "
            f"The circuit breaker opened and no cached value was available [code {code}]"
        )

    def add(
        self, op: str, tool: str, arguments: dict, outcome: dict, deps: list[int] | None = None
    ):
        index = len([s for s in self.steps if s["op"] in ("explore", "action")])
        step: dict[str, Any] = {"op": op, "tool": tool, "arguments": arguments, "outcome": outcome}
        if op == "action":
            step["deps"] = list(deps or [])
        else:
            step["deps"] = []
        self.steps.append(step)
        return index

    def finish(self) -> list[dict[str, Any]]:
        recipe: list[dict[str, Any]] = []
        index = 0
        for step in self.steps:
            if step["op"] == "action":
                recipe.append({"op": "access", "indices": list(step["deps"])})
            recipe.append(step)
            recipe.append({"op": "handle", "index": index})
            index += 1
        return recipe


def _success(output: dict[str, Any]) -> dict[str, Any]:
    if len(canonical_json(output)) > MAX_OUTPUT_CHARS:
        raise ValueError(
            f"Tool output exceeds {MAX_OUTPUT_CHARS} characters: {canonical_json(output)[:80]}"
        )
    return {"status": "succeeded", "output": output}


def _failed(error: str) -> dict[str, Any]:
    return {"status": "failed", "error": error}


def _ledger(b: _Builder):
    rng = b.rng
    accounts = [b.entity() for _ in range(rng.randint(4, 6))]
    while len(set(accounts)) < len(accounts):
        accounts = [b.entity() for _ in accounts]
    target, partner = accounts[0], accounts[1]
    owners = {a: b.person() for a in accounts}
    while len(set(owners.values())) < len(owners):
        owners = {a: b.person() for a in accounts}
    totals = {a: rng.randrange(10_000, 999_999) for a in accounts}
    reads: dict[str, int] = {}
    order = accounts[:]
    rng.shuffle(order)
    for account in order:
        entries = [{"cents": rng.randrange(100, 90_000), "memo": b.pad(40, 70)} for _ in range(3)]
        reads[account] = b.add(
            "explore",
            "get_account",
            {"account_id": account},
            _success(
                {
                    "account_id": account,
                    "entries": entries,
                    "owner": owners[account],
                    "status": rng.choice(["active", "active", "frozen"]),
                    "total_cents": totals[account],
                }
            ),
        )
    snapshot = f"snap-{rng.randrange(10000, 99999)}"
    snapshot_read = b.add(
        "explore",
        "load_snapshot",
        {"scope": "month-end"},
        _success(
            {
                "entries_loaded": rng.randrange(200, 900),
                "notes": b.pad(170, 230),
                "snapshot_id": snapshot,
            }
        ),
    )
    currencies = rng.sample(CURRENCIES, 2)
    named = rng.random() < 0.5
    error_code, decoy_code = b.code(), b.code()
    while decoy_code == error_code:
        decoy_code = b.code()
    failure = b.add(
        "explore",
        "refresh_rates",
        {"currency": currencies[0]},
        _failed(b.failure_text(f"Rate refresh for {currencies[0]}", error_code)),
    )
    if named and rng.random() < 0.7:
        b.add(
            "explore",
            "refresh_rates",
            {"currency": currencies[1]},
            _failed(b.failure_text(f"Rate refresh for {currencies[1]}", decoy_code)),
        )
    total = totals[target] + totals[partner]
    reconcile = b.add(
        "action",
        "reconcile",
        {"account_ids": [target, partner], "snapshot": "month-end"},
        _success(
            {
                "accounts": [target, partner],
                "notes": b.pad(170, 230),
                "reconciled_total_cents": total,
                "snapshot_id": snapshot,
            }
        ),
        deps=[reads[target], reads[partner], snapshot_read],
    )
    other = [a for a in accounts[2:]]
    decoy_reconcile = None
    if other and rng.random() < 0.6:
        decoy_reconcile = b.add(
            "action",
            "reconcile",
            {"account_ids": other[:2], "snapshot": "month-end"},
            _success(
                {
                    "accounts": other[:2],
                    "notes": b.pad(170, 230),
                    "reconciled_total_cents": sum(totals[a] for a in other[:2]),
                    "snapshot_id": snapshot,
                }
            ),
            deps=[reads[a] for a in other[:2]] + [snapshot_read],
        )
    alternatives_owner = [reads[target]]
    if rng.random() < 0.4:
        audit = b.add(
            "explore",
            "get_account_audit",
            {"account_id": target},
            _success({"audit_log": b.pad(240, 330), "owner": owners[target], "target": target}),
        )
        alternatives_owner.append(audit)
    third_is_total = rng.random() < 0.5
    facts = {"owner": owners[target], "error_code": error_code}
    sources = {"owner": alternatives_owner, "error_code": [failure]}
    questions = [
        f"Who owns account {target}?",
        (
            f"What error code did the failed refresh_rates call for {currencies[0]} return?"
            if named
            else "What error code did the failed refresh_rates call return?"
        ),
    ]
    if third_is_total:
        facts["reconciled_total_cents"] = str(total)
        sources["reconciled_total_cents"] = [reconcile]
        questions.append(
            f"What reconciled total in cents did the reconcile step for {target} "
            f"and {partner} produce?"
        )
    else:
        facts["snapshot_id"] = snapshot
        sources["snapshot_id"] = [reconcile, snapshot_read] + (
            [decoy_reconcile] if decoy_reconcile is not None else []
        )
        questions.append(
            f"Which snapshot id did the reconcile step for {target} and {partner} use?"
        )
    return facts, sources, questions


def _deploy(b: _Builder):
    rng = b.rng
    services = [b.entity() for _ in range(rng.randint(4, 6))]
    while len(set(services)) < len(services):
        services = [b.entity() for _ in services]
    target = services[0]
    replicas = {s: rng.randrange(100, 1000) for s in services}
    while len({replicas[s] for s in services}) < len(services):
        replicas = {s: rng.randrange(100, 1000) for s in services}
    reads: dict[str, int] = {}
    order = services[:]
    rng.shuffle(order)
    for service in order:
        version = f"v{rng.randrange(1, 4)}.{rng.randrange(0, 20)}.{rng.randrange(0, 9)}"
        checks = [
            {"name": b.pad(14, 24), "ok": rng.random() < 0.9, "detail": b.pad(40, 60)}
            for _ in range(3)
        ]
        reads[service] = b.add(
            "explore",
            "get_service",
            {"service": service},
            _success(
                {
                    "checks": checks,
                    "owner_team": rng.choice(b.vocabulary),
                    "replicas": replicas[service],
                    "service": service,
                    "version": version,
                }
            ),
        )
    release = f"rel-{rng.randrange(100, 999)}"
    digest = "sha256:" + hashlib.sha256(f"{release}/{b.rng.random()}".encode()).hexdigest()[:40]
    release_read = b.add(
        "explore",
        "get_release",
        {"release": release},
        _success({"notes": b.pad(170, 230), "release": release, "release_digest": digest}),
    )
    named = rng.random() < 0.5
    code, decoy = b.code(), b.code()
    while decoy == code:
        decoy = b.code()
    failure = b.add(
        "explore",
        "run_healthcheck",
        {"service": services[1]},
        _failed(b.failure_text(f"Healthcheck of {services[1]}", code)),
    )
    if named and rng.random() < 0.7:
        b.add(
            "explore",
            "run_healthcheck",
            {"service": services[2]},
            _failed(b.failure_text(f"Healthcheck of {services[2]}", decoy)),
        )
    promote = b.add(
        "action",
        "promote",
        {"release": release, "service": target},
        _success(
            {
                "notes": b.pad(170, 230),
                "promoted_digest": digest,
                "release": release,
                "service": target,
            }
        ),
        deps=[release_read, reads[target]],
    )
    alternatives = [reads[target]]
    if rng.random() < 0.4:
        alternatives.append(
            b.add(
                "explore",
                "describe_service",
                {"service": target},
                _success(
                    {"history": b.pad(240, 330), "replicas": replicas[target], "target": target}
                ),
            )
        )
    facts = {
        "replicas": str(replicas[target]),
        "error_code": code,
        "release_digest": digest,
    }
    sources = {
        "replicas": alternatives,
        "error_code": [failure],
        "release_digest": [promote, release_read],
    }
    questions = [
        f"How many replicas does service {target} run?",
        (
            f"What error code did the failed run_healthcheck call for {services[1]} return?"
            if named
            else "What error code did the failed run_healthcheck call return?"
        ),
        f"Which artifact digest was promoted for release {release} on {target}?",
    ]
    return facts, sources, questions


def _triage(b: _Builder):
    rng = b.rng
    tickets = [b.entity() for _ in range(rng.randint(4, 6))]
    while len(set(tickets)) < len(tickets):
        tickets = [b.entity() for _ in tickets]
    target = tickets[0]
    queues = dict(zip(tickets, rng.sample(b.vocabulary, len(tickets)), strict=True))
    reads: dict[str, int] = {}
    order = tickets[:]
    rng.shuffle(order)
    for ticket in order:
        comments = [{"author": b.person(), "text": b.pad(40, 70)} for _ in range(3)]
        reads[ticket] = b.add(
            "explore",
            "get_ticket",
            {"ticket_id": ticket},
            _success(
                {
                    "comments": comments,
                    "queue": f"q-{queues[ticket]}",
                    "reporter": b.person(),
                    "ticket_id": ticket,
                }
            ),
        )
    team = rng.choice(b.vocabulary)
    engineer, pager = b.person(), f"+65-{rng.randrange(6000, 9999)}-{rng.randrange(1000, 9999)}"
    oncall = b.add(
        "explore",
        "lookup_oncall",
        {"team": team},
        _success(
            {"notes": b.pad(170, 230), "oncall_engineer": engineer, "pager": pager, "team": team}
        ),
    )
    named = rng.random() < 0.5
    queries = rng.sample(b.vocabulary, 2)
    code, decoy = b.code(), b.code()
    while decoy == code:
        decoy = b.code()
    failure = b.add(
        "explore",
        "search_runbook",
        {"query": queries[0]},
        _failed(b.failure_text(f"Runbook search for {queries[0]}", code)),
    )
    if named and rng.random() < 0.7:
        b.add(
            "explore",
            "search_runbook",
            {"query": queries[1]},
            _failed(b.failure_text(f"Runbook search for {queries[1]}", decoy)),
        )
    assign = b.add(
        "action",
        "assign_ticket",
        {"ticket_id": target, "team": team},
        _success(
            {
                "notes": b.pad(170, 230),
                "pager": pager,
                "ticket": target,
                "ticket_assignee": engineer,
            }
        ),
        deps=[reads[target], oncall],
    )
    alternatives = [reads[target]]
    if rng.random() < 0.4:
        alternatives.append(
            b.add(
                "explore",
                "get_ticket_history",
                {"ticket_id": target},
                _success(
                    {"history": b.pad(240, 330), "queue": f"q-{queues[target]}", "ticket": target}
                ),
            )
        )
    facts = {"queue": f"q-{queues[target]}", "error_code": code}
    sources = {"queue": alternatives, "error_code": [failure]}
    questions = [
        f"Which queue is ticket {target} routed to?",
        (
            f"What error code did the failed search_runbook call for {queries[0]} return?"
            if named
            else "What error code did the failed search_runbook call return?"
        ),
    ]
    if rng.random() < 0.5:
        facts["assigned_to"] = engineer
        sources["assigned_to"] = [assign, oncall]
        questions.append(f"Who was ticket {target} assigned to?")
    else:
        facts["pager"] = pager
        sources["pager"] = [assign, oncall]
        questions.append(f"What pager number belongs to the on-call for team {team}?")
    return facts, sources, questions


_BUILDERS = {"ledger": _ledger, "deploy": _deploy, "triage": _triage}


def closure_cost(task_instance: Instance, indices: set[int]) -> int:
    built = replay(task_instance)
    chosen: set[int] = set()
    pending = list(indices)
    while pending:
        item = pending.pop()
        if item in chosen:
            continue
        chosen.add(item)
        pending.extend(built.ids.index(d) for d in built.store.get(built.ids[item]).depends_on)
    return sum(_episode_tokens(built.store.get(built.ids[i])) for i in chosen)


def generate_task(family: str, variant: int, index: int, seed: int) -> LiveTask:
    if family not in _BUILDERS:
        raise ValueError(f"Unknown task family {family!r}; expected one of {FAMILIES}.")
    builder = _Builder(family, variant, index, seed)
    facts, sources, questions = _BUILDERS[family](builder)
    keys = tuple(facts)
    prompt = (
        "Using only the tool results in your memory, answer: "
        + " ".join(f"({n}) {q}" for n, q in enumerate(questions, start=1))
        + f" Return a JSON object with the keys {', '.join(keys)}; "
        "use null for any value you cannot find."
    )
    draft = Instance(
        instance_id=f"{family}{variant}-s{seed}-live-{index}",
        template_id=f"{family}{variant}",
        regime="live",
        recipe=tuple(builder.finish()),
        query=prompt,
        budget=0,
        protected_indices=(),
        requirements=tuple(tuple(sorted(set(v))) for v in sources.values()),
        oracle_form="measured",
    )
    built = replay(draft)
    total = sum(_episode_tokens(built.store.get(i)) for i in built.ids)
    cheapest = min(
        closure_cost(draft, {i for part in combo for i in part})
        for combo in product(*[[(a,) for a in alts] for alts in draft.requirements])
    )
    rng = random.Random(f"budget/{seed}/{family}/{variant}/{index}")
    budget = max(int(total * rng.uniform(0.40, 0.60)), int(cheapest * 1.15))
    task = LiveTask(
        instance=Instance(**{**draft.__dict__, "budget": min(budget, total)}),
        family=family,
        answer_keys=keys,
        expected={k: str(v) for k, v in facts.items()},
        fact_sources={k: tuple(sorted(set(v))) for k, v in sources.items()},
    )
    return task


def projected_texts(task: LiveTask, retained: set[int]) -> dict[int, str]:
    """Model-visible text of each episode when retained (full) or compacted (stub)."""

    from .context import render_episode_views

    return render_episode_views(task, retained, ContextProjectionPolicy())


def validate_task(task: LiveTask) -> None:
    """Fail unless each answer is visible when its episode is retained and hidden when not."""

    count = task.instance.size
    full = projected_texts(task, set(range(count)))
    none = projected_texts(task, set())
    for key, expected in task.expected.items():
        sources = task.fact_sources[key]
        if not any(contains_value(full[i], expected) for i in sources):
            raise ValueError(
                f'Task "{task.instance.instance_id}": answer "{key}" = {expected!r} is not visible '
                f"in any retained source episode {sources}."
            )
        leaked = [i for i in sources if contains_value(none[i], expected)]
        if leaked:
            raise ValueError(
                f'Task "{task.instance.instance_id}": answer "{key}" = {expected!r} survives '
                f"compaction in the stub of episode(s) {leaked}."
            )
    for key, expected in task.expected.items():
        if contains_value(task.instance.query, expected):
            raise ValueError(
                f'Task "{task.instance.instance_id}": answer "{key}" appears in the question.'
            )


def evidence_free_ids(task: LiveTask) -> set[str]:
    """Every episode except those holding a fact and those that depend on one."""

    built = replay(task.instance)
    banned = {built.ids[i] for sources in task.fact_sources.values() for i in sources}
    changed = True
    while changed:
        changed = False
        for episode_id in built.ids:
            if episode_id not in banned and any(
                d in banned for d in built.store.get(episode_id).depends_on
            ):
                banned.add(episode_id)
                changed = True
    return {i for i in built.ids if i not in banned}


def task_digest(task: LiveTask) -> str:
    return hashlib.sha256(json.dumps(task.to_json(), sort_keys=True).encode()).hexdigest()[:16]
