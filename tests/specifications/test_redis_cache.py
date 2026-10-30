import sys
import types

import pytest

from nailong_agent_sdk.specifications.retrieval import RedisRetrievalCache
from nailong_agent_sdk.specifications.retrieval_models import RetrievalResult, RetrievalStatus


class FakeRedis:
    def __init__(self, url, decode_responses):
        self.url = url
        self.decode_responses = decode_responses
        self.values = {}
        self.expiry = {}

    @classmethod
    def from_url(cls, url, decode_responses):
        return cls(url, decode_responses)

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex):
        self.values[key] = value
        self.expiry[key] = ex


@pytest.fixture
def fake_redis(monkeypatch):
    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=FakeRedis))


def result():
    return RetrievalResult(status=RetrievalStatus.RETRIEVED, query_digest="d" * 8, backend="b")


def test_results_are_stored_under_the_namespace_with_an_expiry_and_read_back(fake_redis):
    cache = RedisRetrievalCache("redis://cache.test:6379/0", namespace="sdk:test:")
    assert cache._client.url == "redis://cache.test:6379/0" and cache._client.decode_responses
    assert cache.get("missing") is None
    cache.set("query-1", result(), 60)
    assert list(cache._client.values) == ["sdk:test:query-1"]
    assert cache._client.expiry == {"sdk:test:query-1": 60}
    assert cache.get("query-1") == result()


def test_a_non_positive_expiry_is_refused_before_anything_is_written(fake_redis):
    cache = RedisRetrievalCache("redis://cache.test")
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        cache.set("query-1", result(), 0)
    assert cache._client.values == {}


@pytest.mark.parametrize(
    ("url", "namespace", "message"),
    [
        ("  ", "sdk:", "redis_url must be non-empty"),
        ("redis://cache.test", "sdk", "namespace must end with ':'"),
    ],
)
def test_the_configuration_is_checked_before_a_client_is_created(
    fake_redis, url, namespace, message
):
    with pytest.raises(ValueError, match=message):
        RedisRetrievalCache(url, namespace=namespace)


def test_a_missing_redis_package_names_the_extra_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "redis", None)
    with pytest.raises(RuntimeError, match=r"requires nailong-agent-sdk\[redis-cache\]"):
        RedisRetrievalCache("redis://cache.test")
