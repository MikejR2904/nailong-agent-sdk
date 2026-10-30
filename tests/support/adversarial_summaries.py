"""A labeled adversarial set of summaries over scripted episodes.

Every scenario is a small group of episodes with a known outcome. For each one the set holds
faithful summaries and summaries that are wrong in one named way. Each quotes real text from
the episodes as its evidence, so the quotation checks alone cannot tell them apart. Some wrong
summaries flip the meaning without using a word the screen knows (``reworded-flip``,
``quantifier-flip``), and some faithful ones are paraphrased far from their evidence
(``far-paraphrase``): those are the cases a lexical screen is expected to get wrong, and the set
keeps them so the reported rates describe its limits rather than its best case.

``evaluate`` runs the real compactor over the set and reports, per category, how often a bad
summary is accepted and how often a faithful one is rejected.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

from nailong_agent_sdk.memory.episode_archive import InMemoryEpisodeArchive
from nailong_agent_sdk.memory.episode_models import (
    ClaimReview,
    ClaimVerdict,
    GroundedClaim,
    SemanticCompactionPolicy,
    SemanticSummaryDraft,
)
from nailong_agent_sdk.memory.episode_store import InMemoryEpisodeStore
from nailong_agent_sdk.memory.semantic_compaction import SemanticCompactor

FAITHFUL = ("quote", "paraphrase", "far-paraphrase")
BAD = (
    "status-flip",
    "negation",
    "number-change",
    "entity-swap",
    "fabricated-fact",
    "missing-evidence",
    "outside-citation",
    "omitted-failure",
    "dropped-action",
    "reworded-flip",
    "quantifier-flip",
)
REGIONS = ["eu-west-2", "ap-south-1", "us-east-2"]


@dataclass
class Case:
    category: str
    draft: SemanticSummaryDraft
    faithful: bool


@dataclass
class Metrics:
    accepted: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    total: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def record(self, category: str, accepted: bool) -> None:
        self.total[category] += 1
        self.accepted[category] += int(accepted)

    def false_acceptance(self, category: str) -> float:
        total = self.total[category]
        return self.accepted[category] / total if total else 0.0

    def false_rejection(self, category: str) -> float:
        total = self.total[category]
        return 1 - self.accepted[category] / total if total else 0.0

    def overall_false_acceptance(self) -> float:
        total = sum(self.total[name] for name in BAD)
        return sum(self.accepted[name] for name in BAD) / total if total else 0.0

    def overall_false_rejection(self) -> float:
        total = sum(self.total[name] for name in FAITHFUL)
        return 1 - sum(self.accepted[name] for name in FAITHFUL) / total if total else 0.0

    def table(self) -> dict[str, float]:
        rows = {f"FRR {name}": self.false_rejection(name) for name in FAITHFUL}
        rows.update({f"FAR {name}": self.false_acceptance(name) for name in BAD})
        return rows


def _payload(name, arguments, status, output=None, error=None):
    return {
        "call": {"id": f"c-{name}", "name": name, "arguments": arguments},
        "result": {"status": status, "output": output, "error": error},
    }


@dataclass
class Scenario:
    number: int
    episodes: list[tuple[dict, str]]
    config: str
    region: str
    other_region: str
    suite: str
    passed: int
    wrong: int
    service: str
    account: str


def build_scenario(rng: random.Random, number: int) -> Scenario:
    service = rng.choice(["billing", "search", "auth", "ingest", "export", "metrics"])
    account = rng.choice(["ci-bot", "deployer", "release-agent", "svc-runner"])
    suite = rng.choice(["unit", "api", "contract", "smoke"])
    passed = rng.randint(12, 90)
    region = rng.choice(REGIONS)
    other_region = rng.choice([item for item in REGIONS if item != region])
    config = f"settings_{number}.yaml"
    retries = rng.randint(2, 9)
    episodes = [
        (
            _payload(
                "read_file",
                {"path": config},
                "succeeded",
                {"text": f"region: {region}\nretries: {retries}\nservice: {service}"},
            ),
            f"read_file {config} succeeded: region: {region} retries: {retries} service: {service}",
        ),
        (
            _payload(
                "run_tests",
                {"suite": suite},
                "succeeded",
                {"summary": f"{passed} passed, 0 failed in the {suite} suite"},
            ),
            f"run_tests {suite} succeeded: {passed} passed, 0 failed in the {suite} suite",
        ),
        (
            _payload(
                "deploy",
                {"service": service},
                "failed",
                error=f"permission denied for service account {account}",
            ),
            f"deploy {service} failed: permission denied for service account {account}",
        ),
    ]
    return Scenario(
        number=number,
        episodes=episodes,
        config=config,
        region=region,
        other_region=other_region,
        suite=suite,
        passed=passed,
        wrong=passed + rng.randint(3, 9),
        service=service,
        account=account,
    )


def _claim(statement, ids, evidence):
    return GroundedClaim(claim=statement, source_episode_ids=ids, evidence=evidence)


def cases_for(scenario: Scenario, ids: list[str]) -> list[Case]:
    config_id, tests_id, deploy_id = ids
    config_evidence = f"region: {scenario.region}"
    tests_evidence = f"{scenario.passed} passed, 0 failed"
    deploy_evidence = f"permission denied for service account {scenario.account}"
    issue = f"{deploy_id}: deploy failed - {deploy_evidence}"
    config_claim = _claim(f"the config file sets {config_evidence}", [config_id], config_evidence)
    tests_claim = _claim(
        f"{scenario.passed} tests passed in the {scenario.suite} suite", [tests_id], tests_evidence
    )

    def draft(claims, issues=None):
        return SemanticSummaryDraft(
            summary_text="summary of the episodes",
            key_facts=claims,
            unresolved_issues=[issue] if issues is None else issues,
        )

    swapped = {
        "status-flip": [
            config_claim,
            _claim(f"the {scenario.suite} tests failed", [tests_id], tests_evidence),
        ],
        "negation": [
            config_claim,
            _claim(
                f"there was no problem with the service account {scenario.account}",
                [tests_id],
                tests_evidence,
            ),
        ],
        "number-change": [
            config_claim,
            _claim(
                f"{scenario.wrong} tests passed in the {scenario.suite} suite",
                [tests_id],
                tests_evidence,
            ),
        ],
        "entity-swap": [
            _claim(
                f"the config file sets region: {scenario.other_region}",
                [config_id],
                config_evidence,
            ),
            tests_claim,
        ],
        "fabricated-fact": [
            config_claim,
            _claim(
                f"the {scenario.suite} tests passed and the cluster was restarted afterwards",
                [tests_id],
                tests_evidence,
            ),
        ],
        "missing-evidence": [
            _claim(
                f"the config file sets {config_evidence}",
                [config_id],
                f"region: {scenario.other_region} (as recorded by the operator)",
            ),
            tests_claim,
        ],
        "outside-citation": [
            config_claim,
            _claim(f"{scenario.passed} tests passed", ["episode-9999"], tests_evidence),
        ],
        "reworded-flip": [
            config_claim,
            tests_claim,
            _claim(
                f"service account {scenario.account} had permission for the {scenario.service} "
                "service",
                [deploy_id],
                deploy_evidence,
            ),
        ],
        "quantifier-flip": [
            config_claim,
            _claim(
                f"some of the {scenario.passed} {scenario.suite} tests were skipped",
                [tests_id],
                tests_evidence,
            ),
        ],
    }
    cases = [
        Case("quote", draft([config_claim, tests_claim]), True),
        Case(
            "paraphrase",
            draft(
                [
                    _claim(
                        f"{scenario.config} has {config_evidence}", [config_id], config_evidence
                    ),
                    _claim(
                        f"all {scenario.passed} {scenario.suite} tests succeeded",
                        [tests_id],
                        tests_evidence,
                    ),
                ]
            ),
            True,
        ),
        Case(
            "far-paraphrase",
            draft(
                [
                    _claim(
                        f"everything in the {scenario.suite} suite came back green "
                        f"({scenario.passed} cases)",
                        [tests_id],
                        tests_evidence,
                    ),
                    config_claim,
                ]
            ),
            True,
        ),
    ]
    cases.extend(Case(name, draft(claims), False) for name, claims in swapped.items())
    cases.append(Case("omitted-failure", draft([config_claim, tests_claim], issues=[]), False))
    cases.append(Case("dropped-action", draft([config_claim]), False))
    return cases


class Scripted:
    summarizer_id = "adversarial"

    def __init__(self, draft):
        self._draft = draft

    def summarize(self, request):
        return self._draft


class OracleClaimJudge:
    """A stand-in for a host model: it knows each label and errs a set fraction of the time."""

    judge_id = "oracle"

    def __init__(self, mistakes: float = 0.0, seed: int = 0):
        self._rng = random.Random(seed)
        self._mistakes = mistakes
        self.expect_bad = False

    def judge_claims(self, request, reviews: list[ClaimReview]):
        verdicts = []
        for review in reviews:
            says_bad = self.expect_bad != (self._rng.random() < self._mistakes)
            verdicts.append(
                ClaimVerdict(
                    index=review.index,
                    label="contradicted" if says_bad else "entailed",
                    reason="the oracle disagrees" if says_bad else "",
                )
            )
        return verdicts


def evaluate(policy: SemanticCompactionPolicy, *, scenarios: int = 40, seed: int = 11, judge=None):
    rng = random.Random(seed)
    metrics = Metrics()
    for number in range(scenarios):
        scenario = build_scenario(rng, number)
        store = InMemoryEpisodeStore()
        records = []
        for position, (payload, description) in enumerate(scenario.episodes):
            if position == 1:
                record = store.open_action("agent", [records[0].id], content=payload)
                store.close(record.id)
            else:
                record = store.open_exploratory("agent", content=payload)
                store.close(record.id, description=description)
            records.append(store.get(record.id))
        for case in cases_for(scenario, [record.id for record in records]):
            if judge is not None:
                judge.expect_bad = not case.faithful
            compactor = SemanticCompactor(Scripted(case.draft), policy=policy, claim_judge=judge)
            result = compactor.summarize_group(
                f"group-{number}",
                [record.model_copy(update={"state": "compacted"}) for record in records],
                summary_id="summary-1",
                archive=InMemoryEpisodeArchive(),
                episode_exists=lambda _identifier: True,
            )
            metrics.record(case.category, result.summary is not None)
    return metrics
