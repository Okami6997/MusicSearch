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
            $$(".page").forEach((p) => p.classList.add("hidden"));
            tab.classList.add("active");
            $(`#page-${tab.dataset.page}`).classList.remove("hidden");
            if (tab.dataset.page === "history") loadHistory();
        });
    });

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
            renderSearchResults(data.tracks || []);
        } catch (e) {
            toast(e.message, "error");
            showEmpty();
        }
    }

    function renderSearchResults(tracks) {
        hideAll();
        const results = $("#search-results");
        const list = $("#tracks-list");
        if (!tracks.length) {
            list.innerHTML = '<p class="text-muted">No results found.</p>';
            results.classList.remove("hidden");
            return;
        }
        results.classList.remove("hidden");
        list.innerHTML = tracks.map((t) => `
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
                    <button class="btn-dl" onclick="window.sfDownload('','${esc(t.isrc || '')}','${esc(t.title)}','${esc(t.artist)}','${esc(t.album || '')}','${esc(t.cover_url || '')}',${t.duration_ms || 0})">Download</button>
                </div>
            </div>
        `).join("");
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
            { key: "amazon", name: "Amazon Music", url: data.amazon_url },
            { key: "qobuz", name: "Qobuz", available: data.qobuz },
            { key: "deezer", name: "Deezer", url: data.deezer_url },
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
        // Download button — use tidal_url or amazon_url or isrc
        const dlUrl = data.tidal_url || data.amazon_url || "";
        if (dlUrl || isrc) {
            actionsHtml += `<button class="btn-primary" style="margin-top:12px" onclick="window.sfDownload('${esc(dlUrl)}','${esc(isrc)}','','','','',0)">Download Track</button>`;
        }
        $("#resolve-actions").innerHTML = actionsHtml;
    }

    // ── Download ────────────────────────────────────────────
    window.sfDownload = async function (url, isrc, title, artist, album, cover, durationMs) {
        try {
            const resp = await fetch("/api/download", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, isrc, title, artist, album, cover_url: cover, duration_ms: durationMs }),
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
        list.innerHTML = files.map((f) => `
            <div class="file-row" onclick="window.sfShowFileMeta('${esc(f.path)}')">
                <span class="file-icon">♪</span>
                <span class="file-name">${esc(f.name)}</span>
                <span class="file-size">${fmtSize(f.size)}</span>
            </div>
        `).join("");
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
        if (!loadedAudioFiles.length) { toast("Load files first", "error"); return; }
        const fmt = $("#rename-format").value.trim();
        try {
            const resp = await fetch("/api/files/rename/preview", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ files: loadedAudioFiles, format: fmt }),
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
        if (!loadedAudioFiles.length) return;
        const fmt = $("#rename-format").value.trim();
        try {
            const resp = await fetch("/api/files/rename", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ files: loadedAudioFiles, format: fmt }),
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
    $("#btn-analyze").addEventListener("click", doAnalyze);

    async function doAnalyze() {
        const path = $("#analysis-path").value.trim();
        if (!path) return;
        try {
            const resp = await fetch("/api/analysis", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path }),
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            const el = $("#analysis-result");
            el.classList.remove("hidden");
            el.innerHTML = `
                <div class="analysis-card">
                    <h3>${esc(data.file_name)}</h3>
                    <div class="meta-grid">
                        <div><strong>Codec:</strong> ${esc(data.codec)}</div>
                        <div><strong>Sample Rate:</strong> ${data.sample_rate ? data.sample_rate + ' Hz' : '-'}</div>
                        <div><strong>Bit Depth:</strong> ${esc(data.bit_depth)}</div>
                        <div><strong>Channels:</strong> ${data.channels}</div>
                        <div><strong>Duration:</strong> ${data.duration ? data.duration + 's' : '-'}</div>
                        <div><strong>Bit Rate:</strong> ${data.bit_rate ? Math.round(data.bit_rate / 1000) + ' kbps' : '-'}</div>
                        <div><strong>File Size:</strong> ${fmtSize(data.file_size)}</div>
                    </div>
                </div>`;
        } catch (e) {
            toast(e.message, "error");
        }
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
        loadHistory();
    });

    async function loadHistory() {
        try {
            const resp = await fetch("/api/history/downloads");
            const items = await resp.json();
            const el = $("#history-list");
            if (!items.length) {
                el.innerHTML = '<p class="text-muted">No downloads yet.</p>';
                return;
            }
            el.innerHTML = items.map((h) => `
                <div class="track-row">
                    <img class="track-cover" src="${esc(h.cover_url || '')}" alt=""
                         onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2248%22 height=%2248%22><rect fill=%22%23262626%22 width=%2248%22 height=%2248%22/></svg>'">
                    <div class="track-info">
                        <div class="track-title">${esc(h.title || h.url || 'Unknown')}</div>
                        <div class="track-artist">${esc(h.artist)}${h.source ? ' · via ' + esc(h.source) : ''}</div>
                    </div>
                    <span class="track-duration">${esc(h.quality)} ${esc(h.format)}</span>
                </div>
            `).join("");
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
        if (data.status === "completed") toast(`"${data.title || data.url}" downloaded`, "success");
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
})();
