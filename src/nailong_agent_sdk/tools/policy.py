# Copyright (c) 2026 David Michael Indraputra

"""Deny-by-default capability policy for harness tools."""

from __future__ import annotations

import fnmatch
from enum import StrEnum
from pathlib import Path

from pydantic import Field

from ..foundations.contracts import StrictModel
from .approvals import ApprovalRequest, ApprovalStatus

# Credential and key-material locations that are always denied, independent of
# role, capability grant, or approval state. This is defense-in-depth against
# prompt-injection-directed reads of host secrets; it cannot be widened by a
# CapabilityGrant because it is checked before any grant is consulted.
SENSITIVE_PATH_PATTERNS: tuple[str, ...] = (
    "*/.ssh/*",
    "*/.aws/credentials",
    "*/.aws/config",
    "*/.config/gcloud/*",
    "*/.azure/*",
    "*/.gnupg/*",
    "*/.docker/config.json",
    "*/.kube/config",
    "*/.netrc",
    "*/id_rsa",
    "*/id_ed25519",
    "*/id_ecdsa",
    "*/id_dsa",
    "*/.env",
    "*/.env.*",
    "*/.npmrc",
    "*/.pypirc",
    "*/.git-credentials",
    "*/.pgpass",
    "*.pem",
    "*.p12",
    "*.pfx",
)


class SideEffectClass(StrEnum):
    READ_ONLY = "read-only"
    MUTATING = "mutating"
    PROCESS = "process"
    DESTRUCTIVE = "destructive"


class CapabilityGrant(StrictModel):
    role: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)


class PolicyDecision(StrictModel):
    allowed: bool
    reason: str
    approval_required: bool = False
    approval_request: ApprovalRequest | None = None


class CapabilityPolicy:
    """Check role, capability, path containment, and typed approval state."""

    def __init__(self, grants: list[CapabilityGrant]) -> None:
        self._grants = {grant.role: grant for grant in grants}

    def read_scope(self, role: str) -> tuple[str, ...]:
        grant = self._grants.get(role)
        if grant is None or "filesystem.read" not in grant.capabilities:
            return ()
        return tuple(grant.allowed_paths)

    def evaluate(
        self,
        *,
        role: str,
        capability: str,
        side_effect: SideEffectClass,
        run_root: Path,
        requested_paths: list[str] = (),
        approval: ApprovalRequest | None = None,
    ) -> PolicyDecision:
        for requested_path in requested_paths:
            sensitive_match = self._sensitive_pattern_match(requested_path, run_root)
            if sensitive_match is not None:
                return PolicyDecision(
                    allowed=False,
                    reason=(
                        f'Access denied: "{requested_path}" matches the built-in sensitive-path '
                        f'pattern "{sensitive_match}" and cannot be authorized by any grant.'
                    ),
                )
        grant = self._grants.get(role)
        if grant is None or capability not in grant.capabilities:
            return PolicyDecision(
                allowed=False,
                reason=f'Role "{role}" lacks capability "{capability}".',
            )
        for requested_path in requested_paths:
            if not self._path_is_allowed(requested_path, grant.allowed_paths, run_root):
                return PolicyDecision(
                    allowed=False,
                    reason=f'Path "{requested_path}" is outside the declared capability scope.',
                )
        if side_effect is SideEffectClass.READ_ONLY:
            return PolicyDecision(allowed=True, reason="Read-only capability is authorized.")
        if approval is None:
            return PolicyDecision(
                allowed=False,
                reason=f'Capability "{capability}" requires a typed approval decision.',
                approval_required=True,
            )
        if approval.capability != capability:
            return PolicyDecision(
                allowed=False,
                reason="Approval capability does not match the requested action.",
            )
        if approval.status is ApprovalStatus.APPROVED:
            return PolicyDecision(allowed=True, reason="Typed approval is present.")
        if approval.status is ApprovalStatus.REJECTED:
            return PolicyDecision(
                allowed=False,
                reason=approval.decision_reason or "Typed approval was rejected.",
            )
        return PolicyDecision(
            allowed=False,
            reason="Typed approval is still pending.",
            approval_required=True,
        )

    @staticmethod
    def _sensitive_pattern_match(requested_path: str, run_root: Path) -> str | None:
        """Return the matched built-in deny pattern, or ``None`` if none applies.

        A directory-scoped request (``grep``/``glob`` over a whole folder) may
        target a root such as ``.ssh`` without naming a file beneath it, so both
        the bare resolved path and a trailing-slash form are checked against
        patterns like ``*/.ssh/*``.
        """

        resolved = str((run_root.resolve() / requested_path).resolve()).replace("\\", "/").lower()
        candidates = (resolved, resolved + "/")
        for pattern in SENSITIVE_PATH_PATTERNS:
            if any(fnmatch.fnmatchcase(candidate, pattern.lower()) for candidate in candidates):
                return pattern
        return None

    @staticmethod
    def _path_is_allowed(requested_path: str, allowed_paths: list[str], run_root: Path) -> bool:
        if not allowed_paths:
            return False
        root = run_root.resolve()
        candidate = (root / requested_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return False
        for allowed_path in allowed_paths:
            allowed = (root / allowed_path).resolve()
            try:
                candidate.relative_to(allowed)
                return True
            except ValueError:
                continue
        return False
