---
name: Future Self-Hosted Proxy Plan
about: Architectural blueprint and Cloudflare Worker templates for deploying self-hosted bypass download gateways
title: "[PLAN] Self-Hosted Bypass Proxy and Custom Gateways"
labels: enhancement, documentation
assignees: ''
---

## Deep Dive: How the SpotiFLAC Bypass Gateways Work

Developing a robust solution for third-party audio retrieval requires understanding why public, open-source gateways (such as SpotiFLAC) succeed while standard requests often trigger 403 Forbidden or 401 Unauthorized errors from Cloudflare WAF or API authorization systems.

### 1. Cloudflare WAF and Turnstile Challenges
Upstream audio download gateways enforce strict anti-bot mitigations. Generic HTTP request clients (e.g. standard Python requests) do not carry the necessary signature profiles, fingerprint arrays, or protocol handshakes required to pass.
SpotiFLAC uses specialized bypass endpoints (`api.zarz.moe` and others) combined with a signature User-Agent containing `"SpotiFLAC-Mobile/1.0"`. This signature signal acts as an application authentication pass, notifying the edge router that the request originates from an official client model.

### 2. Hashing and Auth Wrapping
For secure providers like Amazon Music, access is restricted unless accompanied by dynamic signature tokens and hardware-bound parameter maps. The `AmazonDownloader` in [backend/amazon.py](backend/amazon.py) generates a cryptographic `X-Debug-Key` using an AEAD AES-GCM cipher with a preset key constraint:
- Ciphertext Seed: `"spotiflac:amazon:spotbye:api:v1"`
- Custom Nonce Verification
- Unique AAD (Additional Authenticated Data) Payload

A self-hosted gateway acts as an intermediate middleware layer. Instead of direct client-to-stream communication, the local proxy handles the complex handshakes, session rotation, and signature injections.

---

## Architectural Blueprint for Self-Hosted Cloudflare Workers

If you want to transition off public gateways and host your own Cloudflare-backed proxy network, deploy the following Cloudflare Workers architecture. This routes request payloads, masks your server context under legitimate client headers, injects keys, and tunnels data streams.

### 1. Cloudflare Worker Code Template
Deploy this script using Wrangler or paste it directly in the Cloudflare Workers dashboard. This worker acts as a dynamic handler that rewrites inbound headers, proxies payloads to upstream endpoints, and strips security barriers.

```javascript
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    
    // Define target service pathways
    let targetUrl = "";
    const path = url.pathname;
    
    // Route traffic context dynamically to upstream endpoints
    if (path.startsWith("/v1/dl/dzr") || path.startsWith("/dl/dzr")) {
      targetUrl = `https://api.zarz.moe${path}${url.search}`;
    } else if (path.startsWith("/v1/dl/qbz") || path.startsWith("/dl/qbz")) {
      targetUrl = `https://api.zarz.moe${path}${url.search}`;
    } else if (path.startsWith("/v1/dl/tid") || path.startsWith("/dl/tid")) {
      targetUrl = `https://api.zarz.moe${path}${url.search}`;
    } else if (path.startsWith("/api/track") || path.startsWith("/track")) {
      targetUrl = `https://amz.spotbye.qzz.io${path}${url.search}`;
    } else {
      return new Response("Invalid Route Pattern", { status: 404 });
    }

    // Clone headers and inject mock application signatures
    const newHeaders = new Headers(request.headers);
    newHeaders.set("User-Agent", "SpotiFLAC-Mobile/1.0");
    newHeaders.set("Origin", "https://spotidownloader.com");
    newHeaders.set("Referer", "https://spotidownloader.com/");
    
    // Ensure CORS is supported for response routing
    newHeaders.set("Access-Control-Allow-Origin", "*");
    
    // Create the proxy request structure
    const proxyRequest = new Request(targetUrl, {
      method: request.method,
      headers: newHeaders,
      body: request.method === "POST" ? await request.clone().arrayBuffer() : null,
      redirect: "follow"
    });
    
    try {
      const response = await fetch(proxyRequest);
      
      // Inject CORS headers into the returning stream
      const modifiedHeaders = new Headers(response.headers);
      modifiedHeaders.set("Access-Control-Allow-Origin", "*");
      modifiedHeaders.set("X-Proxied-By", "Self-Hosted-Cloudflare-Worker");
      
      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: modifiedHeaders
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: err.message }), {
        status: 500,
        headers: { "Content-Type": "application/json" }
      });
    }
  }
};
```

---

## Local Integration Plan

To bind this self-hosted proxy into your application context, configure the application using environment variables:

1. Add environment configurations into [docker-compose.yml](docker-compose.yml) or environment variables:
   - `SELF_HOSTED_GATEWAY_URL` — e.g. `https://your-worker.workers.dev`

2. When `SELF_HOSTED_GATEWAY_URL` is parsed by the backend container context:
   - For Deezer ([backend/deezer.py](backend/deezer.py)): Reroute `DOWNLOAD_API` queries through `${SELF_HOSTED_GATEWAY_URL}/v1/dl/dzr`
   - For Qobuz ([backend/qobuz.py](backend/qobuz.py)): Reroute `_fetch_stream_url_once` queries through `${SELF_HOSTED_GATEWAY_URL}/v1/dl/qbz`
   - For Tidal ([backend/tidal.py](backend/tidal.py)): Reroute raw POST bodies through `${SELF_HOSTED_GATEWAY_URL}/v1/dl/tid2`
   - For Amazon Music ([backend/amazon.py](backend/amazon.py)): Reroute stream retrieval through `${SELF_HOSTED_GATEWAY_URL}/api/track`

This keeps downstream logic stable, and ensures absolute independence from changing public bypass domain availability in the future.
