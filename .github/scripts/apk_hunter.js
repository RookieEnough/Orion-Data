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

// Ad Blocking List
const BLOCKED_DOMAINS = [
    'googleads', 'doubleclick', 'googlesyndication', 'adservice', 'rubicon', 'criteo', 
    'outbrain', 'taboola', 'adsystem', 'adnxs', 'smartadserver'
];

const configureDownload = async (page) => {
    try {
        const client = await page.target().createCDPSession();
        await client.send('Page.setDownloadBehavior', {
            behavior: 'allow',
            downloadPath: DOWNLOAD_PATH,
        });
        
        await page.setRequestInterception(true);
        page.on('request', (req) => {
            const url = req.url().toLowerCase();
            const resourceType = req.resourceType();
            
            // Block heavy media AND known ad domains
            if (['image', 'font', 'media', 'stylesheet'].includes(resourceType) || 
                BLOCKED_DOMAINS.some(d => url.includes(d))) {
                req.abort();
            } else {
                req.continue();
            }
        });
    } catch (err) {
        console.log("⚠️ Failed to configure page:", err.message);
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
            '--disable-popup-blocking'
        ]
    });

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

    const startTime = Date.now();
    let fileFound = null;
    
    // Store clicked buttons with timestamp to allow retrying (Anti-Popunder)
    let clickedButtons = new Map(); 

    const getAllPages = async () => await browser.pages();

    console.log("🔄  Entering Hunt Loop...");

    while (Date.now() - startTime < MAX_WAIT_MS + 20000) { 
        
        // 1. Check for File
        try {
            const files = fs.readdirSync(DOWNLOAD_PATH);
            const apk = files.find(f => f.endsWith('.apk'));
            const part = files.find(f => f.endsWith('.crdownload'));

            if (apk) {
                const stats = fs.statSync(path.join(DOWNLOAD_PATH, apk));
                if (stats.size > 0) {
                    fileFound = path.join(DOWNLOAD_PATH, apk);
                    console.log(`✅  File detected: ${apk}`);
                    break;
                }
            }

            if (part) {
                process.stdout.write("Dl.");
                await new Promise(r => setTimeout(r, 2000));
                continue; 
            }
        } catch (err) {}

        // 2. Refresh Button State (Retry logic)
        for (const [txt, time] of clickedButtons) {
            if (Date.now() - time > 15000) { 
                // If 15s passed and no download, assume it was a popunder/ad and forget it so we can click again.
                clickedButtons.delete(txt);
            }
        }

        // 3. Scan Pages
        const pages = await getAllPages();
        let actionTaken = false;

        for (const p of pages) {
            try {
                if (p.isClosed()) continue;

                const clickResult = await p.evaluate(() => {
                    window.scrollTo(0, document.body.scrollHeight);
                    
                    const isVisible = (el) => {
                        const rect = el.getBoundingClientRect();
                        // Must be visible AND have some size
                        return rect.width > 0 && rect.height > 0 && el.style.visibility !== 'hidden' && el.style.display !== 'none';
                    };

                    const buttons = [...document.querySelectorAll('a, button, div[role="button"], span, input[type="button"]')];
                    
                    const candidate = buttons.find(el => {
                        if (!isVisible(el)) return false;
                        const t = (el.innerText || el.value || "").toLowerCase().trim();
                        if (t.length < 3) return false;

                        // ⛔ STRICT NEGATIVE MATCHING ⛔
                        // If it says "Generating" or "Wait", it is NOT ready, even if it also says "Download".
                        if (t.includes('generating') || t.includes('please wait') || t.includes('seconds')) return false;
                        
                        // Generic bad keywords
                        if (t.includes('premium') || t.includes('fast') || t.includes('manager') || t.includes('advertisement')) return false;
                        if (t.includes('total downloads') || t.includes('viewed')) return false;

                        // ✅ POSITIVE MATCHING ✅
                        if (t === 'download' || t === 'download apk' || t === 'direct download') return true;
                        if (t.includes('click to download')) return true; // FileCR Final Button
                        if (t.includes('download') && (t.includes('mb') || t.includes('apk') || t.includes('file'))) return true;

                        return false;
                    });

                    if (candidate) {
                        candidate.click();
                        // Return the text to identify this button
                        return (candidate.innerText || candidate.value || "button").substring(0, 50).replace(/\n/g, ' ');
                    }
                    return null;
                });

                if (clickResult && !clickedButtons.has(clickResult)) {
                    console.log(`\nHg  Clicked on [${p.url().substring(0,30)}...]: "${clickResult}". Waiting...`);
                    clickedButtons.set(clickResult, Date.now());
                    actionTaken = true;
                    await new Promise(r => setTimeout(r, 4000)); 
                    break; 
                }
            } catch (e) {}
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
