"""Which hosts count as your own network, for the offline setting and plain-HTTP model servers."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".localdomain")


def host_of(url: str) -> str:
    host = urlsplit(url).hostname
    if not host:
        raise ValueError(f"The URL {url!r} has no host.")
    return host.lower()


def is_loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_private_host(host: str) -> bool:
    if is_loopback(host):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host or host.endswith(LOCAL_SUFFIXES)
    return address.is_private or address.is_link_local


def offline_problems(
    *,
    provider: str,
    base_url: str | None,
    web: bool,
    web_search: bool,
    mcp_servers: dict[str, dict],
    otlp_endpoint: str | None = None,
) -> list[str]:
    problems = []
    if base_url is None:
        problems.append(
            f"provider {provider} has no base_url, so it would use the provider's public "
            "endpoint; set base_url to a server on your own network"
        )
    elif not is_private_host(host_of(base_url)):
        problems.append(
            f'base_url host "{host_of(base_url)}" is not loopback, a private address or a '
            "single-label or .local/.internal/.lan name"
        )
    if web:
        problems.append("web is on, which fetches public pages")
    if web_search:
        problems.append("web_search is on, which queries a public search service")
    if otlp_endpoint and not is_private_host(host_of(otlp_endpoint)):
        problems.append(f'otlp_endpoint host "{host_of(otlp_endpoint)}" is not on your network')
    for name, server in mcp_servers.items():
        url = server.get("url")
        if url and not is_private_host(host_of(url)):
            problems.append(f'mcp server "{name}" url host "{host_of(url)}" is not on your network')
    return problems
