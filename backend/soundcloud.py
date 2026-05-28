"""SoundCloud search client.

Uses SoundCloud web hydration data to discover a public API client id, then
queries the v2 search endpoint for track metadata.
"""

import json
import re

import requests


class SoundCloudClient:
    """Search SoundCloud tracks without requiring user OAuth."""

    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    def __init__(self):
        from .proxy_config import configure_session_proxy
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA
        configure_session_proxy(self.session)
        self._client_id = ""

    def _expand_short_url(self, url: str) -> str:
        if "on.soundcloud.com/" not in url:
            return url
        try:
            resp = self.session.head(url, allow_redirects=True, timeout=20)
            final_url = (resp.url or "").strip()
            return final_url or url
        except Exception:
            return url

    def _fetch_client_id(self, query: str = "music") -> str:
        if self._client_id:
            return self._client_id
        try:
            html = self.session.get(
                f"https://soundcloud.com/search/sounds?q={requests.utils.quote(query)}",
                timeout=20,
            ).text
            m = re.search(r"window\.__sc_hydration\s*=\s*(\[.*?\]);", html, re.S)
            if not m:
                return ""
            arr = json.loads(m.group(1))
            for obj in arr:
                if obj.get("hydratable") == "apiClient":
                    cid = (obj.get("data", {}) or {}).get("id", "")
                    if cid:
                        self._client_id = cid
                        return cid
        except Exception:
            return ""
        return ""

    def resolve_set(self, url: str) -> list[dict]:
        """Resolve a SoundCloud set/playlist URL into a list of tracks.

        SoundCloud's /resolve endpoint sometimes returns "stub" track objects
        that only contain an ``id`` field (no title, permalink_url, etc.).
        Those are fetched in bulk via the /tracks endpoint before building the
        final list so every entry has valid metadata.
        """
        url = self._expand_short_url(url)
        client_id = self._fetch_client_id()
        if not client_id:
            return []
        try:
            resp = self.session.get(
                "https://api-v2.soundcloud.com/resolve",
                params={"url": url, "client_id": client_id},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        out = []
        tracks = data.get("tracks", []) or []

        # Identify stub objects (missing title/permalink_url) and fetch them.
        stub_ids = [
            t["id"] for t in tracks
            if isinstance(t, dict) and t.get("id") and not t.get("permalink_url")
        ]
        hydrated: dict[int, dict] = {}
        if stub_ids:
            # SoundCloud accepts up to 50 ids per request
            for i in range(0, len(stub_ids), 50):
                batch = stub_ids[i : i + 50]
                try:
                    r = self.session.get(
                        "https://api-v2.soundcloud.com/tracks",
                        params={"ids": ",".join(str(x) for x in batch), "client_id": client_id},
                        timeout=20,
                    )
                    r.raise_for_status()
                    for item in r.json() or []:
                        if isinstance(item, dict) and item.get("id"):
                            hydrated[item["id"]] = item
                except Exception:
                    pass

        for t in tracks:
            if not isinstance(t, dict):
                continue
            # Replace stub with full track object if available
            if t.get("id") in hydrated:
                t = hydrated[t["id"]]
            user = t.get("user", {}) or {}
            artwork = t.get("artwork_url", "") or (user.get("avatar_url", "") if user else "")
            out.append({
                "id": t.get("id"),
                "title": t.get("title", ""),
                "artist": user.get("username", ""),
                "album": data.get("title", ""),
                "cover_url": artwork,
                "duration_ms": t.get("full_duration", 0) or t.get("duration", 0) or 0,
                "isrc": "",
                "url": t.get("permalink_url", ""),
                "hires": False,
                "bit_depth": 0,
                "sample_rate": 0,
                "preview_url": "",
                "source": "soundcloud",
                "service": "SoundCloud",
                "year": "",
            })
        return out

    def search_tracks(self, query: str, limit: int = 10) -> list[dict]:
        client_id = self._fetch_client_id(query)
        if not client_id:
            return []

        try:
            resp = self.session.get(
                "https://api-v2.soundcloud.com/search/tracks",
                params={
                    "q": query,
                    "limit": limit,
                    "client_id": client_id,
                },
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return []

        out = []
        for t in payload.get("collection", []) or []:
            user = t.get("user", {}) or {}
            artwork = t.get("artwork_url", "") or (user.get("avatar_url", "") if user else "")
            out.append({
                "id": t.get("id"),
                "title": t.get("title", ""),
                "artist": user.get("username", ""),
                "album": "",
                "cover_url": artwork,
                "duration_ms": t.get("full_duration", 0) or t.get("duration", 0) or 0,
                "isrc": "",
                "url": t.get("permalink_url", ""),
                "hires": False,
                "bit_depth": 0,
                "sample_rate": 0,
                "preview_url": "",
                "source": "soundcloud",
                "service": "SoundCloud",
                "year": "",
            })
        return out
