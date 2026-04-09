"""Tidal downloader - downloads tracks via Tidal API proxies."""

import base64
import json
import os
import random
import subprocess

import requests


class TidalDownloader:
    """Download tracks from Tidal via proxy APIs."""

    APIS = [
        "https://hifi-one.spotisaver.net",
        "https://hifi-two.spotisaver.net",
    ]
    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    def __init__(self, api_url: str = ""):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA
        self.api_url = api_url or self._pick_api()

    def _pick_api(self) -> str:
        for api in self.APIS:
            try:
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
        url = f"{self.api_url}/track/?id={track_id}&quality={quality}"
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        body = resp.json()

        if isinstance(body, dict) and body.get("data", {}).get("manifest"):
            return "MANIFEST:" + body["data"]["manifest"]
        if isinstance(body, list):
            for item in body:
                if item.get("OriginalTrackUrl"):
                    return item["OriginalTrackUrl"]
        raise ValueError("No download URL in Tidal response")

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
                if progress_cb and total:
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

        dl_url = None
        errors = []
        original_api = self.api_url

        try:
            dl_url = self.get_download_url(track_id, quality)
        except Exception as e:
            errors.append(str(e))

        if not dl_url:
            apis = list(self.APIS)
            random.shuffle(apis)
            for api in apis:
                if api == self.api_url:
                    continue
                try:
                    self.api_url = api
                    dl_url = self.get_download_url(track_id, quality)
                    if dl_url:
                        break
                except Exception as e:
                    errors.append(f"{api}: {e}")
            if not dl_url:
                self.api_url = original_api

        if not dl_url:
            if quality == "HI_RES":
                return self.download_track(
                    tidal_url, output_dir, "LOSSLESS", filename, progress_cb)
            raise ValueError(f"All Tidal APIs failed: {'; '.join(errors)}")

        if not filename:
            filename = f"{track_id}.flac"
        if not filename.endswith(".flac"):
            filename += ".flac"
        return self.download_file(dl_url, os.path.join(output_dir, filename),
                                  progress_cb)
