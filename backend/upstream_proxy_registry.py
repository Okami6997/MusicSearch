"""Sync and cache upstream provider endpoints from SpotiFLAC's cloud registry."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .proxy_config import configure_session_proxy

REGISTRY_SOURCE_REPO = "BartolomeoRusso9/SpotiFLAC-Module-Version"
REGISTRY_GIST_URL = (
    "https://gist.githubusercontent.com/BartolomeoRusso9/"
    "ef9fdbbc894818aea89d25a8d99f8c77/raw"
)
REGISTRY_CACHE_FILE = Path(__file__).resolve().parents[1] / ".upstream-proxy-registry.json"
REGISTRY_CACHE_TTL_SECONDS = 60 * 60

_SEED_PARTS = [b"spotif", b"lac:co", b"mmunity:url:v1"]
_AAD = b"spotiflac|community|url|v1"


def _decrypt_base64_payload(b64_string: str) -> dict:
    clean = "".join((b64_string or "").split())
    clean = clean.replace("-", "+").replace("_", "/")
    if not clean:
        raise ValueError("empty upstream registry payload")
    clean += "=" * ((4 - len(clean) % 4) % 4)

    raw_bytes = base64.b64decode(clean)
    nonce = raw_bytes[:12]
    encrypted_payload = raw_bytes[12:]

    hasher = hashlib.sha256()
    for part in _SEED_PARTS:
        hasher.update(part)
    key = hasher.digest()

    decrypted = AESGCM(key).decrypt(nonce, encrypted_payload, _AAD)
    return json.loads(decrypted.decode("utf-8"))


def _fetch_upstream_registry() -> dict:
    session = requests.Session()
    session.headers["User-Agent"] = "SongsFetch-ProxySync/1.0"
    configure_session_proxy(session)
    resp = session.get(f"{REGISTRY_GIST_URL}?t={int(time.time())}", timeout=10)
    resp.raise_for_status()
    return _decrypt_base64_payload(resp.text)


def _load_cache() -> dict:
    if not REGISTRY_CACHE_FILE.exists():
        return {}
    try:
        with REGISTRY_CACHE_FILE.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_cache(payload: dict) -> None:
    REGISTRY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        dir=str(REGISTRY_CACHE_FILE.parent),
        prefix=".proxy-registry-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        os.replace(temp_path, REGISTRY_CACHE_FILE)
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def load_registry(force_refresh: bool = False) -> tuple[dict, str]:
    """Return (registry, source) where source is 'remote' or 'cache'."""
    now = int(time.time())
    cached = _load_cache()
    fetched_at = int(cached.get("_fetched_at", 0) or 0)

    if not force_refresh and cached and (now - fetched_at) < REGISTRY_CACHE_TTL_SECONDS:
        return cached.get("registry", {}), "cache"

    try:
        registry = _fetch_upstream_registry()
        _save_cache({"_fetched_at": now, "registry": registry})
        return registry, "remote"
    except Exception:
        if cached:
            return cached.get("registry", {}), "cache"
        raise


def refresh_registry() -> dict:
    """Force-refresh upstream registry and return a summary payload."""
    registry, source = load_registry(force_refresh=True)
    return {
        "ok": True,
        "source": source,
        "updated_at": int(time.time()),
        "providers": sorted(k for k in registry.keys() if isinstance(registry.get(k), dict)),
        "registry_repo": REGISTRY_SOURCE_REPO,
    }


def _prepend_unique(values: list[str], extra: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in extra + values:
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def provider_overrides() -> dict:
    """Build endpoint overrides consumed by downloader classes."""
    registry, _ = load_registry(force_refresh=False)

    tidal_post = list((registry.get("tidal") or {}).get("post") or [])
    tidal_stream = list((registry.get("tidal") or {}).get("stream") or [])

    qobuz_stream = list((registry.get("qobuz") or {}).get("stream") or [])
    qobuz_dl = list((registry.get("qobuz") or {}).get("dl") or [])
    qobuz_post = list((registry.get("qobuz") or {}).get("post") or [])

    deezer_section = registry.get("deezer") or {}
    deezer_api_candidates = [
        (registry.get("community") or {}).get("deezer", ""),
        deezer_section.get("community", ""),
    ]

    amazon_section = registry.get("amazon") or {}
    amazon_api_bases = [
        amazon_section.get("community", ""),
        amazon_section.get("s", ""),
        amazon_section.get("mono", ""),
    ]

    return {
        "tidal_post": [u for u in tidal_post if isinstance(u, str) and u],
        "tidal_stream": [u for u in tidal_stream if isinstance(u, str) and u],
        "qobuz_stream": [u for u in qobuz_stream if isinstance(u, str) and u],
        "qobuz_dl": [u for u in qobuz_dl if isinstance(u, str) and u],
        "qobuz_post": [u for u in qobuz_post if isinstance(u, str) and u],
        "deezer_api_candidates": [u for u in deezer_api_candidates if isinstance(u, str) and u],
        "amazon_api_bases": [u for u in amazon_api_bases if isinstance(u, str) and u],
        "youtube_cobalt": [
            u
            for u in list((registry.get("youtube") or {}).get("cobalt") or [])
            if isinstance(u, str) and u
        ],
    }


def merge_proxy_list(existing: list[str], preferred: list[str]) -> list[str]:
    """Public helper for classes to prepend upstream endpoints to local defaults."""
    return _prepend_unique(existing or [], preferred or [])
