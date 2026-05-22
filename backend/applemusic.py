"""Apple Music / iTunes downloader - uses iTunes Search API for metadata and playback URL."""

import requests


class AppleMusicDownloader:
    """Download tracks from Apple Music via iTunes API."""

    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA

    def search_track(self, term: str, limit: int = 5):
        """Search iTunes for a track by term (title + artist)."""
        resp = self.session.get(
            "https://itunes.apple.com/search",
            params={"term": term, "media": "music", "entity": "song", "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

    def lookup_track(self, track_id: int) -> dict:
        """Get track details by iTunes track ID."""
        resp = self.session.get(
            "https://itunes.apple.com/lookup",
            params={"id": track_id, "entity": "song"},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        for r in results:
            if r.get("wrapperType") == "track":
                return r
        return {}

    def lookup_album(self, collection_id: int) -> list[dict]:
        """Get all tracks in an album by iTunes collection ID."""
        resp = self.session.get(
            "https://itunes.apple.com/lookup",
            params={"id": collection_id, "entity": "song"},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        # First result is the album metadata, rest are tracks
        tracks = [r for r in results if r.get("wrapperType") == "track"]
        return tracks

    def lookup_playlist(self, playlist_id: str) -> list[dict]:
        """Get tracks in a playlist by iTunes playlist ID.
        
        Note: iTunes does not have a public playlist API. This method
        uses the iTunes Search API to find tracks, but playlist content
        requires Apple Music API authentication. We fall back to
        searching by the playlist name if available.
        """
        # iTunes doesn't expose playlist contents publicly without auth.
        # Use a web search approach via the MusicBrainz search or
        # attempt the iTunes Storefront API.
        resp = self.session.get(
            "https://itunes.apple.com/lookup",
            params={"id": playlist_id, "entity": "song"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

    def get_track_stream_url(self, track_id: int) -> str:
        """Get the preview (30-second) stream URL for a track.
        
        Note: Full Apple Music tracks require an active subscription and
        auth token. This returns the 30-second preview URL available
        without authentication.
        """
        track = self.lookup_track(track_id)
        return track.get("previewUrl", "")

    def parse_apple_music_url(self, url: str) -> dict:
        """Parse an Apple Music URL to extract type and ID.
        
        URL formats:
          - https://music.apple.com/{country}/album/{name}/{id}
          - https://music.apple.com/{country}/playlist/{name}/{id}
          - https://music.apple.com/{country}/song/{name}/{id}
        """
        import re
        # Track inside album URLs often comes as ?i=<track_id>
        m = re.search(r"[?&]i=(\d+)", url)
        if m:
            return {"type": "song", "id": m.group(1)}
        # Album: /album/{name}/{id}
        m = re.search(r"/album/[^/]+/(\d+)", url)
        if m:
            return {"type": "album", "id": m.group(1)}
        # Playlist: /playlist/{name}/{id}
        m = re.search(r"/playlist/[^/]+/([a-zA-Z0-9._-]+)", url)
        if m:
            return {"type": "playlist", "id": m.group(1)}
        # Song: /song/{name}/{id}
        m = re.search(r"/song/[^/]+/(\d+)", url)
        if m:
            return {"type": "song", "id": m.group(1)}
        return {"type": "unknown", "id": ""}

    def expand_album(self, album_id: str) -> list[dict]:
        """Expand an Apple Music album URL/ID into individual track records."""
        parsed = self.parse_apple_music_url(album_id) if '/' in str(album_id) else None
        lookup_id = parsed["id"] if parsed else album_id
        
        try:
            resp = self.session.get(
                "https://itunes.apple.com/lookup",
                params={"id": lookup_id, "entity": "song"},
                timeout=20,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            
            if not results:
                return []
            
            # Check if first result is an album (has collectionId but not trackId in track sense)
            album_info = results[0] if results else {}
            tracks = [r for r in results if r.get("wrapperType") == "track"]
            
            if not tracks and album_info.get("collectionId"):
                # Try with the collection ID directly
                resp2 = self.session.get(
                    "https://itunes.apple.com/lookup",
                    params={"id": album_info["collectionId"], "entity": "song"},
                    timeout=20,
                )
                tracks = [r for r in resp2.json().get("results", []) 
                          if r.get("wrapperType") == "track"]
            
            return self._normalize_tracks(tracks, album_info)
        except Exception:
            return []

    def expand_playlist(self, playlist_url_or_id: str) -> list[dict]:
        """Expand an Apple Music playlist URL into individual track records.
        
        Note: Apple's playlist API requires authentication. This method
        uses web scraping of the playlist page as a fallback for
        unauthenticated access.
        """
        # First check if it's a numeric ID we can look up
        if playlist_url_or_id.isdigit():
            return self._scrape_playlist_by_id(playlist_url_or_id)

        # If a full Apple Music playlist URL was provided, scrape that exact page.
        playlist_input = str(playlist_url_or_id).strip()
        if "music.apple.com" in playlist_input and "/playlist/" in playlist_input:
            tracks = self._scrape_playlist_by_url(playlist_input)
            if tracks:
                return tracks
        
        # Extract playlist ID from URL
        import re
        m = re.search(r"/playlist/[^/]+/([a-zA-Z0-9._-]+)", str(playlist_url_or_id))
        playlist_id = m.group(1) if m else playlist_url_or_id
        
        return self._scrape_playlist_by_id(playlist_id)

    def _scrape_playlist_by_url(self, playlist_url: str) -> list[dict]:
        """Scrape playlist tracks from the full Apple Music playlist URL."""
        try:
            resp = self.session.get(
                playlist_url,
                headers={"User-Agent": self.UA},
                timeout=15,
            )
            if resp.status_code != 200:
                return []

            import re, json
            html = resp.text

            matches = re.findall(r'<script[^>]+type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)
            for match in matches:
                try:
                    data = json.loads(match)
                    tracks = self._extract_tracks_from_json(data)
                    if tracks:
                        return tracks
                except Exception:
                    continue

            m = re.search(r'"tracks"\s*:\s*\[(.*?)\]', html, re.DOTALL)
            if m:
                try:
                    tracks_json = json.loads("[" + m.group(1) + "]")
                    return [self._normalize_single(t) for t in tracks_json if t.get("id")]
                except Exception:
                    pass

            return []
        except Exception:
            return []

    def _scrape_playlist_by_id(self, playlist_id: str) -> list[dict]:
        """Scrape playlist tracks from Apple Music web page."""
        try:
            # Try to fetch the playlist page
            resp = self.session.get(
                f"https://music.apple.com/playlist/{playlist_id}",
                headers={"User-Agent": self.UA},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            
            import re, json
            html = resp.text
            
            # Look for embedded JSON data in the page
            matches = re.findall(r'<script[^>]+type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)
            for match in matches:
                try:
                    data = json.loads(match)
                    # Look for track data in the JSON structure
                    tracks = self._extract_tracks_from_json(data)
                    if tracks:
                        return tracks
                except Exception:
                    continue
            
            # Fallback: try to find track data in other JSON structures
            m = re.search(r'"tracks"\s*:\s*\[(.*?)\]', html, re.DOTALL)
            if m:
                try:
                    tracks_json = json.loads("[" + m.group(1) + "]")
                    return [self._normalize_single(t) for t in tracks_json if t.get("id")]
                except Exception:
                    pass
            
            return []
        except Exception:
            return []

    def _extract_tracks_from_json(self, data: dict) -> list[dict]:
        """Recursively search JSON for track data."""
        tracks = []

        def search(obj):
            if isinstance(obj, dict):
                # Check if this looks like a track
                if obj.get("id") and (obj.get("title") or obj.get("name")):
                    if obj.get("artist") or obj.get("artistName"):
                        tracks.append(obj)
                # Recurse into children
                for v in obj.values():
                    search(v)
            elif isinstance(obj, list):
                for item in obj:
                    search(item)

        search(data)

        # Preserve order but remove duplicates so large playlists can be shown fully.
        normalized = [self._normalize_single(t) for t in tracks]
        out = []
        seen = set()
        for t in normalized:
            key = (
                str(t.get("id") or ""),
                str(t.get("url") or ""),
                str(t.get("title") or ""),
                str(t.get("artist") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
        return out

    def _normalize_tracks(self, tracks: list[dict], album_info: dict) -> list[dict]:
        """Normalize iTunes track results into standard format."""
        album_name = album_info.get("collectionName", "")
        album_artist = album_info.get("artistName", "")
        cover_url = album_info.get("artworkUrl100", "")
        if cover_url:
            cover_url = cover_url.replace("100x100bb", "600x600bb")
        album_year = (album_info.get("releaseDate", "")[:4]) if album_info.get("releaseDate") else ""
        total_tracks = album_info.get("trackCount", len(tracks))
        
        result = []
        for t in tracks:
            artwork = t.get("artworkUrl100", "")
            if artwork:
                artwork = artwork.replace("100x100bb", "600x600bb")
            result.append({
                "id": t.get("trackId"),
                "title": t.get("trackName", ""),
                "artist": t.get("artistName", album_artist),
                "album": t.get("collectionName", album_name),
                "cover_url": artwork or cover_url,
                "duration_ms": int(t.get("trackTimeMillis", 0) or 0),
                "track_number": int(t.get("trackNumber", 0) or 0),
                "disc_number": int(t.get("discNumber", 0) or 1),
                "total_tracks": total_tracks,
                "year": (t.get("releaseDate", "")[:4]) if t.get("releaseDate") else album_year,
                "isrc": t.get("isrc", ""),
                "url": t.get("trackViewUrl", ""),
                "preview_url": t.get("previewUrl", ""),
                "source": "apple_music",
                "service": "Apple Music",
            })
        return result

    def _normalize_single(self, t: dict) -> dict:
        """Normalize a single track dict from web scraping."""
        return {
            "id": t.get("id") or t.get("trackId"),
            "title": t.get("title") or t.get("trackName") or t.get("name", ""),
            "artist": t.get("artist") or t.get("artistName", ""),
            "album": t.get("album") or t.get("collectionName", ""),
            "cover_url": t.get("artworkUrl100", t.get("image", "")),
            "duration_ms": int(t.get("durationMs", t.get("duration_ms", 0))),
            "track_number": int(t.get("trackNumber", 0) or 0),
            "disc_number": int(t.get("discNumber", 0) or 1),
            "total_tracks": int(t.get("trackCount", 0) or 0),
            "year": (t.get("releaseDate", "")[:4]) if t.get("releaseDate") else "",
            "isrc": t.get("isrc", ""),
            "url": t.get("url") or t.get("trackViewUrl", ""),
            "preview_url": t.get("previewUrl", ""),
            "source": "apple_music",
            "service": "Apple Music",
        }
