"""Metadata embedding for audio files (FLAC, MP3, M4A)."""

import os
from dataclasses import dataclass, field

import requests
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (ID3, APIC, TIT2, TPE1, TALB, TPE2, TDRC,
                          TRCK, TPOS, TCOP, TPUB, TSRC, TCON, USLT)
from mutagen.mp4 import MP4, MP4Cover


@dataclass
class Metadata:
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    date: str = ""
    track_number: int = 0
    total_tracks: int = 0
    disc_number: int = 0
    total_discs: int = 0
    copyright: str = ""
    publisher: str = ""
    isrc: str = ""
    genre: str = ""
    lyrics: str = ""
    url: str = ""


def embed_metadata(filepath: str, meta: Metadata, cover_path: str = "") -> None:
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".flac":
        _embed_flac(filepath, meta, cover_path)
    elif ext == ".mp3":
        _embed_mp3(filepath, meta, cover_path)
    elif ext in (".m4a", ".mp4", ".aac"):
        _embed_m4a(filepath, meta, cover_path)
    else:
        raise ValueError(f"Unsupported format: {ext}")


def _embed_flac(fp: str, m: Metadata, cover: str) -> None:
    a = FLAC(fp)
    _set = lambda k, v: a.__setitem__(k, v) if v else None
    _set("TITLE", m.title)
    _set("ARTIST", m.artist)
    _set("ALBUM", m.album)
    _set("ALBUMARTIST", m.album_artist)
    _set("DATE", m.date)
    if m.track_number:
        a["TRACKNUMBER"] = str(m.track_number)
    if m.total_tracks:
        a["TOTALTRACKS"] = str(m.total_tracks)
    if m.disc_number:
        a["DISCNUMBER"] = str(m.disc_number)
    if m.total_discs:
        a["TOTALDISCS"] = str(m.total_discs)
    _set("COPYRIGHT", m.copyright)
    _set("PUBLISHER", m.publisher)
    _set("ISRC", m.isrc)
    _set("GENRE", m.genre)
    _set("LYRICS", m.lyrics)

    if cover and os.path.exists(cover):
        pic = Picture()
        pic.type = 3
        pic.mime = "image/jpeg"
        pic.desc = "Cover"
        with open(cover, "rb") as f:
            pic.data = f.read()
        a.clear_pictures()
        a.add_picture(pic)
    a.save()


def _embed_mp3(fp: str, m: Metadata, cover: str) -> None:
    try:
        a = ID3(fp)
    except Exception:
        a = ID3()
    if m.title:
        a.add(TIT2(encoding=3, text=m.title))
    if m.artist:
        a.add(TPE1(encoding=3, text=m.artist))
    if m.album:
        a.add(TALB(encoding=3, text=m.album))
    if m.album_artist:
        a.add(TPE2(encoding=3, text=m.album_artist))
    if m.date:
        a.add(TDRC(encoding=3, text=m.date))
    if m.track_number:
        t = str(m.track_number)
        if m.total_tracks:
            t += f"/{m.total_tracks}"
        a.add(TRCK(encoding=3, text=t))
    if m.disc_number:
        d = str(m.disc_number)
        if m.total_discs:
            d += f"/{m.total_discs}"
        a.add(TPOS(encoding=3, text=d))
    if m.copyright:
        a.add(TCOP(encoding=3, text=m.copyright))
    if m.publisher:
        a.add(TPUB(encoding=3, text=m.publisher))
    if m.isrc:
        a.add(TSRC(encoding=3, text=m.isrc))
    if m.genre:
        a.add(TCON(encoding=3, text=m.genre))
    if m.lyrics:
        a.add(USLT(encoding=3, lang="eng", desc="", text=m.lyrics))
    if cover and os.path.exists(cover):
        with open(cover, "rb") as f:
            a.add(APIC(encoding=3, mime="image/jpeg", type=3,
                        desc="Cover", data=f.read()))
    a.save(fp)


def _embed_m4a(fp: str, m: Metadata, cover: str) -> None:
    a = MP4(fp)
    t = a.tags if a.tags else {}
    if m.title:
        t["\xa9nam"] = [m.title]
    if m.artist:
        t["\xa9ART"] = [m.artist]
    if m.album:
        t["\xa9alb"] = [m.album]
    if m.album_artist:
        t["aART"] = [m.album_artist]
    if m.date:
        t["\xa9day"] = [m.date]
    if m.track_number:
        t["trkn"] = [(m.track_number, m.total_tracks or 0)]
    if m.disc_number:
        t["disk"] = [(m.disc_number, m.total_discs or 0)]
    if m.genre:
        t["\xa9gen"] = [m.genre]
    if m.copyright:
        t["cprt"] = [m.copyright]
    if m.lyrics:
        t["\xa9lyr"] = [m.lyrics]
    if cover and os.path.exists(cover):
        with open(cover, "rb") as f:
            t["covr"] = [MP4Cover(f.read(), imageformat=MP4Cover.FORMAT_JPEG)]
    a.tags = t
    a.save()


def download_cover(cover_url: str, output_path: str) -> str:
    if not cover_url:
        return ""
    cover_url = _upgrade_cover(cover_url)
    r = requests.get(cover_url, timeout=30)
    r.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(r.content)
    return output_path


def _upgrade_cover(url: str) -> str:
    return url.replace("ab67616d00001e02", "ab67616d0000b273")
