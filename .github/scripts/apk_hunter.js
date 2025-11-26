
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

// Parse Arguments
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

// Helper to configure download behavior on a page
const configureDownload = async (page) => {
    try {
        const client = await page.target().createCDPSession();
        await client.send('Page.setDownloadBehavior', {
            behavior: 'allow',
            downloadPath: DOWNLOAD_PATH,
        });
        // Block heavy resources
        await page.setRequestInterception(true);
        page.on('request', (req) => {
            if (['image', 'font', 'media', 'stylesheet'].includes(req.resourceType())) {
                req.abort();
            } else {
                req.continue();
            }
        });
    } catch (err) {
        console.log("⚠️ Failed to configure page (might be closed):", err.message);
    }
};

(async () => {
    console.log(`\n🕷️  Starting APK Hunter for: ${APP_ID}`);
    console.log(`🔗  Target: ${TARGET_URL}`);
    console.log(`⏱️  Max Wait Time: ${MAX_WAIT_MS / 1000}s`);

    const browser = await puppeteer.launch({
        headless: "new",
        args: [
            '--no-sandbox', 
            '--disable-setuid-sandbox',
            '--disable-features=site-per-process',
            '--window-size=1920,1080',
            '--disable-popup-blocking' // Allow popups for downloads
        ]
    });

    // Listen for new tabs/windows to ensure downloads work there too
    browser.on('targetcreated', async (target) => {
        if (target.type() === 'page') {
            const newPage = await target.page();
            if (newPage) {
                await newPage.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');
                await configureDownload(newPage);
            }
        }
    });

    const page = await browser.newPage();
    await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');
    await configureDownload(page);

    try {
        await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
    } catch (e) {
        console.error("❌  Navigation failed:", e.message);
        await browser.close();
        process.exit(1);
    }

    // --- SMART POLLING LOOP ---
    const startTime = Date.now();
    let fileFound = null;
    let clickedButtons = new Set();
    
    // Keep track of all pages to search buttons on all tabs
    const getAllPages = async () => {
        return await browser.pages();
    };

    console.log("🔄  Entering Hunt Loop...");

    while (Date.now() - startTime < MAX_WAIT_MS + 15000) { 
        
        // 1. Check for File
        try {
            const files = fs.readdirSync(DOWNLOAD_PATH);
            const apk = files.find(f => f.endsWith('.apk'));
            const part = files.find(f => f.endsWith('.crdownload'));

            if (apk) {
                // Ensure file size is stable (download finished)
                const stats = fs.statSync(path.join(DOWNLOAD_PATH, apk));
                if (stats.size > 0) {
                    fileFound = path.join(DOWNLOAD_PATH, apk);
                    console.log(`✅  File detected: ${apk}`);
                    break;
                }
            }

            if (part) {
                // Downloading... wait patiently
                process.stdout.write("Dl.");
                await new Promise(r => setTimeout(r, 2000));
                continue; 
            }
        } catch (err) {
            // ignore fs errors
        }

        // 2. Perform Interaction (Scan all open tabs)
        const pages = await getAllPages();
        let actionTaken = false;

        for (const p of pages) {
            try {
                // Skip if page is closed
                if (p.isClosed()) continue;

                const clickResult = await p.evaluate(() => {
                    // A. Scroll to bottom to trigger lazy loads
                    window.scrollTo(0, document.body.scrollHeight);
                    
                    // B. Helper to check visibility
                    const isVisible = (el) => {
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0 && el.style.visibility !== 'hidden';
                    };

                    const buttons = [...document.querySelectorAll('a, button, div[role="button"], span, input[type="button"]')];
                    
                    // Strategy: Find the best candidate
                    const candidate = buttons.find(el => {
                        if (!isVisible(el)) return false;
                        const t = (el.innerText || el.value || "").toLowerCase().trim();
                        if (t.length < 3) return false;

                        // Negative Keywords (Ads/Stats)
                        if (t.includes('premium') || t.includes('fast') || t.includes('manager') || t.includes('advertisement')) return false;
                        if (t.includes('total downloads') || t.includes('viewed') || t.includes('votes')) return false;

                        // Positive Keywords priority
                        // 1. Exact "Download APK" or "Download" or "Direct Download"
                        if (t === 'download' || t === 'download apk' || t === 'direct download') return true;
                        
                        // 2. FileCR specific: "Click to Download"
                        if (t.includes('click to download')) return true;

                        // 3. Secondary: "Download (X MB)"
                        if (t.includes('download') && (t.includes('mb') || t.includes('apk') || t.includes('file'))) return true;

                        return false;
                    });

                    if (candidate) {
                        candidate.click();
                        return (candidate.innerText || candidate.value || "button").substring(0, 30);
                    }
                    return null;
                });

                if (clickResult && !clickedButtons.has(clickResult)) {
                    console.log(`\nHg  Clicked on [${p.url().substring(0,30)}...]: "${clickResult.replace(/\n/g, ' ')}". Waiting...`);
                    clickedButtons.add(clickResult);
                    actionTaken = true;
                    // Don't wait too long here, we want to check for files, but give it a moment to react
                    await new Promise(r => setTimeout(r, 3000)); 
                    break; // Move to file check
                }
            } catch (e) {
                // Ignore evaluation errors (detached nodes etc)
            }
        }

        if (!actionTaken) {
             process.stdout.write(".");
             await new Promise(r => setTimeout(r, 2000));
        }
    }

    if (fileFound) {
        fs.renameSync(fileFound, OUTPUT_FILE);
        console.log(`🎉  Success! Saved to ${OUTPUT_FILE}`);
        await browser.close();
        process.exit(0);
    } else {
        console.error("\n❌  Timed out. File did not download.");
        await browser.close();
        process.exit(1);
    }
})();
