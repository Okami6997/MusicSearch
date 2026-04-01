"""SongsFetch - Flask web application for music search and download."""

import os

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

DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Music", "SongsFetch")
settings = {
    "output_dir": DEFAULT_DIR,
    "preferred_source": "tidal",
    "quality": "LOSSLESS",
    "embed_lyrics": True,
}


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


_init_download_manager()
history.init_db()
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
                "source": "youtube",
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
    """Search for tracks by query or ISRC."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Query required"}), 400

    # If it looks like an ISRC, search by ISRC via Qobuz
    if len(q) == 12 and q[:2].isalpha() and q[2:].isalnum():
        try:
            from backend.qobuz import QobuzDownloader
            qobuz = QobuzDownloader()
            track = qobuz.search_by_isrc(q)
            # Normalize to frontend-expected format
            track.setdefault("cover_url", "")
            track["duration_ms"] = (track.pop("duration", 0) or 0) * 1000
            return jsonify({"tracks": [track], "source": "qobuz"})
        except Exception:
            pass

    # Try Qobuz search first
    try:
        from backend.qobuz import QobuzDownloader
        qobuz = QobuzDownloader()

        # Search tracks
        resp = qobuz.session.get(
            "https://www.qobuz.com/api.json/0.2/track/search",
            params={"query": q, "limit": 20, "app_id": qobuz.APP_ID},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("tracks", {}).get("items", [])
        tracks = []
        for t in items:
            album_data = t.get("album", {})
            tracks.append({
                "id": t.get("id"),
                "title": t.get("title", ""),
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
            })

        # Search artists
        artists = []
        try:
            aresp = qobuz.session.get(
                "https://www.qobuz.com/api.json/0.2/artist/search",
                params={"query": q, "limit": 5, "app_id": qobuz.APP_ID},
                timeout=15,
            )
            aresp.raise_for_status()
            for a in aresp.json().get("artists", {}).get("items", []):
                img = a.get("image", {})
                artists.append({
                    "id": a.get("id"),
                    "name": a.get("name", ""),
                    "image_url": img.get("large", "") or img.get("medium", "") or img.get("small", ""),
                    "albums_count": a.get("albums_count", 0),
                })
        except Exception:
            pass

        # Search albums
        albums = []
        try:
            alresp = qobuz.session.get(
                "https://www.qobuz.com/api.json/0.2/album/search",
                params={"query": q, "limit": 5, "app_id": qobuz.APP_ID},
                timeout=15,
            )
            alresp.raise_for_status()
            for al in alresp.json().get("albums", {}).get("items", []):
                albums.append({
                    "id": al.get("id"),
                    "title": al.get("title", ""),
                    "artist": al.get("artist", {}).get("name", ""),
                    "cover_url": al.get("image", {}).get("large", ""),
                    "tracks_count": al.get("tracks_count", 0),
                    "release_date": al.get("release_date_original", ""),
                    "hires": al.get("hires_streamable", False),
                })
        except Exception:
            pass

        if tracks or artists or albums:
            # Also fetch YouTube Music results
            yt_tracks = _youtube_search(q, limit=10)
            return jsonify({
                "tracks": tracks, "artists": artists, "albums": albums,
                "youtube_tracks": yt_tracks,
                "source": "qobuz",
            })
    except Exception:
        pass

    # Fallback: iTunes Search API (free, no auth required)
    try:
        # Search tracks
        itunes_resp = http_requests.get(
            "https://itunes.apple.com/search",
            params={"term": q, "media": "music", "entity": "song", "limit": 20},
            timeout=15,
        )
        itunes_resp.raise_for_status()
        results = itunes_resp.json().get("results", [])
        tracks = []
        for t in results:
            artwork = t.get("artworkUrl100", "")
            if artwork:
                artwork = artwork.replace("100x100bb", "600x600bb")
            tracks.append({
                "id": t.get("trackId"),
                "title": t.get("trackName", ""),
                "artist": t.get("artistName", ""),
                "album": t.get("collectionName", ""),
                "cover_url": artwork,
                "duration_ms": t.get("trackTimeMillis", 0) or 0,
                "isrc": "",
                "hires": False,
                "bit_depth": 0,
                "sample_rate": 0,
            })

        # Search artists
        artists = []
        try:
            ar = http_requests.get(
                "https://itunes.apple.com/search",
                params={"term": q, "media": "music", "entity": "musicArtist", "limit": 5},
                timeout=15,
            )
            ar.raise_for_status()
            for a in ar.json().get("results", []):
                artists.append({
                    "id": a.get("artistId"),
                    "name": a.get("artistName", ""),
                    "image_url": "",
                    "albums_count": 0,
                })
        except Exception:
            pass

        # Search albums
        albums = []
        try:
            alr = http_requests.get(
                "https://itunes.apple.com/search",
                params={"term": q, "media": "music", "entity": "album", "limit": 5},
                timeout=15,
            )
            alr.raise_for_status()
            for al in alr.json().get("results", []):
                art = al.get("artworkUrl100", "")
                if art:
                    art = art.replace("100x100bb", "600x600bb")
                albums.append({
                    "id": al.get("collectionId"),
                    "title": al.get("collectionName", ""),
                    "artist": al.get("artistName", ""),
                    "cover_url": art,
                    "tracks_count": al.get("trackCount", 0),
                    "release_date": al.get("releaseDate", ""),
                    "hires": False,
                })
        except Exception:
            pass

        # Also fetch YouTube Music results
        yt_tracks = _youtube_search(q, limit=10)
        return jsonify({
            "tracks": tracks, "artists": artists, "albums": albums,
            "youtube_tracks": yt_tracks,
            "source": "itunes",
        })
    except Exception as e:
        return jsonify({"error": f"Search failed: {str(e)}"}), 500


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
        )
        ids.append(task_id)
    return jsonify({"task_ids": ids})


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
    if not files:
        return jsonify({"error": "files required"}), 400
    if not sample_rate and not bit_depth:
        return jsonify({"error": "sample_rate or bit_depth required"}), 400
    try:
        results = resample.resample_audio(files, sample_rate, bit_depth)
        # Track in history
        details = []
        if sample_rate:
            details.append(f"Sample rate: {sample_rate} Hz")
        if bit_depth:
            details.append(f"Bit depth: {bit_depth}-bit")
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
        return jsonify(data)
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
    socketio.run(app, host="0.0.0.0", port=3000, debug=True)
