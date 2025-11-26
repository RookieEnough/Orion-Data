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
const MAX_WAIT_MS = parseInt(getConfig('wait') || '60000'); 

if (!TARGET_URL || !APP_ID) {
    console.error("❌ Usage: node apk_hunter.js --url <url> --id <app_id> [--wait <ms>] [--out <filename>]");
    process.exit(1);
}

const DOWNLOAD_PATH = path.resolve(__dirname, 'downloads');
if (!fs.existsSync(DOWNLOAD_PATH)) fs.mkdirSync(DOWNLOAD_PATH);

// Ad Blocking & Resource Optimization
const BLOCKED_DOMAINS = [
    'googleads', 'doubleclick', 'googlesyndication', 'adservice', 'rubicon', 'criteo', 
    'outbrain', 'taboola', 'adsystem', 'adnxs', 'smartadserver', 'popcash', 'popads'
];

const configurePage = async (page) => {
    try {
        // Prevent setting up multiple times on the same page
        if (page._isConfigured) return;
        page._isConfigured = true;

        const client = await page.target().createCDPSession();
        await client.send('Page.setDownloadBehavior', {
            behavior: 'allow',
            downloadPath: DOWNLOAD_PATH,
        });
        
        await page.setRequestInterception(true);
        page.on('request', async (req) => {
            // CRITICAL FIX: Check if request is already handled to prevent crash
            if (req.isInterceptResolutionHandled()) return;

            const url = req.url().toLowerCase();
            const resourceType = req.resourceType();
            
            try {
                // Block generic ads and tracking
                if (BLOCKED_DOMAINS.some(d => url.includes(d))) {
                    await req.abort();
                    return;
                }
                // Block heavy media to speed up processing
                if (['image', 'media', 'font'].includes(resourceType)) {
                    await req.abort();
                    return;
                }
                await req.continue();
            } catch (err) {
                // Ignore errors if request is already closed/handled by browser
            }
        });
    } catch (err) {
        // Ignore errors on closed pages
    }
};

(async () => {
    console.log(`\n🕷️  Starting Smart APK Hunter for: ${APP_ID}`);
    console.log(`🔗  Target: ${TARGET_URL}`);
    
    const browser = await puppeteer.launch({
        headless: "new",
        args: [
            '--no-sandbox', 
            '--disable-setuid-sandbox', 
            '--disable-features=site-per-process',
            '--window-size=1280,800', // Standard resolution
            '--disable-popup-blocking'
        ]
    });

    // Handle new tabs (popups/redirects)
    browser.on('targetcreated', async (target) => {
        if (target.type() === 'page') {
            try {
                const newPage = await target.page();
                if (newPage) {
                    await configurePage(newPage);
                    // Close empty/ad tabs quickly
                    setTimeout(async () => {
                        try {
                            if (!newPage.isClosed() && newPage.url() === 'about:blank') await newPage.close();
                        } catch(e){}
                    }, 2000);
                }
            } catch(e) {}
        }
    });

    const page = await browser.newPage();
    // We call this here to ensure the main page is configured, 
    // the check inside configurePage prevents double-init if targetcreated fired first.
    await configurePage(page);

    try {
        await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');
        await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
    } catch (e) {
        console.error("❌  Initial Navigation failed:", e.message);
        await browser.close();
        process.exit(1);
    }

    const startTime = Date.now();
    let fileFound = null;
    let clickedHistory = new Set();

    console.log("🔄  Entering Hunter Loop...");

    // === MAIN LOOP ===
    while (Date.now() - startTime < MAX_WAIT_MS + 30000) {
        
        // 1. Check File System
        try {
            const files = fs.readdirSync(DOWNLOAD_PATH);
            const apk = files.find(f => f.endsWith('.apk'));
            const crdownload = files.find(f => f.endsWith('.crdownload'));

            if (apk) {
                const stats = fs.statSync(path.join(DOWNLOAD_PATH, apk));
                if (stats.size > 0) {
                    fileFound = path.join(DOWNLOAD_PATH, apk);
                    console.log(`\n✅  File detected: ${apk}`);
                    break;
                }
            }
            if (crdownload) {
                process.stdout.write("Dl."); // Downloading
                await new Promise(r => setTimeout(r, 2000));
                continue;
            }
        } catch (err) {}

        // 2. Scan All Pages
        const pages = await browser.pages();
        let actionTaken = false;

        for (const p of pages) {
            if (p.isClosed()) continue;
            
            // Inject logic into page
            try {
                const decision = await p.evaluate(() => {
                    // --- SCORING ENGINE ---
                    const buttons = Array.from(document.querySelectorAll('a, button, div[role="button"], span, input[type="button"], input[type="submit"]'));
                    
                    let bestEl = null;
                    let highestScore = -9999;
                    let debugText = "";

                    const isVisible = (el) => {
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && el.offsetWidth > 0 && el.offsetHeight > 0;
                    };

                    for (let el of buttons) {
                        if (!isVisible(el)) continue;

                        let text = (el.innerText || el.value || "").toLowerCase().replace(/\s+/g, ' ').trim();
                        if (text.length < 3) continue;

                        let score = 0;

                        // ⛔ KILL WORDS (Immediate Disqualification)
                        if (text.includes('ad') || text.includes('sponsored') || text.includes('facebook') || text.includes('twitter')) continue;
                        if (text.includes('login') || text.includes('signup') || text.includes('register')) continue;
                        
                        // ⛔ WAITING WORDS (Detected but not ready)
                        if (text.includes('generating') || text.includes('please wait') || text.includes('seconds')) {
                            // If this looks like a download button but is waiting, return special status
                            if (text.includes('download') || text.includes('link')) {
                                return { action: 'WAITING', text: text.substring(0, 30) };
                            }
                            continue; 
                        }

                        // ⛔ STATS TRAPS (e.g. "Total Downloads: 500")
                        if (text.includes('total') || text.includes('view') || text.includes('date') || text.includes('size')) {
                             score -= 500;
                        }

                        // ⛔ FAKE/PREMIUM BUTTONS
                        if (text.includes('premium') || text.includes('fast') || text.includes('manager')) score -= 100;

                        // ✅ GOOD WORDS
                        if (text === 'download') score += 100;
                        if (text === 'download apk') score += 100;
                        if (text === 'direct download') score += 80;
                        if (text.includes('click to download')) score += 90; // FileCR specific
                        
                        // ✅ CONTEXT WORDS
                        if (text.includes('download') && text.includes('mb')) score += 60; // "Download (50MB)"
                        if (text.includes('download') && text.includes('apk')) score += 50;

                        // Prefer shorter, concise buttons over long paragraphs
                        if (text.length > 50) score -= 20;

                        if (score > highestScore) {
                            highestScore = score;
                            bestEl = el;
                            debugText = text;
                        }
                    }

                    // Threshold: Only click if we are fairly sure (Score > 20)
                    if (bestEl && highestScore > 20) {
                        bestEl.click();
                        return { action: 'CLICKED', text: debugText };
                    }
                    
                    return { action: 'NONE' };
                });

                if (decision.action === 'WAITING') {
                    console.log(`\n⏳  Countdown detected on [${p.url().substring(0,25)}...]: "${decision.text}". Waiting...`);
                    actionTaken = true; 
                    await new Promise(r => setTimeout(r, 2000));
                    break;
                } else if (decision.action === 'CLICKED') {
                    const key = `${p.url()}-${decision.text}`;
                    // Simple debounce
                    if (!clickedHistory.has(key)) {
                        console.log(`\n🎯  Clicked [${p.url().substring(0,25)}...]: "${decision.text}"`);
                        clickedHistory.add(key);
                        setTimeout(() => clickedHistory.delete(key), 10000); 
                        actionTaken = true;
                        // Give it time to react
                        await new Promise(r => setTimeout(r, 4000));
                        break;
                    }
                }

            } catch(e) { /* context destroyed, ignore */ }
        }

        if (!actionTaken) {
            process.stdout.write(".");
            await new Promise(r => setTimeout(r, 1000));
        }
    }

    if (fileFound) {
        fs.renameSync(fileFound, OUTPUT_FILE);
        console.log(`\n🎉  Success! Downloaded to ${OUTPUT_FILE}`);
        await browser.close();
        process.exit(0);
    } else {
        console.error("\n❌  Timed out. Smart Hunter failed to download.");
        await browser.close();
        process.exit(1);
    }
})();
