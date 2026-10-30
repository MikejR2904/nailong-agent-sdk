from trust_bench import aggregate as agg


def counts(total, present, phantom=0, comparable=None, agree=None, recoverable=None):
    comparable = present if comparable is None else comparable
    return {
        "total": total,
        "present": present,
        "phantom": phantom,
        "comparable": comparable,
        "agree": comparable if agree is None else agree,
        "recoverable": present if recoverable is None else recoverable,
    }


def clean_row(case_id, outcomes, *, family="doc", arm="sdk", complete=True, present=4):
    return {
        "case_id": case_id,
        "family": family,
        "arm": arm,
        "fault": None,
        "status": "completed",
        "trace": {
            "calls_complete": complete,
            "counts": {kind: counts(4, present) for kind in agg.KINDS},
            "artifacts": [
                {"path": f"a{i}", "outcome": o, "source_precision": 1.0, "source_recall": 0.5}
                for i, o in enumerate(outcomes)
            ],
        },
        "degradation": {
            "drop-5%": {
                "strict": {"correct": 1, "wrong": 0, "abstain": 1},
                "lenient": {"correct": 2, "wrong": 0, "abstain": 0},
            }
        },
        "diagnoses": {},
    }


def faulty_row(case_id, fault_class, tops, *, arm="sdk", has_truth=True, unsupported=None):
    def entry(top1):
        return {
            "has_truth": has_truth,
            "top1": top1,
            "top3": top1,
            "reciprocal_rank": 1.0 if top1 else 0.0,
            "agent_ok": top1,
            "abstained": False,
            "unsupported": (not has_truth) if unsupported is None else unsupported,
            "evaluations": 3,
        }

    return {
        "case_id": case_id,
        "family": "doc",
        "arm": arm,
        "fault": {"class": fault_class, "operator": "x", "target": None, "label": "l"},
        "status": "completed",
        "diagnoses": {method: entry(top1) for method, top1 in tops.items()},
    }


def verify_row(case_id, quiet, *, arm="sdk", gated=False, ungated=True, **overrides):
    row = {
        "kind": "verify",
        "case_id": case_id,
        "family": "doc",
        "arm": arm,
        "fault": None
        if quiet is None
        else {"class": "F2", "operator": "x", "target": None, "label": "l"},
        "delivered": True,
        "quiet": quiet,
        "format_ok": True,
        "visible_ok": True,
        "ungated": {"status": "completed", "accepted": ungated},
        "gated": {
            "status": "completed" if gated else "failed",
            "accepted": gated,
            "reason": "",
            "record": {"accepted": gated},
            "record_binds": True,
        },
        "same_bytes": True,
        "recorder_agrees": True,
    }
    row.update(overrides)
    return row


def attack_row(gate, attack, *, success, arm="sdk", attempted=True, defective=True):
    return {
        "kind": "attack",
        "case_id": "doc-1",
        "family": "doc",
        "arm": arm,
        "gate": gate,
        "attack": attack,
        "fault": "env_corrupt:digit_swap",
        "attempted": attempted,
        "defective": defective,
        "accepted": success,
        "success": success,
    }


def authority_ticket(
    label="H",
    assigned="H",
    *,
    executed=True,
    bad=False,
    approved=True,
    rule="R3",
    pair=None,
    member=None,
    named=True,
    recomputable=True,
    decided_class=None,
):
    decider = {"D": "bench-validator", "M": "bench-model-judge", "H": "bench-reviewer"}
    cls = decided_class or assigned
    decision = {"class": cls, "decider": decider[cls], "rule": rule, "approved": approved}
    return {
        "id": "t1",
        "pair": pair,
        "member": member,
        "bad": bad,
        "label": label,
        "label_rule": "R3",
        "assigned": assigned,
        "assigned_rule": rule,
        "decisions": [decision],
        "executed": executed,
        "launched": True,
        "status": "completed",
        "native": {
            "class": cls if named else None,
            "decider": decider[cls] if named else None,
            "rule": rule if named else None,
            "approved": approved if named else None,
        },
        "meta_found": recomputable,
        "recomputed": {"class": cls, "rule": rule} if recomputable else None,
    }


def authority_row(tickets, *, arm="sdk", allocation="rule", log_intact=True):
    return {
        "kind": "authority",
        "case_id": "ops-1",
        "arm": arm,
        "allocation": allocation,
        "log_intact": log_intact,
        "tickets": list(tickets),
    }
