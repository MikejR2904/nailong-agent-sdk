import re
from pathlib import Path

SOURCE = Path("src/nailong_agent_sdk")

OWNERS = {
    "RECORDER_": ("observability/recorder/",),
    "TIMESTAMP_": ("observability/timestamp.py",),
    "KEYRING_": ("observability/keyring.py",),
    "EVIDENCE_PROFILE_": ("agent/evidence_profile/",),
    "ED25519_": ("observability/ed25519.py",),
    "AUDIT_": ("observability/audit_log.py",),
    "TREE_HEAD_": ("observability/transparency.py",),
    "WITNESS_": ("observability/witness.py",),
    "RUN_CHECKPOINT_": (
        "observability/run_checkpoints.py",
        "observability/sinks.py",
        "agent/run_checkpointer.py",
    ),
}


def raised_codes(prefix: str) -> dict[str, set[str]]:
    pattern = re.compile(rf'"({prefix}[A-Z0-9_]+)"')
    found: dict[str, set[str]] = {}
    for path in SOURCE.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        relative = path.relative_to(SOURCE).as_posix()
        for code in pattern.findall(path.read_text(encoding="utf-8")):
            found.setdefault(relative, set()).add(code)
    return found


def test_an_error_code_prefix_names_the_module_that_raises_it():
    strays = []
    for prefix, owners in OWNERS.items():
        for relative, codes in raised_codes(prefix).items():
            if not relative.startswith(owners):
                strays.append((prefix, relative, sorted(codes)))
    assert strays == [], (
        "These modules raise a code whose prefix belongs to another module, so the code no "
        f"longer points at the file to debug: {strays}"
    )
