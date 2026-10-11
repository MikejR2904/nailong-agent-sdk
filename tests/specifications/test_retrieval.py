import http.server
import json
import threading
import time

import pytest

from nailong_agent_sdk.observability.metrics import register_standard_metric_definitions
from nailong_agent_sdk.observability.telemetry_models import TelemetryContext
from nailong_agent_sdk.observability.telemetry_store import TelemetryStore
from nailong_agent_sdk.specifications.retrieval import (
    DeterministicLexicalRetrievalIndex,
    GroundedRetrievalService,
    InMemoryRetrievalCache,
    QdrantRetrievalIndex,
    RedisRetrievalCache,
    RetrievalTelemetrySink,
    specification_retrieval_documents,
)
from nailong_agent_sdk.specifications.retrieval_models import (
    RetrievalCandidate,
    RetrievalFailureMode,
    RetrievalQuery,
    RetrievalResult,
    RetrievalStatus,
)
from tests.support.specs import node, sref, tree

F, P = "functional", "ppa"


def trees():
    return [
        tree(
            "d1",
            [
                node("n1", "the adder shall add two operands"),
                node("n2", "reset clears the counter"),
            ],
            F,
        ),
        tree(
            "d2",
            [node("n1", "power budget is 5 mW at 1 GHz", doc="d2", digest="b" * 64)],
            P,
            digest="b" * 64,
        ),
    ]


def query(text="adder operands", categories=(F,), limit=8, snapshot="snap"):
    return RetrievalQuery(
        snapshot_id=snapshot, query_text=text, allowed_categories=list(categories), limit=limit
    )


def service(**kwargs):
    index = DeterministicLexicalRetrievalIndex()
    svc = GroundedRetrievalService(index, **kwargs)
    svc.index_snapshot("snap", trees())
    return svc, index


def test_documents_are_deterministic_and_skip_blank_nodes():
    docs = specification_retrieval_documents(
        "snap", trees() + [tree("d3", [node("n9", "   ", doc="d3")], F)]
    )
    assert [(d.document_id, d.node_id) for d in docs] == [("d1", "n1"), ("d1", "n2"), ("d2", "n1")]
    again = specification_retrieval_documents("snap", reversed(trees()))
    assert [d.stable_id for d in docs] == [d.stable_id for d in again]
    with pytest.raises(ValueError, match="snapshot_id must be non-empty"):
        specification_retrieval_documents("", trees())
    assert len({d.stable_id for d in docs}) == 3


def test_lexical_index_filters_ranks_and_limits():
    svc, index = service()
    result = svc.retrieve(query("adder operands counter"))
    assert [(c.document_id, c.node_id) for c in result.candidates] == [
        ("d1", "n1"),
        ("d1", "n2"),
    ] or [(c.document_id, c.node_id) for c in result.candidates] == [("d1", "n1")]
    assert (
        result.status is RetrievalStatus.RETRIEVED and result.backend == "deterministic-lexical-v1"
    )
    both = svc.retrieve(query("power adder", categories=(F, P)))
    assert {c.document_id for c in both.candidates} == {"d1", "d2"}
    assert svc.retrieve(query("power", categories=(F,))).candidates == []
    assert svc.retrieve(query("adder", snapshot="other")).candidates == []
    assert len(svc.retrieve(query("the", categories=(F, P), limit=1)).candidates) <= 1
    with pytest.raises(ValueError):
        RetrievalQuery(snapshot_id="s", query_text="x", allowed_categories=[F, F])
    with pytest.raises(ValueError):
        RetrievalQuery(snapshot_id="s", query_text="x", allowed_categories=[F], limit=101)
    digest_a = query("x").query_digest
    assert digest_a == query("x").query_digest != query("y").query_digest


def test_cache_hit_validation_and_poisoned_entries():
    cache = InMemoryRetrievalCache()
    svc, _ = service(cache=cache)
    q = query("adder operands")
    first = svc.retrieve(q)
    second = svc.retrieve(q)
    assert first.status is RetrievalStatus.RETRIEVED and second.status is RetrievalStatus.CACHE_HIT
    assert second.candidates == first.candidates
    poisoned = RetrievalResult(
        status=RetrievalStatus.RETRIEVED,
        query_digest=q.query_digest,
        backend="evil",
        candidates=[
            RetrievalCandidate(
                snapshot_id="OTHER",
                category=F,
                document_id="d1",
                node_id="n1",
                source=sref(),
                score=99.0,
                backend_id="x",
            ),
            RetrievalCandidate(
                snapshot_id="snap",
                category=P,
                document_id="d2",
                node_id="n1",
                source=sref(doc="d2"),
                score=98.0,
                backend_id="y",
            ),
            RetrievalCandidate(
                snapshot_id="snap",
                category=F,
                document_id="d1",
                node_id="n2",
                source=sref(),
                score=1.0,
                backend_id="z",
            ),
        ],
    )
    cache.set(q.query_digest, poisoned, 60)
    filtered = svc.retrieve(q)
    assert [c.backend_id for c in filtered.candidates] == ["z"]
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        cache.set("k", first, 0)
    with pytest.raises(ValueError, match="cache_ttl_seconds must be positive"):
        GroundedRetrievalService(DeterministicLexicalRetrievalIndex(), cache_ttl_seconds=0)
    digest_mismatch = poisoned.model_copy(update={"query_digest": "other"})
    cache.set(q.query_digest, digest_mismatch, 60)
    with pytest.raises(ValueError, match="query digest does not match"):
        svc.retrieve(q)


def test_backend_failure_modes():
    class Broken:
        backend_name = "broken"

        def upsert(self, documents):
            pass

        def search(self, query):
            raise ConnectionError("index offline")

    svc = GroundedRetrievalService(Broken())
    fallback = svc.retrieve(query())
    assert fallback.status is RetrievalStatus.UNAVAILABLE and fallback.candidates == []
    assert fallback.failure_reason == "ConnectionError: index offline"
    with pytest.raises(ConnectionError, match="index offline"):
        svc.retrieve(query(), failure_mode=RetrievalFailureMode.REJECT)
    cached = GroundedRetrievalService(Broken(), cache=InMemoryRetrievalCache())
    cached.retrieve(query())
    assert cached._cache.get(query().query_digest) is None


def test_cache_outage_does_not_become_a_retrieval_outage():
    class DownCache:
        def get(self, key):
            raise ConnectionError("redis down")

        def set(self, key, result, ttl_seconds):
            raise ConnectionError("redis down")

    svc, _ = service(cache=DownCache())
    try:
        result = svc.retrieve(query("adder operands"))
        outcome = ("returned", result.status.value, len(result.candidates))
    except Exception as error:
        outcome = (type(error).__name__, str(error))
    assert outcome[0] == "returned", outcome


def test_cache_set_failure_does_not_discard_a_computed_result():
    class WriteOnlyBroken:
        def get(self, key):
            return None

        def set(self, key, result, ttl_seconds):
            raise OSError("cache write failed")

    svc, _ = service(cache=WriteOnlyBroken())
    try:
        result = svc.retrieve(query("adder operands"))
        outcome = ("returned", len(result.candidates))
    except Exception as error:
        outcome = (type(error).__name__, str(error))
    assert outcome[0] == "returned", outcome


def test_resolve_admits_only_locally_verified_candidates():
    svc, _ = service()
    local = trees()
    good = svc.retrieve(query("adder operands"))
    resolved = svc.resolve(good, local)
    assert [n.node_id for n in resolved.nodes] == ["n1"] and resolved.rejected_candidate_ids == []
    forged_hash = good.candidates[0].model_copy(
        update={"source": good.candidates[0].source.model_copy(update={"source_hash": "c" * 64})}
    )
    wrong_loc = good.candidates[0].model_copy(
        update={"source": good.candidates[0].source.model_copy(update={"location": "line:999"})}
    )
    wrong_cat = good.candidates[0].model_copy(update={"category": P})
    unknown = good.candidates[0].model_copy(update={"node_id": "ghost"})
    duplicate = good.candidates[0]
    result = good.model_copy(
        update={"candidates": [forged_hash, wrong_loc, wrong_cat, unknown, duplicate, duplicate]}
    )
    out = svc.resolve(result, local)
    assert len(out.rejected_candidate_ids) == 4
    assert [n.node_id for n in out.nodes] == ["n1", "n1"]


def test_telemetry_sink_writes_digest_only_events(tmp_path):
    store = TelemetryStore(tmp_path)
    try:
        sink = RetrievalTelemetrySink(store, lambda q: TelemetryContext(run_id="run-1"))
        svc, _ = service(cache=InMemoryRetrievalCache(), telemetry_sink=sink)
        q = query("adder operands")
        try:
            svc.retrieve(q)
            svc.retrieve(q)
            registered = "no registration needed"
        except Exception as error:
            registered = f"{type(error).__name__}: {str(error)[:140]}"
        if registered != "no registration needed":
            register_standard_metric_definitions(store)
            svc.retrieve(q)
        events = store.list_events("run-1")
        completed = [e for e in events if e.event_type == "retrieval.completed"]
        assert completed, [e.event_type for e in events]
        blob = json.dumps([e.model_dump(mode="json") for e in completed])
        assert "adder operands" not in blob and q.query_digest in blob
        assert registered == "no registration needed", registered
    finally:
        store.close()


def test_redis_cache_constructor_rules():
    with pytest.raises(ValueError, match="redis_url must be non-empty"):
        RedisRetrievalCache("  ")
    with pytest.raises(ValueError, match="must end with ':'"):
        RedisRetrievalCache("redis://x", namespace="nocolon")
    with pytest.raises(RuntimeError, match=r"nailong-agent-sdk\[redis-cache\]"):
        RedisRetrievalCache("redis://localhost")


class FakeQdrant(http.server.BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *args):
        pass

    def _send(self, code, payload, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length)) if length else None

    def _handle(self, method):
        path = self.path
        body = self._body()
        state = self.state
        state["requests"].append((method, path, body))
        override = state.get("override", {}).get((method, path.split("?")[0].rsplit("/", 1)[-1]))
        if override:
            code, payload, raw = override
            return self._send(code, payload, raw)
        collection = state["collection"]
        if method == "GET" and path == f"/collections/{collection}":
            if state["exists"]:
                return self._send(200, {"status": "ok", "result": {}})
            return self._send(404, {"status": {"error": "Not found: Collection doesn't exist"}})
        if method == "PUT" and path == f"/collections/{collection}":
            state["exists"] = True
            return self._send(200, {"status": "ok", "result": True})
        if method == "PUT" and "/index" in path:
            return self._send(200, {"status": "ok", "result": {}})
        if method == "PUT" and "/points" in path:
            state["points"].extend(body["points"])
            return self._send(200, {"status": "ok", "result": {}})
        if method == "POST" and path.endswith("/points/search"):
            must = {item["key"]: item["match"] for item in body["filter"]["must"]}
            hits = []
            for rank, point in enumerate(state["points"]):
                payload = point["payload"]
                if payload["snapshot_id"] != must["snapshot_id"]["value"]:
                    continue
                if payload["category"] not in must["category"]["any"]:
                    continue
                hits.append({"id": point["id"], "score": 1.0 - rank * 0.01, "payload": payload})
            hits.extend(state.get("extra_hits", []))
            return self._send(200, {"status": "ok", "result": hits[: body["limit"]]})
        return self._send(500, {"status": {"error": f"unexpected {method} {path}"}})

    def do_GET(self):
        self._handle("GET")

    def do_PUT(self):
        self._handle("PUT")

    def do_POST(self):
        self._handle("POST")


@pytest.fixture
def qdrant():
    FakeQdrant.state = {"requests": [], "points": [], "exists": False, "collection": "col"}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeQdrant)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", FakeQdrant.state
    server.shutdown()
    server.server_close()


class Embedder:
    def __init__(self, dims=4):
        self.dims = dims
        self.calls = 0

    def embed(self, text):
        self.calls += 1
        return [float(len(text) % 7), 1.0, 0.5, float(text.count("a"))][: self.dims] + [0.0] * max(
            0, self.dims - 4
        )


def test_qdrant_roundtrip_with_fake_server(qdrant):
    url, state = qdrant
    index = QdrantRetrievalIndex(url, "col", Embedder(), embedding_dimensions=4)
    svc = GroundedRetrievalService(index)
    count = svc.index_snapshot("snap", trees())
    assert count == 3
    methods = [(m, p.split("?")[0]) for m, p, _ in state["requests"]]
    assert ("PUT", "/collections/col") in methods and methods.count(
        ("PUT", "/collections/col/index")
    ) == 2
    put_points = [b for m, p, b in state["requests"] if m == "PUT" and "/points" in p]
    assert len(put_points) == 1 and len(put_points[0]["points"]) == 3
    assert put_points[0]["points"][0]["payload"]["text"]
    result = svc.retrieve(query("adder", categories=(F,)))
    assert result.backend == "qdrant-rest-v1" and result.status is RetrievalStatus.RETRIEVED
    assert {c.document_id for c in result.candidates} == {"d1"}
    resolved = svc.resolve(result, trees())
    assert len(resolved.nodes) == len(result.candidates) and resolved.rejected_candidate_ids == []
    assert index.search(query("adder", snapshot="other")) == []


def test_qdrant_error_mapping_and_malformed_responses(qdrant):
    url, state = qdrant
    index = QdrantRetrievalIndex(url, "col", Embedder(), embedding_dimensions=4)
    index.upsert(specification_retrieval_documents("snap", trees()))
    state["extra_hits"] = [
        "not-a-dict",
        {"id": 1, "score": 0.5},
        {"id": 2, "score": "nan?", "payload": {"snapshot_id": "snap"}},
        {
            "id": 3,
            "score": 0.4,
            "payload": {
                "snapshot_id": "snap",
                "category": "functional",
                "document_id": "d1",
                "node_id": "n1",
                "source": sref().model_dump(mode="json"),
                "stable_id": "s",
            },
        },
    ]
    found = index.search(query("adder", categories=(F, P)))
    assert all(isinstance(c, RetrievalCandidate) for c in found)
    state["extra_hits"] = []
    state["override"] = {
        ("POST", "search"): (
            400,
            {"status": {"error": "Wrong input: Vector dimension error: expected dim: 8, got 4"}},
            None,
        )
    }
    with pytest.raises(RuntimeError):
        index.search(query("adder"))
    state["override"] = {("POST", "search"): (200, None, b"<html>proxy error</html>")}
    with pytest.raises(Exception):
        index.search(query("adder"))
    state["override"] = {("POST", "search"): (200, {"status": "error", "result": []}, None)}
    with pytest.raises(RuntimeError, match="did not report success"):
        index.search(query("adder"))
    state["override"] = {
        ("POST", "search"): (200, {"status": "ok", "result": {"not": "a list"}}, None)
    }
    with pytest.raises(RuntimeError, match="result must be a list"):
        index.search(query("adder"))
    dead = QdrantRetrievalIndex(
        "http://127.0.0.1:9", "col", Embedder(), embedding_dimensions=4, timeout_seconds=1
    )
    with pytest.raises(RuntimeError, match="transport failure"):
        dead.search(query("adder"))
    wrong_dims = QdrantRetrievalIndex(url, "col", Embedder(dims=3), embedding_dimensions=4)
    with pytest.raises(ValueError, match="do not match the Qdrant collection"):
        wrong_dims.search(query("adder"))
    svc = GroundedRetrievalService(dead)
    unavailable = svc.retrieve(query("adder"))
    assert (
        unavailable.status is RetrievalStatus.UNAVAILABLE
        and "transport failure" in unavailable.failure_reason
    )


def test_qdrant_constructor_validation():
    with pytest.raises(ValueError, match="HTTP"):
        QdrantRetrievalIndex("ftp://x", "c", Embedder(), embedding_dimensions=4)
    with pytest.raises(ValueError, match="collection_name"):
        QdrantRetrievalIndex("http://x", "", Embedder(), embedding_dimensions=4)
    with pytest.raises(ValueError, match="dimensions"):
        QdrantRetrievalIndex("http://x", "c", Embedder(), embedding_dimensions=0)
    with pytest.raises(ValueError, match="timeout"):
        QdrantRetrievalIndex("http://x", "c", Embedder(), embedding_dimensions=4, timeout_seconds=0)


def test_qdrant_upsert_batches_large_snapshots(qdrant):
    url, state = qdrant
    many = [node(f"n{i}", f"requirement text number {i} " * 5, doc="big") for i in range(1500)]
    index = QdrantRetrievalIndex(url, "col", Embedder(), embedding_dimensions=4)
    index.upsert(specification_retrieval_documents("snap", [tree("big", many, F)]))
    put_points = [b for m, p, b in state["requests"] if m == "PUT" and "/points" in p]
    sizes = [len(b["points"]) for b in put_points]
    assert max(sizes) <= 512, f"one request carried {max(sizes)} points"


def test_lexical_index_scales(tmp_path):
    many = [
        node(
            f"n{i}",
            f"requirement {i} about clock domain crossing and reset sequencing " * 3,
            doc="big",
        )
        for i in range(20_000)
    ]
    docs = specification_retrieval_documents("snap", [tree("big", many, F)])
    index = DeterministicLexicalRetrievalIndex()
    started = time.monotonic()
    index.upsert(docs)
    time.monotonic() - started
    started = time.monotonic()
    index.search(query("clock reset sequencing"))
    search_seconds = time.monotonic() - started
    assert search_seconds < 2.0, (
        f"one lexical search over {len(docs)} docs took {search_seconds:.1f}s"
    )
