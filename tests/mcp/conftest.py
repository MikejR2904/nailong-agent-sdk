import pytest

from tests.support.mcp_authority import Harness
from tests.support.mcp_server import Server


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    instance = Server(
        tmp_path_factory.mktemp("mcp"),
        extra_environment={"AGENT_RUNTIME_ALLOW_HOST_APPROVALS": "1"},
    )
    yield instance
    instance.stop()


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path)
