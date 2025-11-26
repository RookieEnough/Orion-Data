// apk_hunter.js — FileCR Edition (2025 working)
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const TARGET_URL = process.argv.slice(2).find(a => a.startsWith('http')) || 'https://filecr.com/android/capcut-video-editor/';
const OUTPUT_FILE = 'CapCut_Premium.apk';

const DOWNLOAD_PATH = path.resolve(__dirname, 'downloads');
if (!fs.existsSync(DOWNLOAD_PATH)) fs.mkdirSync(DOWNLOAD_PATH, { recursive: true });

(async () => {
    console.log('Launching browser...');
    const browser = await puppeteer.launch({
        headless: "new",
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-web-security',
            '--disable-features=site-per-process',
            '--window-size=1280,800'
        ]
    });

    const page = await browser.newPage();

    // Allow downloads
    const client = await page.target().createCDPSession();
    await client.send('Page.setDownloadBehavior', {
        behavior: 'allow',
        downloadPath: DOWNLOAD_PATH,
    });

    // Block images, fonts, ads → much faster + avoids detection
    await page.setRequestInterception(true);
    page.on('request', (req) => {
        const type = req.resourceType();
        const url = req.url();
        if (['image', 'media', 'font', 'stylesheet'].includes(type) ||
            url.includes('google') || url.includes('ads') || url.includes('analytics')) {
            req.abort();
        } else {
            req.continue();
        }
    });

    await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
    console.log('Page loaded. Scrolling slowly to force "Direct Download" button...');

    // === SLOW HUMAN-LIKE SCROLL (this is the magic) ===
    await page.evaluate(async () => {
        await new Promise((resolve) => {
            let totalHeight = 0;
            const distance = 300;
            const timer = setInterval(() => {
                window.scrollBy(0, distance);
                totalHeight += distance;
                if (totalHeight >= document.body.scrollHeight - window.innerHeight) {
                    clearInterval(timer);
                    resolve();
                }
            }, 800); // 800ms pause = looks human, triggers lazy load
        });
    });

    // Wait extra time for button to appear
    await page.waitForTimeout(8000);

    // === CLICK "Direct Download" (exact text match) ===
    console.log('Looking for "Direct Download" button...');
    const clicked = await page.evaluate(() => {
        const buttons = Array.from(document.querySelectorAll('a, button, div'));
        const target = buttons.find(el => 
            el.innerText.trim() === 'Direct Download' ||
            el.innerText.trim().toLowerCase() === 'direct download'
        );
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'center' });
            target.click();
            return true;
        }
        return false;
    });

    if (!clicked) {
        console.error('Direct Download button NOT found!');
        await browser.close();
        process.exit(1);
    }
    console.log('Clicked "Direct Download"');

    // === Wait for popup & auto-click "Click to download" ===
    await page.waitForTimeout(4000);

    const pages = await browser.pages();
    const popup = pages[pages.length - 1]; // last tab = popup
    await popup.bringToFront();

    console.log('Waiting for "Click to download" button...');
    await popup.waitForFunction(
        () => {
            const btn = Array.from(document.querySelectorAll('a, button, div')).find(el =>
                el.innerText.toLowerCase().includes('click to download') ||
                el.innerText.toLowerCase().includes('download now')
            );
            if (btn) {
                btn.scrollIntoView();
                btn.click();
                return true;
            }
            return false;
        },
        { timeout: 20000, polling: 1000 }
    );

    console.log('Final download button clicked! Waiting for APK...');

    // === Wait for actual .apk file ===
    let apkFile = null;
    for (let i = 0; i < 40; i++) {
        const files = fs.readdirSync(DOWNLOAD_PATH);
        apkFile = files.find(f => f.endsWith('.apk') && !f.includes('.crdownload'));
        if (apkFile) {
            const stats = fs.statSync(path.join(DOWNLOAD_PATH, apkFile));
            if (stats.size > 50 * 1024 * 1024) break; // >50MB = real
        }
        process.stdout.write('.');
        await new Promise(r => setTimeout(r, 3000));
    }

    if (apkFile) {
        fs.renameSync(path.join(DOWNLOAD_PATH, apkFile), OUTPUT_FILE);
        console.log(`\nSUCCESS! Downloaded → ${OUTPUT_FILE}`);
        console.log(`Size: ${(fs.statSync(OUTPUT_FILE).size / 1024 / 1024).toFixed(1)} MB`);
        await browser.close();
        process.exit(0);
    } else {
        console.error('\nFailed — no APK downloaded');
        await browser.close();
        process.exit(1);
    }
})();
