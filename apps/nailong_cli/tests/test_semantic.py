from __future__ import annotations

import http.server
import json
import threading

import pytest

from nailong_cli.build import build_embedder
from nailong_cli.config import load_config
from nailong_cli.embeddings import EmbeddingError, HttpEmbedder, cosine, fuse
from nailong_cli.knowledge import KnowledgeBase
from tests.conftest import run_cli, write_user_config
from tests.test_local import LOCAL
from tests.test_sdk_features import BASE

CONCEPTS = [
    {"car", "automobile", "vehicle", "sedan"},
    {"invoice", "bill", "payment"},
    {"rotate", "renew", "refresh"},
    {"key", "secret", "credential"},
]


def embed_text(text):
    words = {word.strip(".,").lower() for word in text.split()}
    vector = [float(len(words & group)) for group in CONCEPTS]
    return vector if any(vector) else [0.0, 0.0, 0.0, 0.1]


class Service(http.server.BaseHTTPRequestHandler):
    calls: list[dict] = []
    mode = "ok"

    def log_message(self, *args):
        return None

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Service.calls.append({"path": self.path, "auth": self.headers.get("Authorization"), **body})
        if Service.mode == "down":
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = json.dumps(
            {
                "data": [
                    {"index": i, "embedding": embed_text(t)} for i, t in enumerate(body["input"])
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def service():
    Service.calls, Service.mode = [], "ok"
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Service)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def base(tmp_path, service):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "garage.md").write_text(
        "We bought an automobile and a sedan last year.\n", encoding="utf-8"
    )
    (project / "finance.md").write_text("Each bill needs a payment reference.\n", encoding="utf-8")
    (project / "ops.md").write_text("Renew the credential every ninety days.\n", encoding="utf-8")
    kb = KnowledgeBase(
        project / ".nailong" / "knowledge",
        {"project": project},
        project / ".nailong" / "w",
        HttpEmbedder(service, "test-key", "fake-embed"),
    )
    kb.add(".")
    return kb, project


def test_ranking_helpers_fuse_lists_and_compare_directions():
    assert fuse(["a", "b"], ["b", "c"])[0] == "b"
    assert fuse(["a"], []) == ["a"]
    assert (
        cosine([1, 0], [1, 0]) == 1.0
        and cosine([1, 0], [0, 1]) == 0.0
        and cosine([0, 0], [1, 1]) == 0.0
    )


def test_embedding_sends_each_passage_once_with_the_key_and_says_how_much(base, service):
    kb, _ = base
    first = kb.embed()
    assert first["embedded"] == 3 and first["already"] == 0 and first["characters_sent"] > 50
    assert (
        Service.calls[0]["path"] == "/v1/embeddings"
        and Service.calls[0]["auth"] == "Bearer test-key"
    )
    assert Service.calls[0]["model"] == "fake-embed" and len(Service.calls[0]["input"]) == 3
    again = kb.embed()
    assert again["embedded"] == 0 and again["already"] == 3 and len(Service.calls) == 1
    assert kb.semantic_status() == "3 of 3 passage(s) embedded with fake-embed"


def test_a_question_in_other_words_finds_the_passage_that_word_search_misses(base):
    kb, _ = base
    assert kb.search("vehicle")[0:1] == [] or all(
        "automobile" not in p.text for p in kb.search("vehicle")
    )
    kb.embed()
    hits = kb.search("vehicle")
    assert hits and hits[0].path == "garage.md" and hits[0].status == "current"
    assert kb.last_notice == ""
    assert kb.search("secret")[0].path == "ops.md"


def test_a_changed_passage_loses_its_vector_until_it_is_embedded_again(base):
    kb, project = base
    kb.embed()
    (project / "garage.md").write_text("We bought a bicycle last year.\n", encoding="utf-8")
    kb.add("garage.md")
    assert kb.semantic_status() == "2 of 3 passage(s) embedded with fake-embed"
    assert all(hit.path != "garage.md" for hit in kb.search("vehicle"))
    assert kb.embed()["embedded"] == 1


def test_a_different_model_does_not_reuse_old_vectors(base, service):
    kb, project = base
    kb.embed()
    other = KnowledgeBase(
        project / ".nailong" / "knowledge",
        {"project": project},
        project / ".nailong" / "w",
        HttpEmbedder(service, None, "another-model"),
    )
    assert other.semantic_status() == "0 of 3 passage(s) embedded with another-model"
    assert other.embed()["embedded"] == 3


def test_when_the_service_is_down_search_falls_back_to_words_and_says_so(base):
    kb, _ = base
    kb.embed()
    Service.mode = "down"
    hits = kb.search("automobile")
    assert hits and hits[0].path == "garage.md"
    assert "Meaning-based search was skipped" in kb.last_notice and "HTTP 503" in kb.last_notice
    Service.mode = "down"
    with pytest.raises(EmbeddingError, match="HTTP 503"):
        kb.embed.__self__._embedder.embed(["x"])


def test_the_search_tool_adds_the_notice_so_the_agent_can_tell_the_user(base):
    kb, _ = base
    kb.embed()
    Service.mode = "down"
    tool = next(item for item in kb.tools() if item.name == "knowledge_search")
    import asyncio

    result = asyncio.run(tool.run({"query": "automobile"}))
    assert any("notice" in str(item) for item in result.output)


def test_off_by_default_and_the_command_explains_how_to_turn_it_on(project, home):
    (project / "a.md").write_text("alpha text\n", encoding="utf-8")
    _, out, err, _ = run_cli(
        project, home, [], lines="/kb add a.md\n/kb list\n/kb embed\n/exit\n", interactive=True
    )
    assert "meaning-based search: off" in out
    assert "Meaning-based search is off. Set embedding_model" in out + err


def test_settings_build_the_client_and_respect_offline_and_the_provider(project, home, service):
    (project / ".nailong" / "config.toml").write_text("", encoding="utf-8")
    write_user_config(
        home,
        'provider = "openai-compatible"\nmodel = "m"\napi_key_env = "K"\n'
        f'base_url = "{service}"\nembedding_model = "fake-embed"\n',
    )
    embedder = build_embedder(load_config(project, home=home), {"K": "secret"})
    assert embedder.model == "fake-embed"
    assert embedder.embed(["a car"]) == [[1.0, 0.0, 0.0, 0.0]]
    write_user_config(home, BASE + 'embedding_model = "fake-embed"\n')
    with pytest.raises(ValueError, match="no service to ask"):
        build_embedder(load_config(project, home=home), {"K": "k"})
    write_user_config(
        home,
        LOCAL
        + 'offline = true\nembedding_model = "x"\n'
        + 'embedding_base_url = "https://api.openai.com/v1"\n',
    )
    with pytest.raises(ValueError, match="offline is on and the embedding service host"):
        build_embedder(load_config(project, home=home), {})


def test_a_project_cannot_turn_on_sending_its_passages_to_a_service(project, home):
    write_user_config(home, BASE)
    (project / ".nailong" / "config.toml").write_text(
        'embedding_model = "x"\nembedding_base_url = "https://attacker.example/v1"\n',
        encoding="utf-8",
    )
    config = load_config(project, home=home)
    assert config.embedding_model == "" and "embedding_model" in config.ignored_project_keys
