const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

// --- CONFIGURATION ---
const args = process.argv.slice(2);
const getConfig = (key) => {
    const index = args.indexOf(`--${key}`);
    return index !== -1 ? args[index + 1] : null;
};

const TARGET_URL = getConfig('url');
const APP_ID = getConfig('id');
const OUTPUT_FILE = getConfig('out') || `${APP_ID}-temp.apk`;
const MAX_WAIT_MS = parseInt(getConfig('wait') || '60000', 10);

if (!TARGET_URL || !APP_ID) {
    console.error("❌ Usage: node apk_hunter.js --url <url> --id <app_id> [--wait <ms>] [--out <filename>]");
    process.exit(1);
}

const DOWNLOAD_PATH = path.resolve(__dirname, 'downloads');
if (!fs.existsSync(DOWNLOAD_PATH)) fs.mkdirSync(DOWNLOAD_PATH, { recursive: true });

// Ad Blocking & Resource Optimization
const BLOCKED_DOMAINS = [
    'googleads', 'doubleclick', 'googlesyndication', 'adservice', 'rubicon', 'criteo',
    'outbrain', 'taboola', 'adsystem', 'adnxs', 'smartadserver', 'popcash', 'popads',
    'facebook.net', 'facebook.com', 'twitter.com', 'cdn-ads'
];

const RESOURCE_BLOCK_TYPES = new Set(['image', 'media', 'font', 'stylesheet']);

// A global set used as a mutex to ensure each intercepted request is handled exactly once.
const handledRequests = new Set();

// Helper to create a small safe ID for a request object.
// Uses private props if available, else falls back to URL+method+timestamp.
const reqId = (req) => {
    // Puppeteer's CDP-backed request often has an internal _requestId — use when available.
    return req._requestId || `${req.url()}|${req.method()}`;
};

const configurePage = async (page) => {
    try {
        // prevent double configuration on the same page instance
        if (page._isConfigured) return;
        page._isConfigured = true;

        // Set download behavior via CDP
        try {
            const client = await page.target().createCDPSession();
            await client.send('Page.setDownloadBehavior', {
                behavior: 'allow',
                downloadPath: DOWNLOAD_PATH,
            });
        } catch (err) {
            // If CDP fails for some reason, keep going — downloads might still work
            console.warn("⚠️  Could not set download behavior via CDP:", err.message);
        }

        // Enable request interception
        await page.setRequestInterception(true);

        page.on('request', async (req) => {
            // If request was already handled by our mutex, drop out immediately.
            const id = reqId(req);
            if (handledRequests.has(id)) return;

            // Mark handled immediately to prevent any parallel handler attempting to handle same request.
            handledRequests.add(id);

            // Ensure we cleanup the ID eventually to avoid memory leak.
            // Requests should finish quickly; remove after a short delay.
            const cleanupTimer = setTimeout(() => handledRequests.delete(id), 7000);

            try {
                const u = req.url().toLowerCase();
                const rType = req.resourceType();

                // Block obvious ad/tracker domains
                if (BLOCKED_DOMAINS.some(d => u.includes(d))) {
                    try { await req.abort(); } catch (e) {}
                    return;
                }

                // Block heavy/static resources to speed up crawling
                if (RESOURCE_BLOCK_TYPES.has(rType)) {
                    try { await req.abort(); } catch (e) {}
                    return;
                }

                // Some requests are navigation/frame-internals; allow them.
                try {
                    await req.continue();
                } catch (err) {
                    // If continue() throws because the request was already handled by the browser,
                    // ignore it. Our mutex prevents duplicate continues in normal flow.
                }
            } catch (err) {
                // swallow — we don't want an individual request error to crash the whole script
            } finally {
                clearTimeout(cleanupTimer);
                // Keep a short grace period to ensure we don't re-handle the same id immediately,
                // but remove it to let GC / future requests reuse the same key if needed.
                setTimeout(() => handledRequests.delete(id), 1000);
            }
        });

        // Occasionally pages create many frames/requests — protect against console spam
        page.on('console', (msg) => {
            // Keep important console logs; silence verbose ones.
            const text = msg.text();
            if (/Error|Warning|download/i.test(text)) {
                console.log(`PAGE LOG: ${text}`);
            }
        });

        // Close suspiciously blank pages soon after they open
        page.on('domcontentloaded', async () => {
            try {
                if (!page.isClosed()) {
                    const url = page.url();
                    if (url === 'about:blank') {
                        setTimeout(async () => {
                            if (!page.isClosed() && page.url() === 'about:blank') {
                                try { await page.close(); } catch(e) {}
                            }
                        }, 1500);
                    }
                }
            } catch (e) {}
        });
    } catch (err) {
        // ignore errors on closed pages or if configuration fails
    }
};

(async () => {
    console.log(`\n🕷️  Starting Smart APK Hunter for: ${APP_ID}`);
    console.log(`🔗  Target: ${TARGET_URL}`);

    // Global handlers to show useful info if something goes wrong.
    process.on('unhandledRejection', (reason) => {
        console.error('Unhandled Rejection:', reason && reason.stack ? reason.stack : reason);
    });

    process.on('uncaughtException', (err) => {
        console.error('Uncaught Exception:', err && err.stack ? err.stack : err);
        process.exit(1);
    });

    const browser = await puppeteer.launch({
        headless: "new",
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-features=site-per-process',
            '--window-size=1280,800',
            '--disable-popup-blocking',
            '--disable-background-networking'
        ]
    });

    // Strengthened popup/tab handling
    browser.on('targetcreated', async (target) => {
        try {
            if (target.type() === 'page') {
                const newPage = await target.page();
                if (newPage) {
                    // Bypass CSP might help pages that inject many dynamic scripts
                    try { await newPage.setBypassCSP(true); } catch(e){}
                    await configurePage(newPage);

                    // Close immediately if blank and stays blank
                    setTimeout(async () => {
                        try {
                            if (!newPage.isClosed() && (newPage.url() === 'about:blank' || newPage.url().length === 0)) {
                                await newPage.close();
                            }
                        } catch (e) {}
                    }, 2500);
                }
            }
        } catch (e) {}
    });

    const page = await browser.newPage();
    await configurePage(page);

    try {
        await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');
        await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
    } catch (e) {
        console.error("❌  Initial Navigation failed:", e.message || e);
        await browser.close();
        process.exit(1);
    }

    const startTime = Date.now();
    let fileFound = null;
    const clickedHistory = new Set();

    console.log("🔄  Entering Hunter Loop...");

    // === MAIN LOOP ===
    while (Date.now() - startTime < MAX_WAIT_MS + 30000) {

        // 1. Check File System for completed download files
        try {
            const files = fs.readdirSync(DOWNLOAD_PATH);
            const apk = files.find(f => f.toLowerCase().endsWith('.apk'));
            const crdownload = files.find(f => f.toLowerCase().endsWith('.crdownload') || f.toLowerCase().endsWith('.part'));

            if (apk) {
                const stats = fs.statSync(path.join(DOWNLOAD_PATH, apk));
                if (stats.size > 0) {
                    fileFound = path.join(DOWNLOAD_PATH, apk);
                    console.log(`\n✅  File detected: ${apk}`);
                    break;
                }
            }
            if (crdownload) {
                process.stdout.write("Dl."); // Downloading indicator
                await new Promise(r => setTimeout(r, 2000));
                continue;
            }
        } catch (err) {
            // ignore FS errors
        }

        // 2. Scan All Pages for potential download buttons and click
        const pages = await browser.pages();
        let actionTaken = false;

        for (const p of pages) {
            if (p.isClosed()) continue;

            try {
                // Evaluate page to pick the best element to click (same scoring engine)
                const decision = await p.evaluate(() => {
                    const buttons = Array.from(document.querySelectorAll('a, button, div[role="button"], span, input[type="button"], input[type="submit"]'));
                    const isVisible = (el) => {
                        const style = window.getComputedStyle(el);
                        return style && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && el.offsetWidth > 0 && el.offsetHeight > 0;
                    };

                    let bestEl = null;
                    let highestScore = -9999;
                    let debugText = "";

                    for (let el of buttons) {
                        if (!isVisible(el)) continue;
                        let text = (el.innerText || el.value || el.getAttribute('aria-label') || "").toLowerCase().replace(/\s+/g, ' ').trim();
                        if (!text || text.length < 2) continue;

                        let score = 0;

                        // Kill words
                        if (text.includes('ad') || text.includes('sponsored') || text.includes('facebook') || text.includes('twitter')) continue;
                        if (text.includes('login') || text.includes('signup') || text.includes('register')) continue;

                        // Waiting words
                        if (text.includes('generating') || text.includes('please wait') || text.includes('seconds')) {
                            if (text.includes('download') || text.includes('link')) {
                                return { action: 'WAITING', text: text.substring(0, 60) };
                            }
                            continue;
                        }

                        if (text.includes('download')) score += 80;
                        if (text === 'download' || text === 'download apk') score += 40;
                        if (text.includes('direct download')) score += 60;
                        if (text.length > 70) score -= 20;
                        if (text.includes('premium') || text.includes('manager')) score -= 80;

                        if (score > highestScore) {
                            highestScore = score;
                            bestEl = el;
                            debugText = text;
                        }
                    }

                    if (bestEl && highestScore > 30) {
                        bestEl.click();
                        return { action: 'CLICKED', text: debugText };
                    }
                    return { action: 'NONE' };
                });

                if (!decision) continue;

                if (decision.action === 'WAITING') {
                    console.log(`\n⏳  Countdown detected on [${p.url().slice(0,60)}...]: "${decision.text}". Waiting...`);
                    actionTaken = true;
                    await new Promise(r => setTimeout(r, 2000));
                    break;
                } else if (decision.action === 'CLICKED') {
                    const key = `${p.url()}|${decision.text}`;
                    if (!clickedHistory.has(key)) {
                        console.log(`\n🎯  Clicked [${p.url().slice(0,60)}...]: "${decision.text}"`);
                        clickedHistory.add(key);
                        setTimeout(() => clickedHistory.delete(key), 15000);
                        actionTaken = true;
                        // Give UI time to react and potentially start a download
                        await new Promise(r => setTimeout(r, 3500));
                        break;
                    }
                }
            } catch (e) {
                // page might be navigating/closing - ignore
            }
        }

        if (!actionTaken) {
            process.stdout.write(".");
            await new Promise(r => setTimeout(r, 1000));
        }
    }

    // Finalize
    if (fileFound) {
        try {
            fs.renameSync(fileFound, OUTPUT_FILE);
            console.log(`\n🎉  Success! Downloaded to ${OUTPUT_FILE}`);
            await browser.close();
            process.exit(0);
        } catch (err) {
            console.error("❌  Could not move downloaded file:", err.message || err);
            await browser.close();
            process.exit(1);
        }
    } else {
        console.error("\n❌  Timed out. Smart Hunter failed to download.");
        await browser.close();
        process.exit(1);
    }
})();
