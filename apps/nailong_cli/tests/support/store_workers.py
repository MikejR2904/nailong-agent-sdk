"""Child-process helpers for the multi-process and crash tests; run as a script."""

import sys
import time
from pathlib import Path

from nailong_cli import wiki as wiki_module
from nailong_cli.discoveries import DiscoveryStore
from nailong_cli.wiki import Wiki

mode, project = sys.argv[1], Path(sys.argv[2])
wiki = Wiki(project / ".nailong" / "wiki", project)
discoveries = DiscoveryStore(project / ".nailong" / "discoveries", project)

if mode == "wiki-page":
    wiki.write(sys.argv[3], f"page {sys.argv[3]}", "from a process")
elif mode == "wiki-race":
    try:
        wiki.write("shared", f"from {sys.argv[3]}", "race", base_revision=1)
        print("won")
    except ValueError as error:
        print("lost: " + str(error))
elif mode == "discovery":
    result = discoveries.publish(
        f"topic.{sys.argv[3]}", f"Claim {sys.argv[3]}.", [{"path": "src.py"}], owner=sys.argv[3]
    )
    print(result["id"])
elif mode == "wiki-crash":
    real = wiki_module._atomic_write

    def stall_on_the_page(path, text):
        if path.name == "plan.md":
            print("at-crash-point", flush=True)
            time.sleep(120)
        real(path, text)

    wiki_module._atomic_write = stall_on_the_page
    wiki.write("plan", "version two", "interrupted", base_revision=1)
