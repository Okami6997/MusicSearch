"""SongLink client for resolving any music URL to other platforms."""

import re
import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)

AMAZON_ALBUM_TRACK = re.compile(r"/albums/[A-Z0-9]{10}/(B[0-9A-Z]{9})")
AMAZON_TRACK = re.compile(r"/tracks/(B[0-9A-Z]{9})")
TIDAL_TRACK = re.compile(r"tidal\.com.*?/track/(\d+)")
DEEZER_TRACK = re.compile(r"deezer\.com.*?/track/(\d+)")

# Patterns for recognized music platform URLs
MUSIC_URL_PATTERNS = [
    re.compile(r"open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)"),
    re.compile(r"spotify:(track|album|playlist):([a-zA-Z0-9]+)"),
    re.compile(r"tidal\.com.*?/(track|album|playlist)/(\d+)"),
    re.compile(r"music\.amazon\..+?/(tracks|albums)/(B[0-9A-Z]{9})"),
    re.compile(r"deezer\.com.*?/(track|album|playlist)/(\d+)"),
    re.compile(r"music\.apple\.com/.+?/(album|playlist|song)/"),
    re.compile(r"itunes\.apple\.com/.+?/id\d+"),
    re.compile(r"music\.youtube\.com/watch"),
    re.compile(r"soundcloud\.com/"),
    re.compile(r"qobuz\.com/.+?/(album|track)/"),
]


def is_music_url(url: str) -> bool:
    """Check if a URL is a recognized music platform URL."""
    return any(p.search(url) for p in MUSIC_URL_PATTERNS)


def parse_music_url(url: str) -> dict:
    """Parse a music URL and return platform info."""
    if "tidal.com" in url:
        m = re.search(r"tidal\.com.*?/(track|album|playlist)/(\d+)", url)
        if m:
            return {"platform": "tidal", "type": m.group(1), "id": m.group(2)}
    if "music.amazon" in url:
        m = AMAZON_TRACK.search(url) or AMAZON_ALBUM_TRACK.search(url)
        if m:
            return {"platform": "amazon", "type": "track", "id": m.group(1)}
        m = re.search(r"/albums/([A-Z0-9]{10})", url)
        if m:
            return {"platform": "amazon", "type": "album", "id": m.group(1)}
    if "deezer.com" in url:
        m = re.search(r"deezer\.com.*?/(track|album|playlist)/(\d+)", url)
        if m:
            return {"platform": "deezer", "type": m.group(1), "id": m.group(2)}
    if "qobuz.com" in url:
        m = re.search(r"qobuz\.com.*?/(album|track)/[\w-]+/([\w]+)", url)
        if m:
            return {"platform": "qobuz", "type": m.group(1), "id": m.group(2)}
    if "youtube.com" in url or "youtu.be" in url:
        m = re.search(r"(?:v=|/v/|youtu\.be/|/embed/)([a-zA-Z0-9_-]{11})", url)
        if m:
            return {"platform": "youtube", "type": "track", "id": m.group(1)}
    if "open.spotify.com" in url or url.startswith("spotify:"):
        m = re.search(r"(?:open\.spotify\.com|spotify)[:/](track|album|playlist)[:/]([a-zA-Z0-9]+)", url)
        if m:
            return {"platform": "spotify", "type": m.group(1), "id": m.group(2)}
    if "music.apple.com" in url or "itunes.apple.com" in url:
        m = re.search(r"/(album|playlist|song)/[^/]+/(\d+|[a-zA-Z0-9]+)", url)
        if m:
            return {"platform": "apple_music", "type": m.group(1), "id": m.group(2)}
        m = re.search(r"/id(\d+)", url)
        if m:
            return {"platform": "apple_music", "type": "song", "id": m.group(1)}
        return {"platform": "apple_music", "type": "unknown", "id": ""}
    if "soundcloud.com" in url:
        # SoundCloud permalink URLs generally map to tracks.
        return {"platform": "soundcloud", "type": "track", "id": ""}
    # Generic — let song.link try to resolve it
    return {"platform": "unknown", "type": "unknown", "id": ""}


class SongLinkClient:
    """Resolve any music URL to cross-platform links and ISRC."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA

    def resolve_url(self, url: str, region: str = "") -> dict:
        """Resolve any music platform URL to all available platform links."""
        return self._resolve_by_url(url, region)

    def get_all_urls(self, url: str, region: str = "") -> dict:
        """Get Tidal/Amazon/YouTube/Spotify/ISRC from any music URL."""
        links = self._resolve_by_url(url, region)
        return {
            "tidal_url": links.get("tidal_url", ""),
            "amazon_url": self._norm_amazon(links.get("amazon_url", "")),
            "deezer_url": links.get("deezer_url", ""),
            "youtube_url": links.get("youtube_url", ""),
            "spotify_url": links.get("spotify_url", ""),
            "soundcloud_url": links.get("soundcloud_url", ""),
            "isrc": links.get("isrc", ""),
        }

    def check_availability(self, url: str) -> dict:
        """Check which platforms have this track."""
        links = self._resolve_by_url(url, "")
        tidal = links.get("tidal_url", "")
        amazon = self._norm_amazon(links.get("amazon_url", ""))
        deezer = links.get("deezer_url", "")
        youtube = links.get("youtube_url", "")
        spotify = links.get("spotify_url", "")
        isrc = links.get("isrc", "")
        qobuz = self._check_qobuz(isrc) if isrc else False

        # Try to get ISRC from Deezer if not found
        if not isrc and deezer:
            isrc = self._get_deezer_isrc(deezer)
            if isrc:
                qobuz = self._check_qobuz(isrc)

        return {
            "tidal": bool(tidal), "amazon": bool(amazon),
            "qobuz": qobuz, "deezer": bool(deezer),
            "youtube": bool(youtube), "spotify": bool(spotify),
            "tidal_url": tidal, "amazon_url": amazon,
            "deezer_url": deezer, "youtube_url": youtube,
            "spotify_url": spotify, "isrc": isrc,
        }

    def get_isrc(self, url: str) -> str:
        """Get ISRC from any music URL."""
        links = self._resolve_by_url(url, "")
        isrc = links.get("isrc", "")
        if not isrc:
            deezer = links.get("deezer_url", "")
            if deezer:
                isrc = self._get_deezer_isrc(deezer)
        return isrc

    def _resolve_by_url(self, url: str, region: str) -> dict:
        """Resolve via song.link API using any music URL."""
        result = {"tidal_url": "", "amazon_url": "", "deezer_url": "", "isrc": ""}
        api = (
            f"https://api.song.link/v1-alpha.1/links"
            f"?url={requests.utils.quote(url, safe='')}"
        )
        if region:
            api += f"&userCountry={region}"
        try:
            resp = self.session.get(api, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                lbp = data.get("linksByPlatform", {})
                result["tidal_url"] = lbp.get("tidal", {}).get("url", "")
                result["amazon_url"] = lbp.get("amazonMusic", {}).get("url", "")
                result["deezer_url"] = lbp.get("deezer", {}).get("url", "")
                result["spotify_url"] = lbp.get("spotify", {}).get("url", "")
                result["qobuz_url"] = lbp.get("qobuz", {}).get("url", "")
                result["youtube_url"] = lbp.get("youtubeMusic", {}).get("url", "")
                result["apple_url"] = lbp.get("appleMusic", {}).get("url", "")
                result["soundcloud_url"] = lbp.get("soundcloud", {}).get("url", "")
                for entity in data.get("entitiesByUniqueId", {}).values():
                    if entity.get("isrc"):
                        result["isrc"] = entity["isrc"]
                        break
            elif resp.status_code == 429:
                print("[SongLink] Rate limited, trying fallback...")
        except Exception as e:
            print(f"[SongLink] Error: {e}")
        return result

    def _check_qobuz(self, isrc: str) -> bool:
        try:
            r = self.session.get(
                f"https://www.qobuz.com/api.json/0.2/track/search"
                f"?query={isrc}&limit=1&app_id=798273057",
                timeout=10,
            )
            if r.status_code == 200:
                return r.json().get("tracks", {}).get("total", 0) > 0
        except Exception:
            pass
        return False

    def _get_deezer_isrc(self, deezer_url: str) -> str:
        """Get ISRC from a Deezer track URL."""
        m = DEEZER_TRACK.search(deezer_url)
        if not m:
            return ""
        try:
            r = self.session.get(
                f"https://api.deezer.com/track/{m.group(1)}", timeout=10
            )
            if r.status_code == 200:
                isrc = r.json().get("isrc", "")
                return isrc.upper().strip() if isrc else ""
        except Exception:
            pass
        return ""

    @staticmethod
    def _norm_amazon(url: str) -> str:
        if not url:
            return ""
        m = AMAZON_ALBUM_TRACK.search(url) or AMAZON_TRACK.search(url)
        if m:
            return f"https://music.amazon.com/tracks/{m.group(1)}"
        return url
