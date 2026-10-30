# Copyright (c) 2026 David Michael Indraputra

"""The public authority policy: which class of decider a consequential action needs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).with_name("policy.json")
CLASSES = ("D", "M", "H")
RANK = {"D": 0, "M": 1, "H": 2}
AUTHORITY = {"D": "deterministic", "M": "model-mediated", "H": "human"}
FROM_AUTHORITY = {value: key for key, value in AUTHORITY.items()}
DECIDER = {"D": "bench-validator", "M": "bench-model-judge", "H": "bench-reviewer"}
NON_RULES = ("default", "all-human", "auto-approve", "random", "model")


@dataclass(frozen=True)
class Allocation:
    cls: str
    rule: str


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    for rule in policy["rules"]:
        if rule["class"] not in CLASSES:
            raise ValueError(f'rule {rule["id"]} names the unknown class "{rule["class"]}".')
    return policy


POLICY = load_policy()


def derive(request: Mapping[str, Any], policy: Mapping[str, Any] = POLICY) -> dict[str, Any]:
    """The features the rules read, computed from the raw fields of a request; None if unknown."""
    recipient, amount = request.get("recipient"), request.get("amount")
    audience = None
    if isinstance(recipient, str) and "@" in recipient:
        domain = recipient.rsplit("@", 1)[1]
        audience = "internal" if domain in policy["internal_domains"] else "external"
    stakes = None
    if isinstance(amount, int | float) and not isinstance(amount, bool):
        stakes = "high" if amount >= policy["high_amount"] else "low"
    return {
        "effect": request.get("effect"),
        "audience": audience,
        "stakes": stakes,
        "snapshot": request.get("snapshot"),
        "validator": request.get("validator"),
    }


def evaluate(request: Mapping[str, Any], policy: Mapping[str, Any] = POLICY) -> Allocation:
    features = derive(request, policy)
    for rule in policy["rules"]:
        conditions = {k: v for k, v in rule.items() if k not in {"id", "class", "description"}}
        if all(features.get(name) == value for name, value in conditions.items()):
            return Allocation(rule["class"], rule["id"])
    return Allocation(policy["default"]["class"], policy["default"]["rule"])


def rule_ids(policy: Mapping[str, Any] = POLICY) -> frozenset[str]:
    return frozenset([rule["id"] for rule in policy["rules"]] + [policy["default"]["rule"]])


def describe(policy: Mapping[str, Any] = POLICY) -> str:
    """The policy as text, for a model asked to apply it."""
    lines = [
        f"Policy {policy['policy_id']} version {policy['version']}.",
        "Each action needs the weakest class of decider that satisfies its rule:",
        *(f"{key}: {text}." for key, text in policy["classes"].items()),
        f"A recipient is internal when its domain is one of {policy['internal_domains']}.",
        f"Stakes are high when the amount is at least {policy['high_amount']}.",
        "Rules, first match wins:",
    ]
    for rule in policy["rules"]:
        conditions = ", ".join(
            f"{k}={v}" for k, v in rule.items() if k not in {"id", "class", "description"}
        )
        lines.append(f"{rule['id']}: if {conditions} then {rule['class']} ({rule['description']}).")
    default = policy["default"]
    lines.append(f"Otherwise {default['class']}: {default['description']}.")
    return "\n".join(lines)
