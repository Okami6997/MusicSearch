/**
 * SongsFetch - Self-Hosted Cloudflare Worker Proxy
 * ------------------------------------------------
 * This worker acts as your personal edge gateway to proxy streaming queries 
 * and direct decryption/download tasks to Tidal, Deezer, Qobuz, and Spotify.
 * 
 * Hosting on Cloudflare Workers is free (up to 100k requests/day), runs on clean 
 * IP pools, and can bypass anti-bot and regional locks.
 *
 * HOW TO DEPLOY:
 * 1. Sign up/login to https://dash.cloudflare.com/ (Free account).
 * 2. Go to "Workers & Pages" -> "Create application" -> "Create Worker".
 * 3. Set a name (e.g., "my-music-proxy") and click Deploy.
 * 4. Click "Edit Code", replace the default code with this file's contents, and click "Save and Deploy".
 * 5. Set your custom environment variables if you have personal credentials (e.g., DEEZER_ARL).
 * 6. Copy your Worker's URL (e.g., https://my-music-proxy.yourname.workers.dev) and set it in your SongsFetch configuration!
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const targetService = url.searchParams.get("service");
    const trackId = url.searchParams.get("id");
    const quality = url.searchParams.get("quality") || "LOSSLESS";

    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, HEAD, POST, OPTIONS",
      "Access-Control-Allow-Headers": "*",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    if (!targetService || !trackId) {
      return new Response(
        JSON.stringify({ 
          status: "healthy",
          message: "SongsFetch Edge Proxy is online! Configured services: Deezer, Tidal, Qobuz, Spotify"
        }), 
        { 
          status: 200, 
          headers: { "Content-Type": "application/json", ...corsHeaders } 
        }
      );
    }

    // ── DEEZER HANDLER ─────────────────────────────────────────
    if (targetService === "deezer") {
      const arl = env.DEEZER_ARL || ""; // Personal ARL cookie
      if (arl) {
        // Direct premium Session call using user's own cookie session!
        const targetUrl = `https://api.deezer.com/1.0/track/${trackId}`;
        const response = await fetch(targetUrl, {
          headers: {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Cookie": `arl=${arl}`
          }
        });
        const resText = await response.text();
        return new Response(resText, { headers: { "Content-Type": "application/json", ...corsHeaders } });
      }

      // Fallback: request via public Deezer endpoint with custom referrer
      const targetUrl = `https://api.deezmate.com/dl/${trackId}`;
      const response = await fetch(targetUrl, {
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
          "Referer": "https://deezmate.com/"
        }
      });
      return new Response(response.body, { status: response.status, headers: { ...response.headers, ...corsHeaders } });
    }

    // ── TIDAL HANDLER ──────────────────────────────────────────
    if (targetService === "tidal") {
      // Survive on surviving public Tidal APIs
      const fallbackApis = [
        "https://monochrome-api.samidy.com",
        "https://tidal.kinoplus.online",
        "https://triton.squid.wtf",
        "https://eu-central.monochrome.tf",
        "https://api.monochrome.tf",
      ];
      
      for (const api of fallbackApis) {
        try {
          const targetUrl = `${api}/track/?id=${trackId}&quality=${quality}`;
          const response = await fetch(targetUrl, {
            headers: { 
              "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" 
            },
            signal: AbortSignal.timeout(5000)
          });
          if (response.ok) {
            const data = await response.json();
            return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json", ...corsHeaders } });
          }
        } catch (e) {
          continue;
        }
      }
      return new Response(JSON.stringify({ error: "All fallback Tidal API proxies failed" }), { status: 502, headers: corsHeaders });
    }

    // ── QOBUZ HANDLER ──────────────────────────────────────────
    if (targetService === "qobuz") {
      const fallbackApis = [
        `https://qobuz.squid.wtf/api/download-music?country=US&track_id=${trackId}`,
        `https://dl.musicdl.me/qobuz/download?trackId=${trackId}`,
        `https://api.zarz.moe/dl/qbz?trackId=${trackId}`,
      ];

      for (const targetUrl of fallbackApis) {
        try {
          const response = await fetch(targetUrl, { signal: AbortSignal.timeout(5000) });
          if (response.ok) {
            const data = await response.json();
            return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json", ...corsHeaders } });
          }
        } catch (e) {
          continue;
        }
      }
      return new Response(JSON.stringify({ error: "All fallback Qobuz API proxies failed" }), { status: 502, headers: corsHeaders });
    }

    // ── SPOTIFY HANDLER ──────────────────────────────────────────
    if (targetService === "spotify") {
      // Proxies spotidownloader API to bypass Client IP limits and local blocklists
      const targetUrl = `https://api.spotidownloader.com/download`;
      // We can pass requests to other bypass nodes
      try {
        const response = await fetch(targetUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Origin": "https://spotidownloader.com",
            "Referer": "https://spotidownloader.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          },
          body: JSON.stringify({ id: trackId, flac: true })
        });
        const data = await response.text();
        return new Response(data, { status: response.status, headers: { "Content-Type": "application/json", ...corsHeaders } });
      } catch (e) {
        return new Response(JSON.stringify({ error: e.message }), { status: 502, headers: corsHeaders });
      }
    }

    return new Response(JSON.stringify({ error: "Unsupported service request" }), { status: 400, headers: corsHeaders });
  }
}
