# Changelog

## v1.4.1 (Unreleased)

### New Features & Improvements

- **Global Proxy Support (Warp/System Proxy)** — Added support for `WARP_PROXY` and `ALL_PROXY` environment variables. All outgoing API and download HTTP/HTTPS sessions (including Amazon Music proxy, Spotify, Tidal, Deezer, Qobuz, etc.) are seamlessly routed through the proxy when configured.
- **Remote DNS Proxy Resolution** — Enhanced proxy SOCKS5 config to convert `socks5://` and `socks4://` configurations automatically to DNS-tunneling variants (`socks5h://`, `socks4h://`). This routes DNS requests through the SOCKS proxy itself, avoiding client-side `NameResolutionError`s when local DNS servers lack support for blocked domains.
- **SOCKS5 Support** — Included `PySocks` dependency to support SOCKS5 (`socks5://`) and DNS-resolving SOCKS5 (`socks5h://`) proxy schemes natively in Python's `requests` library.
- **yt-dlp Proxy Integration** — Extended subprocess runners in `backend/youtube.py` to automatically pass the configured proxy address via the `--proxy` argument to `yt-dlp` for YouTube, SoundCloud, and external URL downscaling.

### Bug Fixes

- **Defensive JSON Parsing on Failing Proxy Networks** — Added try-catch defenses to raw `.json()` parsing in Deezer, Spotify, and Amazon API clients. When the proxy is down or returning HTML/Cloudflare blocks, the client raises safe, catchable, and descriptive `ValueError`s instead of crashing thread execution with raw `JSONDecodeError`s.
- **SoundCloud URL Paste: short links and sets** — `on.soundcloud.com` short links are now expanded to their canonical SoundCloud URL before parsing and resolving. SoundCloud `/sets/` links are detected as playlists and expanded into track lists instead of being misclassified as single tracks.
- **SoundCloud playlist tracks: full metadata hydration** — Playlist expansion now hydrates stub track objects returned by the SoundCloud API so every entry includes title, permalink URL, artist, and other required metadata. This fixes "Unknown" track titles and download requests failing with `url, isrc, or title+artist required`.
- **SoundCloud duration handling** — SoundCloud track metadata now uses `full_duration` instead of the 30-second preview `duration` field. This prevents playlist tracks from being treated as 30s previews during validation and fixes incorrect short downloads.

- **URL Paste: Amazon query-style album URLs** — Amazon album links using query parameters (e.g., `?trackAsin=B0FFGW1Y1N`) are now correctly parsed as track links instead of being misclassified as album links. URL parser now extracts `trackAsin` query parameter and detects track ASINs robustly. Applies to URLs like `https://music.amazon.in/albums/B0FFGV7MKD?trackAsin=B0FFGW1Y1N`.
- **View Album: Amazon source now supported** — Album view from search results (detail modal) now works for Amazon Music albums, not just Qobuz/Apple Music. Added album expansion handlers for Amazon, YouTube, Spotify, and Deezer in the `/api/search/expand` endpoint. Error message now lists all supported sources instead of hardcoding "qobuz or itunes".

### Files Changed

- `backend/songlink.py` — Added SoundCloud short-link canonicalization (`on.soundcloud.com` -> `soundcloud.com/...`) before URL parsing so short links resolve as tracks or playlists correctly
- `backend/soundcloud.py` — Added short-link expansion for playlist resolution; hydrated stub track objects in set responses via batched `/tracks` lookups; switched SoundCloud duration mapping from preview `duration` to `full_duration`
- `app.py` — Added SoundCloud playlist expansion support to `/api/resolve/playlist`

- `app.py` — Relaxed source validation in `search_expand()` endpoint; now allows `amazon`, `youtube`, `spotify`, `deezer` for album expansion alongside `qobuz` and `itunes`; added album expansion handlers for all four sources with proper metadata extraction; added source alias normalization (`apple_music` → `itunes`, `youtube_music` → `youtube`)
- `backend/songlink.py` — Added `parse_qs`, `urlparse` imports; enhanced `parse_music_url()` to detect Amazon track ASINs from `trackAsin` query parameter as well as URL path; added `_amazon_track_asin_from_url()` helper to extract ASIN from both URL path and query params; updated `_norm_amazon()` to use the new helper for robust track URL normalization; updated misresolved-track detection to recognize query-style Amazon URLs
- `backend/amazon.py` — Added `parse_qs`, `urlparse` imports; enhanced `extract_asin()` to prioritize explicit `trackAsin` query parameter over album ASIN extraction, handling URL query strings robustly; integrated global proxy support

- `backend/proxy_config.py` — Added global proxy helper to configure proxy sessions using `WARP_PROXY` or `ALL_PROXY`
- `backend/youtube.py` — Configured session with global proxy and added proxy routing to `yt-dlp` commands using the `--proxy` argument
- `backend/applemusic.py`, `backend/deezer.py`, `backend/lyrics.py`, `backend/musicbrainz.py`, `backend/qobuz.py`, `backend/soundcloud.py`, `backend/spotify.py`, `backend/tidal.py`, `backend/search.py` — Updated request sessions to automatically apply proxy settings
- `requirements.txt` — Added `PySocks` requirement for native SOCKS5 support in `requests`

---

## v1.4.0 (Unreleased)

This release adds advanced search with per-field inputs (track, artist, album), multi-source album search (Spotify, Tidal, Deezer), Tidal search integration, retry/backoff for transient failures, per-service health tracking, service-aware album/artist filtering, and a full modal-based results UI.

### New Features

- **Search: Advanced search mode** — A chevron expand button next to the search bar reveals three dedicated input fields for Track, Artist, and Album. Fields are combined into a composite query sent to all services. Collapsing the panel merges the fields back into the main search bar. Enter key works in any field.
- **Search: Multi-source album results** — Albums now come from Spotify, Tidal, and Apple Music (previously Apple Music only). Spotify albums are fetched via the pathfinder `albumsV2` endpoint; Tidal albums are extracted from track search results. Deezer album search is wired up but currently geo-restricted.
- **Search: Tidal integration** — Added `TidalSearchClient` with full track and album search via the spotisaver proxy API. Tidal results appear alongside other services in the unified track list.
- **Search: Per-service health tracking** — Each concurrent search task reports its status (`ok`, `error`), error message, and latency. The aggregated `source_status` object is included in the API response and `search_done` SocketIO event.
- **Download: Provider health and cooldown** — The download queue now tracks provider health, temporarily backs off services after repeated failures, and displays cooldown status with retry timing and last-error details in the queue UI.
- **Settings: Persistent download preferences** — Preferred source, quality, lyrics, auto-resample, and output directory now persist across restarts via a local settings JSON file.
- **Search: Retry with exponential backoff** — Transient HTTP failures (timeouts, 5xx) are retried up to 2 times with exponential backoff before marking a service as failed.
- **UI: Section modals** — Clicking a section heading (Tracks, Artists, Albums) opens a full-screen modal with all items and infinite scroll (batches of 50). Replaces the old inline collapsible sections for a cleaner, unrestricted view.
- **UI: Detail modal for albums/artists** — "View" button on album and artist rows opens a detail modal with cover art, title, and expandable track listing. Detail modal stacks above section modals (z-index 1100 vs 1000).
- **UI: View album from artist expand** — Artist detail modal now shows a "View" button alongside "Download Album" for each album, allowing users to drill into album track listings without leaving the artist view.

### Bug Fixes

- **Search: Tidal API parameter fix** — Tidal proxy API requires `s=` (not `query=`) for the search term. Fixed in `TidalSearchClient.search_tracks()`.
- **Search: Tidal response parsing** — Fixed handling of nested `data.items` response structure and dict-type artist values from the Tidal proxy API.
- **Download: Preferred source ordering** — Fixed source ordering so the selected primary provider is tried first, while SoundCloud and YouTube remain fallback-only sources.
- **Download: Amazon runtime crypto import** — Improved the Amazon downloader to import `cryptography` lazily and recover if the dependency is installed after the app has already started.
- **Search: Apple Music always queried** — iTunes/Apple Music search was previously gated behind a fallback condition and only ran when all primary services failed. Now runs concurrently with all other services.
- **Search: Album/artist service filtering** — Toggling a service filter (e.g., unchecking Apple Music) now correctly hides albums and artists from that service, not just tracks. Added `_applyServiceFiltersToItems()` for generic item filtering.
- **UI: Detail modal hidden behind section modal** — Fixed z-index stacking so the album/artist detail modal always appears above the section modal when triggered from within it.

### Files Changed

- `app.py` — Added `track`, `artist`, `album` advanced search query parameters; added `_do_deezer_albums` task; Spotify tracks task now also fetches albums via shared client; Tidal tracks task extracts albums from track results; `deezer_albums` added to result dict and concurrent task list; album partial events emitted for Spotify, Tidal, and Deezer sources; added `_with_retries()` helper and `source_status` tracking; moved iTunes from fallback-only to always-concurrent
- `backend/search.py` — Added `SpotifySearchClient.search_albums()` method that parses `albumsV2` from the pathfinder search response
- `backend/tidal.py` — Added `_retry_with_backoff()` utility and `TidalSearchClient` class with `search_tracks()` and `get_last_albums()`; fixed `s=` parameter and nested response parsing
- `backend/deezer.py` — Added `DeezerClient.search_albums()` using Deezer `/search/album` API
- `static/js/app.js` — Added advanced search toggle (`_advancedMode`, `_buildAdvancedQuery`, `_getSearchQuery`, `_getAdvancedParams`); `doSearch()` uses `_getSearchQuery()` and passes advanced params to API; added `_applyServiceFiltersToItems()` for album/artist filtering; Enter key support in advanced fields; replaced inline collapsible sections with `_openSectionModal()` / `sfCloseSectionModal()` using modal + IntersectionObserver infinite scroll; removed dead code (`_appendMoreItems`, `_setupSectionScroll`, `_initSectionScrollObservers`, `_sectionCollapsed`); added "View" button in artist expand `renderExpandItems()`; Escape key handler now closes both detail and section modals
- `static/css/style.css` — Added `.advanced-search-fields` grid layout with 3-column responsive design, `.advanced-field` label/input styling, expand toggle rotation animation, slide-in keyframe; mobile breakpoint collapses to single column; added `.section-modal-content` / `.section-modal-body` / `.section-modal-scroll` styles; set `#detail-modal` z-index to 1100 for proper stacking; removed old `.section-body` / `.section-body-scroll` / `.section-body.collapsed` styles
- `templates/index.html` — Added expand toggle button (`#btn-advanced-toggle`) with chevron SVG; added `#advanced-search-fields` container with Track, Artist, Album inputs; added `#section-modal` with title, count badge, and scrollable body; updated CSS/JS cache versions

---

## v1.3.0 (Unreleased)

This release adds album and playlist expansion for Spotify/YouTube Music/Amazon Music, parallel album/playlist downloads with in-order UI completion, auto-resample to Hi-Res FLAC after every download, 30s DRM-free preview playback in search results, and a Docker DNS fix for search latency.

### New Features

- **Album expansion** — Paste any album URL from Spotify, YouTube Music, or Amazon Music to expand it into a track listing with individual download buttons and a "Download All" batch action. Spotify and Amazon use embed-page / SongLink→Deezer API chains; YouTube Music uses yt-dlp flat extraction.
- **Playlist expansion** — Paste any playlist URL from Spotify or YouTube Music to expand it into a track listing. Spotify playlists are expanded via the `__NEXT_DATA__` embed page; YouTube playlists use yt-dlp `extract_flat` mode. Amazon Music playlists are recognized but not expandable (no public API).
- **URL type detection** — `parse_music_url()` now distinguishes track, album, and playlist URL types across all supported platforms. YouTube Music playlist URLs are classified as album (`OLAK*` prefix) or playlist (`PL*`, `RD*`, etc.) based on the list ID prefix. Amazon Music `/playlists/` URLs are now recognized.
- **Download: Auto-resample to 192kHz/24-bit Hi-Res FLAC** — After every successful download a new `resample_inplace()` step runs before metadata embedding. Non-FLAC files (MP3, M4A) are converted to FLAC and the originals removed. Files already at the target spec are skipped. Controlled via the new "Auto-resample to 192kHz/24-bit" toggle in Settings.
- **Download: Parallel album/playlist download with ordered UI completion** — Album and playlist tracks now download concurrently across all 5 workers. Each batch receives a `batch_id`; the `_flush_batch_completion()` helper buffers finished tasks and emits UI notifications strictly in track order, so the queue display always shows tracks completing in sequence.
- **Search: 30s DRM-free preview playback** — Search results that include a preview URL (Deezer tracks use the `preview` field; iTunes tracks use `previewUrl`) now show a circular play/pause button. A single shared `<audio>` element handles playback — starting a new preview automatically stops the previous one.

### Bug Fixes

- **Search: Pagination removed (single-call mode)** — `/api/search` no longer uses offset-based pagination for UI loading. A single search request now fans out concurrently across services and returns one aggregated response (`has_more: false`), while still emitting partial websocket updates during execution.
- **Search: Async fan-out + progressive append + infinite lazy loading** — `/api/search` now accepts the Socket.IO `sid` from the frontend so `search_partial` events are emitted to the correct client during HTTP-driven searches. Partial payloads now include both `append: true` and the active query (`q`), so frontend handlers process only matching searches and append incoming chunks instead of replacing existing content. Track results are merged with YouTube/Deezer/SoundCloud chunks in the live feed using dedupe keys, and non-primary service fetch limits were raised to 20 to reduce first-page truncation. Qobuz track pagination computes `has_more` from API totals (`offset + returned < total`) so paging continues beyond short first pages; the track sentinel remains active with a scroll fallback, allowing continuous lazy loading as new results appear.
- **Search: Preview playback error handling** — Added `oncanplay` and `onerror` handlers to the preview audio element so that failed preview playback (network issues, CORS, invalid URL) resets the button state and shows a toast notification instead of leaving the button in a broken "playing" state. Added a loading spinner state while the audio buffers to give better visual feedback.
- **Docker: Search slowness from eventlet green DNS** — Eventlet's green DNS resolver introduced high latency across concurrent search API calls inside Docker due to the container's embedded DNS server. Fixed by setting `EVENTLET_NO_GREENDNS=yes` in both `Dockerfile` and `docker-compose.yml`, and adding external DNS servers (`8.8.8.8`, `8.8.4.4`) to `docker-compose.yml`.

### Files Changed

- `app.py` — Added `/api/resolve/album` and `/api/resolve/playlist` endpoints; added `/api/download/album` endpoint with handlers for Spotify, YouTube, Amazon, Qobuz, Apple Music; added YouTube handler to `/api/download/playlist`; passes `batch_id` and `batch_seq` for all batch/album/playlist download endpoints
- `backend/spotify.py` — Rewrote `expand_playlist()` using `__NEXT_DATA__` from embed page (same approach as `expand_album`); handles `subtitle` (string) artist field and `coverArt.sources[].height` being `None`
- `backend/youtube.py` — Added `expand_album()` using yt-dlp `extract_flat` mode; added `expand_playlist()` as alias since yt-dlp handles both identically
- `backend/amazon.py` — Added `expand_album()` via SongLink→Deezer API chain
- `backend/songlink.py` — Added Amazon playlist URL pattern (`/playlists/B...`); YouTube playlist URLs now differentiated: `OLAK*` → album, others → playlist
- `backend/downloader.py` — Added `RESAMPLING` status; `auto_resample` flag; calls `resample_inplace()` between download and embedding; added `batch_id`/`batch_seq` fields and `_flush_batch_completion()` to buffer out-of-order completions
- `backend/resample.py` — Added `resample_inplace()` function for single-file in-place resample/conversion
- `backend/deezer.py` — Exposed `preview_url` field from Deezer API response
- `static/js/app.js` — Added `renderAlbumResults()` and `renderPlaylistResults()` for track-listing UI with individual download and "Download All" batch buttons; `doResolve()` detects album/playlist types and routes to expansion endpoints; added `previewBtn()` helper and `sfTogglePreview()` play/pause handler; added `resampling` status label; wired `auto_resample` setting to UI toggle
- `static/css/style.css` — Added `.btn-preview` and `.btn-preview.playing` styles
- `templates/index.html` — Added `<audio id="preview-player">` element; added Auto-resample checkbox in Settings
- `Dockerfile` — Added `EVENTLET_NO_GREENDNS=yes` environment variable
- `docker-compose.yml` — Added `EVENTLET_NO_GREENDNS=yes` env var and external DNS servers

---

## v1.2.0 (Unreleased)

This release adds playlist downloading (Spotify), significant download performance improvements through caching and increased parallelism, and smart resample skip logic.

### New Features

- **Download: Playlist download** — Added `POST /api/download/playlist` endpoint to expand a Spotify playlist URL into individual tracks and queue them for download. The URL Resolve view now shows a "Download Playlist" button when a playlist URL is detected.
- **Download: Performance optimizations** — Added in-memory caching for SongLink URL resolutions and ISRC lookups (5-minute TTL) to avoid repeated API calls for the same content. Increased the download worker pool from 3 to 5 concurrent workers for faster batch downloads.
- **Resample: Smart skip logic for already-processed files** — Resample now skips files that are already inside the target resample folder, files whose target output already exists, and FLAC files that already match the requested sample rate/bit depth.
- **Search: Additional service integrations (Deezer + SoundCloud)** — Added dedicated Deezer and SoundCloud search clients and integrated both into `/api/search` so results appear in their own UI sections.
- **Download: Deezer source integration** — Added a dedicated Deezer downloader flow (Deezer metadata + ISRC provider resolution), and integrated `deezer` as a first-class preferred source in the download order.
- **Download: Deezer/SoundCloud URL support** — Added Deezer/SoundCloud URL parsing and SongLink soundcloud URL propagation so pasted links from these services can resolve cross-platform targets and download from available mapped sources.
- **Download: Qobuz resolver hardening** — Refactored the Qobuz resolver/downloader to use a provider fallback chain with quality fallback (`27` -> `7` -> `6`) and robust streamed writes (`.part` temp file + atomic rename) for improved reliability when individual providers fail.

### UI Improvements

- **Search: Source filter toggles** — Added inline toggle pills in the Search tab so users can show/hide result sections for Tracks, Artists, Albums, YouTube, Deezer, and SoundCloud without running a new search.
- **URL Resolve: Playlist detection** — When a playlist URL is detected, the action button changes from "Download Track" to "Download Playlist" automatically.
- **Settings: Preferred-source list** — The Settings modal now includes all supported download sources: Tidal, Spotify, Deezer, Qobuz, Amazon Music, YouTube, and SoundCloud.
- **Responsive UI: Mobile & tablet overhaul** — Completely redesigned the mobile layout with stacked navigation, full-width touch-friendly buttons (16px font on inputs to prevent iOS zoom), horizontally scrollable tab bars, compressed track rows (3-column on mobile), hidden metadata columns to save space, optimized modal dialogs (90vw width), and responsive styles for tablet (641–1024px) and desktop (>1024px) breakpoints.
- **Files: Delete selected files** — Added a red "Delete Selected" button in the Files section toolbar that removes chosen audio files from disk after confirmation.
- **Resample: Delete originals option** — Added a "Delete originals" checkbox in the Resample tab. When enabled, original (unresampled) files are automatically deleted after a successful resample, saving storage space.
- **Resample: Skipped-file reporting** — The Resample result summary and toast now include skipped counts so users can clearly see when files were intentionally not reprocessed.

### Known Limitations

- **Apple Music download not supported** — Apple Music (iTunes) only exposes 30-second preview clips via its public API. Full-length tracks are DRM-protected and can only be played through Apple's authenticated clients. As a result, Apple Music has been removed as a download source. Apple Music search results and URL resolution continue to work for metadata purposes; downloads fall through to Tidal, Spotify, Qobuz, Amazon, or YouTube instead.

### Bug Fixes

- **Metadata: Lyrics now properly embedded in all audio formats** — Fixed issue where lyrics were not being embedded into MP3 and M4A files. Lyrics are now embedded using ID3 USLT (Unsynchronized Lyrics) frames for MP3 and ©lyr atoms for M4A/MP4. This ensures Subsonic API servers (Navidrome, etc.) can read lyrics directly from the audio files instead of requiring separate .lrc companion files. FLAC lyrics embedding already worked correctly.
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
- `backend/metadata.py` — Added USLT frame embedding for MP3 files and ©lyr atom embedding for M4A/MP4 files to store unsynchronized lyrics; imported USLT from mutagen.id3 for proper ID3v2 lyrics support
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
