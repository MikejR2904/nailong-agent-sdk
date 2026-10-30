# Copyright (c) 2026 David Michael Indraputra

"""A deterministic screen for claims that contradict or outrun the evidence they cite.

It compares a claim with the verbatim excerpt it quotes by word overlap, opposed outcome
words, negation and numbers. It is a heuristic and not entailment: it misses a contradiction
that uses different words and it rejects a paraphrase that shares too few of them. Its
verdicts are recorded as ``heuristic-entailment-screen``, never as a judge's.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from .episode_models import ClaimReview, ClaimVerdict

SCREEN_ID = "lexical-entailment-screen"
SCREEN_VERSION = "1"

_TOKEN = re.compile(r"[a-z0-9_]+(?:[.][a-z0-9_]+)*")
_STOP = frozenset(
    "a an and are as at be been but by for from had has have in into is it its of on or "
    "that the their then there these this those to was were which while with within".split()
)
_POSITIVE = frozenset(
    "succeeded success successful successfully passed pass ok completed complete created found "
    "available enabled allowed granted accepted opened started resolved fixed".split()
)
_NEGATIVE = frozenset(
    "failed failure error errored denied refused missing absent unavailable disabled forbidden "
    "blocked timeout timedout unable rejected crashed broken aborted invalid unresolved "
    "cancelled".split()
)
_ZERO_COUNT = re.compile(
    r"\b(?:0|zero|no)\s+(?:\w+\s+)?(?:failed|failures?|errors?|failing|denied|timeouts?)\b"
)
_NEGATIONS = frozenset(
    "not no never without cannot cant isnt wasnt didnt doesnt dont wont wouldnt shouldnt "
    "couldnt hasnt havent".split()
)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold().replace("'", "").replace("’", ""))


class HeuristicEntailmentScreen:
    screen_id = SCREEN_ID
    version = SCREEN_VERSION

    def __init__(self, *, min_support: float = 0.4, negation_overlap: float = 0.4) -> None:
        if not 0 < min_support <= 1 or not 0 < negation_overlap <= 1:
            raise ValueError("min_support and negation_overlap must be in (0, 1].")
        self._min_support = min_support
        self._negation_overlap = negation_overlap

    def review(self, reviews: Sequence[ClaimReview]) -> list[ClaimVerdict]:
        return [self._one(item) for item in reviews]

    def _one(self, review: ClaimReview) -> ClaimVerdict:
        claim = _tokens(review.statement)
        evidence = set(_tokens(review.evidence))
        polarity = set(_tokens(_ZERO_COUNT.sub(" ", review.evidence.casefold())))
        support = evidence.union(*(set(_tokens(text)) for text in review.source_excerpts.values()))
        claim_set = set(claim)
        content = [
            token
            for token in claim
            if len(token) >= 3 and token not in _STOP and token not in _NEGATIONS
        ]
        overlap = sum(1 for token in content if token in support) / len(content) if content else 1.0
        claim_positive, claim_negative = claim_set & _POSITIVE, claim_set & _NEGATIVE
        evidence_positive, evidence_negative = polarity & _POSITIVE, polarity & _NEGATIVE
        if claim_positive and not claim_negative and evidence_negative and not evidence_positive:
            return self._verdict(
                review,
                "contradicted",
                f"the claim says {sorted(claim_positive)[0]} but its evidence says "
                f"{sorted(evidence_negative)[0]}",
            )
        if claim_negative and not claim_positive and evidence_positive and not evidence_negative:
            return self._verdict(
                review,
                "contradicted",
                f"the claim says {sorted(claim_negative)[0]} but its evidence says "
                f"{sorted(evidence_positive)[0]}",
            )
        if overlap >= self._negation_overlap and bool(claim_set & _NEGATIONS) != bool(
            evidence & _NEGATIONS
        ):
            return self._verdict(
                review, "contradicted", "the claim and its evidence differ in negation"
            )
        numbers = {token for token in claim_set if any(character.isdigit() for character in token)}
        missing = sorted(numbers - support)
        if missing:
            return self._verdict(
                review, "unsupported", f"the claim states {missing[0]}, which no cited text holds"
            )
        if content and overlap < self._min_support:
            return self._verdict(
                review,
                "unsupported",
                f"only {overlap:.0%} of the claim's words occur in the cited text "
                f"(needs {self._min_support:.0%})",
            )
        return ClaimVerdict(index=review.index, label="entailed")

    @staticmethod
    def _verdict(
        review: ClaimReview,
        label: Literal["entailed", "contradicted", "unsupported", "unclear"],
        reason: str,
    ) -> ClaimVerdict:
        return ClaimVerdict(index=review.index, label=label, reason=reason)
