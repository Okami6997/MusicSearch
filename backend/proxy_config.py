"""Proxy configuration utility to route requests through a designated warp or system proxy."""

import os
import requests

def get_proxy() -> str | None:
    """Get the configured proxy (prioritizing WARP_PROXY, then ALL_PROXY) and sanitize it."""
    proxy = os.environ.get("WARP_PROXY") or os.environ.get("ALL_PROXY")
    if proxy:
        proxy = proxy.strip()
        # Automatically upgrade socks5:// to socks5h:// (and socks4 to socks4h)
        # to ensure that DNS resolution is performed remotely on the proxy server,
        # which fixes local NameResolutionError when the local DNS cannot resolve proxy domains.
        if proxy.startswith("socks5://"):
            proxy = proxy.replace("socks5://", "socks5h://", 1)
        elif proxy.startswith("socks4://"):
            proxy = proxy.replace("socks4://", "socks4h://", 1)
    return proxy

def configure_session_proxy(session: requests.Session) -> None:
    """Configure proxy on a requests.Session if a proxy is available."""
    proxy = get_proxy()
    if proxy:
        session.proxies = {
            "http": proxy,
            "https": proxy,
        }
