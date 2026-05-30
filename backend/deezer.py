"""Deezer search client (public API) for metadata discovery.

Note: Deezer API availability can vary by region. This client returns empty
lists on API failures so callers can treat Deezer as an optional source.
"""

import os
import re

import requests


class DeezerClient:
    """Search Deezer tracks via the public API."""

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
                "preview_url": t.get("preview", ""),
                "source": "deezer",
                "service": "Deezer",
                "year": "",
            })
        return out

    def search_albums(self, query: str, limit: int = 10) -> list[dict]:
        """Search Deezer albums and normalize to SongsFetch album shape."""
        try:
            resp = self.session.get(
                "https://api.deezer.com/search/album",
                params={"q": query, "limit": limit},
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return []

        out = []
        for al in payload.get("data", []) or []:
            artist = al.get("artist", {}) or {}
            cover = al.get("cover_xl") or al.get("cover_big") or al.get("cover_medium") or ""
            out.append({
                "id": al.get("id"),
                "title": al.get("title", ""),
                "artist": artist.get("name", ""),
                "cover_url": cover,
                "tracks_count": al.get("nb_tracks", 0),
                "release_date": "",
                "year": "",
                "hires": False,
                "source": "deezer",
                "service": "Deezer",
            })
        return out


class DeezerDownloader:
    """Deezer downloader using Deezer metadata + direct FLAC endpoint."""

    DOWNLOAD_API = "https://api.zarz.moe/v1/dl/dzr"

    UA = (
        "SpotiFLAC-Mobile/1.0"
    )

    DEEZER_TRACK_RE = re.compile(r"deezer\.com.*?/track/(\d+)")

    def __init__(self, timeout: float = 60.0):
        from .proxy_config import configure_session_proxy
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA
        configure_session_proxy(self.session)

    def extract_track_id(self, url: str) -> int:
        m = self.DEEZER_TRACK_RE.search(url or "")
        if not m:
            raise ValueError(f"Could not parse Deezer track id from URL: {url}")
        return int(m.group(1))

    def fetch_track(self, track_id: int) -> dict:
        r = self.session.get(f"https://api.deezer.com/track/{track_id}", timeout=20)
        r.raise_for_status()
        d = r.json()
        if not d or d.get("error"):
            raise ValueError("Failed to fetch Deezer track metadata")
        return d

    def _get_download_url(self, track_id: int) -> str:
        headers = {
            "User-Agent": "SpotiFLAC-Mobile/1.0",
            "Content-Type": "application/json"
        }
        payload = {
            "platform": "deezer",
            "url": f"https://www.deezer.com/track/{track_id}"
        }
        resp = self.session.post(
            self.DOWNLOAD_API,
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            raise ValueError(f"Deezer download API (status {resp.status_code}) returned invalid/HTML response (down or blocked)")
        if not data.get("success"):
            raise ValueError(f"Deezer download API returned error: {data.get('message', 'Unknown error')}")
        stream_url = data.get("direct_download_url") or data.get("download_url")
        if not stream_url:
            raise ValueError("No download URL returned by Deezer resolver")
        return stream_url

    def _stream_download(self, url: str, output_path: str, progress_cb=None) -> str:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        tmp = output_path + ".part"
        try:
            resp = self.session.get(url, stream=True, timeout=180)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            done = 0
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(256 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)
            os.replace(tmp, output_path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        return output_path

    def download_track(
        self,
        deezer_url: str,
        output_dir: str,
        filename: str,
        quality: str,
        progress_cb=None,
        isrc: str = "",
    ) -> str:
        track_id = self.extract_track_id(deezer_url)
        stream_url = self._get_download_url(track_id)
        output_path = os.path.join(output_dir, filename)
        return self._stream_download(stream_url, output_path, progress_cb)
