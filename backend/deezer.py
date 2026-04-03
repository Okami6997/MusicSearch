"""Deezer search client (public API) for metadata discovery.

Note: Deezer API availability can vary by region. This client returns empty
lists on API failures so callers can treat Deezer as an optional source.
"""

import requests


class DeezerClient:
    """Search Deezer tracks via the public API."""

    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA

    def search_tracks(self, query: str, limit: int = 10) -> list[dict]:
        """Search Deezer tracks and normalize to SongsFetch track shape."""
        try:
            resp = self.session.get(
                "https://api.deezer.com/search/track",
                params={"q": query, "limit": limit},
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return []

        out = []
        for t in payload.get("data", []) or []:
            album = t.get("album", {}) or {}
            artist = t.get("artist", {}) or {}
            cover = album.get("cover_xl") or album.get("cover_big") or album.get("cover_medium") or ""
            out.append({
                "id": t.get("id"),
                "title": t.get("title", ""),
                "artist": artist.get("name", ""),
                "album": album.get("title", ""),
                "cover_url": cover,
                "duration_ms": (t.get("duration", 0) or 0) * 1000,
                "isrc": (t.get("isrc", "") or "").upper(),
                "url": t.get("link", ""),
                "hires": False,
                "bit_depth": 0,
                "sample_rate": 0,
                "source": "deezer",
                "service": "Deezer",
                "year": "",
            })
        return out
