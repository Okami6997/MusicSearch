/* SongsFetch - Frontend Application */

(function () {
    "use strict";

    // ── State ───────────────────────────────────────────────
    let queueData = [];
    let loadedAudioFiles = [];
    let resampleFiles = [];
    const socket = io();

    // ── DOM refs ────────────────────────────────────────────
    const $ = (s) => document.querySelector(s);
    const $$ = (s) => document.querySelectorAll(s);

    const searchInput = $("#search-input");
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

    async function doSearch() {
        const q = searchInput.value.trim();
        if (!q) return;
        showLoading();
        try {
            const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            renderSearchResults(data.tracks || [], data.artists || [], data.albums || [], data.youtube_tracks || []);
        } catch (e) {
            toast(e.message, "error");
            showEmpty();
        }
    }

    function renderSearchResults(tracks, artists, albums, youtubeTracks) {
        hideAll();
        const results = $("#search-results");
        const list = $("#tracks-list");
        if (!tracks.length && !artists.length && !albums.length && !youtubeTracks.length) {
            list.innerHTML = '<p class="text-muted">No results found.</p>';
            results.classList.remove("hidden");
            return;
        }
        results.classList.remove("hidden");
        let html = "";

        // Artists section
        if (artists.length) {
            html += '<div class="results-section"><h3 class="results-heading">Artists</h3>';
            html += artists.map((a) => `
                <div class="track-row artist-row">
                    <img class="track-cover" src="${esc(a.image_url || '')}" alt="" loading="lazy" style="border-radius:50%"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22 rx=%2224%22/></svg>'">
                    <div class="track-info">
                        <div class="track-title">${esc(a.name)}</div>
                        <div class="track-artist">${a.albums_count ? a.albums_count + ' albums' : 'Artist'}</div>
                    </div>
                </div>
            `).join("");
            html += '</div>';
        }

        // Albums section
        if (albums.length) {
            html += '<div class="results-section"><h3 class="results-heading">Albums</h3>';
            html += albums.map((al) => `
                <div class="track-row album-row">
                    <img class="track-cover" src="${esc(al.cover_url || '')}" alt="" loading="lazy"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                    <div class="track-info">
                        <div class="track-title">${esc(al.title)}</div>
                        <div class="track-artist">${esc(al.artist)}${al.tracks_count ? ' · ' + al.tracks_count + ' tracks' : ''}${al.release_date ? ' · ' + esc(al.release_date.substring(0, 4)) : ''}</div>
                        ${al.hires ? '<div class="track-meta">Hi-Res</div>' : ''}
                    </div>
                </div>
            `).join("");
            html += '</div>';
        }

        // Tracks section
        if (tracks.length) {
            html += '<div class="results-section"><h3 class="results-heading">Tracks</h3>';
            html += tracks.map((t) => `
                <div class="track-row">
                    <img class="track-cover" src="${esc(t.cover_url || '')}" alt="" loading="lazy"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                    <div class="track-info">
                        <div class="track-title">${esc(t.title)}</div>
                        <div class="track-artist">${esc(t.artist)}${t.album ? ' · ' + esc(t.album) : ''}</div>
                        <div class="track-meta">${t.isrc ? 'ISRC: ' + esc(t.isrc) : ''}${t.hires ? ' · Hi-Res' : ''}${t.sample_rate ? ' · ' + t.sample_rate + 'kHz/' + t.bit_depth + 'bit' : ''}</div>
                    </div>
                    <span class="track-duration">${fmtDuration(t.duration_ms)}</span>
                    <div class="track-actions">
                        <button class="btn-dl" onclick="window.sfDownload('','${esc(t.isrc || '')}','${esc(t.title)}','${esc(t.artist)}','${esc(t.album || '')}','${esc(t.cover_url || '')}',${t.duration_ms || 0},${t.track_number || 0},${t.total_tracks || 0},${t.disc_number || 0})">Download</button>
                    </div>
                </div>
            `).join("");
            html += '</div>';
        }

        // YouTube Music section
        if (youtubeTracks.length) {
            html += '<div class="results-section"><h3 class="results-heading">YouTube Music</h3>';
            html += youtubeTracks.map((t) => `
                <div class="track-row">
                    <img class="track-cover" src="${esc(t.cover_url || '')}" alt="" loading="lazy"
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                    <div class="track-info">
                        <div class="track-title">${esc(t.title || t.id)}</div>
                        <div class="track-artist">${esc(t.artist)}${t.album ? ' · ' + esc(t.album) : ''}</div>
                        <div class="track-meta">YouTube Music · MP3 320kbps</div>
                    </div>
                    <span class="track-duration">${fmtDuration(t.duration_ms)}</span>
                    <div class="track-actions">
                        <button class="btn-dl" onclick="window.sfDownload('${esc(t.url || '')}','','${esc(t.title || '')}','${esc(t.artist || '')}','${esc(t.album || '')}','${esc(t.cover_url || '')}',${t.duration_ms || 0},0,0,0)">Download</button>
                    </div>
                </div>
            `).join("");
            html += '</div>';
        }

        list.innerHTML = html;
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
        let actionsHtml = '';
        if (isrc) {
            actionsHtml += `<div class="info-row"><strong>ISRC:</strong> ${esc(isrc)}</div>`;
        }
        // Download button — use the best available URL or isrc
        const dlUrl = data.tidal_url || data.spotify_url || data.amazon_url || data.youtube_url || "";
        if (dlUrl || isrc) {
            actionsHtml += `<button class="btn-primary" style="margin-top:12px" onclick="window.sfDownload('${esc(dlUrl)}','${esc(isrc)}','','','','',0)">Download Track</button>`;
        }
        $("#resolve-actions").innerHTML = actionsHtml;
    }

    // ── Download ────────────────────────────────────────────
    window.sfDownload = async function (url, isrc, title, artist, album, cover, durationMs, trackNumber, totalTracks, discNumber) {
        try {
            const resp = await fetch("/api/download", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, isrc, title, artist, album, cover_url: cover, duration_ms: durationMs, track_number: trackNumber || 0, total_tracks: totalTracks || 0, disc_number: discNumber || 0 }),
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            toast(`"${title || url || isrc}" added to queue`, "success");
            updateQueueBadge();
        } catch (e) {
            toast(e.message, "error");
        }
    };

    // ── File Manager ────────────────────────────────────────
    $("#btn-load-files").addEventListener("click", loadFiles);
    $("#btn-preview-rename").addEventListener("click", previewRename);
    $("#btn-rename").addEventListener("click", doRename);

    async function loadFiles() {
        const path = $("#files-path").value.trim();
        if (!path) return;
        try {
            const resp = await fetch(`/api/files/audio?path=${encodeURIComponent(path)}`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            loadedAudioFiles = data.map((f) => f.path);
            renderFilesList(data);
        } catch (e) {
            toast(e.message, "error");
        }
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
        `).join("");

        // Select-all toggle
        $("#select-all-files").addEventListener("change", (e) => {
            $$(".file-check").forEach((cb) => cb.checked = e.target.checked);
        });
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

    // ── Analysis ────────────────────────────────────────────
    let analysisFiles = [];
    $("#btn-load-analysis").addEventListener("click", loadAnalysisFiles);
    $("#btn-analyze-selected").addEventListener("click", analyzeSelectedFiles);

    async function loadAnalysisFiles() {
        const path = $("#analysis-path").value.trim();
        if (!path) return;
        try {
            const resp = await fetch(`/api/files/audio?path=${encodeURIComponent(path)}`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            analysisFiles = data.map((f) => f.path);
            renderAnalysisFiles(data);
        } catch (e) {
            toast(e.message, "error");
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
        `).join("");

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

    async function loadResampleFiles() {
        const dir = $("#resample-dir").value.trim();
        if (!dir) return;
        try {
            const resp = await fetch(`/api/files/audio?path=${encodeURIComponent(dir)}`);
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            resampleFiles = data.map((f) => f.path);
            // Get info
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
            `).join("");
            $("#btn-resample").disabled = false;
        } catch (e) {
            toast(e.message, "error");
        }
    }

    async function doResample() {
        if (!resampleFiles.length) return;
        const rate = $("#resample-rate").value;
        const bits = $("#resample-bits").value;
        if (!rate && !bits) { toast("Select sample rate or bit depth", "error"); return; }
        toast("Resampling started...", "");
        try {
            const resp = await fetch("/api/resample", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ files: resampleFiles, sample_rate: rate, bit_depth: bits }),
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const success = data.filter((r) => r.success).length;
            const el = $("#resample-result");
            el.classList.remove("hidden");
            el.innerHTML = `<p class="text-muted">Resampled ${success}/${data.length} files successfully.</p>`;
            toast(`Resampled ${success}/${data.length} files`, "success");
        } catch (e) {
            toast(e.message, "error");
        }
    }

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
            case "downloading": return `Downloading ${Math.round(t.progress)}%`;
            case "converting": return "Converting...";
            case "embedding": return "Embedding metadata...";
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

    // ── Helpers ──────────────────────────────────────────────
    function showLoading() { hideAll(); loading.classList.remove("hidden"); }
    function showEmpty() { hideAll(); empty.classList.remove("hidden"); }
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

    function toast(msg, type = "") {
        const el = document.createElement("div");
        el.className = `toast ${type}`;
        el.textContent = msg;
        $("#toast-container").appendChild(el);
        setTimeout(() => el.remove(), 4000);
    }

    // Init
    updateQueueBadge();
    // Load history on init if history page is active
    if ($("#page-history").classList.contains("active")) loadHistory();
})();
