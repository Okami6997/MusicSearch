"""Spotify downloader - downloads FLAC tracks via SpotiDownloader proxy API."""

import json
import os
import re
import time

import requests


class SpotifyDownloader:
    """Download FLAC tracks from Spotify via SpotiDownloader proxy API.

    No Spotify account required. Uses spotidownloader.com which provides
    FLAC downloads when available, falling back gracefully if not.
    """

    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/155.0.0.0 Safari/537.36"
    )

    _cached_token = None

    def __init__(self):
        from .proxy_config import configure_session_proxy
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA
        self.session.timeout = 15
        configure_session_proxy(self.session)

    # ── Token management ─────────────────────────────────────────

    def _fetch_token(self) -> str:
        """Get a session token from the SpotiDownloader API.

        The service now requires Cloudflare Turnstile verification.
        We attempt multiple approaches to obtain a valid token.
        """
        if SpotifyDownloader._cached_token:
            return SpotifyDownloader._cached_token

        # Try the new session endpoint with an anonymous request
        for attempt in range(3):
            try:
                resp = self.session.post(
                    "https://api.spotidownloader.com/session",
                    json={"token": ""},
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Origin": "https://spotidownloader.com",
                        "Referer": "https://spotidownloader.com/",
                        "User-Agent": self.UA,
                    },
                    timeout=10,
                )
                data = resp.json()
                if data.get("success") and data.get("token"):
                    SpotifyDownloader._cached_token = data["token"]
                    return data["token"]
            except Exception:
                pass

            # Fallback: try legacy token endpoint
            try:
                resp = self.session.get(
                    "https://spdl.afkarxyz.fun/token", timeout=5
                )
                if resp.status_code == 200:
                    token = resp.json().get("token", "")
                    if token:
                        SpotifyDownloader._cached_token = token
                        return token
            except Exception:
                pass

            if attempt < 2:
                time.sleep(1)

        raise ValueError(
            "SpotiDownloader token unavailable - service requires "
            "Cloudflare Turnstile verification"
        )

    # ── URL helpers ──────────────────────────────────────────────

    @staticmethod
    def extract_track_id(spotify_url: str) -> str:
        """Extract the Spotify track ID from a URL or URI."""
        m = re.search(
            r"(?:open\.spotify\.com/track/|spotify:track:)"
            r"([a-zA-Z0-9]{22})",
            spotify_url,
        )
        if m:
            return m.group(1)
        # Fallback: take last path segment
        return spotify_url.split("/")[-1].split("?")[0].strip()

    # ── Metadata ─────────────────────────────────────────────────

    def fetch_track_metadata(self, spotify_url: str) -> dict:
        """Fetch track metadata from Spotify's embed page (no auth required).

        Returns dict with title, artist, album, year, cover_url, duration_ms.
        """
        track_id = self.extract_track_id(spotify_url)
        resp = self.session.get(
            f"https://open.spotify.com/embed/track/{track_id}",
            headers={"User-Agent": self.UA},
            timeout=10,
        )
        resp.raise_for_status()
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            resp.text,
        )
        if not m:
            return {}
        data = json.loads(m.group(1))
        entity = (
            data.get("props", {})
            .get("pageProps", {})
            .get("state", {})
            .get("data", {})
            .get("entity", {})
        )
        if not entity:
            return {}
        artists = entity.get("artists", [])
        artist = ", ".join(a.get("name", "") for a in artists) if artists else ""
        release = (entity.get("releaseDate") or {}).get("isoString", "")
        year = release[:4] if release else ""
        images = entity.get("visualIdentity", {}).get("image", [])
        cover_url = ""
        for img in images:
            if img.get("maxHeight", 0) >= 300:
                cover_url = img.get("url", "")
                break
        if not cover_url and images:
            cover_url = images[0].get("url", "")
        return {
            "title": entity.get("title") or entity.get("name", ""),
            "artist": artist,
            "year": year,
            "cover_url": cover_url,
            "duration_ms": entity.get("duration", 0),
        }

    # ── Download ─────────────────────────────────────────────────

    def _get_flac_link(self, track_id: str, token: str) -> str:
        """Request a FLAC download link from SpotiDownloader."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://spotidownloader.com",
            "Referer": "https://spotidownloader.com/",
            "User-Agent": self.UA,
        }
        resp = self.session.post(
            "https://api.spotidownloader.com/download",
            json={"id": track_id, "flac": True},
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 403:
            SpotifyDownloader._cached_token = None
            raise ValueError(
                "SpotiDownloader returned 403 - token expired or "
                "Turnstile verification required"
            )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            raise ValueError("SpotiDownloader API returned success=false")

        flac_link = data.get("linkFlac", "")
        if flac_link and ".flac" in flac_link:
            return flac_link

        standard_link = data.get("link", "")
        if standard_link and ".flac" in standard_link:
            return standard_link

        raise ValueError(
            "SpotiDownloader did not return a FLAC link for this track"
        )

    def _stream_download(self, url: str, output_path: str, token: str,
                         progress_cb=None) -> str:
        """Download the FLAC file with progress reporting."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Origin": "https://spotidownloader.com",
            "Referer": "https://spotidownloader.com/",
        }
        tmp = output_path + ".part"
        resp = self.session.get(
            url, headers=headers, stream=True, timeout=120
        )
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(262144):  # 256 KB
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)

        os.replace(tmp, output_path)
        return output_path

    # ── Public interface ─────────────────────────────────────────

    def download_track(self, spotify_url: str, output_dir: str,
                       filename: str = "", progress_cb=None) -> str:
        """Download a FLAC track given a Spotify URL.

        Returns the path to the downloaded FLAC file.
        """
        track_id = self.extract_track_id(spotify_url)
        token = self._fetch_token()
        flac_url = self._get_flac_link(track_id, token)

        if not filename:
            filename = f"{track_id}.flac"
        if not filename.endswith(".flac"):
            filename = os.path.splitext(filename)[0] + ".flac"

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)

        return self._stream_download(
            flac_url, output_path, token, progress_cb
        )

    def expand_album(self, spotify_url_or_id: str) -> list[dict]:
        """Expand a Spotify album URL into individual track records via embed page."""
        if "/album/" in str(spotify_url_or_id):
            m = re.search(r"/album/([a-zA-Z0-9]+)", str(spotify_url_or_id))
            album_id = m.group(1) if m else str(spotify_url_or_id)
        else:
            album_id = str(spotify_url_or_id)

        try:
            resp = self.session.get(
                f"https://open.spotify.com/embed/album/{album_id}",
                headers={"User-Agent": self.UA},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                resp.text,
            )
            if not m:
                return []
            data = json.loads(m.group(1))
            entity = (
                data.get("props", {})
                .get("pageProps", {})
                .get("state", {})
                .get("data", {})
                .get("entity", {})
            )
            if not entity:
                return []

            album_name = entity.get("title") or entity.get("name", "")
            album_artists = entity.get("artists") or []
            album_artist = ", ".join(a.get("name", "") for a in album_artists) if album_artists else entity.get("subtitle", "")
            release = (entity.get("releaseDate") or {}).get("isoString", "")
            year = release[:4] if release else ""
            images = entity.get("visualIdentity", {}).get("image", [])
            cover_url = ""
            for img in images:
                if img.get("maxHeight", 0) >= 300:
                    cover_url = img.get("url", "")
                    break
            if not cover_url and images:
                cover_url = images[0].get("url", "")

            track_list = entity.get("trackList", [])
            total_tracks = len(track_list)
            tracks = []
            for idx, t in enumerate(track_list):
                # Artist is in 'subtitle' or 'artists' list
                artists = t.get("artists", [])
                if artists:
                    artist = ", ".join(a.get("name", "") for a in artists)
                else:
                    artist = t.get("subtitle", "") or album_artist
                # Track ID is in the URI (spotify:track:XXXX)
                uri = t.get("uri", "")
                track_id = uri.split(":")[-1] if "track:" in uri else ""
                track_url = f"https://open.spotify.com/track/{track_id}" if track_id else ""
                tracks.append({
                    "title": t.get("title") or t.get("name", ""),
                    "artist": artist,
                    "album": album_name,
                    "cover_url": cover_url,
                    "duration_ms": t.get("duration", 0),
                    "track_number": idx + 1,
                    "total_tracks": total_tracks,
                    "disc_number": 1,
                    "year": year,
                    "url": track_url,
                    "source": "spotify",
                })
            return tracks
        except Exception:
            return []

    def expand_playlist(self, spotify_url_or_id: str) -> list[dict]:
        """Expand a Spotify playlist URL into individual track records via embed page."""
        import re
        if "/playlist/" in str(spotify_url_or_id):
            m = re.search(r"/playlist/([a-zA-Z0-9]+)", str(spotify_url_or_id))
            playlist_id = m.group(1) if m else str(spotify_url_or_id)
        else:
            playlist_id = str(spotify_url_or_id)

        try:
            resp = self.session.get(
                f"https://open.spotify.com/embed/playlist/{playlist_id}",
                headers={"User-Agent": self.UA},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                resp.text,
            )
            if not m:
                return []
            data = json.loads(m.group(1))
            entity = (
                data.get("props", {})
                .get("pageProps", {})
                .get("state", {})
                .get("data", {})
                .get("entity", {})
            )
            if not entity:
                return []

            playlist_name = entity.get("title") or entity.get("name", "")
            cover_art = entity.get("coverArt", {})
            cover_url = ""
            if isinstance(cover_art, dict):
                sources = cover_art.get("sources", [])
                for src in sources:
                    if (src.get("height") or 0) >= 300:
                        cover_url = src.get("url", "")
                        break
                if not cover_url and sources:
                    cover_url = sources[0].get("url", "")
            # Fallback to visualIdentity if coverArt didn't have sources
            if not cover_url:
                images = entity.get("visualIdentity", {}).get("image", [])
                for img in images:
                    if (img.get("maxHeight") or 0) >= 300:
                        cover_url = img.get("url", "")
                        break
                if not cover_url and images:
                    cover_url = images[0].get("url", "")

            track_list = entity.get("trackList", [])
            tracks = []
            for idx, t in enumerate(track_list):
                artists = t.get("artists", [])
                if artists:
                    artist = ", ".join(a.get("name", "") for a in artists)
                else:
                    artist = t.get("subtitle", "")
                uri = t.get("uri", "")
                track_id = uri.split(":")[-1] if "track:" in uri else ""
                track_url = f"https://open.spotify.com/track/{track_id}" if track_id else ""
                tracks.append({
                    "title": t.get("title") or t.get("name", ""),
                    "artist": artist,
                    "album": "",
                    "cover_url": cover_url,
                    "duration_ms": t.get("duration", 0),
                    "track_number": idx + 1,
                    "total_tracks": len(track_list),
                    "disc_number": 1,
                    "year": "",
                    "isrc": "",
                    "url": track_url,
                    "source": "spotify",
                })
            return tracks
        except Exception:
            return []
