# MusicSearch

A web-based music search and download application. Search or paste any music platform URL, then download audio from Tidal, Spotify, Deezer, Qobuz, Amazon Music, YouTube Music, or SoundCloud with embedded metadata, cover art, lyrics, and genre info — optimized for Navidrome and Lidarr compatibility.

> **Note:** Apple Music is not available as a download source. See [Limitations](#limitations) below.

## Features

- **Music Search** — Search by title, artist, or ISRC across Qobuz, iTunes, Deezer, and SoundCloud with concurrent async service fan-out; results are appended progressively as each service returns, and infinite-scroll lazy loading keeps loading additional track pages as you scroll
- **Search Source Toggles** — Show/hide result sections (Tracks, Artists, Albums, YouTube, Deezer, SoundCloud) using inline filter pills in the Search UI
- **URL Resolve** — Paste any music URL (Tidal, Amazon, Deezer, Qobuz, Apple Music, YouTube Music, Soundcloud) to find cross-platform links via SongLink
- **Multi-Source Download** — Downloads from Tidal, Spotify, Deezer, Qobuz, Amazon Music, YouTube Music, and SoundCloud with automatic failover
- **Qobuz/Deezer Resolver Fallbacks** — Download resolvers use multiple provider endpoints with quality fallback for more resilient FLAC retrieval when a single upstream API is unavailable
- **Lossless Audio** — FLAC (16-bit and 24-bit Hi-Res) from Tidal/Qobuz/Amazon; MP3 320kbps via yt-dlp from YouTube
- **Auto-Resample to Hi-Res FLAC** — Every download is automatically resampled to 192kHz/24-bit FLAC using FFmpeg (toggle in Settings). Non-FLAC sources are converted; files already at target spec are skipped
- **Preview Playback** — 30-second DRM-free preview buttons appear on Deezer and iTunes search results; a shared audio player auto-stops when a new preview starts
- **Rich Metadata Embedding** — Title, artist, album, album artist, track number, disc number, total tracks, year, genre (MusicBrainz), label, ISRC, and cover art (FLAC, MP3, M4A)
- **Lyrics** — Synced and plain lyrics from LRCLIB, embedded directly into audio files (USLT frames in MP3, ©lyr atoms in M4A, LYRICS tags in FLAC) for full Subsonic API/Navidrome compatibility
- **Navidrome/Lidarr Compatible** — Full metadata and lyrics embedding ensures downloaded tracks are recognized by media servers without requiring companion files
- **Audio Analysis** — Analyze audio files for codec, sample rate, bit depth, duration, and bitrate (FFprobe)
- **Audio Resampling** — Change sample rate and bit depth of audio files (FFmpeg), with batch processing
- **File Manager** — Browse audio files, view metadata, and batch rename using format templates
- **Operation History** — SQLite-based history tracking downloads, resampling, renaming, and analysis operations
- **Download Validation** — Duration-based validation to detect preview/sample files
- **Real-time Progress** — WebSocket-based download queue with live progress updates
- **Dark UI** — Clean, responsive dark-themed web interface with tabbed navigation

## Requirements

- Python 3.12+
- FFmpeg (for audio conversion, decryption, analysis, and resampling)

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py
```

Open http://localhost:3000 in your browser.

## Docker

### Build and run locally

```bash
docker compose up --build
```

### Multi-architecture build (AMD64 + ARM64)

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t musicsearch:latest .
```

Downloaded music is saved to the `./music` directory (or `/music` inside the container).

## Configuration

Settings are available via the web UI (gear icon):

| Setting | Options | Default |
|---------|---------|---------|
| Download Directory | Any writable path | `~/Music/SongsFetch` |
| Preferred Source | Tidal, Spotify, Deezer, Qobuz, Amazon Music, YouTube, SoundCloud | Tidal |
| Quality | Lossless (16-bit), Hi-Res (24-bit) | Lossless |
| Embed Lyrics | On/Off | On |
| Auto-Resample | On/Off | On |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/search?q=...` | Search Qobuz catalog (title/artist/ISRC) |
| GET | `/api/resolve?url=...` | Resolve any music URL via SongLink |
| GET | `/api/availability?url=...` | Check platform availability |
| GET | `/api/lyrics?track=...&artist=...` | Fetch lyrics from LRCLIB |
| GET | `/api/musicbrainz?isrc=...` | Look up genre/label/year by ISRC |
| POST | `/api/download` | Download a track (url or isrc) |
| POST | `/api/download/batch` | Download multiple tracks |
| GET | `/api/queue` | Get download queue |
| POST | `/api/queue/clear` | Clear completed downloads |
| POST | `/api/analysis` | Analyze audio file metadata |
| POST | `/api/analysis/batch` | Analyze multiple files |
| POST | `/api/resample` | Resample audio files |
| POST | `/api/resample/info` | Get audio file info (batch) |
| GET | `/api/files/list?path=...` | List directory contents |
| GET | `/api/files/audio?path=...` | List audio files in directory |
| POST | `/api/files/metadata` | Read audio metadata |
| POST | `/api/files/rename/preview` | Preview batch rename |
| POST | `/api/files/rename` | Execute batch rename |
| GET | `/api/history/downloads` | Get download history |
| POST | `/api/history/downloads/clear` | Clear download history |
| GET | `/api/history/operations` | Get operation history (resample, analyze, rename) |
| POST | `/api/history/operations/clear` | Clear operation history |
| GET/POST | `/api/settings` | Get/update settings |

## Project Structure

```
MusicSearch/
├── app.py                  # Flask application
├── requirements.txt        # Python dependencies
├── Dockerfile              # Docker image (ARM + AMD64)
├── docker-compose.yml      # Docker Compose config
├── backend/
│   ├── songlink.py         # Cross-platform URL resolver (SongLink API)
│   ├── tidal.py            # Tidal downloader (7 proxy APIs)
│   ├── spotify.py          # Spotify downloader (FLAC via SpotiDownloader)
│   ├── qobuz.py            # Qobuz downloader (ISRC-based search)
│   ├── amazon.py           # Amazon Music downloader
│   ├── youtube.py          # YouTube Music downloader (MP3 via proxy APIs)
│   ├── metadata.py         # FLAC/MP3/M4A metadata embedding (mutagen)
│   ├── lyrics.py           # LRCLIB lyrics client
│   ├── musicbrainz.py      # MusicBrainz genre/label/year lookup
│   ├── analysis.py         # Audio analysis (FFprobe)
│   ├── resample.py         # Audio resampling (FFmpeg)
│   ├── filemanager.py      # File listing, metadata reading, batch rename
│   ├── history.py          # Download & operation history (SQLite)
│   └── downloader.py       # Download orchestrator & queue
├── templates/
│   └── index.html          # Web UI
└── static/
    ├── css/style.css       # Dark theme styles
    └── js/app.js           # Frontend JavaScript
```

## Limitations

### Apple Music Download

Apple Music is not supported as a download source. The public iTunes API only provides 30-second preview clips; full-length tracks are FairPlay DRM-encrypted and can only be decrypted inside Apple's own authenticated clients. There is no valid approach to downloading full tracks via any public API.

Apple Music search results and URL resolution (via SongLink) continue to work for metadata and cross-platform link lookup. When a track is found via iTunes search or an Apple Music URL is pasted, the download will automatically fall through to the next available source (Tidal → Spotify → Qobuz → Amazon Music → YouTube).

## Media Server Integration

MusicSearch is designed to work seamlessly with Navidrome and Lidarr:

- **Navidrome**: Downloaded tracks appear in your library after a scan. The rich metadata embedding ensures proper artist/album/track organization.
- **Lidarr**: Lidarr can monitor your download folder and automatically organize tracks. The embedded metadata (ISRC, genre, year) helps with identification.

Point your media server to the same download directory configured in MusicSearch settings.

## License

MIT
