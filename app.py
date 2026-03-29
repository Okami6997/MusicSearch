"""SongsFetch - Flask web application for music search and download."""

import os

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

from backend.songlink import SongLinkClient, is_music_url, parse_music_url
from backend.lyrics import LyricsClient
from backend.downloader import DownloadManager
from backend.musicbrainz import MusicBrainzClient
from backend import analysis, resample, filemanager, history

app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(32).hex()
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

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
    """Search Qobuz by query or ISRC."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Query required"}), 400
    try:
        from backend.qobuz import QobuzDownloader
        qobuz = QobuzDownloader()
        # If it looks like an ISRC, search by ISRC
        if len(q) == 12 and q[:2].isalpha() and q[2:].isalnum():
            track = qobuz.search_by_isrc(q)
            return jsonify({"tracks": [track], "source": "qobuz"})
        # Otherwise search Qobuz by query
        resp = qobuz.session.get(
            f"https://www.qobuz.com/api.json/0.2/track/search"
            f"?query={q}&limit=20&app_id={qobuz.APP_ID}",
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("tracks", {}).get("items", [])
        tracks = []
        for t in items:
            tracks.append({
                "id": t.get("id"),
                "title": t.get("title", ""),
                "artist": t.get("performer", {}).get("name", ""),
                "album": t.get("album", {}).get("title", ""),
                "cover_url": t.get("album", {}).get("image", {}).get("large", ""),
                "duration_ms": (t.get("duration", 0) or 0) * 1000,
                "isrc": t.get("isrc", ""),
                "hires": t.get("hires_streamable", False),
                "bit_depth": t.get("maximum_bit_depth", 0),
                "sample_rate": t.get("maximum_sampling_rate", 0),
            })
        return jsonify({"tracks": tracks, "source": "qobuz"})
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
    if not url and not isrc:
        return jsonify({"error": "url or isrc required"}), 400
    task_id = download_manager.add_track(
        url=url, isrc=isrc,
        title=body.get("title", ""),
        artist=body.get("artist", ""),
        album=body.get("album", ""),
        cover_url=body.get("cover_url", ""),
        duration_ms=int(body.get("duration_ms", 0)),
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
    socketio.run(app, host="0.0.0.0", port=8080, debug=False)
