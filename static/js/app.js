/* SongsFetch - Frontend Application */

(function () {
    "use strict";

    // ── State ───────────────────────────────────────────────
    let queueData = [];
    let loadedAudioFiles = [];
    let resampleFiles = [];
    let scheduledResampleJobs = [];
    const socket = io();

    // ── Pagination / lazy-load state ────────────────────────
    let searchPagination = { q: '', offset: 0, loading: false, hasMore: false };
    let filesState = { path: '', offset: 0, loading: false, hasMore: false };
    let analysisState = { path: '', offset: 0, loading: false, hasMore: false };
    let resampleState = { path: '', offset: 0, loading: false, hasMore: false };
    const _sectionObservers = new Map();

    // ── DOM refs ────────────────────────────────────────────
    const $ = (s) => document.querySelector(s);
    const $$ = (s) => document.querySelectorAll(s);

    const searchInput = $("#search-input");
    const searchFilters = $("#search-filters");
    const urlInput = $("#url-input");
    const loading = $("#loading");
    const empty = $("#empty");
    const queueModal = $("#queue-modal");
    const settingsModal = $("#settings-modal");
    const queueBadge = $("#queue-badge");

    // ── Page Navigation ─────────────────────────────────────
    $$(".nav-tab").forEach((tab) => {
        tab.addEventListener("click", () => {
            $$(".nav-tab").forEach((t) => t.classList.remove("active"));
            $$(".page").forEach((p) => p.classList.remove("active"));
            tab.classList.add("active");
            $(`#page-${tab.dataset.page}`).classList.add("active");
            if (tab.dataset.page === "history") loadHistory();
            if (tab.dataset.page === "resample") refreshResampleSchedules();
        });
    });

    // ── Observe page visibility for history ──────────────────
    const historyPage = $("#page-history");
    const historyObserver = new MutationObserver(() => {
        if (historyPage.classList.contains("active")) loadHistory();
    });
    historyObserver.observe(historyPage, { attributes: true, attributeFilter: ["class"] });

    // ── Search Sub-tabs ─────────────────────────────────────
    $$("#page-search .tab").forEach((tab) => {
        tab.addEventListener("click", () => {
            $$("#page-search .tab").forEach((t) => t.classList.remove("active"));
            $$("#page-search .tab-content").forEach((c) => c.classList.remove("active"));
            tab.classList.add("active");
            $(`#tab-${tab.dataset.tab}`).classList.add("active");
        });
    });

    // ── Qobuz Search ────────────────────────────────────────
    $("#btn-search").addEventListener("click", doSearch);
    searchInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") doSearch();
    });

    // ── Search state for incremental rendering ──────────────────────────
    let _searchState = null;   // { q, doneSections, rendered }
    let _latestSearchPayload = null;

    $$(".search-filter-toggle").forEach((toggle) => {
        toggle.addEventListener("change", () => {
            if (_latestSearchPayload) {
                _rerenderSearchFromState();
            }
        });
    });

    function _isSectionEnabled(sectionKey) {
        const el = document.querySelector(`.search-filter-toggle[data-section="${sectionKey}"]`);
        return el ? !!el.checked : true;
    }

    function _rerenderSearchFromState() {
        const d = _latestSearchPayload || {};
        renderSearchResults(
            d.tracks || [],
            d.artists || [],
            d.albums || [],
            d.youtube_tracks || [],
            d.deezer_tracks || [],
            d.soundcloud_tracks || [],
            d.source || ""
        );
        if (searchPagination.hasMore && _isSectionEnabled("tracks")) {
            _ensureSearchSentinel();
        }
    }

    async function doSearch() {
        const q = searchInput.value.trim();
        if (!q) return;
        _unwatchSentinel('search-tracks-sentinel');

        // Reset incremental state
        searchPagination = { q, offset: 0, loading: false, hasMore: false };
        _searchState = { q, doneSections: new Set(), rendered: false };

        // Show loading skeleton
        showLoading();
        if (searchFilters) searchFilters.classList.remove("hidden");

        // Set up SocketIO listeners for incremental results
        const onPartial = (payload) => {
            if (!_searchState || payload.q !== _searchState.q) return;
            _searchState.doneSections.add(payload.section);
            // If HTTP response hasn't rendered yet, update incrementally
            if (!_searchState.rendered) {
                _applyPartialResults(payload);
            }
        };
        const onDone = (payload) => {
            if (!_searchState || payload.q !== _searchState.q) return;
            _searchState.rendered = true;
            _searchState = null;
            socket.off("search_partial", onPartial);
            socket.off("search_done", onDone);
        };
        socket.on("search_partial", onPartial);
        socket.on("search_done", onDone);

        try {
            const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}&offset=0`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            _latestSearchPayload = data;

            // Mark all sections as done so socket events are ignored
            _searchState.rendered = true;
            _searchState = null;
            socket.off("search_partial", onPartial);
            socket.off("search_done", onDone);

            searchPagination.hasMore = !!data.has_more;
            searchPagination.offset = (data.tracks || []).length;
            renderSearchResults(
                data.tracks || [],
                data.artists || [],
                data.albums || [],
                data.youtube_tracks || [],
                data.deezer_tracks || [],
                data.soundcloud_tracks || [],
                data.source || ""
            );
            if (searchPagination.hasMore && _isSectionEnabled("tracks")) {
                _ensureSearchSentinel();
            }
        } catch (e) {
            _searchState = null;
            socket.off("search_partial", onPartial);
            socket.off("search_done", onDone);
            toast(e.message, "error");
            showEmpty();
        }
    }

    /** Apply incremental partial results from a SocketIO search_partial event. */
    function _applyPartialResults(payload) {
        const { section, data, done, source, has_more } = payload;
        // Build a minimal results object with whatever sections are done so far
        const tracks    = section === "tracks"        ? data : (_searchState._tracks    || []);
        const artists   = section === "artists"       ? data : (_searchState._artists   || []);
        const albums    = section === "albums"         ? data : (_searchState._albums    || []);
        const ytTracks  = section === "youtube_tracks" ? data : (_searchState._ytTracks  || []);
        const dzTracks  = section === "deezer_tracks" ? data : (_searchState._dzTracks || []);
        const scTracks  = section === "soundcloud_tracks" ? data : (_searchState._scTracks || []);

        // Cache data for later sections
        if (section === "tracks")        _searchState._tracks    = data;
        if (section === "artists")       _searchState._artists   = data;
        if (section === "albums")        _searchState._albums    = data;
        if (section === "youtube_tracks") _searchState._ytTracks = data;
        if (section === "deezer_tracks") _searchState._dzTracks = data;
        if (section === "soundcloud_tracks") _searchState._scTracks = data;

        // Update pagination state if tracks section arrived
        if (section === "tracks") {
            searchPagination.hasMore = !!has_more;
            searchPagination.offset = (data || []).length;
            if (has_more) {
                // Inject sentinel div if not already present
                let sentinel = document.getElementById("search-tracks-sentinel");
                if (!sentinel) {
                    const sectionEl = document.getElementById("tracks-results-section");
                    if (sectionEl) {
                        sentinel = document.createElement("div");
                        sentinel.id = "search-tracks-sentinel";
                        sentinel.className = "load-sentinel";
                        sentinel.textContent = "Scroll to load more tracks";
                        sectionEl.appendChild(sentinel);
                    }
                }
            }
        }

        // For partial: only render sections that have arrived so far
        const partialSource = source || "qobuz";
        _latestSearchPayload = {
            tracks: _searchState._tracks || [],
            artists: _searchState._artists || [],
            albums: _searchState._albums || [],
            youtube_tracks: _searchState._ytTracks || [],
            deezer_tracks: _searchState._dzTracks || [],
            soundcloud_tracks: _searchState._scTracks || [],
            source: partialSource,
        };
        _renderIncrementalSearchResults(tracks, artists, albums, ytTracks, dzTracks, scTracks, partialSource);
        if (searchPagination.hasMore && _isSectionEnabled("tracks")) {
            _ensureSearchSentinel();
        }
    }

    /** Render only the sections that have data so far, with loading indicators for pending sections. */
    function _renderIncrementalSearchResults(tracks, artists, albums, youtubeTracks, deezerTracks, soundcloudTracks, searchSource) {
        hideAll();
        const results = $("#search-results");
        const list = $("#tracks-list");
        results.classList.remove("hidden");

        const visibleTracks = _isSectionEnabled("tracks") ? tracks : [];
        const visibleArtists = _isSectionEnabled("artists") ? artists : [];
        const visibleAlbums = _isSectionEnabled("albums") ? albums : [];
        const visibleYoutube = _isSectionEnabled("youtube") ? youtubeTracks : [];
        const visibleDeezer = _isSectionEnabled("deezer") ? deezerTracks : [];
        const visibleSoundcloud = _isSectionEnabled("soundcloud") ? soundcloudTracks : [];

        const allEmpty = !visibleTracks.length && !visibleArtists.length && !visibleAlbums.length && !visibleYoutube.length && !visibleDeezer.length && !visibleSoundcloud.length;
        if (allEmpty) {
            // Nothing yet — show loading indicator
            list.innerHTML = '<p class="text-muted">Waiting for results...</p>';
            return;
        }

        let html = "";

        // Artists — show data or loading placeholder
        if (visibleArtists.length) {
            html += _renderArtistsSection(visibleArtists, searchSource);
        } else if (_isSectionEnabled("artists") && !_searchState.doneSections.has("qobuz_artists") && !_searchState.doneSections.has("itunes_artists")) {
            html += '<div class="results-section"><h3 class="results-heading">Artists</h3><div class="skeleton-row"><span class="skeleton-loader"></span></div></div>';
        }

        // Albums
        if (visibleAlbums.length) {
            html += _renderAlbumsSection(visibleAlbums, searchSource);
        } else if (_isSectionEnabled("albums") && !_searchState.doneSections.has("qobuz_albums") && !_searchState.doneSections.has("itunes_albums")) {
            html += '<div class="results-section"><h3 class="results-heading">Albums</h3><div class="skeleton-row"><span class="skeleton-loader"></span></div></div>';
        }

        // Tracks
        if (visibleTracks.length) {
            html += _renderTracksSection(visibleTracks, searchSource);
        } else if (_isSectionEnabled("tracks") && !_searchState.doneSections.has("qobuz_tracks") && !_searchState.doneSections.has("itunes_tracks")) {
            html += '<div class="results-section"><h3 class="results-heading">Tracks</h3><div class="skeleton-row"><span class="skeleton-loader"></span></div></div>';
        }

        // YouTube
        if (visibleYoutube.length) {
            html += _renderYoutubeSection(visibleYoutube);
        } else if (_isSectionEnabled("youtube") && !_searchState.doneSections.has("youtube_tracks")) {
            html += '';
        }

        if (visibleDeezer.length) {
            html += _renderExternalServiceSection("Deezer", visibleDeezer, "Deezer");
        }

        if (visibleSoundcloud.length) {
            html += _renderExternalServiceSection("SoundCloud", visibleSoundcloud, "SoundCloud");
        }

        list.innerHTML = html;
    }

    function _renderArtistsSection(artists, searchSource) {
        if (!artists.length) return '';
        return `<div class="results-section"><h3 class="results-heading">Artists</h3>${artists.map((a) => `
            <div class="expand-item">
                <div class="track-row artist-row">
                    <img class="track-cover" src="${esc(a.image_url || '')}" alt="" loading="lazy" style="border-radius:50%"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22 rx=%2224%22/></svg>'">
                    <div class="track-info">
                        <div class="track-title">${esc(a.name)}</div>
                        <div class="track-artist">${a.albums_count ? a.albums_count + ' albums' : 'Artist'}</div>
                        <div class="track-meta">${serviceBadge(a.service || (searchSource === "itunes" ? "Apple Music" : "Qobuz"))}</div>
                    </div>
                    <div class="track-actions album-actions">
                        <button class="btn-ghost" onclick="window.sfToggleSearchExpand(this,'artist','${esc(String(a.id || ''))}','${esc(a.source || searchSource || 'qobuz')}')">Expand</button>
                    </div>
                </div>
                <div class="expand-panel hidden"></div>
            </div>
        `).join('')}</div>`;
    }

    function _renderAlbumsSection(albums, searchSource) {
        if (!albums.length) return '';
        return `<div class="results-section"><h3 class="results-heading">Albums</h3>${albums.map((al) => `
            <div class="expand-item">
                <div class="track-row album-row">
                    <img class="track-cover" src="${esc(al.cover_url || '')}" alt="" loading="lazy"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                    <div class="track-info">
                        <div class="track-title">${esc(al.title)}</div>
                        <div class="track-artist">${esc(al.artist)}${al.tracks_count ? ' · ' + al.tracks_count + ' tracks' : ''}${al.release_date ? ' · ' + esc(al.release_date.substring(0, 4)) : ''}</div>
                        <div class="track-meta">${serviceBadge(al.service || (searchSource === "itunes" ? "Apple Music" : "Qobuz"))}${al.hires ? ' <span>Hi-Res</span>' : ''}</div>
                    </div>
                    <div class="track-actions album-actions">
                        <button class="btn-ghost" onclick="window.sfToggleSearchExpand(this,'album','${esc(String(al.id || ''))}','${esc(al.source || searchSource || 'qobuz')}')">Expand</button>
                        <button class="btn-dl" onclick="window.sfDownloadAlbum('${esc(String(al.id || ''))}','${esc(al.source || searchSource || 'qobuz')}','${esc(al.title || '')}','${esc(al.artist || '')}','${esc(al.cover_url || '')}')">Download Album</button>
                    </div>
                </div>
                <div class="expand-panel hidden"></div>
            </div>
        `).join('')}</div>`;
    }

    function _renderTracksSection(tracks, searchSource) {
        if (!tracks.length) return '';
        let html = `<div class="results-section" id="tracks-results-section"><h3 class="results-heading">Tracks</h3>`;
        html += tracks.map((t) => `
            <div class="track-row">
                <img class="track-cover" src="${esc(t.cover_url || '')}" alt="" loading="lazy"
                     onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                <div class="track-info">
                    <div class="track-title">${esc(t.title)}</div>
                    <div class="track-artist">${esc(t.artist)}${t.album ? ' · ' + esc(t.album) : ''}</div>
                    <div class="track-meta">${serviceBadge(t.service || (searchSource === "itunes" ? "Apple Music" : "Qobuz"))}${t.isrc ? ' <span>ISRC: ' + esc(t.isrc) + '</span>' : ''}${t.hires ? ' <span>Hi-Res</span>' : ''}${t.sample_rate ? ' <span>' + t.sample_rate + 'kHz/' + t.bit_depth + 'bit</span>' : ''}</div>
                </div>
                <span class="track-duration">${fmtDuration(t.duration_ms)}</span>
                <div class="track-actions">
                    ${previewBtn(t.preview_url)}
                    <button class="btn-dl" onclick="window.sfDownload('${escJs(t.url || '')}','${escJs(t.isrc || '')}','${escJs(t.title)}','${escJs(t.artist)}','${escJs(t.album || '')}','${escJs(t.cover_url || '')}',${t.duration_ms || 0},${t.track_number || 0},${t.total_tracks || 0},${t.disc_number || 0},'${escJs(t.year || '')}')">Download</button>
                </div>
            </div>
        `).join("");
        if (searchPagination.hasMore) html += '<div id="search-tracks-sentinel" class="load-sentinel">Scroll to load more tracks</div>';
        html += '</div>';
        return html;
    }

    function _renderYoutubeSection(youtubeTracks) {
        if (!youtubeTracks.length) return '';
        return `<div class="results-section"><h3 class="results-heading">YouTube Music</h3>${youtubeTracks.map((t) => `
            <div class="track-row">
                <img class="track-cover" src="${esc(t.cover_url || '')}" alt="" loading="lazy"
                     onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                <div class="track-info">
                    <div class="track-title">${esc(t.title || t.id)}</div>
                    <div class="track-artist">${esc(t.artist)}${t.album ? ' · ' + esc(t.album) : ''}</div>
                    <div class="track-meta">${serviceBadge(t.service || 'YouTube Music')} <span>MP3 320kbps</span></div>
                </div>
                <span class="track-duration">${fmtDuration(t.duration_ms)}</span>
                <div class="track-actions">
                    ${previewBtn(t.preview_url)}
                    <button class="btn-dl" onclick="window.sfDownload('${escJs(t.url || '')}','','${escJs(t.title || '')}','${escJs(t.artist || '')}','${escJs(t.album || '')}','${escJs(t.cover_url || '')}',${t.duration_ms || 0},0,0,0,'')">Download</button>
                </div>
            </div>
        `).join('')}</div>`;
    }

    function _renderExternalServiceSection(title, tracks, serviceLabel) {
        if (!tracks.length) return '';
        return `<div class="results-section"><h3 class="results-heading">${esc(title)}</h3>${tracks.map((t) => `
            <div class="track-row">
                <img class="track-cover" src="${esc(t.cover_url || '')}" alt="" loading="lazy"
                     onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                <div class="track-info">
                    <div class="track-title">${esc(t.title || t.id)}</div>
                    <div class="track-artist">${esc(t.artist || '')}${t.album ? ' · ' + esc(t.album) : ''}</div>
                    <div class="track-meta">${serviceBadge(t.service || serviceLabel)}</div>
                </div>
                <span class="track-duration">${fmtDuration(t.duration_ms)}</span>
                <div class="track-actions">
                    ${previewBtn(t.preview_url)}
                    <button class="btn-dl" onclick="window.sfDownload('${escJs(t.url || '')}','${escJs(t.isrc || '')}','${escJs(t.title || '')}','${escJs(t.artist || '')}','${escJs(t.album || '')}','${escJs(t.cover_url || '')}',${t.duration_ms || 0},0,0,0,'${escJs(t.year || '')}')">Download</button>
                </div>
            </div>
        `).join('')}</div>`;
    }

    async function loadMoreSearch() {
        if (searchPagination.loading || !searchPagination.hasMore) return;
        searchPagination.loading = true;
        const sentinel = $('#search-tracks-sentinel');
        if (sentinel) sentinel.textContent = 'Loading more\u2026';
        try {
            const resp = await fetch(`/api/search?q=${encodeURIComponent(searchPagination.q)}&offset=${searchPagination.offset}`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const newTracks = data.tracks || [];
            searchPagination.offset += newTracks.length;
            searchPagination.hasMore = !!data.has_more;
            appendSearchTracks(newTracks, searchPagination.hasMore, data.source || '');
        } catch (e) {
            toast(e.message, "error");
        } finally {
            searchPagination.loading = false;
            if (searchPagination.hasMore) _ensureSearchSentinel();
        }
    }

    function renderSearchResults(tracks, artists, albums, youtubeTracks, deezerTracks, soundcloudTracks, searchSource) {
        hideAll();
        const results = $("#search-results");
        const list = $("#tracks-list");
        const visibleTracks = _isSectionEnabled("tracks") ? tracks : [];
        const visibleArtists = _isSectionEnabled("artists") ? artists : [];
        const visibleAlbums = _isSectionEnabled("albums") ? albums : [];
        const visibleYoutube = _isSectionEnabled("youtube") ? youtubeTracks : [];
        const visibleDeezer = _isSectionEnabled("deezer") ? deezerTracks : [];
        const visibleSoundcloud = _isSectionEnabled("soundcloud") ? soundcloudTracks : [];

        if (!visibleTracks.length && !visibleArtists.length && !visibleAlbums.length && !visibleYoutube.length && !visibleDeezer.length && !visibleSoundcloud.length) {
            list.innerHTML = '<p class="text-muted">No results found.</p>';
            results.classList.remove("hidden");
            return;
        }
        results.classList.remove("hidden");
        let html = "";

        // Artists section
        if (visibleArtists.length) {
            html += '<div class="results-section"><h3 class="results-heading">Artists</h3>';
            html += visibleArtists.map((a) => `
                <div class="expand-item">
                    <div class="track-row artist-row">
                        <img class="track-cover" src="${esc(a.image_url || '')}" alt="" loading="lazy" style="border-radius:50%"
                             onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22 rx=%2224%22/></svg>'">
                        <div class="track-info">
                            <div class="track-title">${esc(a.name)}</div>
                            <div class="track-artist">${a.albums_count ? a.albums_count + ' albums' : 'Artist'}</div>
                            <div class="track-meta">${serviceBadge(a.service || (searchSource === "itunes" ? "Apple Music" : "Qobuz"))}</div>
                        </div>
                        <div class="track-actions album-actions">
                            <button class="btn-ghost" onclick="window.sfToggleSearchExpand(this,'artist','${esc(String(a.id || ''))}','${esc(a.source || searchSource || 'qobuz')}')">Expand</button>
                        </div>
                    </div>
                    <div class="expand-panel hidden"></div>
                </div>
            `).join("");
            html += '</div>';
        }

        // Albums section
        if (visibleAlbums.length) {
            html += '<div class="results-section"><h3 class="results-heading">Albums</h3>';
            html += visibleAlbums.map((al) => `
                <div class="expand-item">
                    <div class="track-row album-row">
                        <img class="track-cover" src="${esc(al.cover_url || '')}" alt="" loading="lazy"
                             onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                        <div class="track-info">
                            <div class="track-title">${esc(al.title)}</div>
                            <div class="track-artist">${esc(al.artist)}${al.tracks_count ? ' · ' + al.tracks_count + ' tracks' : ''}${al.release_date ? ' · ' + esc(al.release_date.substring(0, 4)) : ''}</div>
                            <div class="track-meta">${serviceBadge(al.service || (searchSource === "itunes" ? "Apple Music" : "Qobuz"))}${al.hires ? ' <span>Hi-Res</span>' : ''}</div>
                        </div>
                        <div class="track-actions album-actions">
                            <button class="btn-ghost" onclick="window.sfToggleSearchExpand(this,'album','${esc(String(al.id || ''))}','${esc(al.source || searchSource || 'qobuz')}')">Expand</button>
                            <button class="btn-dl" onclick="window.sfDownloadAlbum('${esc(String(al.id || ''))}','${esc(al.source || searchSource || 'qobuz')}','${esc(al.title || '')}','${esc(al.artist || '')}','${esc(al.cover_url || '')}')">Download Album</button>
                        </div>
                    </div>
                    <div class="expand-panel hidden"></div>
                </div>
            `).join("");
            html += '</div>';
        }

        // Tracks section
        if (visibleTracks.length) {
            html += '<div class="results-section" id="tracks-results-section"><h3 class="results-heading">Tracks</h3>';
            html += visibleTracks.map((t) => `
                <div class="track-row">
                    <img class="track-cover" src="${esc(t.cover_url || '')}" alt="" loading="lazy"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                    <div class="track-info">
                        <div class="track-title">${esc(t.title)}</div>
                        <div class="track-artist">${esc(t.artist)}${t.album ? ' · ' + esc(t.album) : ''}</div>
                        <div class="track-meta">${serviceBadge(t.service || (searchSource === "itunes" ? "Apple Music" : "Qobuz"))}${t.isrc ? ' <span>ISRC: ' + esc(t.isrc) + '</span>' : ''}${t.hires ? ' <span>Hi-Res</span>' : ''}${t.sample_rate ? ' <span>' + t.sample_rate + 'kHz/' + t.bit_depth + 'bit</span>' : ''}</div>
                    </div>
                    <span class="track-duration">${fmtDuration(t.duration_ms)}</span>
                    <div class="track-actions">
                        ${previewBtn(t.preview_url)}
                        <button class="btn-dl" onclick="window.sfDownload('${escJs(t.url || '')}','${escJs(t.isrc || '')}','${escJs(t.title)}','${escJs(t.artist)}','${escJs(t.album || '')}','${escJs(t.cover_url || '')}',${t.duration_ms || 0},${t.track_number || 0},${t.total_tracks || 0},${t.disc_number || 0},'${escJs(t.year || '')}')">Download</button>
                    </div>
                </div>
            `).join("");
            if (searchPagination.hasMore) html += '<div id="search-tracks-sentinel" class="load-sentinel">Scroll to load more tracks</div>';
            html += '</div>';
        }

        // YouTube Music section
        if (visibleYoutube.length) {
            html += '<div class="results-section"><h3 class="results-heading">YouTube Music</h3>';
            html += visibleYoutube.map((t) => `
                <div class="track-row">
                    <img class="track-cover" src="${esc(t.cover_url || '')}" alt="" loading="lazy"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                    <div class="track-info">
                        <div class="track-title">${esc(t.title || t.id)}</div>
                        <div class="track-artist">${esc(t.artist)}${t.album ? ' · ' + esc(t.album) : ''}</div>
                        <div class="track-meta">${serviceBadge(t.service || 'YouTube Music')} <span>MP3 320kbps</span></div>
                    </div>
                    <span class="track-duration">${fmtDuration(t.duration_ms)}</span>
                    <div class="track-actions">
                        ${previewBtn(t.preview_url)}
                        <button class="btn-dl" onclick="window.sfDownload('${escJs(t.url || '')}','','${escJs(t.title || '')}','${escJs(t.artist || '')}','${escJs(t.album || '')}','${escJs(t.cover_url || '')}',${t.duration_ms || 0},0,0,0,'')">Download</button>
                    </div>
                </div>
            `).join("");
            html += '</div>';
        }

        if (visibleDeezer.length) {
            html += _renderExternalServiceSection("Deezer", visibleDeezer, "Deezer");
        }

        if (visibleSoundcloud.length) {
            html += _renderExternalServiceSection("SoundCloud", visibleSoundcloud, "SoundCloud");
        }

        list.innerHTML = html;
    }


    function appendSearchTracks(tracks, hasMore, searchSource) {
        if (!_isSectionEnabled("tracks")) return;
        const section = $('#tracks-results-section');
        if (!section) return;
        _unwatchSentinel('search-tracks-sentinel');
        const oldSentinel = $('#search-tracks-sentinel');
        if (oldSentinel) oldSentinel.remove();
        const newHtml = tracks.map((t) => `
                <div class="track-row">
                    <img class="track-cover" src="${esc(t.cover_url || '')}" alt="" loading="lazy"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                    <div class="track-info">
                        <div class="track-title">${esc(t.title)}</div>
                        <div class="track-artist">${esc(t.artist)}${t.album ? ' · ' + esc(t.album) : ''}</div>
                        <div class="track-meta">${serviceBadge(t.service || (searchSource === "itunes" ? "Apple Music" : "Qobuz"))}${t.isrc ? ' <span>ISRC: ' + esc(t.isrc) + '</span>' : ''}${t.hires ? ' <span>Hi-Res</span>' : ''}${t.sample_rate ? ' <span>' + t.sample_rate + 'kHz/' + t.bit_depth + 'bit</span>' : ''}</div>
                    </div>
                    <span class="track-duration">${fmtDuration(t.duration_ms)}</span>
                    <div class="track-actions">
                        ${previewBtn(t.preview_url)}
                        <button class="btn-dl" onclick="window.sfDownload('${escJs(t.url || '')}','${escJs(t.isrc || '')}','${escJs(t.title)}','${escJs(t.artist)}','${escJs(t.album || '')}','${escJs(t.cover_url || '')}',${t.duration_ms || 0},${t.track_number || 0},${t.total_tracks || 0},${t.disc_number || 0},'${escJs(t.year || '')}')">Download</button>
                    </div>
                </div>
            `).join('');
        section.insertAdjacentHTML('beforeend', newHtml);
        searchPagination.hasMore = hasMore;
        if (hasMore) {
            _ensureSearchSentinel();
        }
    }

    // ── URL Resolve ─────────────────────────────────────────
    $("#btn-resolve").addEventListener("click", doResolve);
    urlInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") doResolve();
    });

    async function doResolve() {
        const url = urlInput.value.trim();
        if (!url) return;
        showLoading();
        try {
            const resp = await fetch(`/api/resolve?url=${encodeURIComponent(url)}`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            renderResolveResults(url, data);
        } catch (e) {
            toast(e.message, "error");
            showEmpty();
        }
    }

    function renderResolveResults(url, data) {
        hideAll();
        const card = $("#resolve-results");
        card.classList.remove("hidden");

        const platforms = [
            { key: "tidal", name: "Tidal", url: data.tidal_url },
            { key: "spotify", name: "Spotify", url: data.spotify_url },
            { key: "amazon", name: "Amazon Music", url: data.amazon_url },
            { key: "qobuz", name: "Qobuz", available: data.qobuz },
            { key: "deezer", name: "Deezer", url: data.deezer_url },
            { key: "youtube", name: "YouTube Music", url: data.youtube_url },
            { key: "apple", name: "Apple Music", url: data.apple_url },
        ];

        $("#platform-list").innerHTML = platforms.map((p) => {
            const avail = p.url || p.available;
            return `
                <div class="platform-item ${avail ? 'available' : 'unavailable'}">
                    <span class="platform-name">${p.name}</span>
                    <span class="platform-status">${avail ? '✓' : '✕'}</span>
                </div>`;
        }).join("");

        const isrc = data.isrc || "";
        const parsed = data.parsed || {};
        const isPlaylist = parsed.type === "playlist" || /playlist/.test(url);
        let actionsHtml = '';
        if (isrc) {
            actionsHtml += `<div class="info-row"><strong>ISRC:</strong> ${esc(isrc)}</div>`;
        }
        // Playlist download button
        if (isPlaylist) {
            const src = parsed.platform === "spotify" ? "spotify" : "apple_music";
            actionsHtml += `<button class="btn-primary" style="margin-top:12px" onclick="window.sfDownloadPlaylist('${esc(url)}','${src}')">Download Playlist</button>`;
        } else {
            // Single track download button
            const dlUrl = data.tidal_url || data.spotify_url || data.amazon_url || data.youtube_url || "";
            if (dlUrl || isrc) {
                actionsHtml += `<button class="btn-primary" style="margin-top:12px" onclick="window.sfDownload('${escJs(dlUrl)}','${escJs(isrc)}','','','','',0)">Download Track</button>`;
            }
        }
        $("#resolve-actions").innerHTML = actionsHtml;
    }

    // ── Download ────────────────────────────────────────────
    window.sfDownload = async function (url, isrc, title, artist, album, cover, durationMs, trackNumber, totalTracks, discNumber, year) {
        try {
            const resp = await fetch("/api/download", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, isrc, title, artist, album, cover_url: cover, duration_ms: durationMs, track_number: trackNumber || 0, total_tracks: totalTracks || 0, disc_number: discNumber || 0, year: year || "" }),
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            toast(`"${title || url || isrc}" added to queue`, "success");
            updateQueueBadge();
        } catch (e) {
            toast(e.message, "error");
        }
    };

    window.sfDownloadAlbum = async function (albumId, source, album, artist, coverUrl) {
        if (!albumId) {
            toast("Missing album id", "error");
            return;
        }
        try {
            const resp = await fetch("/api/download/album", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    album_id: albumId,
                    source,
                    album,
                    artist,
                    cover_url: coverUrl,
                }),
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            toast(`Queued ${data.count || 0} album tracks`, "success");
            updateQueueBadge();
        } catch (e) {
            toast(e.message, "error");
        }
    };

    window.sfDownloadPlaylist = async function (playlistUrl, source) {
        if (!playlistUrl) {
            toast("Missing playlist URL", "error");
            return;
        }
        try {
            const resp = await fetch("/api/download/playlist", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    url: playlistUrl,
                    source: source || "apple_music",
                }),
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            toast(`Queued ${data.count || 0} playlist tracks`, "success");
            updateQueueBadge();
        } catch (e) {
            toast(e.message, "error");
        }
    };

    window.sfToggleSearchExpand = async function (btn, kind, id, source) {
        const item = btn.closest(".expand-item");
        const panel = item ? item.querySelector(".expand-panel") : null;
        if (!panel || !id || !source) return;

        // Toggle if already loaded
        if (panel.dataset.loaded === "1") {
            const open = !panel.classList.contains("hidden");
            panel.classList.toggle("hidden", open);
            btn.textContent = open ? "Expand" : "Collapse";
            return;
        }

        btn.disabled = true;
        panel.classList.remove("hidden");
        panel.innerHTML = '<p class="text-muted">Loading details...</p>';

        try {
            const qs = new URLSearchParams({ kind, id, source });
            const resp = await fetch(`/api/search/expand?${qs.toString()}`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);

            panel.innerHTML = renderExpandItems(kind, data.items || [], source);
            panel.dataset.loaded = "1";
            btn.textContent = "Collapse";
        } catch (e) {
            panel.innerHTML = `<p class="text-danger">${esc(e.message)}</p>`;
            toast(e.message, "error");
        } finally {
            btn.disabled = false;
        }
    };

    function renderExpandItems(kind, items, source) {
        if (!items.length) {
            return '<p class="text-muted">No details available.</p>';
        }
        if (kind === "album") {
            return `<div class="expand-list">${items.map((t) => `
                <div class="expand-row">
                    <span class="expand-index">${t.track_number ? String(t.track_number).padStart(2, "0") : "--"}</span>
                    <span class="expand-main">${esc(t.title || "Untitled")}${t.artist ? ` <span class="text-muted">- ${esc(t.artist)}</span>` : ""}</span>
                    <span class="expand-time">${fmtDuration(t.duration_ms || 0)}</span>
                    <span class="expand-action">
                        <button class="btn-ghost" onclick="window.sfDownload('${escJs(t.url || '')}','${escJs(t.isrc || '')}','${escJs(t.title || '')}','${escJs(t.artist || '')}','${escJs(t.album || '')}','${escJs(t.cover_url || '')}',${t.duration_ms || 0},${t.track_number || 0},${t.total_tracks || 0},${t.disc_number || 0},'${escJs(t.year || '')}')">Download</button>
                    </span>
                </div>
            `).join("")}</div>`;
        }

        return `<div class="expand-list">${items.map((al) => `
            <div class="expand-row">
                <span class="expand-index"></span>
                <span class="expand-main">${esc(al.title || "Untitled")}${al.release_date ? ` <span class="text-muted">(${esc(String(al.release_date).substring(0, 4))})</span>` : ""}</span>
                <span class="expand-time">${al.tracks_count ? `${al.tracks_count} tracks` : ""}</span>
                <span class="expand-action">
                    <button class="btn-ghost" onclick="window.sfDownloadAlbum('${esc(String(al.id || ''))}','${esc(al.source || source || 'qobuz')}','${esc(al.title || '')}','${esc(al.artist || '')}','${esc(al.cover_url || '')}')">Download Album</button>
                </span>
            </div>
        `).join("")}</div>`;
    }

    // ── File Manager ────────────────────────────────────────
    $("#btn-load-files").addEventListener("click", loadFiles);
    $("#btn-preview-rename").addEventListener("click", previewRename);
    $("#btn-rename").addEventListener("click", doRename);
    $("#btn-delete-files").addEventListener("click", deleteSelectedFiles);

    async function loadFiles() {
        const path = $("#files-path").value.trim();
        if (!path) return;
        try {
            _unwatchSentinel('files-sentinel');
            filesState = { path, offset: 0, loading: false, hasMore: false };
            loadedAudioFiles = [];
            const resp = await fetch(`/api/files/audio?path=${encodeURIComponent(path)}&offset=0&limit=50`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const files = data.files || [];
            loadedAudioFiles = files.map((f) => f.path);
            filesState.offset = files.length;
            filesState.hasMore = data.has_more;
            renderFilesList(files);
            if (filesState.hasMore) _watchSentinel('files-sentinel', loadMoreFiles);
        } catch (e) {
            toast(e.message, "error");
        }
    }

    async function loadMoreFiles() {
        if (filesState.loading || !filesState.hasMore) return;
        filesState.loading = true;
        const sentinel = $('#files-sentinel');
        if (sentinel) sentinel.textContent = 'Loading more\u2026';
        try {
            const resp = await fetch(`/api/files/audio?path=${encodeURIComponent(filesState.path)}&offset=${filesState.offset}&limit=50`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const files = data.files || [];
            const startIndex = loadedAudioFiles.length;
            loadedAudioFiles.push(...files.map((f) => f.path));
            filesState.offset += files.length;
            filesState.hasMore = data.has_more;
            appendFilesList(files, startIndex);
        } catch (e) {
            toast(e.message, "error");
        } finally {
            filesState.loading = false;
        }
    }

    function appendFilesList(files, startIndex) {
        const list = $("#files-list");
        _unwatchSentinel('files-sentinel');
        const sentinel = $('#files-sentinel');
        if (sentinel) sentinel.remove();
        list.insertAdjacentHTML('beforeend', files.map((f, i) => `
            <div class="file-row">
                <label class="checkbox-label" style="flex-shrink:0">
                    <input type="checkbox" class="file-check" data-index="${startIndex + i}" checked>
                </label>
                <span class="file-icon">♪</span>
                <span class="file-name" onclick="window.sfShowFileMeta('${esc(f.path)}')" style="cursor:pointer">${esc(f.name)}</span>
                <span class="file-size">${fmtSize(f.size)}</span>
            </div>
        `).join(''));
        
        // Add event listeners to newly added checkboxes
        const newCheckboxes = list.querySelectorAll('.file-check:not([data-listeners="1"])');
        newCheckboxes.forEach((cb) => {
            cb.addEventListener("change", updateFileButtonStates);
            cb.setAttribute("data-listeners", "1");
        });
        
        if (filesState.hasMore) {
            list.insertAdjacentHTML('beforeend', '<div id="files-sentinel" class="load-sentinel"></div>');
            _watchSentinel('files-sentinel', loadMoreFiles);
        }
        
        updateFileButtonStates();
    }

    function renderFilesList(files) {
        const list = $("#files-list");
        if (!files.length) {
            list.innerHTML = '<p class="text-muted">No audio files found.</p>';
            return;
        }
        list.innerHTML = `<div class="file-row file-select-all">
                <label class="checkbox-label"><input type="checkbox" id="select-all-files" checked> <strong>Select All</strong></label>
            </div>` +
            files.map((f, i) => `
            <div class="file-row">
                <label class="checkbox-label" style="flex-shrink:0">
                    <input type="checkbox" class="file-check" data-index="${i}" checked>
                </label>
                <span class="file-icon">♪</span>
                <span class="file-name" onclick="window.sfShowFileMeta('${esc(f.path)}')" style="cursor:pointer">${esc(f.name)}</span>
                <span class="file-size">${fmtSize(f.size)}</span>
            </div>
        `).join("") + (filesState.hasMore ? '<div id="files-sentinel" class="load-sentinel"></div>' : '');

        // Select-all toggle
        $("#select-all-files").addEventListener("change", (e) => {
            $$(".file-check").forEach((cb) => cb.checked = e.target.checked);
            updateFileButtonStates();
        });
        
        // Enable rename/delete buttons when files are checked
        $$(".file-check").forEach((cb) => {
            cb.addEventListener("change", updateFileButtonStates);
        });
        
        updateFileButtonStates();
    }
    
    function updateFileButtonStates() {
        const selected = getSelectedFiles();
        $("#btn-rename").disabled = selected.length === 0;
        $("#btn-delete-files").disabled = selected.length === 0;
    }

    function getSelectedFiles() {
        const checks = $$("#files-list .file-check");
        const selected = [];
        checks.forEach((cb) => {
            if (cb.checked) selected.push(loadedAudioFiles[parseInt(cb.dataset.index)]);
        });
        return selected;
    }

    window.sfShowFileMeta = async function (path) {
        try {
            const resp = await fetch("/api/files/metadata", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path }),
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const detail = $("#file-detail");
            detail.classList.remove("hidden");
            detail.innerHTML = `
                <h3>${esc(data.title || 'Unknown')}</h3>
                <div class="meta-grid">
                    <div><strong>Artist:</strong> ${esc(data.artist)}</div>
                    <div><strong>Album:</strong> ${esc(data.album)}</div>
                    <div><strong>Track:</strong> ${data.track_number || '-'}</div>
                    <div><strong>Disc:</strong> ${data.disc_number || '-'}</div>
                    <div><strong>Year:</strong> ${esc(data.year || '-')}</div>
                </div>`;
        } catch (e) {
            toast(e.message, "error");
        }
    };

    async function previewRename() {
        const selected = getSelectedFiles();
        if (!selected.length) { toast("Select files first", "error"); return; }
        const fmt = $("#rename-format").value.trim();
        try {
            const resp = await fetch("/api/files/rename/preview", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ files: selected, format: fmt }),
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const el = $("#rename-preview");
            el.classList.remove("hidden");
            el.innerHTML = '<table class="rename-table"><thead><tr><th>Current</th><th>New Name</th></tr></thead><tbody>' +
                data.map((p) => `<tr><td>${esc(p.old_name)}</td><td>${p.error ? '<span class="text-danger">' + esc(p.error) + '</span>' : esc(p.new_name)}</td></tr>`).join('') +
                '</tbody></table>';
            $("#btn-rename").disabled = false;
        } catch (e) {
            toast(e.message, "error");
        }
    }

    async function doRename() {
        const selected = getSelectedFiles();
        if (!selected.length) return;
        const fmt = $("#rename-format").value.trim();
        try {
            const resp = await fetch("/api/files/rename", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ files: selected, format: fmt }),
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const success = data.filter((r) => r.success).length;
            toast(`Renamed ${success}/${data.length} files`, "success");
            loadFiles(); // refresh
        } catch (e) {
            toast(e.message, "error");
        }
    }

    async function deleteSelectedFiles() {
        const selected = getSelectedFiles();
        if (!selected.length) { toast("Select files first", "error"); return; }
        
        // Confirmation dialog
        const count = selected.length;
        const msg = `Delete ${count} file${count > 1 ? 's' : ''}? This cannot be undone.`;
        if (!confirm(msg)) return;

        try {
            const resp = await fetch("/api/files/delete", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ files: selected }),
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const success = data.filter((r) => r.success).length;
            toast(`Deleted ${success}/${data.length} files`, "success");
            loadFiles(); // refresh
        } catch (e) {
            toast(e.message, "error");
        }
    }

    // ── Analysis ────────────────────────────────────────────
    let analysisFiles = [];
    $("#btn-load-analysis").addEventListener("click", loadAnalysisFiles);
    $("#btn-analyze-selected").addEventListener("click", analyzeSelectedFiles);

    async function loadAnalysisFiles() {
        const path = $("#analysis-path").value.trim();
        if (!path) return;
        try {
            _unwatchSentinel('analysis-sentinel');
            analysisState = { path, offset: 0, loading: false, hasMore: false };
            analysisFiles = [];
            const resp = await fetch(`/api/files/audio?path=${encodeURIComponent(path)}&offset=0&limit=50`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const files = data.files || [];
            analysisFiles = files.map((f) => f.path);
            analysisState.offset = files.length;
            analysisState.hasMore = data.has_more;
            renderAnalysisFiles(files);
            if (analysisState.hasMore) _watchSentinel('analysis-sentinel', loadMoreAnalysisFiles);
        } catch (e) {
            toast(e.message, "error");
        }
    }

    async function loadMoreAnalysisFiles() {
        if (analysisState.loading || !analysisState.hasMore) return;
        analysisState.loading = true;
        const sentinel = $('#analysis-sentinel');
        if (sentinel) sentinel.textContent = 'Loading more\u2026';
        try {
            const resp = await fetch(`/api/files/audio?path=${encodeURIComponent(analysisState.path)}&offset=${analysisState.offset}&limit=50`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const files = data.files || [];
            const startIndex = analysisFiles.length;
            analysisFiles.push(...files.map((f) => f.path));
            analysisState.offset += files.length;
            analysisState.hasMore = data.has_more;
            appendAnalysisFiles(files, startIndex);
        } catch (e) {
            toast(e.message, "error");
        } finally {
            analysisState.loading = false;
        }
    }

    function appendAnalysisFiles(files, startIndex) {
        const list = $("#analysis-files");
        _unwatchSentinel('analysis-sentinel');
        const sentinel = $('#analysis-sentinel');
        if (sentinel) sentinel.remove();
        list.insertAdjacentHTML('beforeend', files.map((f, i) => `
            <div class="file-row">
                <label class="checkbox-label" style="flex-shrink:0">
                    <input type="checkbox" class="analysis-check" data-index="${startIndex + i}" checked>
                </label>
                <span class="file-icon">♪</span>
                <span class="file-name">${esc(f.name)}</span>
                <span class="file-size">${fmtSize(f.size)}</span>
            </div>
        `).join(''));
        if (analysisState.hasMore) {
            list.insertAdjacentHTML('beforeend', '<div id="analysis-sentinel" class="load-sentinel"></div>');
            _watchSentinel('analysis-sentinel', loadMoreAnalysisFiles);
        }
    }

    function renderAnalysisFiles(files) {
        const list = $("#analysis-files");
        if (!files.length) {
            list.innerHTML = '<p class="text-muted">No audio files found.</p>';
            return;
        }
        list.innerHTML = `<div class="file-row file-select-all">
                <label class="checkbox-label"><input type="checkbox" id="select-all-analysis" checked> <strong>Select All</strong></label>
            </div>` +
            files.map((f, i) => `
            <div class="file-row">
                <label class="checkbox-label" style="flex-shrink:0">
                    <input type="checkbox" class="analysis-check" data-index="${i}" checked>
                </label>
                <span class="file-icon">♪</span>
                <span class="file-name">${esc(f.name)}</span>
                <span class="file-size">${fmtSize(f.size)}</span>
            </div>
        `).join("") + (analysisState.hasMore ? '<div id="analysis-sentinel" class="load-sentinel"></div>' : '');

        $("#select-all-analysis").addEventListener("change", (e) => {
            $$(".analysis-check").forEach((cb) => cb.checked = e.target.checked);
        });

        $("#btn-analyze-selected").disabled = false;
    }

    function getSelectedAnalysisFiles() {
        const checks = $$("#analysis-files .analysis-check");
        const selected = [];
        checks.forEach((cb) => {
            if (cb.checked) selected.push(analysisFiles[parseInt(cb.dataset.index)]);
        });
        return selected;
    }

    async function analyzeSelectedFiles() {
        const selected = getSelectedAnalysisFiles();
        if (!selected.length) { toast("Select files first", "error"); return; }
        toast("Analyzing files...", "");
        try {
            const resp = await fetch("/api/analysis/batch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ paths: selected }),
            });
            const results = await resp.json();
            renderAnalysisResults(results);
            toast(`Analyzed ${results.length} files`, "success");
        } catch (e) {
            toast(e.message, "error");
        }
    }

    function renderAnalysisResults(results) {
        const el = $("#analysis-results");
        el.classList.remove("hidden");
        el.innerHTML = `<table class="rename-table"><thead><tr><th>File</th><th>Codec</th><th>Sample Rate</th><th>Bit Depth</th><th>Channels</th><th>Duration</th><th>Size</th></tr></thead><tbody>` +
            results.map((r) => {
                if (r.error) {
                    return `<tr><td>${esc(r.file_path)}</td><td colspan="6" class="text-danger">${esc(r.error)}</td></tr>`;
                }
                return `<tr>
                    <td>${esc(r.file_name)}</td>
                    <td>${esc(r.codec || '-')}</td>
                    <td>${r.sample_rate ? r.sample_rate + ' Hz' : '-'}</td>
                    <td>${esc(r.bit_depth || '-')}</td>
                    <td>${r.channels || '-'}</td>
                    <td>${r.duration ? r.duration + 's' : '-'}</td>
                    <td>${fmtSize(r.file_size)}</td>
                </tr>`;
            }).join("") +
            `</tbody></table>`;
    }

    // ── Resample ────────────────────────────────────────────
    $("#btn-load-resample").addEventListener("click", loadResampleFiles);
    $("#btn-resample").addEventListener("click", doResample);
    $("#btn-schedule-resample").addEventListener("click", scheduleResample);

    async function loadResampleFiles() {
        const dir = $("#resample-dir").value.trim();
        if (!dir) return;
        try {
            _unwatchSentinel('resample-sentinel');
            resampleState = { path: dir, offset: 0, loading: false, hasMore: false };
            resampleFiles = [];
            const resp = await fetch(`/api/files/audio?path=${encodeURIComponent(dir)}&offset=0&limit=50`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const files = data.files || [];
            resampleFiles = files.map((f) => f.path);
            resampleState.offset = files.length;
            resampleState.hasMore = data.has_more;
            // Get info for first page
            const infoResp = await fetch("/api/resample/info", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ paths: resampleFiles }),
            });
            const info = await infoResp.json();
            const el = $("#resample-files");
            el.innerHTML = info.map((f) => `
                <div class="file-row">
                    <span class="file-icon">♪</span>
                    <span class="file-name">${esc(f.path.split('/').pop())}</span>
                    <span class="file-size">${f.sample_rate ? f.sample_rate + 'Hz' : '-'} / ${f.bits_per_sample ? f.bits_per_sample + 'bit' : '-'}</span>
                </div>
            `).join("") + (resampleState.hasMore ? '<div id="resample-sentinel" class="load-sentinel"></div>' : '');
            $("#btn-resample").disabled = false;
            $("#btn-schedule-resample").disabled = false;
            if (resampleState.hasMore) _watchSentinel('resample-sentinel', loadMoreResampleFiles);
        } catch (e) {
            toast(e.message, "error");
        }
    }

    async function loadMoreResampleFiles() {
        if (resampleState.loading || !resampleState.hasMore) return;
        resampleState.loading = true;
        const sentinel = $('#resample-sentinel');
        if (sentinel) sentinel.textContent = 'Loading more\u2026';
        try {
            const resp = await fetch(`/api/files/audio?path=${encodeURIComponent(resampleState.path)}&offset=${resampleState.offset}&limit=50`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const files = data.files || [];
            const newPaths = files.map((f) => f.path);
            resampleFiles.push(...newPaths);
            resampleState.offset += files.length;
            resampleState.hasMore = data.has_more;
            const infoResp = await fetch("/api/resample/info", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ paths: newPaths }),
            });
            const info = await infoResp.json();
            const el = $("#resample-files");
            _unwatchSentinel('resample-sentinel');
            const sentinel2 = $('#resample-sentinel');
            if (sentinel2) sentinel2.remove();
            el.insertAdjacentHTML('beforeend', info.map((f) => `
                <div class="file-row">
                    <span class="file-icon">♪</span>
                    <span class="file-name">${esc(f.path.split('/').pop())}</span>
                    <span class="file-size">${f.sample_rate ? f.sample_rate + 'Hz' : '-'} / ${f.bits_per_sample ? f.bits_per_sample + 'bit' : '-'}</span>
                </div>
            `).join(''));
            if (resampleState.hasMore) {
                el.insertAdjacentHTML('beforeend', '<div id="resample-sentinel" class="load-sentinel"></div>');
                _watchSentinel('resample-sentinel', loadMoreResampleFiles);
            }
        } catch (e) {
            toast(e.message, "error");
        } finally {
            resampleState.loading = false;
        }
    }

    async function doResample() {
        if (!resampleFiles.length) return;
        const rate = $("#resample-rate").value;
        const bits = $("#resample-bits").value;
        const deleteOriginal = $("#resample-delete-original").checked;
        if (!rate && !bits) { toast("Select sample rate or bit depth", "error"); return; }
        toast("Resampling started...", "");
        try {
            const resp = await fetch("/api/resample", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ files: resampleFiles, sample_rate: rate, bit_depth: bits, delete_original: deleteOriginal }),
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const success = data.filter((r) => r.success).length;
            const skipped = data.filter((r) => r.skipped).length;
            const deleted = data.filter((r) => r.original_deleted).length;
            const el = $("#resample-result");
            el.classList.remove("hidden");
            let msg = `<p class="text-muted">Resampled ${success}/${data.length} files successfully.`;
            if (skipped > 0) msg += ` Skipped ${skipped} file${skipped > 1 ? 's' : ''} (already resampled/target format).`;
            if (deleteOriginal && deleted > 0) msg += ` Deleted ${deleted} original files.`;
            msg += '</p>';
            el.innerHTML = msg;
            toast(`Resampled ${success}/${data.length} files${skipped > 0 ? `, skipped ${skipped}` : ''}${deleteOriginal && deleted > 0 ? ` and deleted ${deleted} originals` : ''}`, "success");
        } catch (e) {
            toast(e.message, "error");
        }
    }

    async function scheduleResample() {
        if (!resampleFiles.length) {
            toast("Load files first", "error");
            return;
        }
        const rate = $("#resample-rate").value;
        const bits = $("#resample-bits").value;
        const runAt = $("#resample-schedule-at").value;
        const name = $("#resample-schedule-name").value.trim() || "Scheduled Remux";
        if (!rate && !bits) { toast("Select sample rate or bit depth", "error"); return; }
        if (!runAt) { toast("Choose a schedule time", "error"); return; }

        try {
            const resp = await fetch("/api/resample/schedule", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    files: resampleFiles,
                    sample_rate: rate,
                    bit_depth: bits,
                    run_at: runAt,
                    name,
                }),
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            toast("Scheduled remux created", "success");
            $("#resample-schedule-name").value = "";
            await refreshResampleSchedules();
        } catch (e) {
            toast(e.message, "error");
        }
    }

    async function refreshResampleSchedules() {
        try {
            const resp = await fetch("/api/resample/schedule");
            const jobs = await resp.json();
            scheduledResampleJobs = jobs;
            renderResampleSchedules();
        } catch (e) {
            // ignore silently
        }
    }

    function renderResampleSchedules() {
        const el = $("#resample-schedules");
        if (!el) return;
        if (!scheduledResampleJobs.length) {
            el.innerHTML = '<p class="text-muted">No scheduled remux jobs.</p>';
            return;
        }
        el.innerHTML = scheduledResampleJobs.map((j) => {
            const when = j.run_at ? new Date(j.run_at * 1000).toLocaleString() : "-";
            return `<div class="schedule-row">
                <div class="schedule-main">
                    <div class="track-title">${esc(j.name || "Scheduled Remux")}</div>
                    <div class="track-meta"><span>${esc(j.status || "scheduled")}</span> <span>${esc(when)}</span> <span>${(j.files || []).length} files</span></div>
                    ${j.error ? `<div class="text-danger">${esc(j.error)}</div>` : ""}
                </div>
                <div class="track-actions">
                    ${j.status === "running" ? "" : `<button class="btn-ghost" onclick="window.sfDeleteScheduledResample('${esc(j.id)}')">Delete</button>`}
                </div>
            </div>`;
        }).join("");
    }

    window.sfDeleteScheduledResample = async function (jobId) {
        try {
            const resp = await fetch(`/api/resample/schedule/${encodeURIComponent(jobId)}`, { method: "DELETE" });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            toast("Schedule deleted", "success");
            await refreshResampleSchedules();
        } catch (e) {
            toast(e.message, "error");
        }
    };

    // ── History ─────────────────────────────────────────────
    $("#btn-clear-history").addEventListener("click", async () => {
        await fetch("/api/history/downloads/clear", { method: "POST" });
        await fetch("/api/history/operations/clear", { method: "POST" });
        loadHistory();
    });

    async function loadHistory() {
        try {
            const [downloadsResp, opsResp] = await Promise.all([
                fetch("/api/history/downloads"),
                fetch("/api/history/operations"),
            ]);
            const downloads = await downloadsResp.json();
            const operations = await opsResp.json();

            const allItems = [
                ...downloads.map(d => ({ ...d, _type: "download" })),
                ...operations.map(o => ({ ...o, _type: "operation" })),
            ].sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));

            const el = $("#history-list");
            if (!allItems.length) {
                el.innerHTML = '<p class="text-muted">No history yet.</p>';
                return;
            }
            el.innerHTML = allItems.map((h) => {
                if (h._type === "operation") {
                    const files = JSON.parse(h.files || "[]");
                    return `<div class="track-row">
                        <div class="track-info">
                            <div class="track-title">${esc(h.operation)}</div>
                            <div class="track-artist">${esc(files.length)} file${files.length !== 1 ? 's' : ''}</div>
                            ${h.details ? `<div class="track-meta">${esc(h.details)}</div>` : ''}
                        </div>
                        <span class="track-duration">${esc(h.operation)}</span>
                    </div>`;
                }
                return `<div class="track-row">
                    <img class="track-cover" src="${esc(h.cover_url || '')}" alt=""
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                    <div class="track-info">
                        <div class="track-title">${esc(h.title || h.url || 'Unknown')}</div>
                        <div class="track-artist">${esc(h.artist)}${h.source ? ' · via ' + esc(h.source) : ''}</div>
                        ${h.path ? `<div class="track-meta">${esc(h.path)}</div>` : ''}
                    </div>
                    <span class="track-duration">${esc(h.quality)} ${esc(h.format)}</span>
                </div>`;
            }).join("");
        } catch (e) { /* ignore */ }
    }

    // ── Queue Modal ─────────────────────────────────────────
    $("#btn-queue").addEventListener("click", async () => {
        queueModal.classList.remove("hidden");
        await refreshQueue();
    });
    $("#btn-close-queue").addEventListener("click", () => queueModal.classList.add("hidden"));
    queueModal.querySelector(".modal-overlay").addEventListener("click", () => queueModal.classList.add("hidden"));

    $("#btn-clear-queue").addEventListener("click", async () => {
        await fetch("/api/queue/clear", { method: "POST" });
        await refreshQueue();
    });

    async function refreshQueue() {
        try {
            const resp = await fetch("/api/queue");
            queueData = await resp.json();
            renderQueue();
        } catch (e) { /* ignore */ }
    }

    function renderQueue() {
        const list = $("#queue-list");
        if (!queueData.length) {
            list.innerHTML = '<p class="empty-queue">No downloads yet</p>';
            return;
        }
        list.innerHTML = queueData.map((t) => `
            <div class="queue-item status-${t.status}">
                <img class="queue-cover" src="${esc(t.cover_url || '')}" alt=""
                     onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2240%22 height=%2240%22><rect fill=%22%23262626%22 width=%2240%22 height=%2240%22/></svg>'">
                <div class="queue-info">
                    <div class="queue-title">${esc(t.title || t.url || t.isrc)}</div>
                    <div class="queue-status">${statusText(t)}</div>
                    ${t.status === "downloading" ? `<div class="queue-progress"><div class="queue-progress-bar" style="width:${t.progress}%"></div></div>` : ""}
                    ${t.status === "completed" && t.output_path ? `<div class="queue-path">${esc(t.output_path)}</div>` : ""}
                </div>
            </div>
        `).join("");
    }

    function statusText(t) {
        switch (t.status) {
            case "queued": return "Queued";
            case "resolving": return "Resolving...";
            case "downloading": return t.error ? `⚠ ${t.error}` : `Downloading ${Math.round(t.progress)}%`;
            case "converting": return "Converting...";
            case "resampling": return "Resampling to Hi-Res...";
            case "embedding": return "Embedding metadata & lyrics...";
            case "completed": return `✓ Completed${t.source ? ' via ' + t.source : ''}`;
            case "failed": return `✕ Failed: ${t.error}`;
            default: return t.status;
        }
    }

    function updateQueueBadge() {
        fetch("/api/queue").then(r => r.json()).then(q => {
            const active = q.filter(t => !["completed", "failed"].includes(t.status)).length;
            queueBadge.textContent = active;
            queueBadge.classList.toggle("hidden", active === 0);
        }).catch(() => {});
    }

    // ── WebSocket Progress ──────────────────────────────────
    socket.on("download_progress", (data) => {
        const idx = queueData.findIndex((t) => t.id === data.id);
        if (idx >= 0) queueData[idx] = data;
        else queueData.push(data);
        if (!queueModal.classList.contains("hidden")) renderQueue();
        updateQueueBadge();
        if (data.status === "completed") {
            toast(`"${data.title || data.url}" downloaded`, "success");
            // Refresh history if on history page
            if ($("#page-history").classList.contains("active")) loadHistory();
        }
        if (data.status === "failed") toast(`"${data.title || data.url}" failed: ${data.error}`, "error");
    });

    // ── Settings Modal ──────────────────────────────────────
    $("#btn-settings").addEventListener("click", async () => {
        try {
            const resp = await fetch("/api/settings");
            const s = await resp.json();
            $("#setting-dir").value = s.output_dir || "";
            $("#setting-source").value = s.preferred_source || "tidal";
            $("#setting-quality").value = s.quality || "LOSSLESS";
            $("#setting-lyrics").checked = s.embed_lyrics !== false;
            $("#setting-resample").checked = s.auto_resample !== false;
        } catch (e) { /* ignore */ }
        settingsModal.classList.remove("hidden");
    });
    $("#btn-close-settings").addEventListener("click", () => settingsModal.classList.add("hidden"));
    settingsModal.querySelector(".modal-overlay").addEventListener("click", () => settingsModal.classList.add("hidden"));

    $("#btn-save-settings").addEventListener("click", async () => {
        try {
            await fetch("/api/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    output_dir: $("#setting-dir").value,
                    preferred_source: $("#setting-source").value,
                    quality: $("#setting-quality").value,
                    embed_lyrics: $("#setting-lyrics").checked,
                    auto_resample: $("#setting-resample").checked,
                }),
            });
            toast("Settings saved", "success");
            settingsModal.classList.add("hidden");
        } catch (e) {
            toast(e.message, "error");
        }
    });

    // ── Auto-fill download directory in tabs ─────────────────
    async function fillDownloadDir() {
        try {
            const resp = await fetch("/api/settings");
            const s = await resp.json();
            const dir = s.output_dir || "";
            if (dir) {
                if ($("#files-path") && !$("#files-path").value) $("#files-path").value = dir;
                if ($("#analysis-path") && !$("#analysis-path").value) $("#analysis-path").value = dir;
                if ($("#resample-dir") && !$("#resample-dir").value) $("#resample-dir").value = dir;
            }
        } catch (e) { /* ignore */ }
    }
    fillDownloadDir();
    refreshResampleSchedules();

    // Refresh schedules when returning to the app while the Resample page is open.
    window.addEventListener("focus", () => {
        if ($("#page-resample").classList.contains("active")) refreshResampleSchedules();
    });

    // ── Helpers ──────────────────────────────────────────────
    function showLoading() {
        hideAll();
        if (searchFilters) searchFilters.classList.remove("hidden");
        loading.classList.remove("hidden");
    }

    function showEmpty() {
        hideAll();
        if (searchFilters) searchFilters.classList.add("hidden");
        empty.classList.remove("hidden");
    }
    function hideAll() {
        $("#search-results").classList.add("hidden");
        $("#resolve-results").classList.add("hidden");
        loading.classList.add("hidden");
        empty.classList.add("hidden");
    }

    function fmtDuration(ms) {
        if (!ms) return "";
        const s = Math.round(ms / 1000);
        const m = Math.floor(s / 60);
        return `${m}:${String(s % 60).padStart(2, "0")}`;
    }

    function fmtSize(bytes) {
        if (!bytes) return "0 B";
        const units = ["B", "KB", "MB", "GB"];
        let i = 0;
        let size = bytes;
        while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
        return `${size.toFixed(i ? 1 : 0)} ${units[i]}`;
    }

    function esc(str) {
        if (!str) return "";
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML.replace(/'/g, "&#39;").replace(/"/g, "&quot;");
    }

    function escJs(str) {
        // Safe for single-quoted JS string literals inside HTML attributes.
        return esc(str).replace(/&#39;/g, "\\'").replace(/\n/g, "\\n").replace(/\r/g, "");
    }

    function serviceBadge(name) {
        if (!name) return "";
        return `<span class="service-badge">${esc(name)}</span>`;
    }

    function toast(msg, type = "") {
        const el = document.createElement("div");
        el.className = `toast ${type}`;
        el.textContent = msg;
        $("#toast-container").appendChild(el);
        setTimeout(() => el.remove(), 4000);
    }

    // ── Preview Player ────────────────────────────────────────
    let _currentPreviewBtn = null;
    window.sfTogglePreview = function (url, btn) {
        const player = document.getElementById("preview-player");
        if (!url || !player) return;

        // If same track is playing, pause it
        if (_currentPreviewBtn === btn && !player.paused) {
            player.pause();
            btn.classList.remove("playing", "loading");
            btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
            _currentPreviewBtn = null;
            return;
        }

        // Stop previous track
        if (_currentPreviewBtn && _currentPreviewBtn !== btn) {
            _currentPreviewBtn.classList.remove("playing", "loading");
            _currentPreviewBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
        }

        // Show loading state while buffering
        btn.classList.remove("playing");
        btn.classList.add("loading");
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="20" stroke-dashoffset="10"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="1s" repeatCount="indefinite"/></circle></svg>';
        _currentPreviewBtn = btn;
        player.src = url;

        player.oncanplay = () => {
            player.play().then(() => {
                btn.classList.remove("loading");
                btn.classList.add("playing");
                btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
            }).catch(() => {
                btn.classList.remove("loading", "playing");
                btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
                _currentPreviewBtn = null;
                toast("Preview unavailable — try again later", "error");
            });
        };

        player.onerror = () => {
            btn.classList.remove("loading", "playing");
            btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
            _currentPreviewBtn = null;
            toast("Preview unavailable — try again later", "error");
        };

        player.onended = () => {
            btn.classList.remove("loading", "playing");
            btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
            _currentPreviewBtn = null;
        };
    };

    function previewBtn(previewUrl) {
        if (!previewUrl) return "";
        return `<button class="btn-preview" onclick="window.sfTogglePreview('${escJs(previewUrl)}', this)" title="Preview"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>`;
    }

    function _watchSentinel(sentinelId, callback) {
        const old = _sectionObservers.get(sentinelId);
        if (old) { old.disconnect(); _sectionObservers.delete(sentinelId); }
        const el = $(`#${sentinelId}`);
        if (!el) return;
        const obs = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting) callback();
        }, { rootMargin: '300px 0px' });
        obs.observe(el);
        _sectionObservers.set(sentinelId, obs);
    }

    function _unwatchSentinel(sentinelId) {
        const old = _sectionObservers.get(sentinelId);
        if (old) { old.disconnect(); _sectionObservers.delete(sentinelId); }
    }

    function _ensureSearchSentinel() {
        if (!searchPagination.hasMore || searchPagination.loading) return;
        if (!_isSectionEnabled("tracks")) return;
        const section = $("#tracks-results-section");
        if (!section) return;

        _unwatchSentinel("search-tracks-sentinel");
        const existing = $("#search-tracks-sentinel");
        if (existing) existing.remove();

        const sentinel = document.createElement("div");
        sentinel.id = "search-tracks-sentinel";
        sentinel.className = "load-sentinel";
        sentinel.textContent = "Scroll to load more tracks";
        section.appendChild(sentinel);
        _watchSentinel("search-tracks-sentinel", loadMoreSearch);
    }

    let _searchScrollTicking = false;
    window.addEventListener("scroll", () => {
        if (_searchScrollTicking) return;
        _searchScrollTicking = true;
        requestAnimationFrame(() => {
            _searchScrollTicking = false;
            if (!searchPagination.hasMore || searchPagination.loading) return;
            const sentinel = $("#search-tracks-sentinel");
            if (!sentinel) return;
            const rect = sentinel.getBoundingClientRect();
            if (rect.top <= (window.innerHeight + 300)) {
                loadMoreSearch();
            }
        });
    }, { passive: true });

    // Init
    updateQueueBadge();
    // Load history on init if history page is active
    if ($("#page-history").classList.contains("active")) loadHistory();
})();
