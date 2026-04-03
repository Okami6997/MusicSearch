"""Download orchestrator - manages downloads from multiple sources."""

import os
import tempfile
import traceback
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock, Thread
from typing import Callable, Optional

import requests as _req

from .amazon import AmazonDownloader
from .analysis import validate_download_duration
from .applemusic import AppleMusicDownloader
from .history import add_download
from .lyrics import LyricsClient
from .metadata import Metadata, download_cover, embed_metadata
from .musicbrainz import MusicBrainzClient
from .qobuz import QobuzDownloader
from .songlink import SongLinkClient, parse_music_url
from .spotify import SpotifyDownloader
from .tidal import TidalDownloader
from .youtube import YouTubeDownloader


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    CONVERTING = "converting"
    EMBEDDING = "embedding"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DownloadTask:
    id: str = ""
    url: str = ""
    isrc: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    year: str = ""
    cover_url: str = ""
    duration_ms: int = 0
    track_number: int = 0
    total_tracks: int = 0
    disc_number: int = 0
    total_discs: int = 0
    status: str = DownloadStatus.QUEUED
    progress: float = 0.0
    error: str = ""
    output_path: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "url": self.url, "isrc": self.isrc,
            "title": self.title, "artist": self.artist, "album": self.album,
            "cover_url": self.cover_url, "duration_ms": self.duration_ms,
            "status": self.status, "progress": self.progress,
            "error": self.error, "output_path": self.output_path,
            "source": self.source, "year": self.year,
        }


class DownloadManager:
    """Manages download queue and runs downloads from Tidal/Qobuz/Amazon/YouTube."""

    def __init__(self, output_dir: str = "",
                 on_progress: Optional[Callable] = None):
        self.output_dir = output_dir or os.environ.get("SONGSFETCH_OUTPUT_DIR") or os.path.join(
            os.path.expanduser("~"), "Music", "SongsFetch")
        os.makedirs(self.output_dir, exist_ok=True)
        self.on_progress = on_progress
        self.tasks: OrderedDict[str, DownloadTask] = OrderedDict()
        self._lock = Lock()
        self._running = False
        self._thread: Optional[Thread] = None
        self.tidal = TidalDownloader()
        self.qobuz = QobuzDownloader()
        self.amazon = AmazonDownloader()
        self.applemusic = AppleMusicDownloader()
        self.youtube = YouTubeDownloader()
        self.spotify = SpotifyDownloader()
        self.songlink = SongLinkClient()
        self.lyrics = LyricsClient()
        self.musicbrainz = MusicBrainzClient()
        self.preferred_source: str = "tidal"
        self.quality: str = "LOSSLESS"
        self.embed_lyrics_flag: bool = True
        self.validate_duration: bool = True
        # Performance: cache SongLink resolutions and ISRC lookups
        self._sl_cache: dict[str, dict] = {}
        self._isrc_cache: dict[str, dict] = {}
        self._max_workers = 5

    def add_track(self, url: str = "", isrc: str = "", title: str = "",
                  artist: str = "", album: str = "", cover_url: str = "",
                  duration_ms: int = 0, track_number: int = 0,
                  total_tracks: int = 0, disc_number: int = 0,
                  total_discs: int = 0, year: str = "") -> str:
        """Add a track to the download queue. Provide URL or ISRC."""
        task_id = str(uuid.uuid4())[:8]
        task = DownloadTask(
            id=task_id, url=url, isrc=isrc,
            title=title or url or isrc, artist=artist,
            album=album, year=year, cover_url=cover_url, duration_ms=duration_ms,
            track_number=track_number, total_tracks=total_tracks,
            disc_number=disc_number, total_discs=total_discs,
        )
        with self._lock:
            self.tasks[task_id] = task
        self._notify(task)
        self._ensure_running()
        return task_id

    def get_queue(self) -> list[dict]:
        with self._lock:
            return [t.to_dict() for t in self.tasks.values()]

    def clear_completed(self):
        with self._lock:
            self.tasks = OrderedDict(
                (k, v) for k, v in self.tasks.items()
                if v.status not in (DownloadStatus.COMPLETED, DownloadStatus.FAILED)
            )

    def _ensure_running(self):
        if not self._running:
            self._running = True
            self._thread = Thread(target=self._worker, daemon=True)
            self._thread.start()

    def _worker(self):
        max_workers = self._max_workers
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while True:
                tasks = self._get_queued_tasks(max_workers)
                if not tasks:
                    self._running = False
                    return

                futures = {executor.submit(self._process, task): task for task in tasks}
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        pass  # Exceptions already handled in _process

    def _get_queued_tasks(self, limit: int) -> list[DownloadTask]:
        """Get up to `limit` queued tasks."""
        with self._lock:
            queued = [t for t in self.tasks.values() if t.status == DownloadStatus.QUEUED]
            return queued[:limit]

    def _next_task(self) -> Optional[DownloadTask]:
        with self._lock:
            for t in self.tasks.values():
                if t.status == DownloadStatus.QUEUED:
                    return t
        return None

    def _process(self, task: DownloadTask):
        try:
            task.status = DownloadStatus.RESOLVING
            self._notify(task)

            # Resolve URL to platform-specific links
            links = {}
            isrc = task.isrc
            apple_music_url = ""
            if task.url:
                parsed = parse_music_url(task.url)
                # If it's a direct platform URL, set it directly
                if parsed["platform"] == "tidal" and parsed["type"] == "track":
                    links["tidal_url"] = task.url
                elif parsed["platform"] == "amazon":
                    links["amazon_url"] = task.url
                elif parsed["platform"] == "youtube":
                    links["youtube_url"] = task.url
                elif parsed["platform"] == "deezer" and parsed["type"] == "track":
                    links["deezer_url"] = task.url
                elif parsed["platform"] == "soundcloud":
                    links["soundcloud_url"] = task.url
                elif parsed["platform"] == "spotify" and parsed["type"] == "track":
                    links["spotify_url"] = task.url
                elif parsed["platform"] == "apple_music":
                    apple_music_url = task.url
                # Always try SongLink to get cross-platform links + ISRC (with cache)
                try:
                    cache_key = f"sl:{task.url}"
                    sl = self._sl_cache.get(cache_key)
                    if sl is None:
                        sl = self.songlink.get_all_urls(task.url)
                        # Cache for 5 minutes
                        self._sl_cache[cache_key] = sl
                    links.setdefault("tidal_url", sl.get("tidal_url", ""))
                    links.setdefault("amazon_url", sl.get("amazon_url", ""))
                    links.setdefault("deezer_url", sl.get("deezer_url", ""))
                    links.setdefault("youtube_url", sl.get("youtube_url", ""))
                    links.setdefault("spotify_url", sl.get("spotify_url", ""))
                    links.setdefault("apple_url", sl.get("apple_url", ""))
                    links.setdefault("soundcloud_url", sl.get("soundcloud_url", ""))
                    if not isrc:
                        isrc = sl.get("isrc", "")
                except Exception:
                    pass
            elif isrc:
                # ISRC only — resolve cross-platform URLs via Deezer + SongLink (with cache)
                links["isrc"] = isrc
                # Check ISRC cache first
                isrc_cache_key = f"isrc:{isrc}"
                cached = self._isrc_cache.get(isrc_cache_key)
                if cached:
                    links.setdefault("deezer_url", cached.get("deezer_url", ""))
                    links.setdefault("tidal_url", cached.get("tidal_url", ""))
                    links.setdefault("amazon_url", cached.get("amazon_url", ""))
                    links.setdefault("youtube_url", cached.get("youtube_url", ""))
                    links.setdefault("spotify_url", cached.get("spotify_url", ""))
                else:
                    try:
                        dr = _req.get(
                            f"https://api.deezer.com/2.0/track/isrc:{isrc}",
                            timeout=10,
                        )
                        if dr.status_code == 200:
                            deezer_data = dr.json()
                            if deezer_data.get("id"):
                                deezer_url = deezer_data.get("link", "")
                                if deezer_url:
                                    links.setdefault("deezer_url", deezer_url)
                                    try:
                                        sl = self.songlink.get_all_urls(deezer_url)
                                        links.setdefault("tidal_url", sl.get("tidal_url", ""))
                                        links.setdefault("amazon_url", sl.get("amazon_url", ""))
                                        links.setdefault("youtube_url", sl.get("youtube_url", ""))
                                        links.setdefault("spotify_url", sl.get("spotify_url", ""))
                                        # Cache the ISRC resolution
                                        self._isrc_cache[isrc_cache_key] = {
                                            "deezer_url": deezer_url,
                                            "tidal_url": sl.get("tidal_url", ""),
                                            "amazon_url": sl.get("amazon_url", ""),
                                            "youtube_url": sl.get("youtube_url", ""),
                                            "spotify_url": sl.get("spotify_url", ""),
                                        }
                                    except Exception:
                                        pass
                    except Exception:
                        pass

                # Resolve title+artist via Qobuz for YouTube fallback search (with ISRC cache)
                if not task.title:
                    qres_cache_key = f"qobuz_isrc:{isrc}"
                    qres = self._isrc_cache.get(qres_cache_key)
                    if qres is None:
                        try:
                            qres = self.qobuz.search_by_isrc(isrc)
                            if qres and qres.get("title") and qres.get("artist"):
                                self._isrc_cache[qres_cache_key] = qres
                        except Exception:
                            qres = {}
                    if qres and qres.get("title") and qres.get("artist"):
                        task.title = task.title or qres.get("title", "")
                        task.artist = task.artist or qres.get("artist", "")
                        task.album = task.album or qres.get("album", "")

            # Resolve Apple Music URL via our own client to get track details
            if apple_music_url:
                try:
                    tracks = self.applemusic.expand_album(apple_music_url)
                    if tracks and not task.title:
                        t = tracks[0]
                        task.title = task.title or t.get("title", "")
                        task.artist = task.artist or t.get("artist", "")
                        task.album = task.album or t.get("album", "")
                        task.track_number = task.track_number or t.get("track_number", 0)
                        task.total_tracks = task.total_tracks or t.get("total_tracks", 0)
                        task.disc_number = task.disc_number or t.get("disc_number", 1)
                        if not isrc:
                            isrc = t.get("isrc", "")
                    if tracks and not links.get("apple_url"):
                        links["apple_url"] = apple_music_url
                except Exception:
                    pass

            # If we have title+artist but no links at all, ensure YouTube can search
            if task.title and task.artist:
                links.setdefault("title", task.title)
                links.setdefault("artist", task.artist)

            task.isrc = isrc
            task.status = DownloadStatus.DOWNLOADING
            self._notify(task)

            def progress_cb(done, total):
                task.progress = (done / total * 100) if total else 0
                self._notify(task)

            filepath = self._download(task, links, isrc, progress_cb)

            # Validate duration if enabled
            if self.validate_duration and task.duration_ms > 0:
                expected_sec = task.duration_ms // 1000
                valid, err_msg = validate_download_duration(filepath, expected_sec)
                if not valid:
                    print(f"[Validation] Warning: {err_msg}")

            task.status = DownloadStatus.EMBEDDING
            task.progress = 100
            self._notify(task)

            self._embed(filepath, task, isrc)

            task.status = DownloadStatus.COMPLETED
            task.output_path = filepath
            self._notify(task)

            # Save to history
            fmt = "MP3" if filepath.endswith(".mp3") else "FLAC"
            try:
                add_download({
                    "url": task.url, "title": task.title,
                    "artist": task.artist, "album": task.album,
                    "cover_url": task.cover_url, "quality": self.quality,
                    "format": fmt, "path": filepath, "source": task.source,
                })
            except Exception:
                pass

        except Exception as e:
            task.status = DownloadStatus.FAILED
            task.error = str(e)
            traceback.print_exc()
            self._notify(task)

    def _download(self, task: DownloadTask, links: dict, isrc: str,
                  progress_cb) -> str:
        # Lidarr-compatible layout: Artist / Album (Year) / track - Title
        safe_artist = self._safe_name(task.artist or "Unknown Artist")
        safe_album = self._safe_name(task.album or "Unknown Album")
        year = (task.year or "").strip()
        album_folder = f"{safe_album} ({year})" if year else safe_album
        safe_title = self._safe_name(task.title or task.url or task.isrc)
        if task.track_number:
            filename = f"{task.track_number:02d} - {safe_title}.flac"
        else:
            filename = f"{safe_title}.flac"

        album_dir = os.path.join(self.output_dir, safe_artist, album_folder)
        os.makedirs(album_dir, exist_ok=True)

        sources = self._ordered_sources(links, isrc, task)
        errors = []

        for name, fn in sources:
            try:
                path = fn(album_dir, filename, progress_cb)
                task.source = name
                return path
            except Exception as e:
                errors.append(f"{name}: {e}")

        raise ValueError(
            f"All sources failed for '{task.title}': " + "; ".join(errors))

    def _ordered_sources(self, links: dict, isrc: str, task: DownloadTask):
        sources = []
        tidal_url = links.get("tidal_url", "")
        amazon_url = links.get("amazon_url", "")
        youtube_url = links.get("youtube_url", "")
        spotify_url = links.get("spotify_url", "")
        apple_url = links.get("apple_url", "")

        def tidal_fn(d, f, cb):
            if not tidal_url:
                raise ValueError("No Tidal link")
            tid = self.tidal.parse_track_id(tidal_url)
            dl = self.tidal.get_download_url(tid, self.quality)
            return self.tidal.download_file(dl, os.path.join(d, f), cb)

        def spotify_fn(d, f, cb):
            if not spotify_url:
                raise ValueError("No Spotify link")
            return self.spotify.download_track(spotify_url, d, f, cb)

        def qobuz_fn(d, f, cb):
            if not isrc:
                raise ValueError("No ISRC")
            q = "27" if self.quality == "HI_RES" else "6"
            return self.qobuz.download_track(isrc, d, q, f, cb)

        def amazon_fn(d, f, cb):
            if not amazon_url:
                raise ValueError("No Amazon link")
            asin = self.amazon.extract_asin(amazon_url)
            return self.amazon.download_by_asin(asin, d, f, cb)

        def youtube_fn(d, f, cb):
            # Try direct URL first, fall back to text search
            if youtube_url:
                return self.youtube.download_track(youtube_url, d, f, cb)
            if task.title and task.artist:
                return self.youtube.search_and_download(
                    task.title, task.artist, d, f, cb)
            raise ValueError("No YouTube URL and no title/artist for search")

        order = {
            "tidal": ("Tidal", tidal_fn, bool(tidal_url)),
            "spotify": ("Spotify", spotify_fn, bool(spotify_url)),
            "qobuz": ("Qobuz", qobuz_fn, bool(isrc)),
            "amazon": ("Amazon", amazon_fn, bool(amazon_url)),
            "youtube": ("YouTube", youtube_fn,
                        bool(youtube_url) or bool(task.title and task.artist)),
        }
        pref = self.preferred_source.lower()

        # Preferred source goes first if it has data
        if pref in order:
            name, fn, has_data = order.pop(pref)
            if has_data:
                sources.append((name, fn))

        # Then remaining sources that have data, then those that don't
        with_data = []
        without_data = []
        for key, (name, fn, has_data) in order.items():
            if has_data:
                with_data.append((name, fn))
            else:
                without_data.append((name, fn))
        sources.extend(with_data)
        sources.extend(without_data)

        # Ensure preferred source is always in the list (even without data)
        pref_in_sources = any(n.lower() == pref for n, _ in sources)
        if not pref_in_sources:
            fn_map = {
                "tidal": ("Tidal", tidal_fn),
                "spotify": ("Spotify", spotify_fn),
                "qobuz": ("Qobuz", qobuz_fn),
                "amazon": ("Amazon", amazon_fn),
                "youtube": ("YouTube", youtube_fn),
            }
            if pref in fn_map:
                sources.append(fn_map[pref])

        return sources

    def _embed(self, filepath: str, task: DownloadTask, isrc: str):
        meta = Metadata(
            title=task.title, artist=task.artist, album=task.album,
            album_artist=task.artist,
            track_number=task.track_number or 0,
            total_tracks=task.total_tracks or 0,
            disc_number=task.disc_number or 0,
            total_discs=task.total_discs or 0,
            isrc=isrc,
        )

        # Fetch genre/label/year from MusicBrainz
        if isrc:
            try:
                mb = self.musicbrainz.fetch_metadata(
                    isrc, task.title, task.artist, task.album,
                    single_genre=True,
                )
                if mb.get("genre"):
                    meta.genre = mb["genre"]
                if mb.get("publisher"):
                    meta.publisher = mb["publisher"]
                if mb.get("year"):
                    meta.date = str(mb["year"])
            except Exception:
                pass

        if self.embed_lyrics_flag and task.title and task.artist:
            try:
                lr = self.lyrics.fetch(task.title, task.artist, task.album)
                if lr.get("found"):
                    meta.lyrics = lr.get("synced") or lr.get("plain", "")
            except Exception:
                pass

        cover_path = ""
        if task.cover_url:
            try:
                cover_path = os.path.join(
                    tempfile.gettempdir(), f"sf_cover_{task.id}.jpg")
                download_cover(task.cover_url, cover_path)
            except Exception:
                cover_path = ""

        try:
            embed_metadata(filepath, meta, cover_path)
        except Exception:
            pass
        finally:
            if cover_path and os.path.exists(cover_path):
                os.remove(cover_path)

    def _notify(self, task: DownloadTask):
        if self.on_progress:
            try:
                self.on_progress(task.to_dict())
            except Exception:
                pass

    @staticmethod
    def _safe_name(name: str) -> str:
        for c in '<>:"/\\|?*':
            name = name.replace(c, "")
        return name.strip()[:200]
