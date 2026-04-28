"""Qobuz downloader - robust ISRC resolver + proxy download flow."""

import os
import random
import time
from typing import Callable, Dict

import requests


def build_qobuz_api_url(api_base: str, track_id: int, quality: str) -> str:
    if "qbz.afkarxyz.fun" in api_base or "qbz.afkarxyz.qzz.io" in api_base:
        return f"{api_base}{track_id}?quality={quality}"
    return f"{api_base}{track_id}&quality={quality}"


class QobuzDownloader:
    """Download FLAC tracks from Qobuz-compatible provider APIs."""

    APP_ID = "798273057"
    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    APIS = [
        "https://qbz.afkarxyz.qzz.io/api/track/",
        "https://dab.yeet.su/api/stream?trackId=",
        "https://dabmusic.xyz/api/stream?trackId=",
    ]

    def __init__(self, timeout: float = 60.0, app_id: str = APP_ID):
        self.timeout = timeout
        self.app_id = app_id
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA
        self.progress_callback: Callable[[int, int], None] = lambda _c, _t: None

    def set_progress_callback(self, callback: Callable[[int, int], None]) -> None:
        self.progress_callback = callback

    def _search_by_isrc(self, isrc: str) -> Dict:
        url = (
            f"https://www.qobuz.com/api.json/0.2/track/search"
            f"?query={isrc}&limit=1&app_id={self.app_id}"
        )
        headers = {"User-Agent": self.UA}
        resp = self.session.get(url, headers=headers, timeout=30)
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
        url = build_qobuz_api_url(api_base, track_id, quality)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/145.0.0.0 Safari/537.36"
            )
        }
        max_retries = 3
        for attempt in range(max_retries):
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
        quality_code = quality if quality not in ("", "5") else "6"

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

        try:
            return attempt_download(quality_code)
        except Exception:
            if allow_fallback:
                if quality_code == "27":
                    try:
                        return attempt_download("7")
                    except Exception:
                        pass
                    quality_code = "7"
                if quality_code == "7":
                    try:
                        return attempt_download("6")
                    except Exception:
                        pass
            raise Exception("all APIs and fallbacks failed")

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
