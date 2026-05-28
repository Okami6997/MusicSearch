"""Proxy configuration utility to route requests through a designated warp or system proxy."""

import os
import requests

def get_proxy() -> str | None:
    """Get the configured proxy (prioritizing WARP_PROXY, then ALL_PROXY)."""
    return os.environ.get("WARP_PROXY") or os.environ.get("ALL_PROXY")

def configure_session_proxy(session: requests.Session) -> None:
    """Configure proxy on a requests.Session if a proxy is available."""
    proxy = get_proxy()
    if proxy:
        session.proxies = {
            "http": proxy,
            "https": proxy,
        }
