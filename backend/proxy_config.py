"""Proxy configuration utility to route requests through a designated warp or system proxy."""

import os
import socket
from urllib.parse import urlparse

import requests


def _is_loopback_host(hostname: str) -> bool:
    host = (hostname or "").strip().lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _is_proxy_reachable(proxy: str, timeout: float = 0.35) -> bool:
    """Quick reachability check for loopback proxy endpoints.

    Non-loopback proxies are treated as reachable to avoid blocking
    legitimate remote proxy configurations.
    """
    try:
        parsed = urlparse(proxy)
    except Exception:
        return False

    host = parsed.hostname or ""
    port = parsed.port
    if not host or not port:
        return False
    if not _is_loopback_host(host):
        return True

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def disable_unreachable_local_proxies() -> None:
    """Unset proxy env vars when they point to a dead local proxy.

    This prevents requests made without explicit sessions from inheriting
    broken localhost proxy variables.
    """
    env_proxy_keys = [
        "ALL_PROXY", "all_proxy",
        "HTTPS_PROXY", "https_proxy",
        "HTTP_PROXY", "http_proxy",
    ]
    for key in env_proxy_keys:
        val = (os.environ.get(key) or "").strip()
        if not val:
            continue
        try:
            parsed = urlparse(val)
            if _is_loopback_host(parsed.hostname or "") and not _is_proxy_reachable(val):
                os.environ.pop(key, None)
        except Exception:
            # Keep malformed values untouched; requests may still handle them.
            continue

def get_proxy() -> str | None:
    """Get the configured proxy (prioritizing WARP_PROXY, then ALL_PROXY) and sanitize it."""
    proxy = (
        os.environ.get("WARP_PROXY")
        or os.environ.get("SONGSFETCH_PROXY")
        or os.environ.get("ALL_PROXY")
    )
    if proxy:
        proxy = proxy.strip()
        # Automatically upgrade socks5:// to socks5h:// (and socks4 to socks4h)
        # to ensure that DNS resolution is performed remotely on the proxy server,
        # which fixes local NameResolutionError when the local DNS cannot resolve proxy domains.
        if proxy.startswith("socks5://"):
            proxy = proxy.replace("socks5://", "socks5h://", 1)
        elif proxy.startswith("socks4://"):
            proxy = proxy.replace("socks4://", "socks4h://", 1)
        if not _is_proxy_reachable(proxy):
            return None
    return proxy

def configure_session_proxy(session: requests.Session) -> None:
    """Configure proxy on a requests.Session if a proxy is available."""
    # Prevent accidental inheritance of broken shell proxy variables.
    session.trust_env = False
    proxy = get_proxy()
    if proxy:
        session.proxies = {
            "http": proxy,
            "https": proxy,
        }
