"""Download history - SQLite-based storage for download and fetch records."""

import json
import os
import sqlite3
import time
import threading

MAX_HISTORY = 10000
_lock = threading.Lock()
_db: sqlite3.Connection | None = None


def init_db(db_path: str = "") -> None:
    """Initialize the history database."""
    global _db
    if _db is not None:
        return

    if not db_path:
        candidates = [
            os.environ.get("SONGSFETCH_DATA_DIR"),
            os.environ.get("SONGSFETCH_OUTPUT_DIR"),
            os.path.join(os.path.expanduser("~"), ".songsfetch"),
            "/tmp/songsfetch",
        ]
        data_dir = None
        for d in candidates:
            if not d:
                continue
            try:
                os.makedirs(d, exist_ok=True)
                # Verify we can actually write here
                test_path = os.path.join(d, ".write_test")
                with open(test_path, "w") as f:
                    f.write("ok")
                os.remove(test_path)
                data_dir = d
                break
            except OSError:
                continue
        if data_dir is None:
            raise RuntimeError("No writable directory found for history database")
        db_path = os.path.join(data_dir, "history.db")

    _db = sqlite3.connect(db_path, check_same_thread=False)
    _db.row_factory = sqlite3.Row
    _db.execute("""
        CREATE TABLE IF NOT EXISTS download_history (
            id TEXT PRIMARY KEY,
            url TEXT DEFAULT '',
            title TEXT DEFAULT '',
            artist TEXT DEFAULT '',
            album TEXT DEFAULT '',
            duration_str TEXT DEFAULT '',
            cover_url TEXT DEFAULT '',
            quality TEXT DEFAULT '',
            format TEXT DEFAULT '',
            path TEXT DEFAULT '',
            source TEXT DEFAULT '',
            timestamp INTEGER DEFAULT 0
        )
    """)
    _db.execute("""
        CREATE TABLE IF NOT EXISTS fetch_history (
            id TEXT PRIMARY KEY,
            url TEXT DEFAULT '',
            type TEXT DEFAULT '',
            name TEXT DEFAULT '',
            info TEXT DEFAULT '',
            image TEXT DEFAULT '',
            data TEXT DEFAULT '',
            timestamp INTEGER DEFAULT 0
        )
    """)
    _db.commit()


def _ensure_db():
    if _db is None:
        init_db()


def add_download(item: dict) -> str:
    """Add a download history item. Returns the item ID."""
    _ensure_db()
    item_id = f"{int(time.time() * 1e9)}-{os.getpid()}"
    with _lock:
        _db.execute(
            """INSERT INTO download_history
               (id, url, title, artist, album, duration_str, cover_url,
                quality, format, path, source, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, item.get("url", ""), item.get("title", ""),
             item.get("artist", ""), item.get("album", ""),
             item.get("duration_str", ""), item.get("cover_url", ""),
             item.get("quality", ""), item.get("format", ""),
             item.get("path", ""), item.get("source", ""),
             int(time.time())),
        )
        _trim_table("download_history")
        _db.commit()
    return item_id


def get_downloads() -> list[dict]:
    """Get all download history items, newest first."""
    _ensure_db()
    with _lock:
        rows = _db.execute(
            "SELECT * FROM download_history ORDER BY timestamp DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def clear_downloads() -> None:
    """Clear all download history."""
    _ensure_db()
    with _lock:
        _db.execute("DELETE FROM download_history")
        _db.commit()


def add_fetch(item: dict) -> str:
    """Add a fetch history item, replacing any with the same URL+type."""
    _ensure_db()
    item_id = f"{int(time.time() * 1e9)}-{os.getpid()}"
    url = item.get("url", "")
    item_type = item.get("type", "")
    with _lock:
        if url:
            _db.execute(
                "DELETE FROM fetch_history WHERE url = ? AND type = ?",
                (url, item_type),
            )
        _db.execute(
            """INSERT INTO fetch_history
               (id, url, type, name, info, image, data, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, url, item_type, item.get("name", ""),
             item.get("info", ""), item.get("image", ""),
             item.get("data", ""), int(time.time())),
        )
        _trim_table("fetch_history")
        _db.commit()
    return item_id


def get_fetches() -> list[dict]:
    """Get all fetch history items, newest first."""
    _ensure_db()
    with _lock:
        rows = _db.execute(
            "SELECT * FROM fetch_history ORDER BY timestamp DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def clear_fetches() -> None:
    """Clear all fetch history."""
    _ensure_db()
    with _lock:
        _db.execute("DELETE FROM fetch_history")
        _db.commit()


def clear_fetches_by_type(item_type: str) -> None:
    """Clear fetch history for a specific type."""
    _ensure_db()
    with _lock:
        _db.execute("DELETE FROM fetch_history WHERE type = ?", (item_type,))
        _db.commit()


def close() -> None:
    """Close the database."""
    global _db
    if _db is not None:
        _db.close()
        _db = None


def _trim_table(table: str) -> None:
    """Keep at most MAX_HISTORY rows, deleting oldest."""
    count = _db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if count > MAX_HISTORY:
        to_delete = count - MAX_HISTORY + MAX_HISTORY // 20
        _db.execute(
            f"DELETE FROM {table} WHERE id IN "
            f"(SELECT id FROM {table} ORDER BY timestamp ASC LIMIT ?)",
            (to_delete,),
        )
