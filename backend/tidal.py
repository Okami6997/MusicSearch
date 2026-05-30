"""Tidal downloader - downloads tracks via Tidal API proxies."""

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import random
import subprocess
import time
from typing import Optional

import requests


def _retry_with_backoff(
    func,
    *args,
    retries: int = 3,
    backoff_base: float = 1.0,
    retry_on_statuses: tuple = (429, 500, 502, 503, 504),
    **kwargs,
):
    """Execute a function with exponential backoff retry on transient failures."""
    last_exc = None
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            # Check if it's a retryable HTTP status
            if hasattr(exc, "response") and exc.response is not None:
                if exc.response.status_code not in retry_on_statuses:
                    raise
            elif attempt == retries - 1:
                raise
            time.sleep(backoff_base * (2 ** attempt))
    if last_exc:
        raise last_exc


class TidalSearchClient:
    """Search Tidal tracks via proxy APIs with health tracking."""

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
        self.APIS = [
            "https://hifi-one.spotisaver.net",
            "https://hifi-two.spotisaver.net",
        ]
        self._api_url: str = ""
        self.last_status: str = "unknown"
        self.last_error: str = ""

    def _pick_api(self) -> str:
        """Pick the first healthy API endpoint."""
        for api in self.APIS:
            try:
                r = self.session.head(api, timeout=5)
                if r.status_code < 500:
                    self._api_url = api
                    return api
            except Exception:
                continue
        self._api_url = self.APIS[0]
        return self._api_url

    def search_tracks(self, query: str, limit: int = 20) -> list[dict]:
        """Search Tidal tracks by query term using the proxy API search endpoint."""
        self.last_status = "unknown"
        self.last_error = ""

        try:
            api = self._pick_api()

            def _do_search():
                # Try to use a search endpoint if available, otherwise fall back to
                # trying to fetch track directly via ID resolution
                resp = self.session.get(
                    f"{api}/search",
                    params={"s": query, "limit": limit},
                    timeout=20,
                )
                if resp.status_code == 429:
                    return None, resp.status_code, "rate limited"
                if resp.status_code >= 500:
                    return None, resp.status_code, "server error"
                resp.raise_for_status()
                return resp.json(), resp.status_code, ""

            result, status_code, error_msg = _retry_with_backoff(
                _do_search, retries=3, backoff_base=1.0
            )

            if status_code == 429:
                self.last_status = "rate_limited"
                self.last_error = "429 rate limit"
            elif status_code >= 500:
                self.last_status = "degraded"
                self.last_error = f"status {status_code}"
            elif error_msg:
                self.last_status = "error"
                self.last_error = error_msg
            else:
                self.last_status = "healthy"

            if not result:
                return []

            # Parse the response - Tidal proxy APIs return various formats
            # Format: {"version":"2.8","data":{"limit":5,"offset":0,"totalNumberOfItems":300,"items":[...]}}
            tracks = []
            data_obj = result if isinstance(result, list) else result.get("data", {}) or {}
            items = data_obj if isinstance(data_obj, list) else data_obj.get("items", []) or result.get("tracks", []) or []

            for item in items[:limit]:
                if isinstance(item, dict):
                    # Standardize response format
                    track_id = item.get("id") or item.get("trackId") or ""
                    if not track_id:
                        continue
                    # Artist can be a string or dict
                    artist_val = item.get("artist", "")
                    if isinstance(artist_val, dict):
                        artist_val = artist_val.get("name", "")
                    tracks.append({
                        "id": str(track_id),
                        "title": item.get("title", ""),
                        "artist": artist_val,
                        "album": item.get("album", "") or item.get("albumName", ""),
                        "cover_url": item.get("cover") or item.get("albumArt") or item.get("image", ""),
                        "duration_ms": (item.get("duration", 0) or 0) * 1000,
                        "isrc": item.get("isrc", ""),
                        "hires": item.get("hires", False),
                        "bit_depth": item.get("bit_depth", 0) or item.get("audioQuality", 0),
                        "sample_rate": item.get("sample_rate", 0),
                        "url": item.get("url", "") or f"https://tidal.com/track/{track_id}",
                        "preview_url": "",
                        "source": "tidal",
                        "service": "Tidal",
                        "year": "",
                    })

            # Extract unique albums from track results
            seen_album_ids = set()
            albums_out = []
            for item in items[:50]:  # scan more items for album diversity
                if isinstance(item, dict):
                    album_obj = item.get("album", {})
                    if not album_obj or not album_obj.get("id"):
                        continue
                    aid = album_obj["id"]
                    if aid in seen_album_ids:
                        continue
                    seen_album_ids.add(aid)
                    # Resolve artist name
                    artist_val = item.get("artist", "")
                    if isinstance(artist_val, dict):
                        artist_val = artist_val.get("name", "")
                    cover_uuid = album_obj.get("cover", "")
                    cover_url = f"https://resources.tidal.com/images/{cover_uuid.replace('-', '/')}/640x640.jpg" if cover_uuid else ""
                    albums_out.append({
                        "id": str(aid),
                        "title": album_obj.get("title", ""),
                        "artist": artist_val,
                        "cover_url": cover_url,
                        "tracks_count": 0,
                        "release_date": "",
                        "year": "",
                        "hires": False,
                        "source": "tidal",
                        "service": "Tidal",
                    })
            self._last_albums = albums_out

            return tracks

        except Exception as e:
            self.last_status = "error"
            self.last_error = str(e)
            return []

    def get_last_albums(self) -> list[dict]:
        """Return albums extracted from the previous search_tracks call."""
        return getattr(self, "_last_albums", [])

class TidalDownloader:
    """Download tracks from Tidal via proxy APIs."""

    API_TIMEOUT = 6

    APIS = [
        "https://api.zarz.moe/v1/dl/tid2",
        "https://eu-central.monochrome.tf",
        "https://us-west.monochrome.tf",
        "https://api.monochrome.tf",
        "https://monochrome-api.samidy.com",
        "https://tidal-api.binimum.org",
        "https://tidal.kinoplus.online",
        "https://triton.squid.wtf",
        "https://vogel.qqdl.site",
        "https://maus.qqdl.site",
        "https://hund.qqdl.site",
        "https://katze.qqdl.site",
        "https://wolf.qqdl.site",
        "https://hifi-one.spotisaver.net",
        "https://hifi-two.spotisaver.net",
    ]
    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    def __init__(self, api_url: str = ""):
        from .proxy_config import configure_session_proxy
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA
        configure_session_proxy(self.session)
        self.api_url = api_url or self._pick_api()

    def _pick_api(self) -> str:
        for api in self.APIS:
            try:
                if "zarz.moe" in api:
                    r = self.session.get("https://api.zarz.moe/v1/health", timeout=5)
                else:
                    r = self.session.head(api, timeout=5)
                if r.status_code < 500:
                    return api
            except Exception:
                continue
        return self.APIS[0]

    @staticmethod
    def parse_track_id(tidal_url: str) -> int:
        parts = tidal_url.split("/track/")
        if len(parts) < 2:
            raise ValueError(f"Invalid Tidal URL: {tidal_url}")
        return int(parts[1].split("?")[0].strip())

    def get_download_url(self, track_id: int, quality: str = "LOSSLESS") -> str:
        api_cleaning = self.api_url.rstrip('/')
        is_post_api = "zarz.moe" in api_cleaning or api_cleaning.endswith("/tid2")
        headers = {"User-Agent": "SpotiFLAC-Mobile/1.0" if is_post_api else self.UA}

        if is_post_api:
            resp = self.session.post(
                api_cleaning,
                json={"id": str(track_id), "quality": quality},
                headers=headers,
                timeout=self.API_TIMEOUT,
            )
        else:
            url = f"{api_cleaning}/track/?id={track_id}&quality={quality}"
            resp = self.session.get(url, headers=headers, timeout=self.API_TIMEOUT)

        resp.raise_for_status()
        body = resp.json()

        if isinstance(body, dict):
            if body.get("data", {}).get("manifest"):
                return "MANIFEST:" + body["data"]["manifest"]
            if body.get("manifest"):
                return "MANIFEST:" + body["manifest"]
            if body.get("direct_download_url"):
                return body["direct_download_url"]
            if body.get("download_url"):
                return body["download_url"]
            if body.get("url"):
                return body["url"]

        if isinstance(body, list):
            for item in body:
                if isinstance(item, dict) and item.get("OriginalTrackUrl"):
                    return item["OriginalTrackUrl"]

        raise ValueError("No download URL in Tidal response")

    def _get_download_url_from_api(self, api_url: str, track_id: int, quality: str) -> str:
        original_api = self.api_url
        try:
            self.api_url = api_url
            return self.get_download_url(track_id, quality)
        finally:
            self.api_url = original_api

    def _get_download_url_parallel(self, track_id: int, quality: str) -> tuple[str, str]:
        ordered_apis = [self.api_url] + [api for api in self.APIS if api != self.api_url]
        errors = []

        with ThreadPoolExecutor(max_workers=min(len(ordered_apis), 6)) as executor:
            futures = {
                executor.submit(self._get_download_url_from_api, api, track_id, quality): api
                for api in ordered_apis
            }
            for future in as_completed(futures, timeout=self.API_TIMEOUT + 2):
                api = futures[future]
                try:
                    return api, future.result()
                except Exception as e:
                    errors.append(f"{api}: {e}")

        raise ValueError(f"All Tidal APIs failed: {'; '.join(errors)}")

    def download_file(self, url: str, output_path: str,
                      progress_cb=None) -> str:
        if url.startswith("MANIFEST:"):
            return self._download_manifest(url[9:], output_path, progress_cb)

        resp = self.session.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(65536):
                f.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
        return output_path

    def _download_manifest(self, b64: str, output_path: str,
                           progress_cb=None) -> str:
        manifest = json.loads(base64.b64decode(b64))
        mime = manifest.get("mimeType", "")
        urls = manifest.get("urls", [])
        if not urls:
            raise ValueError("No URLs in manifest")

        direct = urls[0]
        if "flac" in mime.lower():
            return self.download_file(direct, output_path, progress_cb)

        tmp = output_path + ".m4a.tmp"
        self.download_file(direct, tmp, progress_cb)
        return self._to_flac(tmp, output_path)

    @staticmethod
    def _to_flac(src: str, dst: str) -> str:
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", src, "-c:a", "flac",
                 "-compression_level", "8", dst],
                capture_output=True, check=True, timeout=300,
            )
            os.remove(src)
            return dst
        except subprocess.CalledProcessError as e:
            if os.path.exists(src):
                os.remove(src)
            raise ValueError(f"FFmpeg failed: {e.stderr.decode()}")

    def download_track(self, tidal_url: str, output_dir: str,
                       quality: str = "LOSSLESS", filename: str = "",
                       progress_cb=None) -> str:
        """Download a track from a Tidal URL."""
        track_id = self.parse_track_id(tidal_url)

        try:
            winning_api, dl_url = self._get_download_url_parallel(track_id, quality)
            self.api_url = winning_api
        except Exception:
            if quality == "HI_RES":
                return self.download_track(
                    tidal_url, output_dir, "LOSSLESS", filename, progress_cb)
            raise

        if not filename:
            filename = f"{track_id}.flac"
        if not filename.endswith(".flac"):
            filename += ".flac"
        return self.download_file(dl_url, os.path.join(output_dir, filename),
                                  progress_cb)
