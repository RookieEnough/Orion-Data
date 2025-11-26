
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
// Allow custom wait time from config, default to 30 seconds if not specified
const MAX_WAIT_MS = parseInt(getConfig('wait') || '30000'); 

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
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    });

    const page = await browser.newPage();
    
    const client = await page.target().createCDPSession();
    await client.send('Page.setDownloadBehavior', {
        behavior: 'allow',
        downloadPath: DOWNLOAD_PATH,
    });

    try {
        await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
    } catch (e) {
        console.error("❌  Navigation failed:", e.message);
        await browser.close();
        process.exit(1);
    }

    // --- SMART POLLING LOOP ---
    // Instead of sleeping once, we loop repeatedly checking for progress.
    
    const startTime = Date.now();
    let fileFound = null;

    console.log("🔄  Entering Hunt Loop...");

    while (Date.now() - startTime < MAX_WAIT_MS + 10000) { // Add 10s buffer for download time
        
        // 1. Check if file arrived
        const files = fs.readdirSync(DOWNLOAD_PATH);
        const apk = files.find(f => f.endsWith('.apk'));
        const part = files.find(f => f.endsWith('.crdownload'));

        if (apk) {
            fileFound = path.join(DOWNLOAD_PATH, apk);
            console.log(`✅  File detected: ${apk}`);
            break;
        }

        if (part) {
            console.log("⬇️  Downloading in progress...");
            await new Promise(r => setTimeout(r, 2000));
            continue; // Skip clicking if already downloading
        }

        // 2. Try to click buttons
        // We evaluate page to find buttons. Sites like FileCR often have multiple buttons
        // or a button that changes text from "Please Wait" to "Download".
        const clickedText = await page.evaluate(() => {
            const buttons = [...document.querySelectorAll('a, button, .btn, div[role="button"]')];
            
            // Filter visible buttons only
            const visibleButtons = buttons.filter(b => {
                const rect = b.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0 && b.style.display !== 'none' && b.style.visibility !== 'hidden';
            });

            // Priority 1: High confidence text matches
            const target = visibleButtons.find(el => {
                const t = el.innerText.toLowerCase().trim();
                // Avoid "Premium" or "Fast Download" ads
                if (t.includes('premium') || t.includes('fast')) return false;
                
                return (
                    (t.includes('download') && t.includes('apk')) || // "Download APK"
                    (t === 'download') ||                            // Exact "Download"
                    (t.includes('click here') && t.includes('download')) ||
                    (t.includes('generate') && t.includes('link'))   // "Generate Download Link"
                );
            });

            if (target) {
                target.click();
                return target.innerText; // Return text to Node context
            }
            return null;
        });

        if (clickedText) {
            console.log(`Hg  Clicked button: "${clickedText}". Waiting for reaction...`);
            // Wait a bit after clicking to let page react/redirect
            await new Promise(r => setTimeout(r, 4000)); 
        } else {
            // No button found yet (maybe countdown is running?)
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
        await page.screenshot({ path: 'debug_timeout.png' });
        await browser.close();
        process.exit(1);
    }
})();
