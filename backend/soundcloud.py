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
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA
        self._client_id = ""

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
                "duration_ms": t.get("duration", 0) or 0,
                "isrc": "",
                "url": t.get("permalink_url", ""),
                "hires": False,
                "bit_depth": 0,
                "sample_rate": 0,
                "source": "soundcloud",
                "service": "SoundCloud",
                "year": "",
            })
        return out
