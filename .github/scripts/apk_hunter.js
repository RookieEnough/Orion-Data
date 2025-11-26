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
const OUTPUT_FILE = getConfig('out') || `${APP_ID || 'app'}.apk`;
const MAX_WAIT_MS = parseInt(getConfig('wait') || '120000', 10); // 2min default

if (!TARGET_URL || !APP_ID) {
    console.error("Usage: node apk_hunter.js --url <url> --id <app_id> [--wait <ms>] [--out <filename>]");
    process.exit(1);
}

const DOWNLOAD_PATH = path.resolve(__dirname, 'downloads');
if (!fs.existsSync(DOWNLOAD_PATH)) fs.mkdirSync(DOWNLOAD_PATH, { recursive: true });

// --- BLOCKED DOMAINS & RESOURCES ---
const BLOCKED_DOMAINS = [
    'googleads', 'doubleclick', 'googlesyndication', 'adservice', 'rubicon', 'criteo',
    'outbrain', 'taboola', 'adsystem', 'adnxs', 'smartadserver', 'popcash', 'popads'
];

const configurePage = async (page) => {
    if (page._configured) return;
    page._configured = true;

    // Enable downloads
    const client = await page.target().createCDPSession();
    await client.send('Page.setDownloadBehavior', {
        behavior: 'allow',
        downloadPath: DOWNLOAD_PATH,
    });

    // Request interception — NO async/await inside!
    await page.setRequestInterception(true);
    page.on('request', (req) => {
        if (req.isInterceptResolutionHandled()) return;

        const url = req.url().toLowerCase();
        const type = req.resourceType();

        if (BLOCKED_DOMAINS.some(d => url.includes(d))) {
            return void req.abort().catch(() => {});
        }
        if (['image', 'media', 'font', 'stylesheet', 'imageset'].includes(type)) {
            return void req.abort().catch(() => {});
        }

        req.continue().catch(() => {});
    });
};

(async () => {
    console.log(`\nStarting Smart APK Hunter for: ${APP_ID}`);
    console.log(`Target: ${TARGET_URL}\n`);

    const browser = await puppeteer.launch({
        headless: "new",
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-features=site-per-process',
            '--disable-web-security',
            '--disable-popup-blocking',
            '--window-size=1280,800'
        ],
        defaultViewport: null
    });

    // Auto-configure new pages (popups)
    browser.on('targetcreated', async (target) => {
        if (target.type() === 'page') {
            try {
                const newPage = await target.page();
                if (newPage && newPage.url() !== 'about:blank') {
                    await configurePage(newPage);
                    console.log(`New popup detected: ${newPage.url().substring(0, 50)}`);
                }
            } catch (e) {}
        }
    });

    const page = await browser.newPage();
    await configurePage(page);
    await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36');

    try {
        await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
        console.log("Page loaded. Scrolling to reveal download section...");
        // CRITICAL: Scroll to bottom to load "Direct Download"
        await page.evaluate(async () => {
            await new Promise(resolve => {
                let totalHeight = 0;
                const distance = 100;
                const timer = setInterval(() => {
                    const scrollHeight = document.body.scrollHeight;
                    window.scrollBy(0, distance);
                    totalHeight += distance;
                    if (totalHeight >= scrollHeight) {
                        clearInterval(timer);
                        resolve();
                    }
                }, 100);
            });
        });
        await new Promise(r => setTimeout(r, 2000)); // Stabilize
    } catch (e) {
        console.error("Failed to load/scroll page:", e.message);
        await browser.close();
        process.exit(1);
    }

    const startTime = Date.now();
    let downloadedFile = null;
    const clicked = new Set();
    let popupPage = null;

    console.log("Hunting for download button...\n");

    while (Date.now() - startTime < MAX_WAIT_MS) {
        // 1. Check for completed APK
        try {
            const files = fs.readdirSync(DOWNLOAD_PATH);
            const apk = files.find(f => f.endsWith('.apk') && !f.endsWith('.crdownload'));
            const crdl = files.find(f => f.endsWith('.crdownload'));

            if (apk) {
                const fullPath = path.join(DOWNLOAD_PATH, apk);
                const stats = fs.statSync(fullPath);
                if (stats.size > 500000) { // >500KB
                    downloadedFile = fullPath;
                    console.log(`\nAPK Downloaded: ${apk} (${(stats.size/1024/1024).toFixed(2)} MB)`);
                    break;
                }
            }
            if (crdl) {
                process.stdout.write("Downloading.");
                await new Promise(r => setTimeout(r, 3000));
                continue;
            }
        } catch (e) {}

        // 2. Get all pages (main + popups)
        const pages = await browser.pages();
        let acted = false;

        for (const p of pages) {
            if (p.isClosed()) continue;

            try {
                // Special handling for popups (FileCR download page)
                if (p.url().includes('filecr.com') && p.url() !== TARGET_URL && !popupPage) {
                    popupPage = p;
                    console.log("Switching to download popup...");
                    await p.bringToFront();
                    acted = true;
                    // Inject state monitor for "Generating..." → "Click to download"
                    await p.evaluate(() => {
                        const monitorButton = () => {
                            const btn = document.querySelector('a, button, .download-btn, [class*="download"]');
                            if (!btn) return 'NO_BUTTON';
                            const text = (btn.innerText || btn.value || '').toLowerCase().trim();
                            if (text.includes('generating download link') || text.includes('please wait')) return 'GENERATING';
                            if (text.includes('click to download') || text.includes('download now')) return 'READY';
                            return 'UNKNOWN';
                        };

                        const interval = setInterval(() => {
                            const state = monitorButton();
                            if (state === 'READY') {
                                const btn = document.querySelector('a, button, .download-btn, [class*="download"]');
                                if (btn) {
                                    btn.scrollIntoView({ block: 'center' });
                                    btn.click();
                                    clearInterval(interval);
                                }
                            }
                        }, 1000); // Poll every 1s

                        setTimeout(() => clearInterval(interval), 15000); // Max 15s
                    });
                    await new Promise(r => setTimeout(r, 8000)); // Wait for generation
                    continue;
                }

                // Main page or generic scanning
                const result = await p.evaluate(() => {
                    const candidates = Array.from(document.querySelectorAll('a, button, div[onclick], span[onclick], input[type="button"], .download-btn'));
                    let best = null;
                    let score = -999;

                    const visible = el => {
                        const s = window.getComputedStyle(el);
                        return s.display !== 'none' && s.visibility !== 'hidden' && el.offsetWidth > 0 && el.offsetHeight > 0;
                    };

                    for (const el of candidates) {
                        if (!visible(el)) continue;
                        const text = (el.innerText || el.value || '').toLowerCase().trim().replace(/\s+/g, ' ');

                        if (text.length < 3) continue;

                        let s = 0;
                        if (text.includes('ad') || text.includes('sponsored') || text.includes('login')) continue;
                        if (text.includes('premium') || text.includes('vip')) s -= 50; // Less penalty

                        // FileCR-specific boosts
                        if (text.includes('direct download')) s += 150;
                        if (text === 'download apk') s += 200;
                        if (text === 'download') s += 100;
                        if (text.includes('click to download')) s += 120;
                        if (text.includes('download') && text.includes('mb')) s += 80;

                        if (s > score) {
                            score = s;
                            best = el;
                        }
                    }

                    if (best && score > 20) { // Lowered threshold for "Direct Download"
                        best.scrollIntoView({ block: 'center' });
                        best.click();
                        return { clicked: true, text: best.innerText.substring(0, 50), score };
                    }
                    return { clicked: false };
                });

                if (result.clicked) {
                    const key = `${p.url()}-${result.text}`;
                    if (!clicked.has(key)) {
                        console.log(`Clicked: "${result.text}" (Score: ${result.score})`);
                        clicked.add(key);
                        setTimeout(() => clicked.delete(key), 15000);
                        acted = true;
                        await new Promise(r => setTimeout(r, 3000)); // Wait for popup
                    }
                }
            } catch (e) {}
        }

        if (!acted) {
            process.stdout.write(".");
            await new Promise(r => setTimeout(r, 2000)); // Slower loop for stability
        }
    }

    // Cleanup
    if (popupPage) await popupPage.close().catch(() => {});

    // Final result
    if (downloadedFile) {
        fs.renameSync(downloadedFile, OUTPUT_FILE);
        console.log(`\nSUCCESS! Saved as: ${OUTPUT_FILE}`);
        await browser.close();
        process.exit(0);
    } else {
        console.error("\nFailed — No APK downloaded in time. Check if scroll/popup handling needs tweak.");
        await browser.close();
        process.exit(1);
    }
})();
