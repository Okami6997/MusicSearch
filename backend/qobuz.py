"""Qobuz downloader - downloads FLAC tracks via Qobuz API proxies."""

import os
import random

import requests


class QobuzDownloader:
    """Download FLAC tracks from Qobuz via proxy APIs."""

    APP_ID = "798273057"
    APIS = [
        "https://dab.yeet.su/api/stream?trackId=",
        "https://dabmusic.xyz/api/stream?trackId=",
        "https://qbz.afkarxyz.qzz.io/api/track/",
    ]
    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA

    def search_by_isrc(self, isrc: str) -> dict:
        url = (f"https://www.qobuz.com/api.json/0.2/track/search"
               f"?query={isrc}&limit=1&app_id={self.APP_ID}")
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        items = resp.json().get("tracks", {}).get("items", [])
        if not items:
            raise ValueError(f"Track not found on Qobuz for ISRC: {isrc}")
        t = items[0]
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

    def get_download_url(self, track_id: int, quality: str = "6") -> str:
        if not quality or quality == "5":
            quality = "6"

        apis = list(self.APIS)
        random.shuffle(apis)
        errors = []
        for base in apis:
            try:
                sep = "?" if "qbz.afkarxyz" in base else "&"
                url = f"{base}{track_id}{sep}quality={quality}"
                r = self.session.get(url, timeout=60)
                if r.status_code != 200:
                    raise ValueError(f"status {r.status_code}")
                data = r.json()
                dl = (data.get("url")
                      or data.get("data", {}).get("url", ""))
                if dl:
                    return dl
                raise ValueError("No URL in response")
            except Exception as e:
                errors.append(f"{base}: {e}")

        if quality == "27":
            return self.get_download_url(track_id, "7")
        if quality == "7":
            return self.get_download_url(track_id, "6")
        raise ValueError(f"All Qobuz APIs failed: {'; '.join(errors)}")

    def download_file(self, url: str, output_path: str,
                      progress_cb=None) -> str:
        resp = self.session.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(65536):
                f.write(chunk)
                done += len(chunk)
                if progress_cb and total:
                    progress_cb(done, total)
        return output_path

    def download_track(self, isrc: str, output_dir: str, quality: str = "6",
                       filename: str = "", progress_cb=None) -> str:
        track = self.search_by_isrc(isrc)
        dl = self.get_download_url(track["id"], quality)
        if not filename:
            t = self._safe(track.get("title", str(track["id"])))
            a = self._safe(track.get("artist", "Unknown"))
            filename = f"{t} - {a}.flac"
        return self.download_file(dl, os.path.join(output_dir, filename),
                                  progress_cb)

    @staticmethod
    def _safe(name: str) -> str:
        for c in '<>:"/\\|?*':
            name = name.replace(c, "")
        return name.strip()
