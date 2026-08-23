"""Download orchestrator - manages downloads from multiple sources."""

import os
import tempfile
import time
import traceback
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock, Thread
from typing import Callable, Optional

import requests as _req
from mutagen import File as MutagenFile

from .amazon import AmazonDownloader
from .analysis import validate_download_duration
from .applemusic import AppleMusicDownloader
from .deezer import DeezerDownloader
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
    RESAMPLING = "resampling"
    EMBEDDING = "embedding"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class _CancelledError(Exception):
    """Raised internally when a task is cancelled."""


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
    batch_id: str = ""
    batch_seq: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "url": self.url, "isrc": self.isrc,
            "title": self.title, "artist": self.artist, "album": self.album,
            "cover_url": self.cover_url, "duration_ms": self.duration_ms,
            "status": self.status, "progress": self.progress,
            "error": self.error, "output_path": self.output_path,
            "source": self.source, "year": self.year,
            "batch_id": self.batch_id,
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
        self.deezer = DeezerDownloader()
        self.youtube = YouTubeDownloader()
        self.spotify = SpotifyDownloader()
        self.songlink = SongLinkClient()
        self.lyrics = LyricsClient()
        self.musicbrainz = MusicBrainzClient()
        self.preferred_source: str = "tidal"
        self.quality: str = "LOSSLESS"
        self.embed_lyrics_flag: bool = True
        self.validate_duration: bool = True
        self.auto_resample: bool = True
        # Performance: cache SongLink resolutions and ISRC lookups
        self._sl_cache: dict[str, dict] = {}
        self._isrc_cache: dict[str, dict] = {}
        self._max_workers = 5
        self._source_backoff_until: dict[str, float] = {}
        self._source_last_error: dict[str, str] = {}
        # Batch ordering: buffer completed tasks until they can be flushed in seq order
        self._batch_next_seq: dict[str, int] = {}
        self._batch_buffer: dict[str, dict[int, tuple]] = {}  # bid -> {seq: (task, filepath)}
        self._cancelled_ids: set[str] = set()

    def add_track(self, url: str = "", isrc: str = "", title: str = "",
                  artist: str = "", album: str = "", cover_url: str = "",
                  duration_ms: int = 0, track_number: int = 0,
                  total_tracks: int = 0, disc_number: int = 0,
                  total_discs: int = 0, year: str = "",
                  batch_id: str = "", batch_seq: int = 0) -> str:
        """Add a track to the download queue. Provide URL or ISRC."""
        task_id = str(uuid.uuid4())[:8]
        task = DownloadTask(
            id=task_id, url=url, isrc=isrc,
            title=title or url or isrc, artist=artist,
            album=album, year=year, cover_url=cover_url, duration_ms=duration_ms,
            track_number=track_number, total_tracks=total_tracks,
            disc_number=disc_number, total_discs=total_discs,
            batch_id=batch_id, batch_seq=batch_seq,
        )
        with self._lock:
            self.tasks[task_id] = task
        self._notify(task)
        self._ensure_running()
        return task_id

    def get_queue(self) -> list[dict]:
        with self._lock:
            return [t.to_dict() for t in self.tasks.values()]

    def get_provider_health(self) -> list[dict]:
        now = time.time()
        providers = ["Tidal", "Spotify", "Deezer", "Qobuz", "Amazon", "SoundCloud", "YouTube"]
        health = []
        for name in providers:
            key = name.lower()
            backoff_until = self._source_backoff_until.get(key, 0)
            retry_after = max(0, int(backoff_until - now))
            health.append({
                "name": name,
                "status": "cooldown" if retry_after > 0 else "ready",
                "retry_after_seconds": retry_after,
                "last_error": self._source_last_error.get(key, ""),
            })
        return health

    def cancel_task(self, task_id: str):
        """Cancel a queued or in-progress download."""
        with self._lock:
            task = self.tasks.get(task_id)
            if task and task.status not in (
                DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED
            ):
                self._cancelled_ids.add(task_id)
                if task.status == DownloadStatus.QUEUED:
                    task.status = DownloadStatus.CANCELLED
                    self._notify(task)

    def cancel_all(self):
        """Cancel all queued and in-progress downloads."""
        with self._lock:
            for task in self.tasks.values():
                if task.status not in (
                    DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED
                ):
                    self._cancelled_ids.add(task.id)
                    if task.status == DownloadStatus.QUEUED:
                        task.status = DownloadStatus.CANCELLED
                        self._notify(task)

    def clear_completed(self):
        with self._lock:
            self.tasks = OrderedDict(
                (k, v) for k, v in self.tasks.items()
                if v.status not in (
                    DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED
                )
            )
            self._cancelled_ids.difference_update(
                set(self.tasks.keys())
            )

    def reload_provider_clients(self):
        """Re-create provider clients so refreshed endpoint overrides are picked up."""
        self.tidal = TidalDownloader()
        self.qobuz = QobuzDownloader()
        self.amazon = AmazonDownloader()
        self.deezer = DeezerDownloader()
        self.youtube = YouTubeDownloader()
        self.spotify = SpotifyDownloader()

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
        """Get up to `limit` queued tasks that are not cancelled."""
        with self._lock:
            queued = [
                t for t in self.tasks.values()
                if t.status == DownloadStatus.QUEUED and t.id not in self._cancelled_ids
            ]
            return queued[:limit]

    def _next_task(self) -> Optional[DownloadTask]:
        with self._lock:
            for t in self.tasks.values():
                if t.status == DownloadStatus.QUEUED:
                    return t
        return None

    def _check_cancelled(self, task: DownloadTask):
        if task.id in self._cancelled_ids:
            raise _CancelledError()

    def _process(self, task: DownloadTask):
        try:
            self._check_cancelled(task)
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
                elif parsed["platform"] == "amazon" and parsed["type"] == "track":
                    # Always keep the original Amazon URL — SongLink may resolve a
                    # track URL to an album URL causing wrong-track downloads.
                    links["amazon_url"] = task.url
                    # add_track defaults title to the URL when none is provided,
                    # so treat url-as-title the same as "no title".
                    has_real_title = task.title and task.title != task.url
                    if parsed["type"] == "track" and not has_real_title:
                        try:
                            ameta = self.amazon.fetch_track_metadata(task.url)
                            # Use ameta values directly — task.title may be the
                            # URL placeholder and should be overwritten.
                            task.title = ameta.get("title", "") or task.title
                            task.artist = ameta.get("artist", "") or task.artist
                            task.album = ameta.get("album", "") or task.album
                            task.year = ameta.get("year", "") or task.year
                            if not isrc:
                                isrc = ameta.get("isrc", "")
                        except Exception:
                            pass
                elif parsed["platform"] == "youtube" and parsed["type"] != "album":
                    links["youtube_url"] = task.url
                elif parsed["platform"] == "deezer" and parsed["type"] == "track":
                    links["deezer_url"] = task.url
                elif parsed["platform"] == "soundcloud":
                    links["soundcloud_url"] = task.url
                elif parsed["platform"] == "spotify" and parsed["type"] == "track":
                    links["spotify_url"] = task.url
                    has_real_title = self._has_real_title(task)
                    if not has_real_title:
                        try:
                            smeta = self.spotify.fetch_track_metadata(task.url)
                            task.title = smeta.get("title", "") or task.title
                            task.artist = smeta.get("artist", "") or task.artist
                            task.year = smeta.get("year", "") or task.year
                            if smeta.get("cover_url"):
                                task.cover_url = task.cover_url or smeta["cover_url"]
                            if smeta.get("duration_ms"):
                                task.duration_ms = task.duration_ms or smeta["duration_ms"]
                        except Exception:
                            pass
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
                    # Do NOT use SongLink's amazon_url here — for Amazon track URLs,
                    # SongLink can return the parent album URL instead of the track URL.
                    # The original task.url was already set above and must be preserved.
                    if not links.get("amazon_url"):
                        links["amazon_url"] = sl.get("amazon_url", "")
                    links.setdefault("deezer_url", sl.get("deezer_url", ""))
                    links.setdefault("youtube_url", sl.get("youtube_url", ""))
                    links.setdefault("spotify_url", sl.get("spotify_url", ""))
                    links.setdefault("soundcloud_url", sl.get("soundcloud_url", ""))
                    if not isrc:
                        isrc = sl.get("isrc", "")
                    # Only use SongLink title/artist when an ISRC was found — a missing
                    # ISRC indicates SongLink resolved to an album entity rather than a
                    # track, so the title would be the album name, not the track name.
                    sl_isrc = sl.get("isrc", "")
                    if sl_isrc and not self._has_real_title(task):
                        task.title = sl.get("title", "") or task.title
                        task.artist = sl.get("artist", "") or task.artist
                        task.album = sl.get("album", "") or task.album
                except Exception:
                    pass

            if not isrc and links.get("deezer_url"):
                try:
                    deezer_track_id = self.deezer.extract_track_id(links["deezer_url"])
                    deezer_meta = self.deezer.fetch_track(deezer_track_id)
                    deezer_isrc = (deezer_meta.get("isrc") or "").upper().strip()
                    if deezer_isrc:
                        isrc = deezer_isrc
                    if not self._has_real_title(task):
                        task.title = deezer_meta.get("title", "") or task.title
                    if not task.artist:
                        task.artist = deezer_meta.get("artist", {}).get("name", "") or task.artist
                    if not task.album:
                        task.album = deezer_meta.get("album", {}).get("title", "") or task.album
                    if not task.cover_url:
                        task.cover_url = (
                            deezer_meta.get("album", {}).get("cover_xl")
                            or deezer_meta.get("album", {}).get("cover_big")
                            or deezer_meta.get("album", {}).get("cover_medium")
                            or task.cover_url
                        )
                    if not task.duration_ms and deezer_meta.get("duration"):
                        task.duration_ms = int((deezer_meta.get("duration") or 0) * 1000)
                except Exception:
                    pass

            if not isrc and links.get("amazon_url"):
                try:
                    ameta = self.amazon.fetch_track_metadata(links["amazon_url"])
                    if ameta.get("isrc"):
                        isrc = ameta.get("isrc", "")
                    task.title = ameta.get("title", "") or task.title
                    task.artist = ameta.get("artist", "") or task.artist
                    task.album = ameta.get("album", "") or task.album
                    task.year = ameta.get("year", "") or task.year
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
                if not self._has_real_title(task):
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
                        task.title = qres.get("title", "") or task.title
                        task.artist = task.artist or qres.get("artist", "")
                        task.album = task.album or qres.get("album", "")

            # Resolve Apple Music URL for metadata only (title/artist/ISRC).
            # Apple Music is not a download source — metadata is used so
            # Tidal/Qobuz/etc. can download the actual full track.
            if apple_music_url:
                try:
                    tracks = self.applemusic.expand_album(apple_music_url)
                    if tracks and not self._has_real_title(task):
                        t = tracks[0]
                        task.title = t.get("title", "") or task.title
                        task.artist = t.get("artist", "") or task.artist
                        task.album = t.get("album", "") or task.album
                        task.track_number = task.track_number or t.get("track_number", 0)
                        task.total_tracks = task.total_tracks or t.get("total_tracks", 0)
                        task.disc_number = task.disc_number or t.get("disc_number", 1)
                        if not isrc:
                            isrc = t.get("isrc", "")
                except Exception:
                    pass

            # If we have title+artist but no links at all, ensure YouTube can search
            if task.title and task.artist:
                links.setdefault("title", task.title)
                links.setdefault("artist", task.artist)

            task.isrc = isrc
            self._check_cancelled(task)
            task.status = DownloadStatus.DOWNLOADING
            self._notify(task)

            def progress_cb(done, total):
                self._check_cancelled(task)
                if total:
                    task.progress = done / total * 100
                else:
                    # No Content-Length — estimate progress assuming a typical
                    # track size (~5 MB). Cap at 99 so the bar moves visibly.
                    task.progress = min(done / 5_000_000 * 100, 99)
                self._notify(task)

            print(f"[Download] Starting download for '{task.title}' — links: {links}")
            filepath = self._download(task, links, isrc, progress_cb)
            task.error = ""

            self._check_cancelled(task)

            # Auto-resample to 192kHz/24-bit if enabled
            if self.auto_resample:
                task.status = DownloadStatus.RESAMPLING
                self._notify(task)
                from .resample import resample_inplace
                filepath = resample_inplace(filepath)

            task.status = DownloadStatus.EMBEDDING
            task.progress = 100
            self._notify(task)

            self._embed(filepath, task, isrc)

            task.output_path = filepath

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

            # Batch-ordered completion: buffer and flush in sequence order
            if task.batch_id:
                self._flush_batch_completion(task, failed=False)
            else:
                task.status = DownloadStatus.COMPLETED
                self._notify(task)

        except _CancelledError:
            task.status = DownloadStatus.CANCELLED
            task.error = "Cancelled"
            with self._lock:
                self._cancelled_ids.discard(task.id)
            self._notify(task)
        except Exception as e:
            task.error = str(e)
            traceback.print_exc()
            if task.batch_id:
                self._flush_batch_completion(task, failed=True)
            else:
                task.status = DownloadStatus.FAILED
                self._notify(task)

    @staticmethod
    def _has_real_title(task: 'DownloadTask') -> bool:
        """Return True if task.title contains an actual track name, not a URL placeholder."""
        t = task.title.strip()
        if not t:
            return False
        if t == task.url:
            return False
        if t.startswith(("http://", "https://")):
            return False
        return True

    def _download(self, task: DownloadTask, links: dict, isrc: str,
                  progress_cb) -> str:
        # Lidarr-compatible layout: Artist / Album (Year) / track - Title
        # Use resolved metadata from song.link if title not provided
        resolved_title = links.get("title", "") if isinstance(links, dict) else ""
        resolved_artist = links.get("artist", "") if isinstance(links, dict) else ""
        resolved_album = links.get("album", "") if isinstance(links, dict) else ""

        has_real = self._has_real_title(task)
        if not has_real and resolved_title:
            task.title = resolved_title
            has_real = True
        if not task.artist and resolved_artist:
            task.artist = resolved_artist
        if not task.album and resolved_album:
            task.album = resolved_album

        safe_artist = self._safe_name(task.artist or "Unknown Artist")
        safe_album = self._safe_name(task.album or "Unknown Album")
        year = (task.year or "").strip()
        album_folder = f"{safe_album} ({year})" if year else safe_album
        safe_title = self._safe_name(task.title if self._has_real_title(task) else (task.isrc or "track"))
        if task.track_number:
            filename = f"{task.track_number:02d} - {safe_title}.flac"
        else:
            filename = f"{safe_title}.flac"

        album_dir = os.path.join(self.output_dir, safe_artist, album_folder)
        os.makedirs(album_dir, exist_ok=True)

        sources = self._ordered_sources(links, isrc, task)
        errors = []
        expected_sec = (task.duration_ms // 1000) if task.duration_ms > 0 else 0

        print(f"[Download] '{task.title}' — available links: Tidal={bool(links.get('tidal_url'))} Spotify={bool(links.get('spotify_url'))} Deezer={bool(links.get('deezer_url'))} Qobuz={bool(isrc)} Amazon={bool(links.get('amazon_url'))} YouTube={bool(links.get('youtube_url'))} SoundCloud={bool(links.get('soundcloud_url'))}")
        print(f"[Download] '{task.title}' — source order: {[n for n,_ in sources]}")

        for name, fn in sources:
            print(f"[Download] '{task.title}' — trying {name}…")
            try:
                path = fn(album_dir, filename, progress_cb)
                # Validate duration to reject preview/sample clips before accepting
                if self.validate_duration and expected_sec > 0:
                    valid, err_msg = validate_download_duration(path, expected_sec)
                    if not valid:
                        print(f"[Validation] {name}: {err_msg} — skipping, trying next source")
                        task.error = f"Preview from {name}, retrying…"
                        self._notify(task)
                        task.error = ""
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                        errors.append(f"{name}: {err_msg}")
                        continue
                if not (self._has_real_title(task) and task.artist and task.album):
                    if self._populate_task_from_embedded_metadata(path, task):
                        path = self._move_to_task_path(path, task)
                task.source = name
                return path
            except Exception as e:
                print(f"[Download] '{task.title}' — {name} failed: {e}")
                self._record_source_failure(name, str(e))
                task.error = f"{name} failed, trying next source"
                self._notify(task)
                errors.append(f"{name}: {e}")

        raise ValueError(
            f"All sources failed for '{task.title}': " + "; ".join(errors))

    def _ordered_sources(self, links: dict, isrc: str, task: DownloadTask):
        sources = []
        tidal_url = links.get("tidal_url", "")
        amazon_url = links.get("amazon_url", "")
        youtube_url = links.get("youtube_url", "")
        spotify_url = links.get("spotify_url", "")
        deezer_url = links.get("deezer_url", "")
        soundcloud_url = links.get("soundcloud_url", "")

        def tidal_fn(d, f, cb):
            if not tidal_url:
                raise ValueError("No Tidal link")
            return self.tidal.download_track(tidal_url, d, self.quality, f, cb)

        def spotify_fn(d, f, cb):
            if not spotify_url:
                raise ValueError("No Spotify link")
            return self.spotify.download_track(spotify_url, d, f, cb)

        def qobuz_fn(d, f, cb):
            if not isrc:
                raise ValueError("No ISRC")
            q = "27" if self.quality == "HI_RES" else "6"
            return self.qobuz.download_track(isrc, d, q, f, cb)

        def deezer_fn(d, f, cb):
            if not deezer_url:
                raise ValueError("No Deezer link")
            q = "27" if self.quality == "HI_RES" else "6"
            return self.deezer.download_track(
                deezer_url=deezer_url,
                output_dir=d,
                filename=f,
                quality=q,
                progress_cb=cb,
                isrc=isrc,
            )

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

        def soundcloud_fn(d, f, cb):
            # First try the original SoundCloud URL via yt-dlp, then mapped YouTube,
            # then title/artist text search.
            if soundcloud_url:
                return self.youtube.download_external_url(soundcloud_url, d, f, cb)
            if youtube_url:
                return self.youtube.download_track(youtube_url, d, f, cb)
            if task.title and task.artist:
                return self.youtube.search_and_download(task.title, task.artist, d, f, cb)
            raise ValueError("No mapped stream for SoundCloud track")

        order = {
            "tidal": ("Tidal", tidal_fn, bool(tidal_url)),
            "spotify": ("Spotify", spotify_fn, bool(spotify_url)),
            "deezer": ("Deezer", deezer_fn, bool(deezer_url)),
            "qobuz": ("Qobuz", qobuz_fn, bool(isrc)),
            "amazon": ("Amazon", amazon_fn, bool(amazon_url)),
        }
        pref = self.preferred_source.lower()

        # Preferred source is only allowed to reorder the primary providers.
        # SoundCloud and YouTube remain fixed fallbacks after the normal sources.
        if pref in order:
            name, fn, has_data = order.pop(pref)
            if has_data and not self._is_source_in_backoff(name):
                sources.append((name, fn))

        # Then remaining sources that actually have data.
        with_data = []
        for key, (name, fn, has_data) in order.items():
            if has_data and not self._is_source_in_backoff(name):
                with_data.append((name, fn))
        sources.extend(with_data)

        # Providers in cooldown are appended after healthy primaries and fallback-only sources.
        cooled_down = []
        for key, (name, fn, has_data) in order.items():
            if has_data and self._is_source_in_backoff(name):
                cooled_down.append((name, fn))

        # SoundCloud fallback — appended after all other sources.
        # Uses YouTube as its fallback internally.
        soundcloud_in_sources = any(n.lower() == "soundcloud" for n, _ in sources)
        if not soundcloud_in_sources and (bool(soundcloud_url) or bool(youtube_url) or bool(task.title and task.artist)):
            sources.append(("SoundCloud", soundcloud_fn))

        # YouTube last resort — only appended after all other sources including SoundCloud.
        # Uses title+artist search when no youtube_url is available.
        youtube_in_sources = any(n.lower() == "youtube" for n, _ in sources)
        if not youtube_in_sources and (bool(youtube_url) or bool(task.title and task.artist)):
            sources.append(("YouTube", youtube_fn))

        sources.extend(cooled_down)

        return sources

    def _is_source_in_backoff(self, source_name: str) -> bool:
        key = source_name.lower()
        return self._source_backoff_until.get(key, 0) > time.time()

    def _record_source_failure(self, source_name: str, error_message: str):
        cooldown = self._source_cooldown_seconds(error_message)
        key = source_name.lower()
        self._source_last_error[key] = error_message
        if cooldown > 0:
            self._source_backoff_until[key] = time.time() + cooldown

    @staticmethod
    def _source_cooldown_seconds(error_message: str) -> int:
        message = error_message.lower()
        if any(token in message for token in (
            "failed to resolve",
            "name resolution",
            "service unavailable",
            "bad gateway",
            "read timed out",
            "connection",
            "forbidden",
            "unauthorized",
            "token refresh failed",
            "expecting value",
            "invalid json",
            "received html",
            "support unavailable",
            "user authentication is required",
        )):
            return 15 * 60
        return 0

    def _flush_batch_completion(self, task: DownloadTask, failed: bool = False):
        """Buffer a completed/failed batch task and flush all consecutive
        completed tasks starting from the next expected sequence number."""
        bid = task.batch_id
        with self._lock:
            if bid not in self._batch_next_seq:
                # Determine the lowest batch_seq for this batch
                seqs = [t.batch_seq for t in self.tasks.values()
                        if t.batch_id == bid]
                self._batch_next_seq[bid] = min(seqs) if seqs else 0
            self._batch_buffer.setdefault(bid, {})[task.batch_seq] = (task, failed)

            # Flush all consecutive completed entries
            while self._batch_next_seq[bid] in self._batch_buffer.get(bid, {}):
                seq = self._batch_next_seq[bid]
                buf_task, buf_failed = self._batch_buffer[bid].pop(seq)
                buf_task.status = DownloadStatus.FAILED if buf_failed else DownloadStatus.COMPLETED
                self._batch_next_seq[bid] = seq + 1
                self._notify(buf_task)

            # Clean up batch tracking when all tasks are done
            batch_tasks = [t for t in self.tasks.values() if t.batch_id == bid]
            all_done = all(t.status in (DownloadStatus.COMPLETED, DownloadStatus.FAILED)
                          for t in batch_tasks)
            if all_done and bid in self._batch_next_seq:
                del self._batch_next_seq[bid]
                self._batch_buffer.pop(bid, None)

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

    def _populate_task_from_embedded_metadata(self, filepath: str, task: DownloadTask) -> bool:
        try:
            audio = MutagenFile(filepath)
            tags = getattr(audio, "tags", None)
            if not tags:
                return False
        except Exception:
            return False

        def first_tag(*names: str) -> str:
            for name in names:
                try:
                    value = tags.get(name)
                except Exception:
                    # Some mutagen tag containers can raise for unknown keys.
                    continue
                if not value:
                    continue
                if isinstance(value, list):
                    value = value[0]
                if hasattr(value, "text"):
                    text = getattr(value, "text", [])
                    if text:
                        value = text[0]
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="ignore")
                if value:
                    return str(value).strip()
            return ""

        try:
            changed = False
            title = first_tag("TITLE", "title", "©nam")
            artist = first_tag("ARTIST", "artist", "albumartist", "©ART")
            album = first_tag("ALBUM", "album", "©alb")

            # Overwrite title if current value is a URL placeholder
            has_real = DownloadManager._has_real_title(task)
            if title and not has_real:
                task.title = title
                changed = True
            if artist and not task.artist:
                task.artist = artist
                changed = True
            if album and not task.album:
                task.album = album
                changed = True
            return changed
        except Exception:
            return False

    def _move_to_task_path(self, filepath: str, task: DownloadTask) -> str:
        ext = os.path.splitext(filepath)[1] or ".flac"
        safe_artist = self._safe_name(task.artist or "Unknown Artist")
        safe_album = self._safe_name(task.album or "Unknown Album")
        year = (task.year or "").strip()
        album_folder = f"{safe_album} ({year})" if year else safe_album
        safe_title = self._safe_name(task.title or task.url or task.isrc or "track")
        if task.track_number:
            filename = f"{task.track_number:02d} - {safe_title}{ext}"
        else:
            filename = f"{safe_title}{ext}"
        target_dir = os.path.join(self.output_dir, safe_artist, album_folder)
        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, filename)
        if os.path.abspath(target_path) == os.path.abspath(filepath):
            return filepath
        if os.path.exists(target_path):
            base, ext = os.path.splitext(target_path)
            counter = 1
            while os.path.exists(f"{base} ({counter}){ext}"):
                counter += 1
            target_path = f"{base} ({counter}){ext}"
        os.replace(filepath, target_path)
        return target_path

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
