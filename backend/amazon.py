"""Amazon Music downloader - downloads tracks via Amazon API proxy."""

import os
import re
import subprocess

import requests


class AmazonDownloader:
    """Download tracks from Amazon Music via API proxy."""

    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )
    ASIN_RE = re.compile(r"(B[0-9A-Z]{9})")

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA

    def extract_asin(self, url: str) -> str:
        m = self.ASIN_RE.search(url)
        if not m:
            raise ValueError(f"No ASIN in URL: {url}")
        return m.group(1)

    def get_stream(self, asin: str) -> dict:
        r = self.session.get(
            f"https://amzn.afkarxyz.qzz.io/api/track/{asin}", timeout=60
        )
        r.raise_for_status()
        d = r.json()
        if not d.get("streamUrl"):
            raise ValueError("No stream URL from Amazon API")
        return {"stream_url": d["streamUrl"],
                "decryption_key": d.get("decryptionKey", "")}

    def download_track(self, amazon_url: str, output_dir: str,
                       filename: str = "", progress_cb=None) -> str:
        """Download a track from an Amazon Music URL."""
        asin = self.extract_asin(amazon_url)
        return self.download_by_asin(asin, output_dir, filename, progress_cb)

    def download_by_asin(self, asin: str, output_dir: str,
                         filename: str = "", progress_cb=None) -> str:
        info = self.get_stream(asin)
        tmp = os.path.join(output_dir, f"{asin}.m4a")

        resp = self.session.get(info["stream_url"], stream=True, timeout=120)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(65536):
                f.write(chunk)
                done += len(chunk)
                if progress_cb and total:
                    progress_cb(done, total)

        codec = self._codec(tmp)
        ext = ".flac" if codec == "flac" else ".m4a"
        if not filename:
            filename = f"{asin}{ext}"

        out = os.path.join(output_dir, filename)
        if not out.endswith(ext):
            out = os.path.splitext(out)[0] + ext

        if info["decryption_key"]:
            self._decrypt(tmp, out, info["decryption_key"], ext)
        else:
            if tmp != out:
                os.rename(tmp, out)
        return out

    @staticmethod
    def _codec(path: str) -> str:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-select_streams", "a:0",
                 "-show_entries", "stream=codec_name",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=30,
            )
            return r.stdout.strip()
        except Exception:
            return "aac"

    @staticmethod
    def _decrypt(src: str, dst: str, key: str, ext: str):
        try:
            cmd = ["ffmpeg", "-y", "-decryption_key", key, "-i", src]
            if ext == ".flac":
                cmd += ["-c:a", "flac", "-compression_level", "8"]
            else:
                cmd += ["-c:a", "copy"]
            cmd.append(dst)
            subprocess.run(cmd, capture_output=True, check=True, timeout=300)
        finally:
            if os.path.exists(src) and src != dst:
                os.remove(src)
