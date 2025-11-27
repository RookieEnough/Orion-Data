const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');
const https = require('https'); 

// Helper: Sleep
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
    // --- 1. CONFIGURATION ---
    const args = process.argv.slice(2);
    const getArg = (key) => {
        const index = args.indexOf('--' + key);
        return index !== -1 ? args[index + 1] : null;
    };

    const CONFIG_PATH = path.resolve(process.cwd(), 'mirror_config.json');
    let APP_ID = getArg('id');
    let TARGET_URL = getArg('url');
    let OUTPUT_FILE = getArg('out');
    let MODE = 'direct';
    let WAIT_TIME = 60000;

    // Load Config
    if (APP_ID && fs.existsSync(CONFIG_PATH)) {
        try {
            const rawConfig = fs.readFileSync(CONFIG_PATH, 'utf8');
            const config = JSON.parse(rawConfig);
            const appConfig = config.find(c => c.id === APP_ID);
            if (appConfig) {
                if (!TARGET_URL) TARGET_URL = appConfig.downloadUrl;
                if (appConfig.mode) MODE = appConfig.mode;
                if (appConfig.wait) WAIT_TIME = parseInt(appConfig.wait);
            }
        } catch (e) {
            console.log('Warning: Config read error: ' + e.message);
        }
    }

    if (!APP_ID) APP_ID = 'unknown-app';
    if (!OUTPUT_FILE) OUTPUT_FILE = APP_ID + '.apk';
    if (!TARGET_URL) {
        console.error('Error: No target URL provided.');
        process.exit(1);
    }

    // Prepare Download Directory for Puppeteer
    const DOWNLOAD_DIR = path.resolve(process.cwd(), 'temp_dl_' + Date.now());
    if (!fs.existsSync(DOWNLOAD_DIR)) {
        fs.mkdirSync(DOWNLOAD_DIR);
    }

    console.log('--- APK HUNTER V3.4 (Stealth Mobile) ---');
    console.log(`Target: ${APP_ID}`);
    console.log(`URL: ${TARGET_URL}`);
    console.log(`Temp Dir: ${DOWNLOAD_DIR}`);
    console.log('-----------------------------------------');

    // --- 2. PUPPETEER LOGIC ---
    const runScrape = async () => {
        let browser = null;
        try {
            console.log('Launching Browser (Stealth Mobile Mode)...');
            
            // PIXEL 5 USER AGENT
            const MOBILE_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36';

            browser = await puppeteer.launch({
                headless: "new",
                args: [
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled', // Hide automation
                    `--user-agent=${MOBILE_UA}`
                ]
            });

            const page = await browser.newPage();
            
            // 1. OVERRIDE BROWSER FINGERPRINT (Critical for avoiding PC Installer)
            await page.evaluateOnNewDocument(() => {
                // Force platform to look like Android ARM, not Linux x86
                Object.defineProperty(navigator, 'platform', { get: () => 'Linux armv8l' });
                // Simulate Touch Screen
                Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 5 });
                // Hide webdriver
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
            });

            // 2. Set Viewport & UA
            await page.setUserAgent(MOBILE_UA);
            await page.setViewport({ width: 393, height: 851, isMobile: true, hasTouch: true });

            // 3. Enable Download Behavior
            const client = await page.target().createCDPSession();
            await client.send('Page.setDownloadBehavior', {
                behavior: 'allow',
                downloadPath: DOWNLOAD_DIR
            });

            // Block heavy media for speed
            await page.setRequestInterception(true);
            page.on('request', (req) => {
                const rType = req.resourceType();
                if (['image', 'font', 'stylesheet', 'media'].includes(rType)) req.abort();
                else req.continue();
            });

            console.log(`Navigating to ${TARGET_URL}...`);
            await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });

            console.log('Scanning for buttons...');
            
            // Wait a moment for dynamic content
            await delay(2000);

            // Get all anchor handles
            const anchors = await page.$$('a');
            let bestHandle = null;
            let bestText = '';
            let bestHref = '';
            let maxScore = -100;

            for (const handle of anchors) {
                const meta = await page.evaluate(el => {
                    const txt = (el.innerText || el.textContent || '').toLowerCase().trim();
                    const href = (el.href || '').toLowerCase();
                    
                    let score = 0;
                    
                    // Filter: Must look like a download link
                    if (!href.includes('/download') && !href.includes('file.apkdone.io')) return { score: -1000 };

                    // Penalize: Fast Download / Adware / Installer / Telegram
                    if (txt.includes('fast download') || txt.includes('installer') || txt.includes('telegram')) return { score: -1000 };

                    // --- SCORING RULES V3.4 ---

                    // 1. Text Content - Priority #1
                    const hasDownloadText = txt.includes('download apk');
                    const hasSizeText = /\d+\s*(mb|gb)/.test(txt); // Matches "277 MB" etc

                    if (hasDownloadText) score += 20;
                    if (hasSizeText) score += 20;

                    // THE COMBO: "Download APK" + Size found in text = 99% Real Button
                    if (hasDownloadText && hasSizeText) score += 150;

                    // 2. Domain Match (Secondary)
                    if (href.includes('file.apkdone.io')) score += 20;

                    return { score, txt, href };
                }, handle);

                if (meta.score > maxScore) {
                    maxScore = meta.score;
                    bestHandle = handle;
                    bestText = meta.txt;
                    bestHref = meta.href;
                }
            }

            if (!bestHandle || maxScore <= 0) {
                // Fallback: Try searching for just class names if text fails
                throw new Error('No valid download button found.');
            }

            console.log(`🎯 Found Target: "${bestText}"`);
            console.log(`🔗 Link: ${bestHref}`);
            console.log(`🏆 Score: ${maxScore}`);
            
            // CLICK THE BUTTON
            // We use standard click here. Puppeteer click triggers the download.
            try {
                await bestHandle.click();
            } catch (e) {
                console.log('Click warning:', e.message);
            }

            console.log('Waiting for download to start...');
            
            // MONITOR DOWNLOAD FOLDER
            let downloadedFile = null;
            const maxWaitTime = 300000; // 5 mins
            const startTime = Date.now();

            while (Date.now() - startTime < maxWaitTime) {
                const files = fs.readdirSync(DOWNLOAD_DIR);
                const finishedFile = files.find(f => !f.endsWith('.crdownload') && f.endsWith('.apk'));
                const inProgress = files.find(f => f.endsWith('.crdownload'));

                if (finishedFile) {
                    downloadedFile = path.join(DOWNLOAD_DIR, finishedFile);
                    // Check size to prevent small installer files
                    const stats = fs.statSync(downloadedFile);
                    
                    // V3.4 Check: If file is too small (< 20MB) and we expected a big file, it might be the installer.
                    // But we can't be sure, so we just log it for now.
                    if (stats.size > 0) break;
                }
                if (inProgress) process.stdout.write('.'); 
                await delay(2000);
            }

            console.log('\n');

            if (!downloadedFile) throw new Error('Download timed out or failed.');

            console.log(`File downloaded: ${path.basename(downloadedFile)}`);
            
            const finalStats = fs.statSync(downloadedFile);
            if (finalStats.size < 20 * 1024 * 1024) {
                 console.warn("⚠️  WARNING: Downloaded file is small (< 20MB). It might be the installer.");
            }

            // Final Move
            fs.renameSync(downloadedFile, OUTPUT_FILE);
            
            // Cleanup
            await browser.close();
            fs.rmSync(DOWNLOAD_DIR, { recursive: true, force: true });
            
            console.log(`Success! Saved to ${OUTPUT_FILE}`);

        } catch (err) {
            console.error('Puppeteer Error: ' + err.message);
            if (browser) await browser.close();
            if (fs.existsSync(DOWNLOAD_DIR)) fs.rmSync(DOWNLOAD_DIR, { recursive: true, force: true });
            process.exit(1);
        }
    };

    // --- 3. DIRECT MODE ---
    const runDirect = async () => {
        return new Promise((resolve, reject) => {
            const file = fs.createWriteStream(OUTPUT_FILE);
            https.get(TARGET_URL, (response) => {
                if (response.statusCode === 200) {
                    response.pipe(file);
                    file.on('finish', () => { file.close(() => resolve()); });
                } else {
                    reject(new Error(`HTTP ${response.statusCode}`));
                }
            }).on('error', (err) => {
                fs.unlink(OUTPUT_FILE, () => {});
                reject(err);
            });
        });
    };

    // --- 4. EXECUTION ---
    try {
        if (MODE === 'scrape') {
            await runScrape();
        } else {
            console.log('Running Direct Download...');
            await runDirect();
        }
        
        if (fs.existsSync(OUTPUT_FILE)) {
            const stats = fs.statSync(OUTPUT_FILE);
            console.log(`Final File Size: ${(stats.size/1024/1024).toFixed(2)} MB`);
        }

    } catch (e) {
        console.error('Fatal: ' + e.message);
        process.exit(1);
    }
})();
