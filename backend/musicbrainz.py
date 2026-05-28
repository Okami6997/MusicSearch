"""MusicBrainz metadata lookup - genre and label info via ISRC."""

import time
from urllib.parse import quote

import requests


class MusicBrainzClient:
    """Fetch genre and label metadata from MusicBrainz by ISRC."""

    API_BASE = "https://musicbrainz.org/ws/2"

    def __init__(self):
        from .proxy_config import configure_session_proxy
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "SongsFetch/1.0 ( hi@songsfetch.app )"
        configure_session_proxy(self.session)

    def fetch_metadata(self, isrc: str, title: str = "", artist: str = "",
                       album: str = "", single_genre: bool = False) -> dict:
        """Fetch genre/label from MusicBrainz by ISRC."""
        if not isrc:
            return {}

        query = f"isrc:{isrc}"
        url = (
            f"{self.API_BASE}/recording?query={quote(query)}&fmt=json"
            f"&inc=releases+artist-credits+tags+media+release-groups+labels"
        )

        data = self._request_with_retry(url)
        if not data:
            return {}

        recordings = data.get("recordings", [])
        if not recordings:
            return {}

        recording = recordings[0]
        result = {}

        # Extract genre from tags
        tags = recording.get("tags", [])
        if tags:
            sorted_tags = sorted(tags, key=lambda t: t.get("count", 0), reverse=True)
            if single_genre:
                result["genre"] = sorted_tags[0]["name"].title()
            else:
                genres = [t["name"].title() for t in sorted_tags[:5]]
                result["genre"] = "; ".join(genres)

        # Extract label from releases
        releases = recording.get("releases", [])
        if releases:
            for release in releases:
                label_info = release.get("label-info", [])
                if label_info:
                    label_name = label_info[0].get("label", {}).get("name", "")
                    if label_name:
                        result["publisher"] = label_name
                        break
                # Extract year from release date
                if "date" in release and not result.get("year"):
                    date_str = release.get("date", "")
                    if date_str and len(date_str) >= 4:
                        result["year"] = date_str[:4]

        return result

    def _request_with_retry(self, url: str, retries: int = 3) -> dict | None:
        for i in range(retries):
            try:
                resp = self.session.get(url, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 503 and i < retries - 1:
                    time.sleep(2)
                    continue
            except Exception:
                if i < retries - 1:
                    time.sleep(2)
        return None
