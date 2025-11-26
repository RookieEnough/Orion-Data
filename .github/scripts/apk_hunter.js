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
const MAX_WAIT_MS = parseInt(getConfig('wait') || '120000', 10);

if (!TARGET_URL || !APP_ID) {
    console.error("Usage: node apk_hunter.js --url <url> --id <app_id> [--wait <ms>] [--out <filename>]");
    process.exit(1);
}

const DOWNLOAD_PATH = path.resolve(__dirname, 'downloads');
if (!fs.existsSync(DOWNLOAD_PATH)) fs.mkdirSync(DOWNLOAD_PATH, { recursive: true });

const BLOCKED_DOMAINS = [
    'googleads', 'doubleclick', 'googlesyndication', 'adservice', 'rubicon', 'criteo',
    'outbrain', 'taboola', 'adsystem', 'adnxs', 'smartadserver', 'popcash', 'popads'
];

const configurePage = async (page) => {
    if (page._configured) return;
    page._configured = true;

    const client = await page.target().createCDPSession();
    await client.send('Page.setDownloadBehavior', {
        behavior: 'allow',
        downloadPath: DOWNLOAD_PATH,
    });

    await page.setRequestInterception(true);
    page.on('request', (req) => {
        if (req.isInterceptResolutionHandled()) return;
        const url = req.url().toLowerCase();
        const type = req.resourceType();
        if (BLOCKED_DOMAINS.some(d => url.includes(d))) return void req.abort().catch(() => {});
        if (['image', 'media', 'font', 'stylesheet', 'imageset'].includes(type)) return void req.abort().catch(() => {});
        req.continue().catch(() => {});
    });
};

(async () => {
    console.log(`\nStarting Simple Direct Download Hunter for: ${APP_ID}`);
    console.log(`Target: ${TARGET_URL}\n`);

    const browser = await puppeteer.launch({
        headless: "new",
        args: [
            '--no-sandbox', '--disable-setuid-sandbox', '--disable-features=site-per-process',
            '--disable-web-security', '--disable-popup-blocking', '--window-size=1280,800',
            '--disable-blink-features=AutomationControlled'  // Stealth
        ],
        defaultViewport: null
    });

    browser.on('targetcreated', async (target) => {
        if (target.type() === 'page') {
            try {
                const newPage = await target.page();
                if (newPage && newPage.url() !== 'about:blank') {
                    await configurePage(newPage);
                    console.log(`Popup detected: ${newPage.url().substring(0, 50)}`);
                }
            } catch (e) {}
        }
    });

    const page = await browser.newPage();
    await configurePage(page);
    await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');
    await page.evaluateOnNewDocument(() => {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    });  // Extra stealth

    try {
        await page.goto(TARGET_URL, { waitUntil: 'networkidle0', timeout: 60000 });  // Wait for idle
        console.log("Page loaded. Starting slow human-like scroll...");
        
        // Slow scroll to trigger lazy loads
        await page.evaluate(async () => {
            const scrollStep = () => {
                return new Promise(resolve => {
                    let pos = 0;
                    const timer = setInterval(() => {
                        window.scrollBy(0, 200);
                        pos += 200;
                        if (pos >= document.body.scrollHeight) {
                            clearInterval(timer);
                            resolve();
                        }
                    }, 500);  // 500ms pauses = human speed
                });
            };
            await scrollStep();
            await new Promise(r => setTimeout(r, 3000));  // Stabilize
        });
    } catch (e) {
        console.error("Load/scroll failed:", e.message);
        await browser.close();
        process.exit(1);
    }

    const startTime = Date.now();
    let downloadedFile = null;
    let directClicked = false;
    let popupPage = null;

    console.log("Scanning for 'Direct Download' button...\n");

    // Phase 1: Find & Click "Direct Download" (retry every 3s)
    while (!directClicked && Date.now() - startTime < 30000) {  // 30s max for button hunt
        try {
            const result = await page.evaluate(() => {
                const candidates = Array.from(document.querySelectorAll('a, button, div[onclick], [class*="download"], .btn-download, .dl-link'));
                let best = null;
                let bestText = '';
                let bestScore = -1;

                const visible = el => {
                    const s = window.getComputedStyle(el);
                    return s.display !== 'none' && s.visibility !== 'hidden' && el.offsetWidth > 0 && el.offsetHeight > 0;
                };

                for (const el of candidates) {
                    if (!visible(el)) continue;
                    const text = (el.innerText || el.value || '').toLowerCase().trim().replace(/\s+/g, ' ');
                    if (text.length < 5) continue;

                    let score = 0;
                    if (text.includes('ad') || text.includes('login')) continue;
                    if (text.includes('direct download')) score = 200;  // Exact match priority
                    else if (text.includes('download apk') || text.includes('get now')) score = 100;
                    else if (text.includes('download')) score = 50;

                    if (score > bestScore) {
                        bestScore = score;
                        best = el;
                        bestText = text;
                    }
                }

                // Debug: Log all candidates
                console.log('Candidates found:', candidates.map(el => ({ text: el.innerText?.substring(0, 30), class: el.className })));

                if (best && bestScore >= 50) {
                    best.scrollIntoView({ block: 'center' });
                    // Human-like click: Move mouse first
                    const rect = best.getBoundingClientRect();
                    const x = rect.left + rect.width / 2;
                    const y = rect.top + rect.height / 2;
                    document.dispatchEvent(new MouseEvent('mouseover', { clientX: x, clientY: y }));
                    await new Promise(r => setTimeout(r, 500));
                    best.click();
                    return { clicked: true, text: bestText, score: bestScore };
                }
                return { clicked: false };
            });

            if (result.clicked) {
                console.log(`🎯 Clicked 'Direct Download': "${result.text}" (Score: ${result.score})`);
                directClicked = true;
                await new Promise(r => setTimeout(r, 5000));  // Wait for popup
            } else {
                console.log("No button yet. Retrying in 3s...");
                await new Promise(r => setTimeout(r, 3000));
            }
        } catch (e) {
            console.log("Scan error:", e.message);
            await new Promise(r => setTimeout(r, 3000));
        }
    }

    if (!directClicked) {
        console.error("\n❌ 'Direct Download' button not found after retries. Site may have changed selectors.");
        await browser.close();
        process.exit(1);
    }

    // Phase 2: Handle Popup (as you described)
    const pages = await browser.pages();
    popupPage = pages.find(p => p.url() !== TARGET_URL && !p.isClosed());
    if (popupPage) {
        await popupPage.bringToFront();
        console.log("Popup loaded. Monitoring for 'Generating...' → 'Click to download'...");
        
        // Poll for button state change
        const genStart = Date.now();
        while (Date.now() - genStart < 15000) {  // 15s max for generation
            const state = await popupPage.evaluate(() => {
                const btn = document.querySelector('a, button, [class*="download"], .dl-btn');
                if (!btn) return 'NO_BUTTON';
                const text = (btn.innerText || '').toLowerCase().trim();
                if (text.includes('generating download link') || text.includes('please wait')) return 'GENERATING';
                if (text.includes('click to download') || text.includes('download now') || text.includes('successful')) return 'READY';
                return 'LOADING';
            });

            if (state === 'GENERATING') {
                console.log("⏳ Generating link... (wait 5-10s)");
                await new Promise(r => setTimeout(r, 2000));
            } else if (state === 'READY') {
                await popupPage.evaluate(() => {
                    const btn = document.querySelector('a, button, [class*="download"], .dl-btn');
                    if (btn) {
                        btn.scrollIntoView({ block: 'center' });
                        btn.click();
                    }
                });
                console.log("✅ Clicked 'Click to download'!");
                break;
            } else if (state === 'NO_BUTTON') {
                console.log("No button in popup. Retrying...");
                await new Promise(r => setTimeout(r, 1000));
            }
        }
    }

    // Phase 3: Wait for Download
    const dlStart = Date.now();
    while (Date.now() - dlStart < 30000) {  // 30s for download
        try {
            const files = fs.readdirSync(DOWNLOAD_PATH);
            const apk = files.find(f => f.endsWith('.apk') && !f.endsWith('.crdownload'));
            if (apk) {
                const fullPath = path.join(DOWNLOAD_PATH, apk);
                const stats = fs.statSync(fullPath);
                if (stats.size > 1000000) {  // >1MB
                    downloadedFile = fullPath;
                    console.log(`\n✅ APK Ready: ${apk} (${(stats.size/1024/1024).toFixed(2)} MB)`);
                    break;
                }
            }
            const crdl = files.find(f => f.endsWith('.crdownload'));
            if (crdl) process.stdout.write(".");
            await new Promise(r => setTimeout(r, 2000));
        } catch (e) {}
    }

    if (popupPage) await popupPage.close().catch(() => {});

    if (downloadedFile) {
        fs.renameSync(downloadedFile, OUTPUT_FILE);
        console.log(`\n🎉 SUCCESS! Saved: ${OUTPUT_FILE}`);
        await browser.close();
        process.exit(0);
    } else {
        console.error("\n❌ Download failed after click. Check popup logs.");
        await browser.close();
        process.exit(1);
    }
})();
