"""Amazon Music downloader - downloads tracks via Amazon API proxy."""

import json
import os
import re
import subprocess
import time
import uuid
from urllib.parse import urlparse

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
        for attempt in range(3):
            try:
                r = self.session.get(
                    f"https://amzn.afkarxyz.qzz.io/api/track/{asin}", timeout=60
                )
                if r.status_code == 401:
                    raise ValueError(
                        "Amazon proxy API returned 401 Unauthorized - "
                        "proxy credentials may have expired"
                    )
                r.raise_for_status()
                d = r.json()
                if d.get("error"):
                    raise ValueError(f"Amazon API error: {d['error']}")
                if not d.get("streamUrl"):
                    raise ValueError("No stream URL from Amazon API")
                return {"stream_url": d["streamUrl"],
                        "decryption_key": d.get("decryptionKey", "")}
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ValueError(f"Amazon proxy API unavailable: {e}")
        raise ValueError("Amazon proxy API failed after retries")

    def fetch_track_metadata(self, amazon_url_or_asin: str) -> dict:
        asin = self.extract_asin(amazon_url_or_asin)
        parsed = urlparse(amazon_url_or_asin)
        host = parsed.netloc or "music.amazon.com"
        base_url = f"https://{host}"

        cfg = self.session.get(f"{base_url}/config.json", timeout=20).json()
        headers_payload = {
            "x-amzn-authentication": json.dumps(
                {
                    "interface": "ClientAuthenticationInterface.v1_0.ClientTokenElement",
                    "accessToken": cfg.get("accessToken", ""),
                },
                separators=(",", ":"),
            ),
            "x-amzn-device-model": "WEBPLAYER",
            "x-amzn-device-width": "1920",
            "x-amzn-device-family": "WebPlayer",
            "x-amzn-device-id": cfg["deviceId"],
            "x-amzn-user-agent": self.UA,
            "x-amzn-session-id": cfg["sessionId"],
            "x-amzn-device-height": "1080",
            "x-amzn-request-id": str(uuid.uuid4()),
            "x-amzn-device-language": cfg.get("displayLanguage", "en_IN"),
            "x-amzn-currency-of-preference": "INR",
            "x-amzn-os-version": "1.0",
            "x-amzn-application-version": cfg.get("version", "1.0"),
            "x-amzn-device-time-zone": "Asia/Calcutta",
            "x-amzn-timestamp": str(int(time.time() * 1000)),
            "x-amzn-csrf": json.dumps(
                {
                    "interface": "CSRFInterface.v1_0.CSRFHeaderElement",
                    "token": cfg["csrf"]["token"],
                    "timestamp": cfg["csrf"]["ts"],
                    "rndNonce": cfg["csrf"]["rnd"],
                },
                separators=(",", ":"),
            ),
            "x-amzn-music-domain": host,
            "x-amzn-referer": "",
            "x-amzn-affiliate-tags": "",
            "x-amzn-ref-marker": "",
            "x-amzn-page-url": f"{base_url}/tracks/{asin}",
            "x-amzn-weblab-id-overrides": "",
            "x-amzn-video-player-token": "",
            "x-amzn-feature-flags": "",
            "x-amzn-has-profile-id": "",
            "x-amzn-age-band": "",
        }
        payload = {
            "id": asin,
            "userHash": json.dumps({"level": "LIBRARY_MEMBER"}, separators=(",", ":")),
            "headers": json.dumps(headers_payload, separators=(",", ":")),
        }
        resp = self.session.post(
            "https://zaz.mesk.skill.music.a2z.com/api/cosmicTrack/displayCatalogTrack",
            data=json.dumps(payload, separators=(",", ":")),
            headers={
                "Content-Type": "text/plain;charset=UTF-8",
                "Referer": f"{base_url}/",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        template = (((data.get("methods") or [{}])[0]).get("template") or {})
        result = {
            "title": "",
            "artist": "",
            "album": "",
            "isrc": "",
            "year": "",
        }
        seo_head = (template.get("templateData") or {}).get("seoHead") or {}
        for script in seo_head.get("script", []):
            inner = script.get("innerHTML", "")
            if not inner:
                continue
            try:
                schema = json.loads(inner)
            except Exception:
                continue
            if schema.get("@type") != "MusicRecording":
                continue
            result["title"] = schema.get("name", "")
            result["artist"] = (schema.get("byArtist") or {}).get("name", "")
            result["album"] = (schema.get("inAlbum") or {}).get("name", "")
            result["isrc"] = schema.get("isrcCode", "")
            published = schema.get("datePublished", "")
            if published:
                result["year"] = published[:4]
            break

        if not result["title"]:
            result["title"] = (template.get("headerText") or {}).get("text", "")
        if not result["artist"]:
            result["artist"] = template.get("headerPrimaryText", "") or template.get("headerPrimaryText2", "")
        return result

    def expand_album(self, amazon_url: str) -> list[dict]:
        """Expand an Amazon album URL into individual tracks.

        Uses SongLink to find the Deezer equivalent album, then queries
        the Deezer API for the full tracklist (with ISRCs).  Each track
        is mapped back to an Amazon track URL so the downloader can
        fetch it from Amazon's CDN.
        """
        asin = self.extract_asin(amazon_url)
        parsed = urlparse(amazon_url)
        host = parsed.netloc or "music.amazon.com"
        base_url = f"https://{host}"

        # 1. Resolve via SongLink → find Deezer album
        deezer_album_id = None
        try:
            sl = self.session.get(
                "https://api.song.link/v1-alpha.1/links",
                params={"url": f"{base_url}/albums/{asin}"},
                timeout=20,
            )
            if sl.status_code == 200:
                lbp = sl.json().get("linksByPlatform", {})
                deezer_url = lbp.get("deezer", {}).get("url", "")
                m = re.search(r"/album/(\d+)", deezer_url)
                if m:
                    deezer_album_id = m.group(1)
        except Exception:
            pass

        if not deezer_album_id:
            return []

        # 2. Fetch tracklist from Deezer
        try:
            album_r = self.session.get(
                f"https://api.deezer.com/album/{deezer_album_id}",
                timeout=15,
            ).json()
            tracks_r = self.session.get(
                f"https://api.deezer.com/album/{deezer_album_id}/tracks",
                timeout=15,
            ).json()
        except Exception:
            return []

        album_title = album_r.get("title", "")
        album_artist = album_r.get("artist", {}).get("name", "")
        cover_url = album_r.get("cover_big", "")
        album_year = (album_r.get("release_date") or "")[:4]
        total_tracks = album_r.get("nb_tracks", 0)

        tracks: list[dict] = []
        for t in tracks_r.get("data", []):
            isrc = t.get("isrc", "")
            tracks.append({
                "title": t.get("title", ""),
                "artist": t.get("artist", {}).get("name", "") or album_artist,
                "album": album_title,
                "cover_url": cover_url,
                "duration_ms": int((t.get("duration", 0) or 0) * 1000),
                "track_number": int(t.get("track_position", 0) or 0),
                "total_tracks": total_tracks,
                "disc_number": int(t.get("disk_number", 0) or 1),
                "year": album_year,
                "isrc": isrc,
                "url": "",      # resolved at download time via ISRC
                "source": "amazon",
            })
        return tracks

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
