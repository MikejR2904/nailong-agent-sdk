import json

from nailong_agent_sdk.agent.retention import (
    read_tombstones,
    tombstone_path,
    tombstone_signed_message,
)
from nailong_agent_sdk.agent.retention_anchor import read_anchor
from tests.support.mcp_cases import scripted_run
from tests.support.mcp_server import Server


def test_the_inventory_reports_every_class_and_leaves_the_run_root_untouched(tmp_path):
    instance = Server(tmp_path, approver=True)
    try:
        for name in ("inv-a", "inv-b"):
            scripted_run(instance, name)
        before = sorted(path.name for path in instance.run_root.rglob("*"))
        report = instance.call("inventory_retention", {"older_than_seconds": 0.001})
        assert report["ok"], report
        inventory = report["inventory"]
        classes = {item["resource_class"]: item for item in inventory["classes"]}
        assert {"telemetry", "audit-transcript"} <= set(classes)
        assert classes["telemetry"]["prunable"] == 2
        assert inventory["total_bytes"] == sum(item["bytes"] for item in inventory["classes"])
        assert inventory["policy"]["older_than_seconds"] == 0.001
        listed = {item["resource_id"] for item in inventory["items"]}
        assert {"inv-a", "inv-b"} <= listed
        bounded = instance.call(
            "inventory_retention", {"older_than_seconds": 0.001, "max_items": 1}
        )
        assert len(bounded["inventory"]["items"]) == 1
        assert bounded["inventory"]["items_omitted"] == inventory["total_items"] - 1
        refused = instance.as_approver("inventory_retention", {"older_than_seconds": 1})
        assert refused["errors"][0]["type"] == "OperationNotPermitted"
        invalid = instance.call("inventory_retention", {"older_than_seconds": 0})
        assert invalid["ok"] is False and "older_than_seconds" in json.dumps(invalid["errors"])
        assert sorted(path.name for path in instance.run_root.rglob("*")) == before
    finally:
        instance.stop()


def test_the_service_anchors_its_tombstone_log_where_the_operator_says_and_checks_it(tmp_path):
    anchor = tmp_path / "off-host" / "tombstones.anchor.json"
    base = tmp_path / "service"
    base.mkdir()
    instance = Server(base, extra_environment={"AGENT_RUNTIME_RETENTION_ANCHOR_PATH": str(anchor)})
    try:
        for name in ("anc-a", "anc-b", "anc-c"):
            scripted_run(instance, name)
        first = instance.call(
            "prune_runs", {"older_than_seconds": 0.001, "dry_run": False, "max_runs": 2}
        )
        assert first["ok"], first
        assert first["report"]["anchor"]["written"] is True
        assert first["report"]["anchor"]["entries"] == 2
        assert read_anchor(anchor).entries == 2
        log = tombstone_path(instance.run_root)
        lines = log.read_text("utf-8").splitlines()
        log.write_text(lines[0] + "\n", encoding="utf-8")
        assert len(read_tombstones(instance.run_root)) == 1
        refused = instance.call("prune_runs", {"older_than_seconds": 0.001, "dry_run": False})
        assert refused["ok"] is False
        message = refused["errors"][0]["message"]
        assert "does not match the anchor" in message and "truncated" in message
        assert "Nothing was pruned" in message
        assert instance.call("get_telemetry_events", {"run_id": "anc-c"})["events"]
        dry = instance.call("prune_runs", {"older_than_seconds": 0.001})
        assert dry["report"]["anchor"]["verdict"] == "truncated"
        assert dry["report"]["problems"][0]["run_id"] == "(tombstone log)"
    finally:
        instance.stop()


def test_the_service_signs_the_tombstones_it_writes_when_it_is_told_to(tmp_path):
    from nailong_agent_sdk.observability.ed25519 import Ed25519Signer

    key = tmp_path / "ed.key"
    signer = Ed25519Signer.generate("service")
    key.write_text(signer.private_key_hex(), encoding="utf-8")
    base = tmp_path / "service"
    base.mkdir()
    instance = Server(
        base,
        extra_environment={
            "AGENT_RUNTIME_CHECKPOINT_DIR": str(tmp_path / "off-host" / "checkpoints"),
            "AGENT_RUNTIME_CHECKPOINT_KEY_FILE": str(key),
            "AGENT_RUNTIME_CHECKPOINT_ALGORITHM": "ed25519",
            "AGENT_RUNTIME_CHECKPOINT_SIGNER_ID": "service",
            "AGENT_RUNTIME_CHECKPOINT_SIGNED_TOMBSTONES": "1",
        },
    )
    try:
        scripted_run(instance, "sign-a")
        report = instance.call("prune_runs", {"older_than_seconds": 0.001, "dry_run": False})
        assert report["ok"], report
        (stone,) = read_tombstones(instance.run_root)
        assert stone.signer_id == "service" and stone.signature
        assert signer.verify(tombstone_signed_message(stone), stone.signature)
    finally:
        instance.stop()
