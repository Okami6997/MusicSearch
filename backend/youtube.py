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
        # Primary path: parse YouTube Music search results directly.
        # Fallback to ytsearch only if Music search is sparse.

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
        else:
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

        if len(results) < max(8, limit // 2):
            results = self._merge_unique_tracks(results, self._playlist_tracks_from_search(query, limit), limit)

        if len(results) < max(4, limit // 4):
            results = self._merge_unique_tracks(results, self._yt_dlp_youtube_search_tracks(query, limit), limit)

        return results

    def _merge_unique_tracks(self, base: list[dict], extra: list[dict], limit: int) -> list[dict]:
        out = list(base or [])
        seen = set((t.get("id") or "") for t in out)
        for t in (extra or []):
            vid = t.get("id") or ""
            if not vid or vid in seen:
                continue
            seen.add(vid)
            out.append(t)
            if len(out) >= limit:
                break
        return out

    def _yt_dlp_youtube_search_tracks(self, query: str, limit: int) -> list[dict]:
        """Fallback search via yt_dlp ytsearch when YT Music parsing is sparse."""
        try:
            from yt_dlp import YoutubeDL
        except Exception:
            return []

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
            "noplaylist": True,
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            entries = (info or {}).get("entries", []) or []
        except Exception as e:
            print(f"[YouTube Music] yt_dlp fallback search error: {e}")
            return []

        out = []
        seen = set()
        for e in entries:
            vid = (e or {}).get("id", "")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            out.append({
                "id": vid,
                "title": (e or {}).get("title", ""),
                "artist": (e or {}).get("uploader", "") or (e or {}).get("channel", ""),
                "album": "",
                "cover_url": (e or {}).get("thumbnail", "") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "duration_ms": int(((e or {}).get("duration") or 0) * 1000),
                "url": f"https://music.youtube.com/watch?v={vid}",
            })
            if len(out) >= limit:
                break
        return out

    def _playlist_tracks_from_search(self, query: str, limit: int) -> list[dict]:
        """Expand top playlist hits from YouTube Music search into track-like entries."""
        try:
            from yt_dlp import YoutubeDL
        except Exception:
            return []

        search_url = f"https://music.youtube.com/search?q={quote(query)}"
        try:
            resp = self.session.get(search_url, headers={"User-Agent": self.UA}, timeout=20)
            resp.raise_for_status()
            html = resp.text
        except Exception:
            return []

        playlist_ids = []
        seen_ids = set()
        for m in re.finditer(r'"playlistId":"([A-Za-z0-9_-]{10,})"', html):
            pid = m.group(1)
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            playlist_ids.append(pid)
            if len(playlist_ids) >= 3:
                break

        if not playlist_ids:
            return []

        out = []
        for pid in playlist_ids:
            remain = max(1, limit - len(out))
            fetch_n = min(25, remain)
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": True,
                "noplaylist": False,
                "playlistend": fetch_n,
            }
            try:
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(f"https://music.youtube.com/playlist?list={pid}", download=False)
                entries = (info or {}).get("entries", []) or []
                for e in entries:
                    vid = (e or {}).get("id", "")
                    if not vid:
                        continue
                    out.append({
                        "id": vid,
                        "title": (e or {}).get("title", ""),
                        "artist": (e or {}).get("uploader", "") or (e or {}).get("channel", ""),
                        "album": (info or {}).get("title", ""),
                        "cover_url": (e or {}).get("thumbnail", "") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                        "duration_ms": int(((e or {}).get("duration") or 0) * 1000),
                        "url": f"https://music.youtube.com/watch?v={vid}",
                    })
                    if len(out) >= limit:
                        return out
            except Exception:
                continue

        return out

    def expand_album(self, url_or_id: str) -> list[dict]:
        """Expand a YouTube Music album/playlist URL into individual tracks."""
        from yt_dlp import YoutubeDL

        # Normalise: accept playlist ID, browse ID, or full URL
        if url_or_id.startswith("http"):
            playlist_url = url_or_id
        elif url_or_id.startswith("OLAK") or url_or_id.startswith("PL"):
            playlist_url = f"https://music.youtube.com/playlist?list={url_or_id}"
        elif url_or_id.startswith("MPRE"):
            playlist_url = f"https://music.youtube.com/browse/{url_or_id}"
        else:
            playlist_url = f"https://music.youtube.com/playlist?list={url_or_id}"

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
            "noplaylist": False,
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)
        except Exception:
            return []

        if not info:
            return []

        album_title = info.get("title", "")
        entries = info.get("entries", []) or []
        total_tracks = len(entries)
        tracks = []
        for idx, e in enumerate(entries):
            if not e:
                continue
            vid = e.get("id", "")
            if not vid:
                continue
            tracks.append({
                "title": e.get("title", ""),
                "artist": e.get("uploader", "") or e.get("channel", ""),
                "album": album_title,
                "cover_url": e.get("thumbnail", "") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "duration_ms": int((e.get("duration") or 0) * 1000),
                "track_number": idx + 1,
                "total_tracks": total_tracks,
                "disc_number": 1,
                "year": "",
                "url": f"https://music.youtube.com/watch?v={vid}",
                "source": "youtube",
            })
        return tracks

    def expand_playlist(self, url_or_id: str) -> list[dict]:
        """Expand a YouTube Music playlist URL into individual tracks.

        Delegates to expand_album since yt-dlp handles both identically.
        """
        return self.expand_album(url_or_id)

    # ── Download APIs ────────────────────────────────────────────

    def _download_with_ytdlp(self, video_id: str, output_path: str,
                              progress_cb=None) -> str:
        """Download audio via yt-dlp (primary engine). Returns output_path."""
        import subprocess, shutil

        if not shutil.which("yt-dlp"):
            raise ValueError("yt-dlp not found in PATH")

        url = f"https://music.youtube.com/watch?v={video_id}"
        tmp_template = os.path.splitext(output_path)[0] + ".%(ext)s"
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--output", tmp_template,
            "--no-progress",
            "--quiet",
        ]
        # Pass node runtime if available so yt-dlp can solve JS challenges
        node_path = shutil.which("node")
        if node_path:
            cmd += ["--js-runtimes", f"node:{node_path}"]
        cmd.append(url)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                raise ValueError(f"yt-dlp exited {result.returncode}: {result.stderr.strip()}")
            if os.path.exists(output_path):
                return output_path
            base = os.path.splitext(output_path)[0]
            for ext in (".mp3", ".m4a", ".opus", ".webm"):
                candidate = base + ext
                if os.path.exists(candidate):
                    if candidate != output_path:
                        os.rename(candidate, output_path)
                    return output_path
            raise ValueError("yt-dlp finished but output file not found")
        except subprocess.TimeoutExpired:
            raise ValueError("yt-dlp timed out")

    def _download_with_ytdlp_search(self, track_name: str, artist_name: str,
                                     output_path: str, progress_cb=None) -> str:
        """Use yt-dlp ytsearch to find and download a track by text. Returns output_path."""
        import subprocess, shutil

        if not shutil.which("yt-dlp"):
            raise ValueError("yt-dlp not found in PATH")

        query = f"{track_name} {artist_name} audio"
        tmp_template = os.path.splitext(output_path)[0] + ".%(ext)s"
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--output", tmp_template,
            "--no-progress",
            "--quiet",
            f"ytsearch1:{query}",
        ]
        node_path = shutil.which("node")
        if node_path:
            cmd.insert(-1, "--js-runtimes")
            cmd.insert(-1, f"node:{node_path}")
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                raise ValueError(f"yt-dlp search exited {result.returncode}: {result.stderr.strip()}")
            if os.path.exists(output_path):
                return output_path
            base = os.path.splitext(output_path)[0]
            for ext in (".mp3", ".m4a", ".opus", ".webm"):
                candidate = base + ext
                if os.path.exists(candidate):
                    if candidate != output_path:
                        os.rename(candidate, output_path)
                    return output_path
            raise ValueError("yt-dlp search finished but output file not found")
        except subprocess.TimeoutExpired:
            raise ValueError("yt-dlp search timed out")

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

    def _download_via_proxy(self, video_id: str, output_path: str,
                            progress_cb=None) -> str:
        """Download via SpotubeDL/Cobalt proxy APIs. Returns output_path."""
        dl_url = self._request_spotube_dl(video_id)
        if not dl_url:
            dl_url = self._request_cobalt(video_id)
        if not dl_url:
            raise ValueError("All YouTube proxy APIs failed")

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

    # ── Public interface ─────────────────────────────────────────

    def download_track(self, youtube_url: str, output_dir: str,
                       filename: str = "", progress_cb=None) -> str:
        """Download a track from a YouTube Music URL.

        Tries yt-dlp first (reliable, maintained), falls back to
        SpotubeDL/Cobalt proxy APIs. Returns the path to the MP3 file.
        """
        video_id = self.extract_video_id(youtube_url)

        if not filename:
            filename = f"{video_id}.mp3"
        if not filename.endswith(".mp3"):
            filename = os.path.splitext(filename)[0] + ".mp3"

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)

        errors = []
        try:
            return self._download_with_ytdlp(video_id, output_path, progress_cb)
        except Exception as e:
            errors.append(f"yt-dlp: {e}")

        try:
            return self._download_via_proxy(video_id, output_path, progress_cb)
        except Exception as e:
            errors.append(f"proxy: {e}")

        raise ValueError("YouTube download failed: " + "; ".join(errors))

    def search_and_download(self, track_name: str, artist_name: str,
                            output_dir: str, filename: str = "",
                            progress_cb=None) -> str:
        """Search YouTube for a track by name+artist and download it.

        Tries yt-dlp ytsearch first (no URL needed), falls back to
        HTML scrape + download_track.
        """
        if not filename:
            filename = f"{track_name}.mp3"
        if not filename.endswith(".mp3"):
            filename = os.path.splitext(filename)[0] + ".mp3"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)

        errors = []
        try:
            return self._download_with_ytdlp_search(
                track_name, artist_name, output_path, progress_cb)
        except Exception as e:
            errors.append(f"yt-dlp search: {e}")

        # Fallback: HTML scrape for URL then download
        try:
            yt_url = self.search_video(track_name, artist_name)
            if not yt_url:
                raise ValueError("No results from HTML search")
            return self.download_track(yt_url, output_dir, filename, progress_cb)
        except Exception as e:
            errors.append(f"html search: {e}")

        raise ValueError(
            f"YouTube search failed for '{track_name}' by '{artist_name}': "
            + "; ".join(errors)
        )
