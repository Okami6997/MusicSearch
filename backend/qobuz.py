"""Qobuz downloader - robust ISRC resolver + proxy download flow."""

import hashlib
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict

import requests


def build_qobuz_api_url(api_base: str, track_id: int, quality: str) -> str:
    if "qbz.afkarxyz.fun" in api_base or "qbz.afkarxyz.qzz.io" in api_base:
        return f"{api_base}{track_id}?quality={quality}"
    return f"{api_base}{track_id}&quality={quality}"


class QobuzDownloader:
    """Download FLAC tracks from Qobuz-compatible provider APIs."""

    API_BASE = "https://www.qobuz.com/api.json/0.2"
    OPEN_URL = "https://open.qobuz.com/track/"
    APP_ID = "712109809"
    APP_SECRET = "589be88e4538daea11f509d29e4a23b1"
    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    BUNDLE_RE = re.compile(
        r'<script[^>]+src="([^"]+/js/main\.js|/resources/[^"]+/js/main\.js)"'
    )
    API_CONFIG_RE = re.compile(
        r'app_id:"(?P<app_id>\d{9})",app_secret:"(?P<app_secret>[a-f0-9]{32})"'
    )

    APIS = [
        "https://dab.yeet.su/api/stream?trackId=",
        "https://dabmusic.xyz/api/stream?trackId=",
        "https://qbz.afkarxyz.qzz.io/api/track/",
        "https://qobuz.spotbye.qzz.io/api/track/",
        "https://qobuz.squid.wtf/api/download-music?country=US&track_id=",
        "https://dl.musicdl.me/qobuz/download",
        "https://api.zarz.moe/dl/qbz",
        "https://www.musicdl.me/api/qobuz/download",
    ]

    MUSICDL_APIS = {
        "https://www.musicdl.me/api/qobuz/download",
        "https://dl.musicdl.me/qobuz/download",
        "https://api.zarz.moe/dl/qbz",
    }

    QUALITY_FALLBACK = {
        "27": ["27", "7", "6"],
        "7": ["7", "6"],
        "6": ["6"],
        "5": ["6"],
        "": ["6"],
        "LOSSLESS": ["6"],
        "HI_RES": ["27", "7", "6"],
    }

    @dataclass
    class Credentials:
        app_id: str
        app_secret: str
        source: str = "embedded-default"
        fetched_at: float = field(default_factory=time.time)
        user_auth_token: str | None = None

    def __init__(self, timeout: float = 60.0, app_id: str = APP_ID):
        from .proxy_config import configure_session_proxy
        self.timeout = timeout
        self.app_id = app_id
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA
        configure_session_proxy(self.session)
        self.progress_callback: Callable[[int, int], None] = lambda _c, _t: None
        self._creds: QobuzDownloader.Credentials | None = None
        self._qobuz_token = os.environ.get("QOBUZ_AUTH_TOKEN")

    @staticmethod
    def _compute_signature(path: str, params: dict, timestamp: str, secret: str) -> str:
        normalized = path.strip("/").replace("/", "")
        excluded = {"app_id", "request_ts", "request_sig"}
        payload = normalized
        for key in sorted(k for k in params if k not in excluded):
            payload += key + str(params[key])
        payload += timestamp + secret
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def _scrape_credentials(self) -> Credentials:
        resp = self.session.get(f"{self.OPEN_URL}1", timeout=15)
        resp.raise_for_status()
        match = self.BUNDLE_RE.search(resp.text)
        if not match:
            raise Exception("Qobuz bundle URL not found")

        bundle_url = match.group(1)
        if bundle_url.startswith("/"):
            bundle_url = "https://open.qobuz.com" + bundle_url

        bundle = self.session.get(bundle_url, timeout=30)
        bundle.raise_for_status()
        config = self.API_CONFIG_RE.search(bundle.text)
        if not config:
            raise Exception("Qobuz app_id/app_secret not found in bundle")

        return self.Credentials(
            app_id=config.group("app_id"),
            app_secret=config.group("app_secret"),
            source=bundle_url,
            user_auth_token=self._qobuz_token,
        )

    def _get_credentials(self) -> Credentials:
        if self._creds is not None:
            return self._creds
        try:
            self._creds = self._scrape_credentials()
        except Exception:
            self._creds = self.Credentials(
                app_id=self.APP_ID,
                app_secret=self.APP_SECRET,
                user_auth_token=self._qobuz_token,
            )
        return self._creds

    def _do_signed_get(self, path: str, params: dict, use_user_token: bool = False) -> requests.Response:
        creds = self._get_credentials()
        timestamp = str(int(time.time()))
        request_sig = self._compute_signature(path, params, timestamp, creds.app_secret)
        req_params = {
            **params,
            "app_id": creds.app_id,
            "request_ts": timestamp,
            "request_sig": request_sig,
        }
        headers = {"X-App-Id": creds.app_id}
        if use_user_token and creds.user_auth_token:
            headers["X-User-Auth-Token"] = creds.user_auth_token
        return self.session.get(
            f"{self.API_BASE}/{path.strip('/')}",
            params=req_params,
            headers=headers,
            timeout=20,
        )

    def set_progress_callback(self, callback: Callable[[int, int], None]) -> None:
        self.progress_callback = callback

    def _search_by_isrc(self, isrc: str) -> Dict:
        resp = self._do_signed_get("track/search", {"query": isrc, "limit": "1"})
        if resp.status_code in (400, 401) and self._qobuz_token:
            resp = self._do_signed_get(
                "track/search",
                {"query": isrc, "limit": "1"},
                use_user_token=True,
            )
        if resp.status_code != 200:
            try:
                err_msg = resp.json().get("message", f"Status {resp.status_code}")
            except Exception:
                err_msg = f"Status {resp.status_code}"
            raise Exception(f"Qobuz API error: {err_msg}")

        data = resp.json()
        items = data.get("tracks", {}).get("items", [])
        if not items:
            raise Exception(f"Track not found for ISRC: {isrc}")
        return items[0]

    def search_by_isrc(self, isrc: str) -> dict:
        t = self._search_by_isrc(isrc)
        return {
            "id": t.get("id"),
            "title": t.get("title", ""),
            "duration": t.get("duration", 0),
            "isrc": t.get("isrc", ""),
            "bit_depth": t.get("maximum_bit_depth", 0),
            "sample_rate": t.get("maximum_sampling_rate", 0),
            "hires": t.get("hires_streamable", False),
            "artist": t.get("performer", {}).get("name", ""),
            "album": t.get("album", {}).get("title", ""),
        }

    def _download_from_standard(self, api_base: str, track_id: int, quality: str) -> str:
        headers = {"User-Agent": self.UA}
        is_musicdl = api_base in self.MUSICDL_APIS
        max_retries = 3
        for attempt in range(max_retries):
            if is_musicdl:
                mapped_quality = "hi-res-max" if quality == "27" else "hi-res" if quality == "7" else "cd"
                resp = self.session.post(
                    api_base,
                    json={
                        "quality": mapped_quality,
                        "upload_to_r2": False,
                        "url": f"{self.OPEN_URL}{track_id}",
                    },
                    headers=headers,
                    timeout=self.timeout,
                )
            else:
                url = build_qobuz_api_url(api_base, track_id, quality)
                resp = self.session.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 429 or (
                resp.status_code == 200
                and "Too many" in resp.text[:100]
            ):
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise Exception("rate limited after retries")
            break
        if resp.status_code != 200:
            raise Exception(f"status {resp.status_code}")
        if not resp.text.strip():
            raise Exception("empty body")
        if resp.text.strip().startswith("<!"):
            raise Exception("received HTML instead of JSON (service down)")

        try:
            data = resp.json()
        except Exception:
            raise Exception("invalid response")

        if isinstance(data, dict):
            if data.get("error"):
                raise Exception(data["error"])
            if data.get("url"):
                return data["url"]
            if data.get("data", {}).get("url"):
                return data["data"]["url"]
        raise Exception("invalid response payload")

    def get_download_url(self, track_id: int, quality: str = "6", allow_fallback: bool = True) -> str:
        quality_chain = self.QUALITY_FALLBACK.get(quality, [quality if quality else "6"])

        def attempt_download(qual: str) -> str:
            providers = []
            for api in self.APIS:
                providers.append({
                    "name": f"Standard({api})",
                    "func": lambda a=api: self._download_from_standard(a, track_id, qual),
                })

            random.shuffle(providers)
            last_err = None
            errors = []
            for p in providers:
                try:
                    url = p["func"]()
                    if url:
                        return url
                except Exception as e:
                    errors.append(f"{p['name']}: {e}")
                    last_err = e
            raise Exception("; ".join(errors) if errors else "all providers failed")

        errors = []
        for qual in quality_chain if allow_fallback else quality_chain[:1]:
            try:
                return attempt_download(qual)
            except Exception as e:
                errors.append(f"{qual}: {e}")
        raise Exception("all APIs and fallbacks failed: " + "; ".join(errors))

    def _stream_download(self, url: str, filepath: str, progress_cb=None) -> None:
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        temp_path = filepath + ".part"
        try:
            with self.session.get(url, stream=True, timeout=300) as resp:
                if resp.status_code != 200:
                    raise Exception(f"download failed with status {resp.status_code}")

                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                with open(temp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        cb = progress_cb or self.progress_callback
                        if cb:
                            cb(downloaded, total or 0)
            os.replace(temp_path, filepath)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def download_file(self, url: str, output_path: str, progress_cb=None) -> str:
        self._stream_download(url, output_path, progress_cb)
        return output_path

    def download_track(
        self,
        isrc: str,
        output_dir: str,
        quality: str = "6",
        filename: str = "",
        progress_cb=None,
    ) -> str:
        track = self.search_by_isrc(isrc)
        dl = self.get_download_url(int(track["id"]), quality, allow_fallback=True)
        if not filename:
            t = self._safe(track.get("title", str(track["id"])))
            a = self._safe(track.get("artist", "Unknown"))
            filename = f"{t} - {a}.flac"
        output_path = os.path.join(output_dir, filename)
        self._stream_download(dl, output_path, progress_cb)
        return output_path

    @staticmethod
    def _safe(name: str) -> str:
        for c in '<>:"/\\|?*':
            name = name.replace(c, "")
        return name.strip()
