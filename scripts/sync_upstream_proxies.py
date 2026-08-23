#!/usr/bin/env python3
"""Sync upstream SpotiFLAC proxy registry into local cache file.

Updates .upstream-proxy-registry.json only when registry content changes.
Exits with code 0 in all normal cases; prints SYNC_CHANGED=1 when changed.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_FILE = REPO_ROOT / ".upstream-proxy-registry.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.upstream_proxy_registry import _fetch_upstream_registry  # noqa: E402, SLF001


def load_existing_registry() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("registry"), dict):
        return data["registry"]
    # Backward-compat: if file is directly a registry object
    if isinstance(data, dict):
        return data
    return {}


def write_cache(registry: dict) -> None:
    payload = {
        "_fetched_at": int(time.time()),
        "registry": registry,
    }
    CACHE_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    current = load_existing_registry()
    upstream = _fetch_upstream_registry()

    if current == upstream:
        print("SYNC_CHANGED=0")
        print("Proxy registry unchanged")
        return 0

    write_cache(upstream)
    print("SYNC_CHANGED=1")
    print("Proxy registry updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
