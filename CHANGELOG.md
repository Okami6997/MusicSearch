# Changelog

## v1.2.0 (Unreleased)

This release adds playlist downloading (Spotify), significant download performance improvements through caching and increased parallelism, and smart resample skip logic.

### New Features

- **Download: Playlist download** — Added `POST /api/download/playlist` endpoint to expand a Spotify playlist URL into individual tracks and queue them for download. The URL Resolve view now shows a "Download Playlist" button when a playlist URL is detected.
- **Download: Performance optimizations** — Added in-memory caching for SongLink URL resolutions and ISRC lookups (5-minute TTL) to avoid repeated API calls for the same content. Increased the download worker pool from 3 to 5 concurrent workers for faster batch downloads.
- **Resample: Smart skip logic for already-processed files** — Resample now skips files that are already inside the target resample folder, files whose target output already exists, and FLAC files that already match the requested sample rate/bit depth.

### UI Improvements

- **URL Resolve: Playlist detection** — When a playlist URL is detected, the action button changes from "Download Track" to "Download Playlist" automatically.
- **Settings: Preferred-source list** — The Settings modal now includes all supported download sources: Tidal, Spotify, Qobuz, Amazon Music, and YouTube.
- **Responsive UI: Mobile & tablet overhaul** — Completely redesigned the mobile layout with stacked navigation, full-width touch-friendly buttons (16px font on inputs to prevent iOS zoom), horizontally scrollable tab bars, compressed track rows (3-column on mobile), hidden metadata columns to save space, optimized modal dialogs (90vw width), and responsive styles for tablet (641–1024px) and desktop (>1024px) breakpoints.
- **Files: Delete selected files** — Added a red "Delete Selected" button in the Files section toolbar that removes chosen audio files from disk after confirmation.
- **Resample: Delete originals option** — Added a "Delete originals" checkbox in the Resample tab. When enabled, original (unresampled) files are automatically deleted after a successful resample, saving storage space.
- **Resample: Skipped-file reporting** — The Resample result summary and toast now include skipped counts so users can clearly see when files were intentionally not reprocessed.

### Known Limitations

- **Apple Music download not supported** — Apple Music (iTunes) only exposes 30-second preview clips via its public API. Full-length tracks are DRM-protected and can only be played through Apple's authenticated clients. As a result, Apple Music has been removed as a download source. Apple Music search results and URL resolution continue to work for metadata purposes; downloads fall through to Tidal, Spotify, Qobuz, Amazon, or YouTube instead.

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
- **Download: Queue worker was processing one task at a time** — The download worker now processes multiple queued tasks concurrently via `ThreadPoolExecutor` (default 5 workers), enabling true parallel downloads instead of sequential processing.
- **Search: Infinite scroll sometimes stuck at 20 tracks** — Fixed Tracks section pagination rendering so the first-page sentinel is reliably inserted, cleaned duplicated row markup that could interfere with rendering, and added a scroll-based fallback trigger when IntersectionObserver does not fire in some layouts.
- **Resample: Polling for schedule every 30 seconds removed** — The background `setInterval` that polled `/api/resample/schedule` every 30 seconds has been removed. Schedules are now refreshed on-demand: when the Resample tab is opened, when a schedule is created or deleted, and when the browser window regains focus while on the Resample page.

### Files Changed

- `app.py` — Added `/api/download/playlist` endpoint for Apple Music and Spotify playlists; added `applemusic` client to `DownloadManager`; increased `_max_workers` to 5
- `backend/applemusic.py` — New file: Apple Music / iTunes downloader with `expand_album()`, `expand_playlist()`, `get_track_stream_url()`, and `parse_apple_music_url()` methods
- `backend/spotify.py` — Added `expand_playlist()` method using Spotify embed API to extract playlist track metadata
- `backend/downloader.py` — Added `AppleMusicDownloader` integration; added SongLink and ISRC in-memory caches with 5-min TTL; increased worker pool from 3 to 5 concurrent workers; added `apple_url` to links dict and source priority order
- `backend/songlink.py` — Updated `MUSIC_URL_PATTERNS` to include `/song/` in Apple Music regex and `"song"` type in `parse_music_url()`; `get_all_urls()` now returns `apple_url` from SongLink response
- `backend/__init__.py` — Re-exported `AppleMusicDownloader`
- `static/js/app.js` — Added `sfDownloadPlaylist()` function; updated `renderResolveResults()` to show Apple Music platform and detect playlist URLs with appropriate download button; increased download worker pool reference to 5 workers
- `static/js/app.js` — Added `deleteSelectedFiles()` function and `updateFileButtonStates()` for Files section delete feature; added `resample-delete-original` checkbox handling in `doResample()`; removed `setInterval(refreshResampleSchedules, 30000)` polling and replaced with on-demand refresh on tab switch and window focus
- `templates/index.html` — Added "Delete Selected" button in Files toolbar; added "Delete originals" checkbox in Resample options; updated hint text to mention Spotify & Apple Music playlists
- `static/css/style.css` — Comprehensive responsive media queries: mobile (<640px) with stacked layout, 16px input fonts to prevent zoom, scrollable navbars, compressed 3-column track rows; tablet (641–1024px) intermediate layout; desktop (>1024px) full layout; `.btn-delete` danger styling; resample checkbox label styling
- `backend/resample.py` — Added guardrails to skip already-resampled files (target folder or existing output) and FLAC files already in target format (sample rate/bit depth), with structured `skipped` / `skip_reason` response fields
- `static/js/app.js` — Updated resample result messaging to display skipped-file counts in both in-page summary and toast

---

## v1.1.0
- `static/css/style.css` — Added `.results-section` and `.results-heading` styles, service badges, album action alignment, scheduled remux list rows, and `.load-sentinel` infinite-scroll indicator styling

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
