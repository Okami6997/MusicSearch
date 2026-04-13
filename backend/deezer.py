"""Deezer search client (public API) for metadata discovery.

Note: Deezer API availability can vary by region. This client returns empty
lists on API failures so callers can treat Deezer as an optional source.
"""

import os
import random
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
    """Deezer downloader using Deezer metadata + ISRC provider fallback.

    Flow:
    1) Resolve Deezer track URL -> Deezer track metadata (includes ISRC)
    2) Resolve ISRC -> provider stream URL (Qobuz-compatible providers)
    3) Stream to output file
    """

    APP_ID = "798273057"
    PROVIDER_APIS = [
        "https://qbz.afkarxyz.qzz.io/api/track/",
        "https://dab.yeet.su/api/stream?trackId=",
        "https://dabmusic.xyz/api/stream?trackId=",
    ]

    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    DEEZER_TRACK_RE = re.compile(r"deezer\.com.*?/track/(\d+)")

    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA

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

    def _search_qobuz_track_by_isrc(self, isrc: str) -> dict:
        r = self.session.get(
            "https://www.qobuz.com/api.json/0.2/track/search",
            params={"query": isrc, "limit": 1, "app_id": self.APP_ID},
            timeout=30,
        )
        r.raise_for_status()
        items = r.json().get("tracks", {}).get("items", [])
        if not items:
            raise ValueError(f"Track not found for ISRC: {isrc}")
        return items[0]

    def _provider_download_url(self, qobuz_track_id: int, quality: str) -> str:
        q = quality if quality not in ("", "5") else "6"
        providers = list(self.PROVIDER_APIS)
        random.shuffle(providers)
        errors = []
        for base in providers:
            try:
                sep = "?" if "qbz.afkarxyz" in base else "&"
                url = f"{base}{qobuz_track_id}{sep}quality={q}"
                max_retries = 3
                for attempt in range(max_retries):
                    r = self.session.get(url, timeout=self.timeout)
                    if r.status_code == 429 or (
                        r.status_code == 200
                        and "Too many" in r.text[:100]
                    ):
                        if attempt < max_retries - 1:
                            import time
                            time.sleep(2 ** attempt)
                            continue
                        raise ValueError("rate limited after retries")
                    break
                if r.status_code != 200:
                    raise ValueError(f"status {r.status_code}")
                if r.text.strip().startswith("<!"):
                    raise ValueError("received HTML instead of JSON (service down)")
                data = r.json()
                if data.get("error"):
                    raise ValueError(data["error"])
                stream_url = data.get("url") or data.get("data", {}).get("url", "")
                if stream_url:
                    return stream_url
                raise ValueError("No stream URL in provider response")
            except Exception as e:
                errors.append(f"{base}: {e}")

        # Standard quality fallbacks used by Qobuz providers
        if q == "27":
            return self._provider_download_url(qobuz_track_id, "7")
        if q == "7":
            return self._provider_download_url(qobuz_track_id, "6")
        raise ValueError("All Deezer provider APIs failed: " + "; ".join(errors))

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
                    if progress_cb and total:
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
        # Resolve Deezer metadata first (source of truth for ISRC if missing)
        track_id = self.extract_track_id(deezer_url)
        d = self.fetch_track(track_id)
        deezer_isrc = (d.get("isrc") or "").upper().strip()
        isrc = (isrc or deezer_isrc).upper().strip()
        if not isrc:
            raise ValueError("No ISRC found for Deezer track")

        qobuz_track = self._search_qobuz_track_by_isrc(isrc)
        stream_url = self._provider_download_url(int(qobuz_track.get("id")), quality)
        output_path = os.path.join(output_dir, filename)
        return self._stream_download(stream_url, output_path, progress_cb)
