"""Lyrics fetching from LRCLIB."""

from urllib.parse import quote

import requests


class LyricsClient:
    """Fetch lyrics from LRCLIB API."""

    BASE = "https://lrclib.net/api"

    def __init__(self):
        from .proxy_config import configure_session_proxy
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "SongsFetch/1.0"
        configure_session_proxy(self.session)

    def fetch(self, track: str, artist: str, album: str = "",
              duration: int = 0) -> dict:
        result = (
            self._get(track, artist, album, duration)
            or (self._get(track, artist, "", duration) if album else None)
            or (self._get(track, artist, album, 0) if duration else None)
            or self._search(track, artist)
        )
        return result or {"synced": "", "plain": "", "found": False}

    def _get(self, track, artist, album, dur) -> dict | None:
        url = (f"{self.BASE}/get?artist_name={quote(artist)}"
               f"&track_name={quote(track)}")
        if album:
            url += f"&album_name={quote(album)}"
        if dur:
            url += f"&duration={dur}"
        try:
            r = self.session.get(url, timeout=15)
            if r.status_code == 200:
                d = r.json()
                s, p = d.get("syncedLyrics", ""), d.get("plainLyrics", "")
                if s or p:
                    return {"synced": s, "plain": p, "found": True}
        except Exception:
            pass
        return None

    def _search(self, track, artist) -> dict | None:
        url = (f"{self.BASE}/search?track_name={quote(track)}"
               f"&artist_name={quote(artist)}")
        try:
            r = self.session.get(url, timeout=15)
            if r.status_code == 200:
                items = r.json()
                if items:
                    b = items[0]
                    s, p = b.get("syncedLyrics", ""), b.get("plainLyrics", "")
                    if s or p:
                        return {"synced": s, "plain": p, "found": True}
        except Exception:
            pass
        return None

    @staticmethod
    def to_lrc(data: dict, track: str = "", artist: str = "") -> str:
        synced = data.get("synced", "")
        if synced:
            lines = []
            if track:
                lines.append(f"[ti:{track}]")
            if artist:
                lines.append(f"[ar:{artist}]")
            lines.append(synced)
            return "\n".join(lines)
        return data.get("plain", "")
