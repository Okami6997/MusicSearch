"""File manager - directory listing, audio metadata reading, and batch rename."""

import os
import re
import subprocess

from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

AUDIO_EXTS = {".flac", ".mp3", ".m4a"}


def list_directory(dir_path: str) -> list[dict]:
    """List contents of a directory recursively."""
    if not os.path.isdir(dir_path):
        raise FileNotFoundError(f"Directory not found: {dir_path}")

    result = []
    for entry in sorted(os.scandir(dir_path), key=lambda e: e.name):
        info = {
            "name": entry.name,
            "path": entry.path,
            "is_dir": entry.is_dir(),
            "size": entry.stat().st_size if not entry.is_dir() else 0,
        }
        if entry.is_dir():
            try:
                info["children"] = list_directory(entry.path)
            except PermissionError:
                info["children"] = []
        result.append(info)
    return result


def list_audio_files(dir_path: str) -> list[dict]:
    """Walk a directory and return all audio files."""
    if not os.path.isdir(dir_path):
        raise FileNotFoundError(f"Directory not found: {dir_path}")

    result = []
    for root, _, files in os.walk(dir_path):
        for name in sorted(files):
            ext = os.path.splitext(name)[1].lower()
            if ext in AUDIO_EXTS:
                fp = os.path.join(root, name)
                result.append({
                    "name": name,
                    "path": fp,
                    "is_dir": False,
                    "size": os.path.getsize(fp),
                })
    return result


def read_audio_metadata(filepath: str) -> dict:
    """Read metadata from an audio file (FLAC, MP3, M4A)."""
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".flac":
        return _read_flac(filepath)
    elif ext == ".mp3":
        return _read_mp3(filepath)
    elif ext == ".m4a":
        return _read_m4a(filepath)
    else:
        raise ValueError(f"Unsupported format: {ext}")


def _read_flac(fp: str) -> dict:
    f = FLAC(fp)
    tags = f.tags or {}
    return {
        "title": _first(tags, "title"),
        "artist": _first(tags, "artist"),
        "album": _first(tags, "album"),
        "album_artist": _first(tags, "albumartist"),
        "track_number": _int_val(_first(tags, "tracknumber")),
        "disc_number": _int_val(_first(tags, "discnumber")),
        "year": _first(tags, "date") or _first(tags, "year"),
    }


def _read_mp3(fp: str) -> dict:
    try:
        tags = ID3(fp)
    except Exception:
        return _empty_meta()

    def _text(key):
        frames = tags.getall(key)
        return str(frames[0]) if frames else ""

    track_str = _text("TRCK")
    disc_str = _text("TPOS")
    return {
        "title": _text("TIT2"),
        "artist": _text("TPE1"),
        "album": _text("TALB"),
        "album_artist": _text("TPE2"),
        "track_number": _int_val(track_str.split("/")[0] if track_str else ""),
        "disc_number": _int_val(disc_str.split("/")[0] if disc_str else ""),
        "year": _text("TDRC"),
    }


def _read_m4a(fp: str) -> dict:
    try:
        f = MP4(fp)
        tags = f.tags or {}
    except Exception:
        return _empty_meta()

    def _str(key):
        v = tags.get(key)
        return str(v[0]) if v else ""

    def _trkn(key):
        v = tags.get(key)
        return v[0][0] if v and v[0] else 0

    return {
        "title": _str("\xa9nam"),
        "artist": _str("\xa9ART"),
        "album": _str("\xa9alb"),
        "album_artist": _str("aART"),
        "track_number": _trkn("trkn"),
        "disc_number": _trkn("disk"),
        "year": _str("\xa9day"),
    }


def generate_filename(metadata: dict, fmt: str, ext: str) -> str:
    """Build a filename from metadata and format template.
    Supported tokens: {title}, {artist}, {album}, {album_artist}, {year}, {date}, {track}, {disc}
    """
    result = fmt
    year = (metadata.get("year") or "")[:4]
    result = result.replace("{title}", _sanitize(metadata.get("title", "")))
    result = result.replace("{artist}", _sanitize(metadata.get("artist", "")))
    result = result.replace("{album}", _sanitize(metadata.get("album", "")))
    result = result.replace("{album_artist}", _sanitize(metadata.get("album_artist", "")))
    result = result.replace("{year}", _sanitize(year))
    result = result.replace("{date}", _sanitize(metadata.get("year", "")))

    tn = metadata.get("track_number", 0)
    result = result.replace("{track}", f"{tn:02d}" if tn else "")

    dn = metadata.get("disc_number", 0)
    result = result.replace("{disc}", str(dn) if dn else "")

    result = " ".join(result.split()).strip(" -._")
    if not result:
        return ""
    return result + ext


def preview_rename(files: list[str], fmt: str) -> list[dict]:
    """Preview what files would be renamed to."""
    previews = []
    for fp in files:
        preview = {"old_path": fp, "old_name": os.path.basename(fp),
                    "new_name": "", "new_path": "", "error": "", "metadata": {}}
        try:
            meta = read_audio_metadata(fp)
            preview["metadata"] = meta
            ext = os.path.splitext(fp)[1]
            new_name = generate_filename(meta, fmt, ext)
            if not new_name:
                preview["error"] = "Could not generate filename (missing metadata)"
            else:
                preview["new_name"] = new_name
                preview["new_path"] = os.path.join(os.path.dirname(fp), new_name)
        except Exception as e:
            preview["error"] = str(e)
        previews.append(preview)
    return previews


def rename_files(files: list[str], fmt: str) -> list[dict]:
    """Rename files according to a format template."""
    results = []
    for fp in files:
        result = {"old_path": fp, "new_path": "", "success": False, "error": ""}
        try:
            meta = read_audio_metadata(fp)
            ext = os.path.splitext(fp)[1]
            new_name = generate_filename(meta, fmt, ext)
            if not new_name:
                result["error"] = "Could not generate filename (missing metadata)"
                results.append(result)
                continue
            new_path = os.path.join(os.path.dirname(fp), new_name)
            result["new_path"] = new_path
            if new_path != fp and os.path.exists(new_path):
                result["error"] = "File already exists"
                results.append(result)
                continue
            if new_path != fp:
                os.rename(fp, new_path)
            result["success"] = True
        except Exception as e:
            result["error"] = str(e)
        results.append(result)
    return results


def get_file_sizes(files: list[str]) -> dict[str, int]:
    """Get sizes for a list of files."""
    return {fp: os.path.getsize(fp) for fp in files if os.path.isfile(fp)}


# ── Helpers ──────────────────────────────────────────────────

def _first(tags, key: str) -> str:
    v = tags.get(key) or tags.get(key.upper())
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v) if v else ""


def _int_val(s: str) -> int:
    try:
        return int(s.split("/")[0]) if s else 0
    except (ValueError, IndexError):
        return 0


def _sanitize(name: str) -> str:
    for c in '<>:"/\\|?*':
        name = name.replace(c, "")
    return name.strip()


def _empty_meta() -> dict:
    return {"title": "", "artist": "", "album": "", "album_artist": "",
            "track_number": 0, "disc_number": 0, "year": ""}
