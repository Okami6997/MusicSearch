"""SongsFetch - Flask web application for music search and download."""

import os
import time
import uuid
from datetime import datetime
from threading import Lock, Thread

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from werkzeug.middleware.proxy_fix import ProxyFix

import requests as http_requests

from backend.songlink import SongLinkClient, is_music_url, parse_music_url
from backend.lyrics import LyricsClient
from backend.downloader import DownloadManager
from backend.musicbrainz import MusicBrainzClient
from backend.youtube import YouTubeDownloader
from backend import analysis, resample, filemanager, history

try:
    import eventlet
    eventlet.monkey_patch()
    _async_mode = "eventlet"
except Exception:
    _async_mode = "threading"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(32).hex()
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=_async_mode, ping_timeout=60)

# ── Global state ─────────────────────────────────────────────

songlink_client = SongLinkClient()
lyrics_client = LyricsClient()
musicbrainz_client = MusicBrainzClient()
download_manager: DownloadManager | None = None

DEFAULT_DIR = os.environ.get("SONGSFETCH_OUTPUT_DIR") or os.path.join(os.path.expanduser("~"), "Music", "SongsFetch")
settings = {
    "output_dir": DEFAULT_DIR,
    "preferred_source": "tidal",
    "quality": "LOSSLESS",
    "embed_lyrics": True,
}
scheduler_jobs: dict[str, dict] = {}
scheduler_lock = Lock()
_scheduler_running = False
_scheduler_thread: Thread | None = None


def _init_download_manager():
    global download_manager
    download_manager = DownloadManager(
        output_dir=settings["output_dir"],
        on_progress=_on_progress,
    )
    download_manager.preferred_source = settings["preferred_source"]
    download_manager.quality = settings["quality"]
    download_manager.embed_lyrics_flag = settings["embed_lyrics"]


def _on_progress(task_data: dict):
    socketio.emit("download_progress", task_data)


def _parse_schedule_time(value: str) -> datetime:
    # Accepts ISO 8601 or HTML datetime-local values.
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _execute_scheduled_resample(job_id: str):
    with scheduler_lock:
        job = scheduler_jobs.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["last_run"] = int(time.time())
        job["error"] = ""

    try:
        results = resample.resample_audio(
            job["files"],
            job["sample_rate"],
            job["bit_depth"],
        )
        success = sum(1 for r in results if r.get("success"))
        details = [f"Scheduled job: {job['name']}"]
        if job["sample_rate"]:
            details.append(f"Sample rate: {job['sample_rate']} Hz")
        if job["bit_depth"]:
            details.append(f"Bit depth: {job['bit_depth']}-bit")
        details.append(f"Success: {success}/{len(results)}")
        history.add_operation("resample_scheduled", job["files"], ", ".join(details))
        with scheduler_lock:
            if job_id in scheduler_jobs:
                scheduler_jobs[job_id]["status"] = "completed"
    except Exception as e:
        with scheduler_lock:
            if job_id in scheduler_jobs:
                scheduler_jobs[job_id]["status"] = "failed"
                scheduler_jobs[job_id]["error"] = str(e)


def _scheduler_worker():
    global _scheduler_running
    while True:
        due_ids = []
        now = datetime.now()
        with scheduler_lock:
            for job_id, job in scheduler_jobs.items():
                if job.get("status") != "scheduled":
                    continue
                run_at = datetime.fromtimestamp(job["run_at"])
                if run_at <= now:
                    due_ids.append(job_id)
                    job["status"] = "queued"

        for job_id in due_ids:
            Thread(target=_execute_scheduled_resample, args=(job_id,), daemon=True).start()

        time.sleep(10)


def _ensure_scheduler_running():
    global _scheduler_running, _scheduler_thread
    if _scheduler_running:
        return
    _scheduler_running = True
    _scheduler_thread = Thread(target=_scheduler_worker, daemon=True)
    _scheduler_thread.start()


_init_download_manager()
history.init_db()
_ensure_scheduler_running()
youtube_client = YouTubeDownloader()


def _youtube_search(q: str, limit: int = 10) -> list[dict]:
    """Search YouTube Music and return tracks in the standard format."""
    try:
        yt_results = youtube_client.search_tracks(q, limit=limit)
        tracks = []
        for r in yt_results:
            tracks.append({
                "id": r.get("id", ""),
                "title": r.get("title", ""),
                "artist": r.get("artist", ""),
                "album": r.get("album", ""),
                "cover_url": r.get("cover_url", ""),
                "duration_ms": r.get("duration_ms", 0),
                "isrc": "",
                "hires": False,
                "bit_depth": 0,
                "sample_rate": 0,
                "url": r.get("url", ""),
                "preview_url": "",
                "source": "youtube",
                "service": "YouTube Music",
            })
        return tracks
    except Exception:
        return []


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


# ── Routes ───────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/resolve", methods=["GET"])
def resolve_url():
    """Resolve any music platform URL to cross-platform links."""
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL required"}), 400
    if not is_music_url(url):
        return jsonify({"error": "Not a recognized music URL"}), 400
    try:
        parsed = parse_music_url(url)
        links = songlink_client.check_availability(url)
        links["parsed"] = parsed
        # Save to fetch history
        try:
            history.add_fetch({
                "url": url, "type": parsed.get("type", "track"),
                "name": url, "info": parsed.get("platform", ""),
            })
        except Exception:
            pass
        return jsonify(links)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search", methods=["GET"])
def search():
    """Search for tracks by query or ISRC. Emits partial results via SocketIO as
    each service responds, then returns the final aggregated payload."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Query required"}), 400

    # If it looks like an ISRC, search by ISRC via Qobuz (fast single-result path)
    if len(q) == 12 and q[:2].isalpha() and q[2:].isalnum():
        try:
            from backend.qobuz import QobuzDownloader
            qobuz = QobuzDownloader()
            track = qobuz.search_by_isrc(q)
            track.setdefault("cover_url", "")
            track["duration_ms"] = (track.pop("duration", 0) or 0) * 1000
            return jsonify({"tracks": [track], "source": "qobuz"})
        except Exception:
            pass

    offset = request.args.get("offset", 0, type=int)
    sid = getattr(request, "sid", "") or ""   # empty for plain HTTP requests

    # ── Concurrent search helper ────────────────────────────────────────────
    def _run_concurrent(q: str, offset: int, fallback: bool = False) -> dict:
        """Run all service searches concurrently; emit partial SocketIO events
        as each one completes, then return the final aggregated dict."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from backend.qobuz import QobuzDownloader

        def _do_qobuz_tracks():
            qobuz_dl = QobuzDownloader()
            r = qobuz_dl.session.get(
                "https://www.qobuz.com/api.json/0.2/track/search",
                params={"query": q, "limit": 20, "offset": offset, "app_id": qobuz_dl.APP_ID},
                timeout=15,
            )
            r.raise_for_status()
            out = []
            for t in r.json().get("tracks", {}).get("items", []):
                album_data = t.get("album", {})
                out.append({
                    "id": t.get("id"), "title": t.get("title", ""),
                    "artist": t.get("performer", {}).get("name", ""),
                    "album": album_data.get("title", ""),
                    "cover_url": album_data.get("image", {}).get("large", ""),
                    "duration_ms": (t.get("duration", 0) or 0) * 1000,
                    "isrc": t.get("isrc", ""),
                    "hires": t.get("hires_streamable", False),
                    "bit_depth": t.get("maximum_bit_depth", 0),
                    "sample_rate": t.get("maximum_sampling_rate", 0),
                    "track_number": t.get("track_number", 0),
                    "total_tracks": t.get("album", {}).get("tracks_count", 0),
                    "disc_number": t.get("media_number", 0) or 1,
                    "year": (album_data.get("release_date_original") or "")[:4],
                    "preview_url": "",
                    "source": "qobuz", "service": "Qobuz",
                })
            return out

        def _do_qobuz_artists():
            qobuz_dl = QobuzDownloader()
            r = qobuz_dl.session.get(
                "https://www.qobuz.com/api.json/0.2/artist/search",
                params={"query": q, "limit": 5, "app_id": qobuz_dl.APP_ID},
                timeout=15,
            )
            r.raise_for_status()
            out = []
            for a in r.json().get("artists", {}).get("items", []):
                img = a.get("image", {})
                out.append({
                    "id": a.get("id"), "name": a.get("name", ""),
                    "image_url": img.get("large", "") or img.get("medium", "") or img.get("small", ""),
                    "albums_count": a.get("albums_count", 0),
                    "source": "qobuz", "service": "Qobuz",
                })
            return out

        def _do_qobuz_albums():
            qobuz_dl = QobuzDownloader()
            r = qobuz_dl.session.get(
                "https://www.qobuz.com/api.json/0.2/album/search",
                params={"query": q, "limit": 5, "app_id": qobuz_dl.APP_ID},
                timeout=15,
            )
            r.raise_for_status()
            out = []
            for al in r.json().get("albums", {}).get("items", []):
                out.append({
                    "id": al.get("id"), "title": al.get("title", ""),
                    "artist": al.get("artist", {}).get("name", ""),
                    "cover_url": al.get("image", {}).get("large", ""),
                    "tracks_count": al.get("tracks_count", 0),
                    "release_date": al.get("release_date_original", ""),
                    "year": (al.get("release_date_original") or "")[:4],
                    "hires": al.get("hires_streamable", False),
                    "source": "qobuz", "service": "Qobuz",
                })
            return out

        def _do_itunes_tracks():
            r = http_requests.get(
                "https://itunes.apple.com/search",
                params={"term": q, "media": "music", "entity": "song", "limit": 20, "offset": offset},
                timeout=15,
            )
            r.raise_for_status()
            out = []
            for t in r.json().get("results", []):
                artwork = t.get("artworkUrl100", "")
                if artwork:
                    artwork = artwork.replace("100x100bb", "600x600bb")
                out.append({
                    "id": t.get("trackId"), "title": t.get("trackName", ""),
                    "artist": t.get("artistName", ""), "album": t.get("collectionName", ""),
                    "cover_url": artwork,
                    "duration_ms": t.get("trackTimeMillis", 0) or 0,
                    "url": t.get("trackViewUrl", ""), "isrc": "",
                    "hires": False, "bit_depth": 0, "sample_rate": 0,
                    "year": (t.get("releaseDate") or "")[:4],
                    "preview_url": t.get("previewUrl", ""),
                    "source": "itunes", "service": "Apple Music",
                })
            return out

        def _do_itunes_artists():
            r = http_requests.get(
                "https://itunes.apple.com/search",
                params={"term": q, "media": "music", "entity": "musicArtist", "limit": 5},
                timeout=15,
            )
            r.raise_for_status()
            out = []
            for a in r.json().get("results", []):
                out.append({
                    "id": a.get("artistId"), "name": a.get("artistName", ""),
                    "image_url": "", "albums_count": 0,
                    "source": "itunes", "service": "Apple Music",
                })
            return out

        def _do_itunes_albums():
            r = http_requests.get(
                "https://itunes.apple.com/search",
                params={"term": q, "media": "music", "entity": "album", "limit": 5},
                timeout=15,
            )
            r.raise_for_status()
            out = []
            for al in r.json().get("results", []):
                art = al.get("artworkUrl100", "")
                if art:
                    art = art.replace("100x100bb", "600x600bb")
                out.append({
                    "id": al.get("collectionId"), "title": al.get("collectionName", ""),
                    "artist": al.get("artistName", ""), "cover_url": art,
                    "tracks_count": al.get("trackCount", 0),
                    "release_date": al.get("releaseDate", ""),
                    "year": (al.get("releaseDate") or "")[:4],
                    "hires": False, "source": "itunes", "service": "Apple Music",
                })
            return out

        def _do_deezer_tracks():
            from backend.deezer import DeezerClient
            return DeezerClient().search_tracks(q, limit=10)

        def _do_soundcloud_tracks():
            from backend.soundcloud import SoundCloudClient
            return SoundCloudClient().search_tracks(q, limit=10)

        def _do_youtube():
            return _youtube_search(q, 10)

        # Build list of (label, future) pairs so we can emit when each completes
        if offset == 0:
            tasks = [
                ("qobuz_tracks",   _do_qobuz_tracks),
                ("qobuz_artists",  _do_qobuz_artists),
                ("qobuz_albums",   _do_qobuz_albums),
                ("youtube_tracks", _do_youtube),
                ("deezer_tracks", _do_deezer_tracks),
                ("soundcloud_tracks", _do_soundcloud_tracks),
            ]
            if fallback:
                tasks += [
                    ("itunes_tracks",  _do_itunes_tracks),
                    ("itunes_artists", _do_itunes_artists),
                    ("itunes_albums",  _do_itunes_albums),
                ]
        else:
            # Pagination: only tracks
            tasks = [("qobuz_tracks", _do_qobuz_tracks)]
            if fallback:
                tasks.append(("itunes_tracks", _do_itunes_tracks))

        result = {
            "tracks": [], "artists": [], "albums": [],
            "youtube_tracks": [], "deezer_tracks": [], "soundcloud_tracks": [], "source": "qobuz",
            "has_more": False, "offset": offset, "q": q,
        }
        if fallback:
            result["source"] = "itunes"

        done_labels = set()
        with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
            futures = {ex.submit(fn): label for label, fn in tasks}
            for future in as_completed(futures):
                label = futures[future]
                done_labels.add(label)
                try:
                    data = future.result()
                except Exception as exc:
                    data = []
                    print(f"[search] {label} failed: {exc}")

                if label == "qobuz_tracks":
                    result["tracks"] = data
                    result["has_more"] = len(data) >= 20
                    if sid:
                        socketio.emit("search_partial", {
                            "section": "tracks", "data": data, "done": list(done_labels),
                            "source": result["source"], "has_more": result["has_more"],
                        }, room=sid)
                elif label == "qobuz_artists":
                    result["artists"] = data
                    if sid:
                        socketio.emit("search_partial", {
                            "section": "artists", "data": data, "done": list(done_labels),
                        }, room=sid)
                elif label == "qobuz_albums":
                    result["albums"] = data
                    if sid:
                        socketio.emit("search_partial", {
                            "section": "albums", "data": data, "done": list(done_labels),
                        }, room=sid)
                elif label == "youtube_tracks":
                    result["youtube_tracks"] = data
                    if sid:
                        socketio.emit("search_partial", {
                            "section": "youtube_tracks", "data": data, "done": list(done_labels),
                        }, room=sid)
                elif label == "deezer_tracks":
                    result["deezer_tracks"] = data
                    if sid:
                        socketio.emit("search_partial", {
                            "section": "deezer_tracks", "data": data, "done": list(done_labels),
                        }, room=sid)
                elif label == "soundcloud_tracks":
                    result["soundcloud_tracks"] = data
                    if sid:
                        socketio.emit("search_partial", {
                            "section": "soundcloud_tracks", "data": data, "done": list(done_labels),
                        }, room=sid)
                elif label == "itunes_tracks":
                    result["tracks"] = data
                    result["source"] = "itunes"
                    result["has_more"] = len(data) >= 20
                    if sid:
                        socketio.emit("search_partial", {
                            "section": "tracks", "data": data, "done": list(done_labels),
                            "source": "itunes", "has_more": result["has_more"],
                        }, room=sid)
                elif label == "itunes_artists":
                    result["artists"] = data
                    if sid:
                        socketio.emit("search_partial", {
                            "section": "artists", "data": data, "done": list(done_labels),
                        }, room=sid)
                elif label == "itunes_albums":
                    result["albums"] = data
                    if sid:
                        socketio.emit("search_partial", {
                            "section": "albums", "data": data, "done": list(done_labels),
                        }, room=sid)

        if sid:
            socketio.emit("search_done", {
                "tracks": result["tracks"], "artists": result["artists"],
                "albums": result["albums"], "youtube_tracks": result["youtube_tracks"],
                "deezer_tracks": result["deezer_tracks"],
                "soundcloud_tracks": result["soundcloud_tracks"],
                "source": result["source"], "has_more": result["has_more"],
                "offset": result["offset"],
            }, room=sid)

        return result

    # ── Try Qobuz first; fall back to iTunes on any error ──────────────────
    try:
        result = _run_concurrent(q, offset, fallback=False)
        if result["tracks"] or result["artists"] or result["albums"]:
            return jsonify(result)
    except Exception:
        pass

    # iTunes fallback
    try:
        result = _run_concurrent(q, offset, fallback=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"Search failed: {str(e)}"}), 500

@app.route("/api/search/expand", methods=["GET"])
def search_expand():
    """Fetch expandable details for album tracks or artist albums."""
    kind = request.args.get("kind", "").strip().lower()  # album | artist
    source = request.args.get("source", "").strip().lower()  # qobuz | itunes
    item_id = request.args.get("id", "").strip()

    if kind not in ("album", "artist"):
        return jsonify({"error": "kind must be album or artist"}), 400
    if source not in ("qobuz", "itunes"):
        return jsonify({"error": "source must be qobuz or itunes"}), 400
    if not item_id:
        return jsonify({"error": "id required"}), 400

    try:
        if source == "qobuz":
            from backend.qobuz import QobuzDownloader
            qobuz = QobuzDownloader()

            if kind == "album":
                resp = qobuz.session.get(
                    "https://www.qobuz.com/api.json/0.2/album/get",
                    params={"album_id": item_id, "app_id": qobuz.APP_ID},
                    timeout=20,
                )
                resp.raise_for_status()
                album = resp.json()
                album_title = album.get("title", "")
                album_cover = album.get("image", {}).get("large", "")
                album_year = (album.get("release_date_original") or "")[:4]
                total_tracks = int(album.get("tracks_count", 0) or 0)
                items = []
                for t in album.get("tracks", {}).get("items", []):
                    items.append({
                        "id": t.get("id"),
                        "title": t.get("title", ""),
                        "artist": t.get("performer", {}).get("name", ""),
                        "duration_ms": int((t.get("duration", 0) or 0) * 1000),
                        "track_number": int(t.get("track_number", 0) or 0),
                        "disc_number": int(t.get("media_number", 0) or 1),
                        "total_tracks": total_tracks,
                        "album": album_title,
                        "cover_url": album_cover,
                        "year": album_year,
                        "isrc": t.get("isrc", ""),
                        "url": "",
                        "source": "qobuz",
                    })
                return jsonify({"kind": kind, "source": source, "items": items})

            # artist
            resp = qobuz.session.get(
                "https://www.qobuz.com/api.json/0.2/artist/get",
                params={"artist_id": item_id, "extra": "albums", "limit": 50, "app_id": qobuz.APP_ID},
                timeout=20,
            )
            resp.raise_for_status()
            artist = resp.json()
            albums = artist.get("albums", {})
            rows = albums.get("items", []) if isinstance(albums, dict) else albums or []
            items = []
            for al in rows:
                items.append({
                    "id": al.get("id"),
                    "title": al.get("title", ""),
                    "artist": al.get("artist", {}).get("name", "") or artist.get("name", ""),
                    "cover_url": al.get("image", {}).get("large", ""),
                    "tracks_count": al.get("tracks_count", 0),
                    "release_date": al.get("release_date_original", ""),
                    "source": "qobuz",
                })
            return jsonify({"kind": kind, "source": source, "items": items})

        # iTunes source
        if kind == "album":
            resp = http_requests.get(
                "https://itunes.apple.com/lookup",
                params={"id": item_id, "entity": "song"},
                timeout=20,
            )
            resp.raise_for_status()
            rows = [r for r in resp.json().get("results", []) if r.get("wrapperType") == "track"]
            album_name = rows[0].get("collectionName", "") if rows else ""
            album_year = (rows[0].get("releaseDate", "")[:4]) if rows else ""
            total_tracks = int(rows[0].get("trackCount", 0) or len(rows)) if rows else 0
            album_cover = ""
            if rows:
                album_cover = rows[0].get("artworkUrl100", "")
                if album_cover:
                    album_cover = album_cover.replace("100x100bb", "600x600bb")
            items = []
            for t in rows:
                items.append({
                    "id": t.get("trackId"),
                    "title": t.get("trackName", ""),
                    "artist": t.get("artistName", ""),
                    "duration_ms": int(t.get("trackTimeMillis", 0) or 0),
                    "track_number": int(t.get("trackNumber", 0) or 0),
                    "disc_number": int(t.get("discNumber", 0) or 1),
                    "total_tracks": total_tracks,
                    "album": album_name,
                    "cover_url": album_cover,
                    "year": album_year,
                    "isrc": "",
                    "url": t.get("trackViewUrl", ""),
                    "source": "itunes",
                })
            return jsonify({"kind": kind, "source": source, "items": items})

        # iTunes artist albums
        resp = http_requests.get(
            "https://itunes.apple.com/lookup",
            params={"id": item_id, "entity": "album", "limit": 200},
            timeout=20,
        )
        resp.raise_for_status()
        rows = [
            r for r in resp.json().get("results", [])
            if r.get("wrapperType") == "collection" and r.get("collectionType") == "Album"
        ]
        seen = set()
        items = []
        for al in rows:
            if al.get("collectionId") in seen:
                continue
            seen.add(al.get("collectionId"))
            art = al.get("artworkUrl100", "")
            if art:
                art = art.replace("100x100bb", "600x600bb")
            items.append({
                "id": al.get("collectionId"),
                "title": al.get("collectionName", ""),
                "artist": al.get("artistName", ""),
                "cover_url": art,
                "tracks_count": al.get("trackCount", 0),
                "release_date": al.get("releaseDate", ""),
                "source": "itunes",
            })
        return jsonify({"kind": kind, "source": source, "items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/availability", methods=["GET"])
def availability():
    """Check platform availability for a URL."""
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL required"}), 400
    try:
        data = songlink_client.check_availability(url)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lyrics", methods=["GET"])
def lyrics():
    track = request.args.get("track", "")
    artist = request.args.get("artist", "")
    album = request.args.get("album", "")
    if not track or not artist:
        return jsonify({"error": "track and artist required"}), 400
    try:
        data = lyrics_client.fetch(track, artist, album)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/musicbrainz", methods=["GET"])
def musicbrainz_lookup():
    """Look up genre/label by ISRC."""
    isrc = request.args.get("isrc", "").strip()
    if not isrc:
        return jsonify({"error": "ISRC required"}), 400
    try:
        data = musicbrainz_client.fetch_metadata(isrc)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Download routes ──────────────────────────────────────────

@app.route("/api/download", methods=["POST"])
def download():
    body = request.get_json(silent=True) or {}
    url = body.get("url", "").strip()
    isrc = body.get("isrc", "").strip()
    title = body.get("title", "").strip()
    artist = body.get("artist", "").strip()
    if not url and not isrc and not (title and artist):
        return jsonify({"error": "url, isrc, or title+artist required"}), 400
    task_id = download_manager.add_track(
        url=url, isrc=isrc,
        title=title,
        artist=artist,
        album=body.get("album", ""),
        cover_url=body.get("cover_url", ""),
        duration_ms=int(body.get("duration_ms", 0)),
        track_number=int(body.get("track_number", 0)),
        total_tracks=int(body.get("total_tracks", 0)),
        disc_number=int(body.get("disc_number", 0)),
        total_discs=int(body.get("total_discs", 0)),
        year=str(body.get("year", ""))[:4],
    )
    return jsonify({"task_id": task_id})


@app.route("/api/download/batch", methods=["POST"])
def download_batch():
    body = request.get_json(silent=True) or {}
    tracks = body.get("tracks", [])
    if not tracks:
        return jsonify({"error": "tracks list required"}), 400
    ids = []
    for t in tracks:
        task_id = download_manager.add_track(
            url=t.get("url", ""),
            isrc=t.get("isrc", ""),
            title=t.get("title", ""),
            artist=t.get("artist", ""),
            album=t.get("album", ""),
            cover_url=t.get("cover_url", ""),
            duration_ms=int(t.get("duration_ms", 0)),
            track_number=int(t.get("track_number", 0)),
            total_tracks=int(t.get("total_tracks", 0)),
            disc_number=int(t.get("disc_number", 0)),
            total_discs=int(t.get("total_discs", 0)),
            year=str(t.get("year", ""))[:4],
        )
        ids.append(task_id)
    return jsonify({"task_ids": ids})


@app.route("/api/download/album", methods=["POST"])
def download_album():
    body = request.get_json(silent=True) or {}
    album_id = str(body.get("album_id", "")).strip()
    source = str(body.get("source", "qobuz")).strip().lower()
    if not album_id:
        return jsonify({"error": "album_id required"}), 400

    task_ids = []

    if source == "qobuz":
        try:
            from backend.qobuz import QobuzDownloader

            qobuz = QobuzDownloader()
            resp = qobuz.session.get(
                "https://www.qobuz.com/api.json/0.2/album/get",
                params={"album_id": album_id, "app_id": qobuz.APP_ID},
                timeout=20,
            )
            resp.raise_for_status()
            album = resp.json()
            title = album.get("title", body.get("album", ""))
            artist = album.get("artist", {}).get("name", body.get("artist", ""))
            cover_url = album.get("image", {}).get("large", body.get("cover_url", ""))
            total_tracks = int(album.get("tracks_count", 0) or 0)
            total_discs = int(album.get("media_count", 0) or 0)
            album_year = (album.get("release_date_original") or "")[:4]

            tracks = album.get("tracks", {}).get("items", [])
            if not tracks:
                return jsonify({"error": "No tracks found for this album"}), 404

            for t in tracks:
                task_id = download_manager.add_track(
                    isrc=t.get("isrc", ""),
                    title=t.get("title", ""),
                    artist=t.get("performer", {}).get("name", "") or artist,
                    album=title,
                    cover_url=cover_url,
                    duration_ms=int((t.get("duration", 0) or 0) * 1000),
                    track_number=int(t.get("track_number", 0) or 0),
                    total_tracks=total_tracks,
                    disc_number=int(t.get("media_number", 0) or 1),
                    total_discs=total_discs,
                    year=album_year,
                )
                task_ids.append(task_id)
            return jsonify({"task_ids": task_ids, "count": len(task_ids)})
        except Exception as e:
            return jsonify({"error": f"Qobuz album fetch failed: {str(e)}"}), 500

    if source in ("itunes", "apple", "apple_music"):
        try:
            lookup = http_requests.get(
                "https://itunes.apple.com/lookup",
                params={"id": album_id, "entity": "song"},
                timeout=20,
            )
            lookup.raise_for_status()
            rows = lookup.json().get("results", [])
            tracks = [r for r in rows if r.get("wrapperType") == "track"]
            if not tracks:
                return jsonify({"error": "No tracks found for this album"}), 404

            album_name = tracks[0].get("collectionName", body.get("album", ""))
            cover_url = body.get("cover_url", "")
            if not cover_url:
                cover_url = tracks[0].get("artworkUrl100", "").replace("100x100bb", "600x600bb")
            total_tracks = int(tracks[0].get("trackCount", 0) or len(tracks))
            album_year = (tracks[0].get("releaseDate") or "")[:4]

            for t in tracks:
                task_id = download_manager.add_track(
                    url=t.get("trackViewUrl", ""),
                    title=t.get("trackName", ""),
                    artist=t.get("artistName", ""),
                    album=album_name,
                    cover_url=cover_url,
                    duration_ms=int(t.get("trackTimeMillis", 0) or 0),
                    track_number=int(t.get("trackNumber", 0) or 0),
                    total_tracks=total_tracks,
                    disc_number=int(t.get("discNumber", 0) or 1),
                    total_discs=int(t.get("discCount", 0) or 1),
                    year=album_year,
                )
                task_ids.append(task_id)
            return jsonify({"task_ids": task_ids, "count": len(task_ids)})
        except Exception as e:
            return jsonify({"error": f"Apple Music album fetch failed: {str(e)}"}), 500

    return jsonify({"error": f"Album download not supported for source: {source}"}), 400


@app.route("/api/download/playlist", methods=["POST"])
def download_playlist():
    """Download all tracks from a playlist URL."""
    body = request.get_json(silent=True) or {}
    playlist_url = str(body.get("url", "")).strip()
    source = str(body.get("source", "apple_music")).strip().lower()

    if not playlist_url:
        return jsonify({"error": "url required"}), 400

    task_ids = []

    if source in ("apple_music", "apple"):
        try:
            from backend.applemusic import AppleMusicDownloader
            apple = AppleMusicDownloader()
            tracks = apple.expand_playlist(playlist_url)
            if not tracks:
                return jsonify({"error": "Could not retrieve playlist tracks. Note: Apple Music playlists require authentication."}), 404

            for t in tracks:
                task_id = download_manager.add_track(
                    url=t.get("url", ""),
                    isrc=t.get("isrc", ""),
                    title=t.get("title", ""),
                    artist=t.get("artist", ""),
                    album=t.get("album", ""),
                    cover_url=t.get("cover_url", ""),
                    duration_ms=int(t.get("duration_ms", 0)),
                    track_number=int(t.get("track_number", 0) or 0),
                    total_tracks=int(t.get("total_tracks", 0) or 0),
                    disc_number=int(t.get("disc_number", 0) or 1),
                    total_discs=1,
                    year=str(t.get("year", ""))[:4],
                )
                task_ids.append(task_id)
            return jsonify({"task_ids": task_ids, "count": len(task_ids)})
        except Exception as e:
            return jsonify({"error": f"Apple Music playlist fetch failed: {str(e)}"}), 500

    if source == "spotify":
        try:
            from backend.spotify import SpotifyDownloader
            spotify = SpotifyDownloader()
            tracks = spotify.expand_playlist(playlist_url)
            if not tracks:
                return jsonify({"error": "Could not retrieve playlist tracks. Spotify playlists may require authentication."}), 404

            for t in tracks:
                task_id = download_manager.add_track(
                    url=t.get("url", ""),
                    isrc=t.get("isrc", ""),
                    title=t.get("title", ""),
                    artist=t.get("artist", ""),
                    album=t.get("album", ""),
                    cover_url=t.get("cover_url", ""),
                    duration_ms=int(t.get("duration_ms", 0)),
                    track_number=int(t.get("track_number", 0) or 0),
                    total_tracks=int(t.get("total_tracks", 0) or 0),
                    disc_number=1,
                    total_discs=1,
                    year=str(t.get("year", ""))[:4],
                )
                task_ids.append(task_id)
            return jsonify({"task_ids": task_ids, "count": len(task_ids)})
        except Exception as e:
            return jsonify({"error": f"Spotify playlist fetch failed: {str(e)}"}), 500

    return jsonify({"error": f"Playlist download not supported for source: {source}"}), 400


@app.route("/api/queue", methods=["GET"])
def queue():
    return jsonify(download_manager.get_queue())


@app.route("/api/queue/clear", methods=["POST"])
def clear_queue():
    download_manager.clear_completed()
    return jsonify({"ok": True})


# ── Analysis routes ──────────────────────────────────────────

@app.route("/api/analysis", methods=["POST"])
def analyze_file():
    """Analyze an audio file's technical metadata."""
    body = request.get_json(silent=True) or {}
    filepath = body.get("path", "").strip()
    if not filepath:
        return jsonify({"error": "path required"}), 400
    if not os.path.isfile(filepath):
        return jsonify({"error": "File not found"}), 404
    try:
        data = analysis.get_track_metadata(filepath)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analysis/batch", methods=["POST"])
def analyze_batch():
    """Analyze multiple audio files."""
    body = request.get_json(silent=True) or {}
    paths = body.get("paths", [])
    if not paths:
        return jsonify({"error": "paths required"}), 400
    results = []
    for p in paths:
        try:
            results.append(analysis.get_track_metadata(p))
        except Exception as e:
            results.append({"file_path": p, "error": str(e)})
    # Track in history
    history.add_operation("analyze", paths, f"Analyzed {len(results)} file(s)")
    return jsonify(results)


# ── Resample routes ──────────────────────────────────────────

@app.route("/api/resample", methods=["POST"])
def resample_files():
    """Resample audio files to new sample rate / bit depth."""
    body = request.get_json(silent=True) or {}
    files = body.get("files", [])
    sample_rate = body.get("sample_rate", "")
    bit_depth = body.get("bit_depth", "")
    delete_original = body.get("delete_original", False)
    if not files:
        return jsonify({"error": "files required"}), 400
    if not sample_rate and not bit_depth:
        return jsonify({"error": "sample_rate or bit_depth required"}), 400
    try:
        results = resample.resample_audio(files, sample_rate, bit_depth, delete_original)
        # Track in history
        details = []
        if sample_rate:
            details.append(f"Sample rate: {sample_rate} Hz")
        if bit_depth:
            details.append(f"Bit depth: {bit_depth}-bit")
        if delete_original:
            details.append("Delete originals: Yes")
        history.add_operation("resample", files, ", ".join(details))
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/resample/info", methods=["POST"])
def resample_info():
    """Get sample rate/bit depth info for audio files."""
    body = request.get_json(silent=True) or {}
    paths = body.get("paths", [])
    if not paths:
        return jsonify({"error": "paths required"}), 400
    try:
        results = resample.get_flac_info_batch(paths)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/resample/schedule", methods=["GET"])
def resample_schedule_list():
    with scheduler_lock:
        jobs = list(scheduler_jobs.values())
    jobs.sort(key=lambda j: j.get("run_at", 0))
    return jsonify(jobs)


@app.route("/api/resample/schedule", methods=["POST"])
def resample_schedule_create():
    body = request.get_json(silent=True) or {}
    files = body.get("files", [])
    sample_rate = str(body.get("sample_rate", "")).strip()
    bit_depth = str(body.get("bit_depth", "")).strip()
    run_at_raw = str(body.get("run_at", "")).strip()
    name = str(body.get("name", "Scheduled Remux")).strip() or "Scheduled Remux"

    if not files:
        return jsonify({"error": "files required"}), 400
    if not sample_rate and not bit_depth:
        return jsonify({"error": "sample_rate or bit_depth required"}), 400
    if not run_at_raw:
        return jsonify({"error": "run_at required"}), 400

    try:
        run_at_dt = _parse_schedule_time(run_at_raw)
    except Exception:
        return jsonify({"error": "run_at must be ISO datetime"}), 400

    if run_at_dt <= datetime.now():
        return jsonify({"error": "run_at must be in the future"}), 400

    job_id = str(uuid.uuid4())[:8]
    job = {
        "id": job_id,
        "name": name,
        "files": files,
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
        "run_at": int(run_at_dt.timestamp()),
        "status": "scheduled",
        "error": "",
        "created_at": int(time.time()),
        "last_run": 0,
    }
    with scheduler_lock:
        scheduler_jobs[job_id] = job
    return jsonify(job)


@app.route("/api/resample/schedule/<job_id>", methods=["DELETE"])
def resample_schedule_delete(job_id: str):
    with scheduler_lock:
        job = scheduler_jobs.get(job_id)
        if not job:
            return jsonify({"error": "schedule not found"}), 404
        if job.get("status") == "running":
            return jsonify({"error": "cannot delete a running schedule"}), 409
        del scheduler_jobs[job_id]
    return jsonify({"ok": True})


# ── File Manager routes ──────────────────────────────────────

@app.route("/api/files/list", methods=["GET"])
def files_list():
    """List directory contents."""
    path = request.args.get("path", settings["output_dir"])
    try:
        data = filemanager.list_directory(path)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/files/audio", methods=["GET"])
def files_audio():
    """List audio files in a directory."""
    path = request.args.get("path", settings["output_dir"])
    try:
        data = filemanager.list_audio_files(path)
        total = len(data)
        offset = request.args.get("offset", 0, type=int)
        limit = request.args.get("limit", 50, type=int)
        page = data[offset: offset + limit]
        return jsonify({
            "files": page,
            "total": total,
            "has_more": (offset + limit) < total,
            "offset": offset,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/files/metadata", methods=["POST"])
def files_metadata():
    """Read audio metadata for a file."""
    body = request.get_json(silent=True) or {}
    filepath = body.get("path", "").strip()
    if not filepath:
        return jsonify({"error": "path required"}), 400
    try:
        data = filemanager.read_audio_metadata(filepath)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/files/rename/preview", methods=["POST"])
def files_rename_preview():
    """Preview batch rename."""
    body = request.get_json(silent=True) or {}
    files = body.get("files", [])
    fmt = body.get("format", "{track} {title} - {artist}")
    if not files:
        return jsonify({"error": "files required"}), 400
    data = filemanager.preview_rename(files, fmt)
    return jsonify(data)


@app.route("/api/files/rename", methods=["POST"])
def files_rename():
    """Execute batch rename."""
    body = request.get_json(silent=True) or {}
    files = body.get("files", [])
    fmt = body.get("format", "{track} {title} - {artist}")
    if not files:
        return jsonify({"error": "files required"}), 400
    data = filemanager.rename_files(files, fmt)
    history.add_operation("rename", files, f"Format: {fmt}")
    return jsonify(data)


@app.route("/api/files/delete", methods=["POST"])
def files_delete():
    """Delete audio files from disk."""
    body = request.get_json(silent=True) or {}
    files = body.get("files", [])
    if not files:
        return jsonify({"error": "files required"}), 400
    try:
        results = filemanager.delete_files(files)
        success_count = sum(1 for r in results if r.get("success"))
        history.add_operation("delete", files, f"Deleted: {success_count}/{len(files)}")
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── History routes ───────────────────────────────────────────

@app.route("/api/history/downloads", methods=["GET"])
def history_downloads():
    return jsonify(history.get_downloads())


@app.route("/api/history/downloads/clear", methods=["POST"])
def history_downloads_clear():
    history.clear_downloads()
    return jsonify({"ok": True})


@app.route("/api/history/fetches", methods=["GET"])
def history_fetches():
    return jsonify(history.get_fetches())


@app.route("/api/history/fetches/clear", methods=["POST"])
def history_fetches_clear():
    history.clear_fetches()
    return jsonify({"ok": True})


@app.route("/api/history/operations", methods=["GET"])
def history_operations():
    return jsonify(history.get_operations())


@app.route("/api/history/operations/clear", methods=["POST"])
def history_operations_clear():
    history.clear_operations()
    return jsonify({"ok": True})


# ── Settings ─────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(settings)


@app.route("/api/settings", methods=["POST"])
def update_settings():
    body = request.get_json(silent=True) or {}
    if "output_dir" in body:
        d = body["output_dir"].strip()
        if d:
            settings["output_dir"] = d
            download_manager.output_dir = d
            os.makedirs(d, exist_ok=True)
    if "preferred_source" in body:
        settings["preferred_source"] = body["preferred_source"]
        download_manager.preferred_source = body["preferred_source"]
    if "quality" in body:
        settings["quality"] = body["quality"]
        download_manager.quality = body["quality"]
    if "embed_lyrics" in body:
        settings["embed_lyrics"] = bool(body["embed_lyrics"])
        download_manager.embed_lyrics_flag = bool(body["embed_lyrics"])
    return jsonify(settings)


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": "1.0.0"})


# ── WebSocket ────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    emit("connected", {"status": "ok"})


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=4000, debug=True)
