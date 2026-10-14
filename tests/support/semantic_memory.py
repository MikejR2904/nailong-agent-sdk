from nailong_agent_sdk.memory.episode_archive import InMemoryEpisodeArchive
from nailong_agent_sdk.memory.episode_models import SemanticCompactionPolicy
from nailong_agent_sdk.memory.episode_store import FileEpisodeStore, InMemoryEpisodeStore
from nailong_agent_sdk.memory.semantic_compaction import (
    ExtractiveEpisodeSummarizer,
    SemanticCompactor,
)


def payload(index, status="succeeded", error=None, words=60, args=None):
    return {
        "call": {
            "id": f"c{index}",
            "name": "read_file",
            "arguments": args if args is not None else {"path": f"f{index}.txt"},
        },
        "result": {
            "status": status,
            "output": {"text": f"contents {index} " + "lorem ipsum " * words},
            "error": error,
        },
    }


def fill(store, count=8, *, failed=(), actions=(), start=0):
    ids = []
    originals = {}
    for index in range(start, start + count):
        status = "failed" if index in failed else "succeeded"
        content = payload(index, status, "no such file" if status == "failed" else None)
        record = store.open_exploratory("agent", content=content)
        store.close(record.id, description=f"read_file f{index}.txt {status}")
        ids.append(record.id)
        originals[record.id] = content
    for offset, parent in enumerate(actions):
        content = payload(100 + start + offset)
        action = store.open_action("agent", [ids[parent]], content=content)
        store.close(action.id)
        ids.append(action.id)
        originals[action.id] = content
    return ids, originals


def compactor_for(summarizer=None, *, archive=None, policy=None, judge=None):
    return SemanticCompactor(
        summarizer or ExtractiveEpisodeSummarizer(),
        archive=archive,
        policy=policy or SemanticCompactionPolicy(consolidate_when_summaries=0),
        judge=judge,
    )


def semantic_store(summarizer=None, *, archive=None, policy=None, judge=None, **options):
    return InMemoryEpisodeStore(
        semantic=compactor_for(summarizer, archive=archive, policy=policy, judge=judge), **options
    )


def file_store(run_root, summarizer=None, *, policy=None, judge=None, **options):
    return FileEpisodeStore(
        run_root, semantic=compactor_for(summarizer, policy=policy, judge=judge), **options
    )


def compact_all(store, query="nothing"):
    return store.compact(1, relevance_query=query)


class Scripted:
    summarizer_id = "scripted"

    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    def summarize(self, request):
        self.requests.append(request)
        return self.handler(request)


def default_draft(request):
    return ExtractiveEpisodeSummarizer().summarize(request)


class CountingArchive(InMemoryEpisodeArchive):
    def __init__(self):
        super().__init__()
        self.puts = 0

    def put(self, record):
        self.puts += 1
        return super().put(record)
