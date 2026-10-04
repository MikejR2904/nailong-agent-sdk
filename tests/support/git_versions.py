import subprocess

import nailong_agent_sdk.specifications.git_versioning as gv
from nailong_agent_sdk.specifications.gate import SpecificationGate
from nailong_agent_sdk.specifications.gate_models import VersionChangeKind, VersionMetadata
from nailong_agent_sdk.specifications.git_models import GitApproval
from tests.support.specs import spec, tree

M, N, P = VersionChangeKind.MAJOR, VersionChangeKind.MINOR, VersionChangeKind.PATCH


def sh(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout.strip()


def approval(action="create-specification-lock", approved=True):
    return GitApproval(
        approved=approved,
        approver_id="designer",
        reason="ok",
        action=action,
        approval_id="appr-1",
        at_utc="2026-01-01T00:00:00+00:00",
    )


def inputs(version, requirements, kind):
    specification = spec(version=version, reqs=requirements, trees=[tree()])
    graph, report = SpecificationGate().validate(specification, required_categories=set())
    metadata = VersionMetadata(
        version=version,
        change_kind=kind,
        unified_specification_hash=gv._sha256(specification.model_dump(mode="json")),
        soft_locked=True,
    )
    return specification, graph, report, metadata


def lock(service, repo, version, requirements, kind, **overrides):
    specification, graph, report, metadata = inputs(version, requirements, kind)
    metadata = metadata.model_copy(update=overrides.pop("metadata_update", {}))
    return service.create_lock(
        repo,
        overrides.pop("specification", specification),
        graph,
        report,
        metadata,
        overrides.pop("approval", approval()),
    )
