"""Spotify downloader - downloads FLAC tracks via SpotiDownloader proxy API."""

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
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA
        self.session.timeout = 15

    # ── Token management ─────────────────────────────────────────

    def _fetch_token(self) -> str:
        """Get a session token from the SpotiDownloader API (cached)."""
        if SpotifyDownloader._cached_token:
            return SpotifyDownloader._cached_token

        for attempt in range(3):
            try:
                resp = self.session.get(
                    "https://spdl.afkarxyz.fun/token", timeout=10
                )
                resp.raise_for_status()
                token = resp.json().get("token", "")
                if token:
                    SpotifyDownloader._cached_token = token
                    return token
            except Exception as e:
                if attempt == 2:
                    raise ValueError(
                        f"Failed to fetch SpotiDownloader token: {e}"
                    )
                time.sleep(1)

        raise ValueError("SpotiDownloader token not found in response")

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

    # ── Download ─────────────────────────────────────────────────

    def _get_flac_link(self, track_id: str, token: str) -> str:
        """Request a FLAC download link from SpotiDownloader."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
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
                    if progress_cb and total:
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

    def expand_playlist(self, spotify_url_or_id: str) -> list[dict]:
        """Expand a Spotify playlist URL into individual track records.
        
        Spotify's public playlist API doesn't require authentication for
        some endpoints. We use the embed API and web scraping to extract
        track data. For full playlist content, authentication would be needed.
        """
        import re
        # Extract playlist ID from URL
        if "/playlist/" in str(spotify_url_or_id):
            m = re.search(r"/playlist/([a-zA-Z0-9]+)", str(spotify_url_or_id))
            playlist_id = m.group(1) if m else str(spotify_url_or_id)
        else:
            playlist_id = str(spotify_url_or_id)
        
        try:
            # Try the embed endpoint which returns track data without auth
            resp = self.session.get(
                f"https://open.spotify.com/embed/playlist/{playlist_id}",
                headers={"User-Agent": self.UA},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            
            import json
            data = resp.json()
            
            # Extract tracks from the embed response
            tracks = []
            track_data = data.get("tracks", {}).get("tracks", data.get("tracks", []))
            
            for item in track_data:
                if isinstance(item, dict):
                    track = item.get("track") or item
                    if not track.get("id"):
                        continue
                    
                    # Get album art
                    album = track.get("album", {})
                    images = album.get("images", [])
                    cover_url = images[0].get("url", "") if images else ""
                    
                    # Get duration in ms
                    duration_ms = track.get("duration_ms", 0)
                    
                    # Get artists
                    artists = track.get("artists", [])
                    artist_name = ", ".join(a.get("name", "") for a in artists)
                    
                    tracks.append({
                        "id": track.get("id"),
                        "title": track.get("name", ""),
                        "artist": artist_name,
                        "album": album.get("name", ""),
                        "cover_url": cover_url,
                        "duration_ms": duration_ms,
                        "track_number": track.get("track_number", 0),
                        "disc_number": track.get("disc_number", 1),
                        "total_tracks": track.get("total_track_count", album.get("total_tracks", 0)),
                        "year": (track.get("album", {}).get("release_date", "")[:4]) if track.get("album", {}).get("release_date") else "",
                        "isrc": "",
                        "url": f"https://open.spotify.com/track/{track.get('id')}",
                        "source": "spotify",
                        "service": "Spotify",
                    })
            
            return tracks
        except Exception:
            return []
