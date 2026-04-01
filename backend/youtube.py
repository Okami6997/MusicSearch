"""YouTube Music downloader - downloads tracks via proxy APIs (lossy MP3)."""

import os
import re
from urllib.parse import quote

import requests


class YouTubeDownloader:
    """Download audio from YouTube Music via SpotubeDL / Cobalt proxy APIs.

    Output is MP3 320kbps (YouTube does not offer lossless).
    Use as a last-resort fallback after Tidal/Qobuz/Amazon.
    """

    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA
        self.session.timeout = 120

    # ── URL helpers ──────────────────────────────────────────────

    @staticmethod
    def extract_video_id(url: str) -> str:
        """Extract an 11-char YouTube video ID from any YouTube URL."""
        m = re.search(
            r"(?:v=|/v/|youtu\.be/|/embed/)([a-zA-Z0-9_-]{11})", url
        )
        if m:
            return m.group(1)
        raise ValueError(f"Could not extract YouTube video ID from: {url}")

    def search_video(self, track_name: str, artist_name: str) -> str:
        """Search YouTube for a track and return the first video URL.

        Uses YouTube's HTML search page — no API key required.
        """
        query = quote(f"{track_name} {artist_name} audio")
        search_url = f"https://www.youtube.com/results?search_query={query}"

        try:
            resp = self.session.get(search_url, timeout=15)
            resp.raise_for_status()
            m = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp.text)
            if m:
                return f"https://music.youtube.com/watch?v={m.group(1)}"
        except Exception as e:
            print(f"[YouTube] Search error: {e}")

        return ""

    def search_tracks(self, query: str, limit: int = 20) -> list[dict]:
        """Search YouTube Music for tracks and return a list of results.

        Uses YouTube Music's search page — no API key required.
        """
        search_url = (
            f"https://music.youtube.com/search?q={quote(query)}"
        )
        try:
            headers = {
                "User-Agent": self.UA,
                "Accept-Language": "en-US,en;q=0.9",
            }
            resp = self.session.get(search_url, headers=headers, timeout=15)
            resp.raise_for_status()
            text = resp.text
        except Exception as e:
            print(f"[YouTube Music] Search error: {e}")
            return []

        # Extract video data from the page's initial data JSON
        results = []
        seen_ids = set()

        # Find all videoId + title pairs from the page
        # YouTube embeds data in ytInitialData JSON
        import json as _json
        m = re.search(r'var ytInitialData\s*=\s*(\{.+?\});', text)
        if not m:
            # Fallback: scrape videoId occurrences
            for vid_match in re.finditer(
                r'"videoId":"([a-zA-Z0-9_-]{11})"', text
            ):
                vid = vid_match.group(1)
                if vid not in seen_ids and len(results) < limit:
                    seen_ids.add(vid)
                    results.append({
                        "id": vid,
                        "title": "",
                        "artist": "",
                        "album": "",
                        "cover_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                        "duration_ms": 0,
                        "url": f"https://music.youtube.com/watch?v={vid}",
                    })
            return results

        try:
            data = _json.loads(m.group(1))
            # Navigate the nested structure to find music results
            contents = (
                data.get("contents", {})
                .get("tabbedSearchResultsRenderer", {})
                .get("tabs", [{}])[0]
                .get("tabRenderer", {})
                .get("content", {})
                .get("sectionListRenderer", {})
                .get("contents", [])
            )
            for section in contents:
                items = (
                    section.get("musicShelfRenderer", {})
                    .get("contents", [])
                )
                for item in items:
                    if len(results) >= limit:
                        break
                    renderer = item.get(
                        "musicResponsiveListItemRenderer", {}
                    )
                    # Extract video ID
                    overlay = renderer.get("overlay", {})
                    play_btn = (
                        overlay
                        .get("musicItemThumbnailOverlayRenderer", {})
                        .get("content", {})
                        .get("musicPlayButtonRenderer", {})
                        .get("playNavigationEndpoint", {})
                        .get("watchEndpoint", {})
                    )
                    vid = play_btn.get("videoId", "")
                    if not vid or vid in seen_ids:
                        continue
                    seen_ids.add(vid)

                    # Extract title and artist from flex columns
                    flex_cols = renderer.get("flexColumns", [])
                    title = ""
                    artist = ""
                    album = ""
                    if len(flex_cols) > 0:
                        runs = (
                            flex_cols[0]
                            .get("musicResponsiveListItemFlexColumnRenderer", {})
                            .get("text", {})
                            .get("runs", [])
                        )
                        if runs:
                            title = runs[0].get("text", "")
                    if len(flex_cols) > 1:
                        runs = (
                            flex_cols[1]
                            .get("musicResponsiveListItemFlexColumnRenderer", {})
                            .get("text", {})
                            .get("runs", [])
                        )
                        parts = [r.get("text", "") for r in runs]
                        # Format: "Artist • Album • Duration" separated by " • "
                        text_parts = "".join(parts).split(" \u2022 ")
                        if text_parts:
                            artist = text_parts[0].strip()
                        if len(text_parts) > 1:
                            album = text_parts[1].strip()

                    # Extract thumbnail
                    thumbs = (
                        renderer.get("thumbnail", {})
                        .get("musicThumbnailRenderer", {})
                        .get("thumbnail", {})
                        .get("thumbnails", [])
                    )
                    cover = ""
                    if thumbs:
                        cover = thumbs[-1].get("url", "")

                    results.append({
                        "id": vid,
                        "title": title,
                        "artist": artist,
                        "album": album,
                        "cover_url": cover or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                        "duration_ms": 0,
                        "url": f"https://music.youtube.com/watch?v={vid}",
                    })
        except Exception as e:
            print(f"[YouTube Music] Parse error: {e}")
            # Fallback to simple videoId scraping
            for vid_match in re.finditer(
                r'"videoId":"([a-zA-Z0-9_-]{11})"', text
            ):
                vid = vid_match.group(1)
                if vid not in seen_ids and len(results) < limit:
                    seen_ids.add(vid)
                    results.append({
                        "id": vid,
                        "title": "",
                        "artist": "",
                        "album": "",
                        "cover_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                        "duration_ms": 0,
                        "url": f"https://music.youtube.com/watch?v={vid}",
                    })

        return results

    # ── Download APIs ────────────────────────────────────────────

    def _request_spotube_dl(self, video_id: str) -> str:
        """Try SpotubeDL proxy engines for an MP3 download URL."""
        for engine in ("v1", "v3", "v2"):
            api_url = (
                f"https://spotubedl.com/api/download/{video_id}"
                f"?engine={engine}&format=mp3&quality=320"
            )
            try:
                resp = self.session.get(api_url, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    dl = data.get("url", "")
                    if dl:
                        if dl.startswith("/"):
                            dl = "https://spotubedl.com" + dl
                        return dl
            except Exception:
                continue
        return ""

    def _request_cobalt(self, video_id: str) -> str:
        """Fallback: Cobalt API for an MP3 download URL."""
        payload = {
            "url": f"https://music.youtube.com/watch?v={video_id}",
            "audioFormat": "mp3",
            "audioBitrate": "320",
            "downloadMode": "audio",
            "filenameStyle": "basic",
            "disableMetadata": True,
        }
        try:
            resp = self.session.post(
                "https://api.qwkuns.me",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") in ("tunnel", "redirect") and data.get("url"):
                    return data["url"]
        except Exception:
            pass
        return ""

    def _get_download_url(self, video_id: str) -> str:
        """Try all proxy APIs and return the first working download URL."""
        url = self._request_spotube_dl(video_id)
        if url:
            return url
        url = self._request_cobalt(video_id)
        if url:
            return url
        raise ValueError("All YouTube download APIs failed")

    # ── Public interface ─────────────────────────────────────────

    def download_track(self, youtube_url: str, output_dir: str,
                       filename: str = "", progress_cb=None) -> str:
        """Download a track from a YouTube Music URL.

        Returns the path to the downloaded MP3 file.
        """
        video_id = self.extract_video_id(youtube_url)
        dl_url = self._get_download_url(video_id)

        if not filename:
            filename = f"{video_id}.mp3"
        if not filename.endswith(".mp3"):
            filename = os.path.splitext(filename)[0] + ".mp3"

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)

        resp = self.session.get(dl_url, stream=True, timeout=120)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb and total:
                        progress_cb(done, total)

        return output_path

    def search_and_download(self, track_name: str, artist_name: str,
                            output_dir: str, filename: str = "",
                            progress_cb=None) -> str:
        """Search YouTube for a track by name+artist and download it.

        Useful when no YouTube URL is available from SongLink.
        """
        yt_url = self.search_video(track_name, artist_name)
        if not yt_url:
            raise ValueError(
                f"YouTube search found no results for '{track_name}' by '{artist_name}'"
            )
        return self.download_track(yt_url, output_dir, filename, progress_cb)
