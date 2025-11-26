
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
            '--window-size=1920,1080'
        ]
    });

    const page = await browser.newPage();
    
    // 1. Spoof User Agent (Look like a real PC to avoid mobile sites/blocking)
    await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

    // 2. Setup Download Behavior
    const client = await page.target().createCDPSession();
    await client.send('Page.setDownloadBehavior', {
        behavior: 'allow',
        downloadPath: DOWNLOAD_PATH,
    });

    // 3. Speed Optimization: Block images, fonts, and media
    await page.setRequestInterception(true);
    page.on('request', (req) => {
        if (['image', 'font', 'media', 'stylesheet'].includes(req.resourceType())) {
            req.abort();
        } else {
            req.continue();
        }
    });

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

    console.log("🔄  Entering Hunt Loop...");

    while (Date.now() - startTime < MAX_WAIT_MS + 10000) { 
        
        // 1. Check for File
        const files = fs.readdirSync(DOWNLOAD_PATH);
        const apk = files.find(f => f.endsWith('.apk'));
        const part = files.find(f => f.endsWith('.crdownload'));

        if (apk) {
            fileFound = path.join(DOWNLOAD_PATH, apk);
            console.log(`✅  File detected: ${apk}`);
            break;
        }

        if (part) {
            // Downloading... wait patiently
            await new Promise(r => setTimeout(r, 2000));
            continue; 
        }

        // 2. Perform Interaction
        try {
            const clickResult = await page.evaluate(() => {
                // A. Scroll to bottom to trigger lazy loads
                window.scrollTo(0, document.body.scrollHeight);
                
                // B. Helper to check visibility
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0 && el.style.visibility !== 'hidden';
                };

                const buttons = [...document.querySelectorAll('a, button, div[role="button"], span')];
                
                // Strategy: Find the best candidate
                const candidate = buttons.find(el => {
                    if (!isVisible(el)) return false;
                    const t = el.innerText?.toLowerCase().trim() || "";
                    
                    // Negative Keywords (Ads)
                    if (t.includes('premium') || t.includes('fast') || t.includes('manager') || t.includes('advertisement')) return false;

                    // Positive Keywords
                    // Priority: Exact "Download APK" or "Download"
                    if (t === 'download' || t === 'download apk') return true;
                    
                    // Secondary: "Download (X MB)"
                    if (t.includes('download') && (t.includes('mb') || t.includes('apk') || t.includes('file'))) return true;

                    // Fallback: Just "Download"
                    return t.includes('download');
                });

                if (candidate) {
                    candidate.click();
                    return candidate.innerText;
                }
                return null;
            });

            if (clickResult && !clickedButtons.has(clickResult)) {
                console.log(`Hg  Clicked: "${clickResult}". Waiting...`);
                clickedButtons.add(clickResult);
                await new Promise(r => setTimeout(r, 5000)); // Wait for redirect/popup
            }
        } catch (e) {
            // Ignore evaluation errors (detached nodes etc)
        }

        process.stdout.write(".");
        await new Promise(r => setTimeout(r, 2000));
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

