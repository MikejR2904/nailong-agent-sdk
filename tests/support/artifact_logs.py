import hashlib
import json
import os

from nailong_agent_sdk.tools.artifacts import ArtifactStore


def long_history(root, relative_path, contents, count):
    """Write each distinct content once, then log ``count`` more versions that reuse them.

    ``contents`` maps a version number (1-based) to the text of that version; versions not
    listed repeat the nearest earlier listed text. Returns the sha256 of every version.
    """

    store = ArtifactStore(root)
    digests = {}
    for text in dict.fromkeys(contents.values()):
        store.write_text("scratch/blobs.txt", text)
        digests[text] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    log = root / ".agent-artifacts" / "history" / "writes.jsonl"
    key = relative_path.lower() if os.name == "nt" else relative_path
    with log.open("rb") as stream:
        last = sum(1 for _ in stream)
    current = None
    shas = []
    with log.open("ab") as stream:
        for number in range(1, count + 1):
            current = contents.get(number, current)
            sha = digests[current]
            shas.append(sha)
            line = {
                "write_sequence": last + number,
                "path_key": key,
                "occurrence_id": f"occ-{number:032x}",
                "artifact_id": f"sha256:{sha}",
                "relative_path": relative_path,
                "sha256": sha,
                "size_bytes": len(current),
                "kind": "draft",
                "manifest": {"agent_identity": f"agent-{number % 7}", "iteration": number},
                "written_at_utc": f"2026-01-01T00:{number // 60 % 60:02d}:{number % 60:02d}+00:00",
            }
            stream.write(json.dumps(line, separators=(",", ":")).encode("utf-8") + b"\n")
    return shas
