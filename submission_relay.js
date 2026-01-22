
/**
 * ORION GHOST RELAY (Leaderboard Worker)
 * --------------------------------------
 * Handles secure submission and caching for the Orion Leaderboard.
 * Deploy to Cloudflare Workers.
 * 
 * Env Vars required in Cloudflare:
 * - GITHUB_TOKEN: PAT with repo scope
 * - GITHUB_REPO: owner/repo
 * - SALT_KEY: Matches client key
 */

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

// Hardcoded for the Free Tier example, but ideally use Env Vars
const SALT_KEY = "ORION_OMEGA_PROTOCOL_X9_SECURE_HASH_V1"; 
const REPO_OWNER = "RookieEnough";
const REPO_NAME = "Orion-Data";
const BRANCH = "data";
const LEADERBOARD_FILE = "leaderboard.json";

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS_HEADERS });
    }

    const url = new URL(request.url);

    // --- READ (GET) ---
    if (request.method === "GET") {
        const cache = caches.default;
        const cacheKey = new Request(url.toString(), request);
        let response = await cache.match(cacheKey);

        if (!response) {
            // Fetch from GitHub Raw
            const ghUrl = `https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${BRANCH}/${LEADERBOARD_FILE}`;
            const ghRes = await fetch(ghUrl);
            
            if (!ghRes.ok) {
                return new Response(JSON.stringify([]), { headers: CORS_HEADERS }); // Fallback empty
            }

            response = new Response(ghRes.body, ghRes);
            response.headers.set('Content-Type', 'application/json');
            response.headers.set('Cache-Control', 'public, max-age=3600'); // 1 HOUR CACHE
            response.headers.set('Access-Control-Allow-Origin', '*');
            
            ctx.waitUntil(cache.put(cacheKey, response.clone()));
        }
        return response;
    }

    // --- WRITE (POST) ---
    if (request.method === "POST") {
        try {
            const body = await request.json();
            const { data, signature } = body;

            // 1. Rate Limit (Simple KV Check)
            const clientIP = request.headers.get("CF-Connecting-IP");
            const kvKey = `limit_${clientIP}`;
            
            // Note: env.ORION_KV must be bound in Cloudflare Dashboard
            if (env.ORION_KV) {
                const existing = await env.ORION_KV.get(kvKey);
                if (existing) {
                    return new Response(JSON.stringify({ error: "Daily limit reached." }), { status: 429, headers: CORS_HEADERS });
                }
                // Lock for 24 hours
                await env.ORION_KV.put(kvKey, "1", { expirationTtl: 86400 });
            }

            // 2. Validate Signature
            const sortedKeys = Object.keys(data).sort();
            const message = sortedKeys.map(key => `${key}:${data[key]}`).join('|') + SALT_KEY;
            const msgBuffer = new TextEncoder().encode(message);
            const hashBuffer = await crypto.subtle.digest('SHA-256', msgBuffer);
            const hashArray = Array.from(new Uint8Array(hashBuffer));
            const calculatedSig = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');

            if (calculatedSig !== signature) {
                return new Response(JSON.stringify({ error: "Security check failed." }), { status: 403, headers: CORS_HEADERS });
            }

            // 3. Create GitHub Issue
            // We use the Issue body to store the JSON payload
            const issueTitle = `Leaderboard Submission: ${data.username}`;
            const issueBody = `
### Leaderboard Submission
\`\`\`json
${JSON.stringify(data, null, 2)}
\`\`\`
*Verified via Ghost Relay*
            `.trim();

            const ghRes = await fetch(`https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/issues`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${env.GITHUB_TOKEN}`,
                    'User-Agent': 'Orion-Ghost-Relay',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ title: issueTitle, body: issueBody })
            });

            if (!ghRes.ok) {
                throw new Error("Failed to create record.");
            }

            return new Response(JSON.stringify({ success: true }), { headers: CORS_HEADERS });

        } catch (e) {
            return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: CORS_HEADERS });
        }
    }

    return new Response("Method Not Allowed", { status: 405, headers: CORS_HEADERS });
  },
};
