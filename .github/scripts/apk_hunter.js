const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
const getConfig = (key) => {
    const index = args.indexOf(`--${key}`);
    return index !== -1 ? args[index + 1] : null;
};

const TARGET_URL = getConfig('url');
const APP_ID = getConfig('id');
const OUTPUT_FILE = getConfig('out') || `${APP_ID}.apk`;
const MAX_WAIT_MS = parseInt(getConfig('wait') || '90000', 10);

if (!TARGET_URL || !APP_ID) {
    console.error("Usage: node apk_hunter.js --url <url> --id <app_id> [--wait <ms>] [--out <filename>]");
    process.exit(1);
}

const DOWNLOAD_PATH = path.resolve(__dirname, '../downloads'); // Relative to scripts/
if (!fs.existsSync(DOWNLOAD_PATH)) fs.mkdirSync(DOWNLOAD_PATH, { recursive: true });

(async () => {
    console.log(`\n🕷️  Universal APK Hunter for: ${APP_ID}`);
    console.log(`🔗  Target: ${TARGET_URL}\n`);

    const browser = await puppeteer.launch({
        headless: "new",
        args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1280,800']
    });

    const page = await browser.newPage();

    // Safe downloads & blocking (no async handler bugs)
    const client = await page.target().createCDPSession();
    await client.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: DOWNLOAD_PATH });

    await page.setRequestInterception(true);
    page.on('request', (req) => {
        const type = req.resourceType();
        if (['image', 'media', 'font', 'stylesheet'].includes(type)) {
            req.abort().catch(() => {});
        } else {
            req.continue().catch(() => {});
        }
    });

    await page.goto(TARGET_URL, { waitUntil: 'networkidle2', timeout: 30000 });

    // Universal flow: Click first "Download" → scroll new page → click final (with MB/size)
    let currentPage = page;
    // Initial click
    await currentPage.evaluate(() => {
        const btn = Array.from(document.querySelectorAll('a, button')).find(el =>
            el.innerText.toLowerCase().includes('download') && el.offsetHeight > 0
        );
        if (btn) btn.click();
    });

    // Handle new page (if redirected)
    await new Promise(r => setTimeout(r, 3000));
    const pages = await browser.pages();
    currentPage = pages[pages.length - 1]; // Latest page
    await currentPage.bringToFront();

    // Scroll & final click (universal for APKDone-style sites)
    await currentPage.evaluate(() => window.scrollBy(0, 1200));
    await currentPage.waitForTimeout(1500);
    await currentPage.evaluate(() => {
        const btn = Array.from(document.querySelectorAll('a, button')).find(el => {
            const text = el.innerText.toLowerCase();
            return (text.includes('download apk') && text.includes('mb')) || text.includes('fast download');
        });
        if (btn) btn.click();
    });

    // Wait for download
    const startTime = Date.now();
    let fileFound = null;
    while (Date.now() - startTime < MAX_WAIT_MS) {
        const files = fs.readdirSync(DOWNLOAD_PATH);
        const apk = files.find(f => f.endsWith('.apk') && !f.includes('.crdownload'));
        if (apk) {
            const stats = fs.statSync(path.join(DOWNLOAD_PATH, apk));
            if (stats.size > 50 * 1024 * 1024) { // >50MB
                fileFound = path.join(DOWNLOAD_PATH, apk);
                break;
            }
        }
        process.stdout.write('.');
        await new Promise(r => setTimeout(r, 2000));
    }

    if (fileFound) {
        fs.renameSync(fileFound, path.resolve(__dirname, `../${OUTPUT_FILE}`)); // To root
        console.log(`\n🎉  Success! ${OUTPUT_FILE}`);
        await browser.close();
        process.exit(0);
    } else {
        console.error('\n❌  Timeout - no APK.');
        await browser.close();
        process.exit(1);
    }
})();
