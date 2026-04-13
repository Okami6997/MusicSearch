"""Search clients for external music services.

This module is search-only. Download flows remain in service-specific downloaders.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import requests


class AmazonSearchClient:
    """Amazon Music public web search via showSearch endpoint."""

    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA

    @staticmethod
    def _currency_for_host(host: str) -> str:
        if host.endswith(".in"):
            return "INR"
        if host.endswith(".co.uk"):
            return "GBP"
        if host.endswith(".de") or host.endswith(".fr") or host.endswith(".it") or host.endswith(".es"):
            return "EUR"
        return "USD"

    def _fetch_public_config(self, base_url: str) -> dict:
        cfg = self.session.get(f"{base_url}/config.json", timeout=20).json()
        csrf = cfg.get("csrf") or {}
        if not cfg.get("deviceId") or not cfg.get("sessionId") or not csrf.get("token"):
            raise ValueError("Amazon public config is missing required session fields")
        return cfg

    @staticmethod
    def _text_value(value) -> str:
        if isinstance(value, dict):
            return (value.get("text") or "").strip()
        if isinstance(value, str):
            return value.strip()
        return ""

    def _extract_track_info(self, obj, seen_asins: set[str]) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        if isinstance(obj, dict):
            title_text = self._text_value(obj.get("primaryText") or obj.get("titleText"))
            artist_text = self._text_value(obj.get("secondaryText") or obj.get("subsequentText"))
            image_url = obj.get("image") if isinstance(obj.get("image"), str) else ""
            observer = ((obj.get("iconButton") or {}).get("observer") or {})
            storage_key = observer.get("storageKey", "") if isinstance(observer, dict) else ""

            track_asin = ""
            album_asin = ""
            if ":" in storage_key:
                album_asin, track_asin = storage_key.split(":", 1)
            elif storage_key.startswith("B"):
                track_asin = storage_key

            if title_text and track_asin and track_asin not in seen_asins:
                seen_asins.add(track_asin)
                results.append({
                    "asin": track_asin,
                    "album_asin": album_asin,
                    "title": title_text,
                    "artist": artist_text,
                    "image_url": image_url,
                })

            for value in obj.values():
                if isinstance(value, (dict, list)):
                    results.extend(self._extract_track_info(value, seen_asins))
        elif isinstance(obj, list):
            for item in obj:
                results.extend(self._extract_track_info(item, seen_asins))
        return results

    def search_tracks(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        try:
            base_url = "https://music.amazon.com"
            host = urlparse(base_url).netloc
            cfg = self._fetch_public_config(base_url)
            request_id = str(uuid.uuid4())
            timestamp_ms = str(int(time.time() * 1000))
            headers = {
                "User-Agent": self.UA,
                "Content-Type": "application/json",
                "Referer": f"{base_url}/search/{query.replace(' ', '+')}?filter=IsLibrary%257Cfalse&sc=none",
                "Origin": base_url,
            }
            inner_headers = json.dumps({
                "x-amzn-authentication": json.dumps({
                    "interface": "ClientAuthenticationInterface.v1_0.ClientTokenElement",
                    "accessToken": cfg.get("accessToken", ""),
                }, separators=(",", ":")),
                "x-amzn-device-model": "WEBPLAYER",
                "x-amzn-device-width": "1920",
                "x-amzn-device-family": "WebPlayer",
                "x-amzn-device-id": cfg["deviceId"],
                "x-amzn-user-agent": self.UA,
                "x-amzn-session-id": cfg["sessionId"],
                "x-amzn-device-height": "1080",
                "x-amzn-request-id": request_id,
                "x-amzn-device-language": cfg.get("displayLanguage", "en_US"),
                "x-amzn-currency-of-preference": self._currency_for_host(host),
                "x-amzn-os-version": "1.0",
                "x-amzn-application-version": cfg.get("version", "1.0"),
                "x-amzn-device-time-zone": "UTC",
                "x-amzn-timestamp": timestamp_ms,
                "x-amzn-csrf": json.dumps({
                    "interface": "CSRFInterface.v1_0.CSRFHeaderElement",
                    "token": cfg["csrf"]["token"],
                    "timestamp": cfg["csrf"]["ts"],
                    "rndNonce": cfg["csrf"]["rnd"],
                }, separators=(",", ":")),
                "x-amzn-music-domain": host,
                "x-amzn-page-url": f"{base_url}/search/{query.replace(' ', '+')}",
                "x-amzn-referer": "",
                "x-amzn-affiliate-tags": "",
                "x-amzn-ref-marker": "",
                "x-amzn-weblab-id-overrides": "",
                "x-amzn-video-player-token": "",
                "x-amzn-feature-flags": "hd-supported,uhd-supported",
                "x-amzn-has-profile-id": "",
                "x-amzn-age-band": "",
            })
            payload = {
                "filter": '{"IsLibrary":["false"]}',
                "keyword": json.dumps({
                    "interface": "Web.TemplatesInterface.v1_0.Touch.SearchTemplateInterface.SearchKeywordClientInformation",
                    "keyword": query,
                }, separators=(",", ":")),
                "suggestedKeyword": query,
                "userHash": json.dumps({"level": "ANONYMOUS"}, separators=(",", ":")),
                "headers": inner_headers,
            }
            resp = self.session.post(
                "https://na.web.skill.music.a2z.com/api/showSearch",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
        except Exception:
            return []

        seen_asins: set[str] = set()
        track_info_list = self._extract_track_info(data, seen_asins)
        tracks: list[dict[str, Any]] = []
        for info in track_info_list[:limit]:
            asin = info.get("asin", "")
            if not asin:
                continue
            tracks.append({
                "id": asin,
                "title": info.get("title", ""),
                "artist": info.get("artist", ""),
                "album": "",
                "cover_url": info.get("image_url", ""),
                "duration_ms": 0,
                "url": f"https://music.amazon.com/tracks/{asin}",
                "isrc": "",
                "hires": False,
                "bit_depth": 0,
                "sample_rate": 0,
                "year": "",
                "preview_url": "",
                "asin": asin,
                "source": "amazon",
                "service": "Amazon Music",
            })
        return tracks


class SpotifySearchClient:
    """Anonymous Spotify search via api-partner pathfinder endpoint."""

    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    )
    APP_SERVER_CONFIG_RE = re.compile(
        r'<script id="appServerConfig" type="text/plain">([^<]+)</script>'
    )

    TOTP_SECRET_B32 = (
        "GM3TMMJTGYZTQNZVGM4DINJZHA4TGOBYGMZTCMRTGEYDSMJRHE4TEOBUG4YTCMRU"
        "GQ4DQOJUGQYTAMRRGA2TCMJSHE3TCMBY"
    )
    TOTP_VERSION = 61
    TOTP_PERIOD = 30
    TOTP_DIGITS = 6

    SEARCH_SHA256 = "21b3fe49546912ba782db5c47e9ef5a7dbd20329520ba0c7d0fcfadee671d24e"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.UA
        self._client_version = ""
        self._device_id = ""
        self._client_id = ""
        self._access_token = ""
        self._client_token = ""

    def _totp_now(self) -> str:
        key = base64.b32decode(self.TOTP_SECRET_B32, casefold=True)
        counter = int(time.time() // self.TOTP_PERIOD)
        msg = counter.to_bytes(8, "big")
        digest = hmac.new(key, msg, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        code_int = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
        return str(code_int % (10 ** self.TOTP_DIGITS)).zfill(self.TOTP_DIGITS)

    def _init_web_session(self) -> None:
        if self._client_version and self._device_id:
            return
        resp = self.session.get("https://open.spotify.com", timeout=self.timeout)
        resp.raise_for_status()

        match = self.APP_SERVER_CONFIG_RE.search(resp.text)
        if not match:
            raise ValueError("Spotify appServerConfig not found")

        raw = base64.b64decode(match.group(1)).decode("utf-8")
        cfg = json.loads(raw)
        self._client_version = cfg.get("clientVersion", "") or ""
        self._device_id = self.session.cookies.get("sp_t", "") or ""
        if not self._client_version or not self._device_id:
            raise ValueError("Spotify web session missing client version/device id")

    def _fetch_access_token(self) -> None:
        if self._access_token and self._client_id:
            return
        self._init_web_session()

        totp_code = self._totp_now()
        token_resp = self.session.get(
            "https://open.spotify.com/api/token",
            params={
                "reason": "init",
                "productType": "web-player",
                "totp": totp_code,
                "totpServer": totp_code,
                "totpVer": str(self.TOTP_VERSION),
            },
            headers={
                "User-Agent": self.UA,
                "Accept": "application/json",
                "Content-Type": "application/json;charset=UTF-8",
                "Referer": "https://open.spotify.com/",
                "Origin": "https://open.spotify.com",
            },
            timeout=self.timeout,
        )
        token_resp.raise_for_status()
        payload = token_resp.json()
        self._access_token = payload.get("accessToken", "") or ""
        self._client_id = payload.get("clientId", "") or ""

        if not self._access_token or not self._client_id:
            raise ValueError("Spotify access token bootstrap failed")

    def _fetch_client_token(self) -> None:
        if self._client_token:
            return
        self._fetch_access_token()

        body = {
            "client_data": {
                "client_version": self._client_version,
                "client_id": self._client_id,
                "js_sdk_data": {
                    "device_brand": "unknown",
                    "device_model": "unknown",
                    "os": "windows",
                    "os_version": "NT 10.0",
                    "device_id": self._device_id,
                    "device_type": "computer",
                },
            }
        }
        resp = self.session.post(
            "https://clienttoken.spotify.com/v1/clienttoken",
            json=body,
            headers={
                "User-Agent": self.UA,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._access_token}",
                "Spotify-App-Version": self._client_version,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self._client_token = (
            (data.get("granted_token") or {}).get("token", "")
            or ""
        )
        if not self._client_token:
            raise ValueError("Spotify client token bootstrap failed")

    @staticmethod
    def _track_id_from_uri(uri: str) -> str:
        if not uri:
            return ""
        if ":" in uri:
            return uri.split(":")[-1]
        return uri.rstrip("/").split("/")[-1]

    def search_tracks(self, query: str, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
        try:
            self._fetch_client_token()
            payload = {
                "variables": {
                    "searchTerm": query,
                    "offset": max(0, int(offset)),
                    "limit": max(1, min(int(limit), 50)),
                    "numberOfTopResults": 5,
                    "includeAudiobooks": True,
                    "includeArtistHasConcertsField": False,
                    "includePreReleases": True,
                    "includeAuthors": False,
                    "includeEpisodeContentRatingsV2": False,
                },
                "operationName": "searchDesktop",
                "extensions": {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": self.SEARCH_SHA256,
                    }
                },
            }
            resp = self.session.post(
                "https://api-partner.spotify.com/pathfinder/v2/query",
                json=payload,
                headers={
                    "User-Agent": self.UA,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._access_token}",
                    "Client-Token": self._client_token,
                    "Spotify-App-Version": self._client_version,
                    "Origin": "https://open.spotify.com",
                    "Referer": "https://open.spotify.com/",
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        items = (
            (data.get("data") or {})
            .get("searchV2", {})
            .get("tracksV2", {})
            .get("items", [])
        )
        out: list[dict[str, Any]] = []
        for node in items:
            track_data = (((node or {}).get("item") or {}).get("data") or {})
            if not track_data:
                continue

            uri = track_data.get("uri", "") or ""
            track_id = self._track_id_from_uri(uri)
            artists = (((track_data.get("artists") or {}).get("items")) or [])
            artist_names = []
            for artist in artists:
                name = ((artist or {}).get("profile") or {}).get("name", "")
                if name:
                    artist_names.append(name)

            album_obj = track_data.get("albumOfTrack") or {}
            cover_sources = ((album_obj.get("coverArt") or {}).get("sources") or [])
            cover = ""
            if cover_sources:
                cover = cover_sources[0].get("url", "") or ""

            duration_ms = int((track_data.get("duration") or {}).get("totalMilliseconds", 0) or 0)
            title = track_data.get("name", "") or ""
            if not title or not track_id:
                continue

            out.append({
                "id": track_id,
                "title": title,
                "artist": ", ".join(artist_names),
                "album": album_obj.get("name", "") or "",
                "cover_url": cover,
                "duration_ms": duration_ms,
                "url": f"https://open.spotify.com/track/{track_id}",
                "isrc": "",
                "hires": False,
                "bit_depth": 0,
                "sample_rate": 0,
                "year": "",
                "preview_url": "",
                "source": "spotify",
                "service": "Spotify",
            })

        return out

    def search_albums(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search Spotify albums via pathfinder endpoint."""
        try:
            self._fetch_client_token()
            payload = {
                "variables": {
                    "searchTerm": query,
                    "offset": 0,
                    "limit": max(1, min(int(limit), 50)),
                    "numberOfTopResults": 5,
                    "includeAudiobooks": True,
                    "includeArtistHasConcertsField": False,
                    "includePreReleases": True,
                    "includeAuthors": False,
                    "includeEpisodeContentRatingsV2": False,
                },
                "operationName": "searchDesktop",
                "extensions": {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": self.SEARCH_SHA256,
                    }
                },
            }
            resp = self.session.post(
                "https://api-partner.spotify.com/pathfinder/v2/query",
                json=payload,
                headers={
                    "User-Agent": self.UA,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._access_token}",
                    "Client-Token": self._client_token,
                    "Spotify-App-Version": self._client_version,
                    "Origin": "https://open.spotify.com",
                    "Referer": "https://open.spotify.com/",
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        items = (
            (data.get("data") or {})
            .get("searchV2", {})
            .get("albumsV2", {})
            .get("items", [])
        )
        out: list[dict[str, Any]] = []
        for node in items:
            album_data = ((node or {}).get("data") or {})
            if not album_data:
                continue
            uri = album_data.get("uri", "") or ""
            album_id = self._track_id_from_uri(uri)
            name = album_data.get("name", "") or ""
            if not name or not album_id:
                continue

            artists = ((album_data.get("artists") or {}).get("items") or [])
            artist_names = [
                ((a or {}).get("profile") or {}).get("name", "")
                for a in artists
            ]
            artist_names = [n for n in artist_names if n]

            cover_sources = ((album_data.get("coverArt") or {}).get("sources") or [])
            cover = ""
            for src in cover_sources:
                url = src.get("url", "")
                if url:
                    cover = url
                    if (src.get("width") or 0) >= 300:
                        break

            year = ""
            date_obj = album_data.get("date") or {}
            if date_obj.get("year"):
                year = str(date_obj["year"])

            out.append({
                "id": album_id,
                "title": name,
                "artist": ", ".join(artist_names),
                "cover_url": cover,
                "tracks_count": 0,
                "release_date": "",
                "year": year,
                "hires": False,
                "source": "spotify",
                "service": "Spotify",
            })

        return out
