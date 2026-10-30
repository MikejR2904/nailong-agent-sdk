import importlib.metadata

from nailong_agent_sdk.foundations import version
from nailong_agent_sdk.mcp import server as mcp_server
from tests.support.paths import SRC_ROOT


def test_the_package_version_is_read_from_the_installed_metadata():
    assert version.PACKAGE_NAME == "nailong-agent-sdk"
    assert version.package_version() == importlib.metadata.version("nailong-agent-sdk")
    assert version.http_user_agent("evidence client") == (
        f"nailong-agent-sdk/{version.package_version()} evidence client"
    )


def test_an_uninstalled_package_reports_an_unknown_version(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(version, "version", missing)
    version.package_version.cache_clear()
    try:
        assert version.package_version() == "0+unknown"
    finally:
        version.package_version.cache_clear()


def test_the_server_constants_come_from_the_package_identity():
    assert mcp_server.SERVER_NAME == "nailong-agent-sdk"
    assert mcp_server.SERVER_VERSION == version.package_version()


def test_no_source_file_still_carries_the_retired_product_name():
    offenders = {}
    for path in sorted((SRC_ROOT / "nailong_agent_sdk").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in ("agent-design", "agent_design"):
            if needle in text:
                offenders.setdefault(path.name, []).append(needle)
    assert offenders == {}
