from nailong_agent_sdk.state.project_state_models import (
    StageStateSchema,
    StateAuthority,
    StateEvidence,
    StateTransition,
    StateTransitionKind,
)

SCHEMA = StageStateSchema(schema_id="s-v1", stage="design")
T = StateTransitionKind


def evidence(tag="e"):
    return [StateEvidence(evidence_id=tag, kind="test", content_hash="h")]


def transition(kind, payload, actor=StateAuthority.HARNESS, action="a1"):
    return StateTransition(
        kind=kind, actor=actor, action_id=action, payload=payload, evidence=evidence(action)
    )
