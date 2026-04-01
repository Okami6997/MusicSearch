# Changelog

## v1.1.0 (Unreleased)

### Bug Fixes

- **Download: Double JSON parse on Deezer response** — The ISRC→Deezer lookup was calling `dr.json()` twice (once for the `id` check, once for the `link`), which could fail on some response types. Now parses JSON once into `deezer_data` and reuses it.
- **Download: Dead Spotify ISRC search removed** — Removed a stub that called Spotify's `/v1/search` API without authentication (always 401), did nothing with the response, and wasted up to 10 seconds on timeout.
- **Download: SongLink failure crashes Deezer resolution** — If the SongLink call failed after a successful Deezer ISRC lookup, the entire Deezer block would fail and no cross-platform URLs would be set. SongLink is now wrapped in its own inner try/except so the Deezer URL is preserved even if SongLink is down.
- **Download: Inline `import requests` moved to module level** — `import requests as _req` was being called inside the `_process` method on every download. Moved to module-level import for correctness and clarity.
- **Config: `SONGSFETCH_OUTPUT_DIR` environment variable ignored** — The `DEFAULT_DIR` in `app.py` and the fallback in `DownloadManager.__init__` were hardcoded to `~/Music/SongsFetch`, ignoring the `SONGSFETCH_OUTPUT_DIR` env var. Downloads now honor the env var, fixing Docker deployments where output should go to `/downloads`.
- **Download: ISRC not passed from search results** — When downloading tracks from Qobuz search results (which only provide ISRC, no URL), the ISRC was not being used to resolve cross-platform URLs. The downloader now resolves ISRC via Deezer → SongLink to obtain Tidal, Spotify, Amazon, and YouTube URLs before attempting downloads.
- **Download: Missing metadata in download requests** — The frontend Download button was not passing `track_number`, `total_tracks`, or `disc_number` to the backend. These fields are now included in all download requests from search results.
- **Download: Fallback when ISRC-only resolution fails** — When only an ISRC is available and Deezer/SongLink resolution fails, the downloader now falls back to Qobuz ISRC search to populate title/artist, enabling YouTube text-search as a last-resort download source.
- **Download: Preferred source priority** — Simplified and fixed the `_ordered_sources` logic so the user's preferred download service is always tried first (when it has data), followed by other services with data, then remaining services. The preferred source is always included in the list even if it initially lacks data.

### New Features

- **Search: Artist and album results** — The search API now returns artists and albums alongside tracks. Qobuz search queries the `/artist/search` and `/album/search` endpoints (up to 5 results each). The iTunes fallback also searches for artists (`musicArtist` entity) and albums (`album` entity).
- **Search: YouTube Music results** — Search results now include a dedicated YouTube Music section. A new `search_tracks()` method on `YouTubeDownloader` scrapes YouTube Music's search page and parses `ytInitialData` JSON to extract video IDs, titles, artists, albums, and thumbnails. Results are labeled "YouTube Music · MP3 320kbps" and download directly via YouTube URL.
- **URL Resolve: YouTube Music and Spotify in platform availability** — The URL resolve view now displays 6 platforms (Tidal, Spotify, Amazon Music, Qobuz, Deezer, YouTube Music) instead of the previous 4. The download button also prefers URLs in order: Tidal → Spotify → Amazon → YouTube.

### UI Improvements

- **Search results sections** — Search results are now organized into labeled sections: Artists, Albums, Tracks, and YouTube Music, each with a styled heading and separator.
- **Artist display** — Artists are shown with a circular avatar image and album count.
- **Album display** — Albums show cover art, artist name, track count, release year, and a Hi-Res badge when applicable.

### Files Changed

- `app.py` — `DEFAULT_DIR` now reads `SONGSFETCH_OUTPUT_DIR` env var; added artist/album search for Qobuz and iTunes, YouTube Music search integration, imported `YouTubeDownloader`
- `backend/downloader.py` — Fixed double `.json()` call, removed dead Spotify ISRC stub, wrapped SongLink in inner try/except, moved `import requests` to module level, `__init__` fallback now honors `SONGSFETCH_OUTPUT_DIR` env var, improved ISRC-only resolution and source priority ordering
- `backend/youtube.py` — Added `search_tracks()` method for YouTube Music search page scraping
- `static/js/app.js` — Updated `renderSearchResults` for artists/albums/YouTube sections, added YouTube Music and Spotify to platform availability, `sfDownload` now passes track_number/total_tracks/disc_number
- `static/css/style.css` — Added `.results-section` and `.results-heading` styles

---

## v1.0.3 (`4cf1c63`)

### Bug Fixes

- **Download: ISRC-to-URL resolution for all services** — When only an ISRC is available (no URL), the downloader now looks up the ISRC on Deezer (`api.deezer.com/2.0/track/isrc:{isrc}`) to get a Deezer URL, then resolves it via SongLink to obtain Tidal, Amazon, YouTube, and Spotify URLs. Previously only Qobuz could download by ISRC alone.
- **Download: Smart source ordering with availability check** — `_ordered_sources` now tracks whether each service actually has the data it needs (URL or ISRC). The preferred source goes first if it has data, then other services with data, then services without data. This avoids wasting time on services that will immediately fail.
- **Download: Qobuz ISRC fallback for title/artist** — When downloading by ISRC with no title/artist metadata, the downloader queries Qobuz by ISRC to populate title, artist, and album fields, enabling YouTube text-search as a last-resort fallback.

### Files Changed

- `backend/downloader.py` — ISRC resolution via Deezer→SongLink, smart source ordering, Qobuz ISRC fallback for metadata
- `app.py` — Search improvements: ISRC detection, iTunes fallback, track metadata fields (`track_number`, `total_tracks`, `disc_number`)
- `static/js/app.js` — Queue UI shows output path, file manager select-all styling, download passes all metadata fields
- `static/css/style.css` — Queue path and file select-all styles

---

## v1.0.2 (`4a9cb40`)

### New Features

- **Download: Track number and disc metadata** — `DownloadTask` now carries `track_number`, `total_tracks`, `disc_number`, and `total_discs` fields. These are passed through `add_track()` and embedded into the downloaded audio file's metadata.
- **Metadata: Year from MusicBrainz** — The metadata embedding step now extracts and embeds the release year from MusicBrainz alongside genre and publisher.
- **History: Operation history** — Added `operations_history` table to track file operations (resample, analyze, rename) with operation type, file list, and details. New endpoints: `GET /api/history/operations`, `POST /api/history/operations/clear`.
- **Download: Batch download endpoint** — Added `POST /api/download/batch` to queue multiple tracks in a single request.
- **Settings: Embed lyrics toggle** — Added `embed_lyrics` setting to control whether lyrics are fetched and embedded.

### UI Improvements

- **Queue: Output path display** — Completed downloads now show the output file path in the queue modal.
- **Queue: Clear queue button** — Added button to clear completed/failed items from the queue.
- **History: Combined view** — History page shows both download history and operation history in a unified timeline sorted by timestamp.
- **Settings modal** — Full settings UI with output directory, preferred source, quality, and embed lyrics toggle.

### Files Changed

- `backend/downloader.py` — Track number/disc fields in `DownloadTask`, `add_track()`, and `_embed()`; year from MusicBrainz
- `backend/history.py` — Operations history table and CRUD functions
- `backend/musicbrainz.py` — Extract release year from MusicBrainz date field
- `app.py` — Batch download endpoint, settings endpoint, operation history endpoints, download passes track/disc numbers
- `static/js/app.js` — Queue UI, history UI, settings modal, batch operations
- `templates/index.html` — Settings modal markup, history page, page visibility fixes
- `README.md` — Updated documentation

---

## v1.0.1 (`a9a29e4`)

### New Features

- **Search: ISRC direct lookup** — If the search query looks like an ISRC (12 characters, first 2 alpha, rest alphanumeric), it is searched directly via Qobuz's `search_by_isrc` endpoint.
- **Search: iTunes fallback** — When Qobuz search fails, the app falls back to iTunes Search API (free, no auth) to return results with high-res artwork (600x600).
- **Download: YouTube text-search fallback** — When title and artist are available but no platform URLs, the downloader can now search YouTube by text and download as MP3 320kbps.

### Bug Fixes

- **Eventlet graceful fallback** — The app now tries to import and monkey-patch eventlet; if unavailable, falls back to threading mode. This fixes crashes when eventlet is not installed.
- **Page visibility** — Removed `hidden` class from File Manager, Analysis, Resample, and History pages so they render correctly when their tab is selected.

### UI Improvements

- **File manager: Select-all checkbox** — Added a "Select All" checkbox at the top of file lists for easier bulk selection.
- **Queue: File path display** — Completed downloads show the output file path.

### Files Changed

- `app.py` — ISRC search, iTunes fallback, eventlet fallback, `requests` import
- `backend/downloader.py` — YouTube search fallback via title+artist
- `static/js/app.js` — iTunes result rendering, file select-all, queue path display
- `static/css/style.css` — Queue path style, file select-all border
- `templates/index.html` — Page visibility fixes

---

## v1.0.0 (`3ac867f`)

### Bug Fixes

- **CORS: Cross-origin request support** — Added `ProxyFix` middleware and explicit CORS headers (`Access-Control-Allow-Origin: *`) on all responses. Fixes browser errors when frontend and backend are on different origins or behind a proxy.
- **Port: Updated to 3000** — Default application port changed to 3000 for Docker compatibility.

### Files Changed

- `app.py` — CORS headers, ProxyFix middleware, WebSocket ping timeout
- `docker-compose.yml` — Port mapping updated

---

## v0.9.0 (`c980683` – Initial Release)

### Features

- **Multi-platform music search** — Search tracks via Qobuz API with ISRC, quality, and Hi-Res metadata.
- **URL resolution** — Resolve any music platform URL (Spotify, Tidal, Amazon, Deezer, YouTube, Apple Music, Qobuz) to cross-platform links via song.link API.
- **Multi-source download** — Download tracks from Tidal (FLAC/Hi-Res), Qobuz (FLAC/Hi-Res), Amazon Music (FLAC/M4A), Spotify (FLAC), and YouTube (MP3 320kbps) with automatic fallback across services.
- **Configurable source priority** — Set preferred download source (Tidal, Spotify, Qobuz, Amazon, YouTube) with automatic fallback to other services.
- **Quality settings** — Choose between LOSSLESS and HI_RES quality levels.
- **Metadata embedding** — Automatically embeds title, artist, album, album artist, track/disc numbers, ISRC, genre, publisher, year, cover art, and synced/plain lyrics into downloaded files (FLAC, MP3, M4A).
- **Lyrics** — Fetches synced (LRC) and plain lyrics from LRCLIB.
- **MusicBrainz integration** — Looks up genre, label, and year by ISRC via MusicBrainz API.
- **Audio analysis** — Analyze audio files for codec, sample rate, bit depth, channels, duration, and file size via FFprobe.
- **Audio resampling** — Batch resample FLAC files to different sample rates and bit depths via FFmpeg.
- **File manager** — Browse audio files, view metadata, batch rename with customizable format patterns.
- **Download queue** — Real-time download progress via WebSocket with status tracking (queued → resolving → downloading → converting → embedding → completed/failed).
- **Download history** — SQLite-backed history of all downloads and URL fetches.
- **Duration validation** — Validates downloaded files against expected duration to detect preview/sample files.
- **Docker support** — Dockerfile and docker-compose.yml for containerized deployment.
- **CI/CD** — GitHub Actions workflow for multi-arch (amd64 + arm64) Docker image publishing to Docker Hub and GHCR.

### Architecture

- Flask + Flask-SocketIO backend with eventlet async mode
- Vanilla JavaScript frontend (no framework dependencies)
- Background download worker thread with ordered source priority
- Proxy API architecture — no direct service accounts required (uses community proxy endpoints for Tidal, Qobuz, Amazon, Spotify, YouTube)
- SongLink/Odesli for cross-platform URL resolution and ISRC discovery
