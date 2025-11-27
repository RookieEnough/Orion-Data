const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');
const https = require('https'); // Kept for direct mode only

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

    console.log('--- APK HUNTER V3.1 (Stealth Native) ---');
    console.log(`Target: ${APP_ID}`);
    console.log(`URL: ${TARGET_URL}`);
    console.log(`Mode: ${MODE}`);
    console.log(`Temp Dir: ${DOWNLOAD_DIR}`);
    console.log('-----------------------------------------');

    // --- 2. PUPPETEER LOGIC ---
    const runScrape = async () => {
        let browser = null;
        try {
            console.log('Launching Browser (Stealth Mode)...');
            
            // USE A REAL DESKTOP USER AGENT TO TRICK SERVER
            const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36';

            browser = await puppeteer.launch({
                headless: "new",
                args: [
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--window-size=1920,1080', // Standard Desktop
                    `--user-agent=${USER_AGENT}`
                ]
            });

            const page = await browser.newPage();
            
            // 1. Override UA in Page
            await page.setUserAgent(USER_AGENT);
            
            // 2. Mask Webdriver (Crucial for bypass)
            await page.evaluateOnNewDocument(() => {
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
            });

            const client = await page.target().createCDPSession();

            // Enable file download behavior
            await client.send('Page.setDownloadBehavior', {
                behavior: 'allow',
                downloadPath: DOWNLOAD_DIR
            });

            // Block heavy media to speed up
            await page.setRequestInterception(true);
            page.on('request', (req) => {
                const rType = req.resourceType();
                if (['image', 'font', 'stylesheet', 'media'].includes(rType)) req.abort();
                else req.continue();
            });

            console.log(`Navigating to ${TARGET_URL}...`);
            await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });

            console.log('Scanning for buttons...');
            
            // Get all anchor handles
            const anchors = await page.$$('a');
            let bestHandle = null;
            let bestText = '';
            let maxScore = -1;

            for (const handle of anchors) {
                // Evaluate each node in browser context
                const meta = await page.evaluate(el => {
                    const txt = (el.innerText || el.textContent || '').toLowerCase().trim();
                    const href = el.href || '';
                    
                    let score = 0;
                    
                    // Filter: Must look like a download link
                    if (!href.includes('/download') && !href.includes('file.apkdone.io')) return { score: -1 };

                    // Penalize: Fast Download / Adware
                    if (txt.includes('fast download') || txt.includes('with apkdone')) return { score: -1 };

                    // Score Calculation
                    if (txt.includes('download apk')) score += 20;
                    if (/\d+\s*(mb|gb)/.test(txt)) score += 10; // Contains size
                    if (href.includes('file.apkdone.io')) score += 5;

                    return { score, txt, href };
                }, handle);

                if (meta.score > maxScore) {
                    maxScore = meta.score;
                    bestHandle = handle;
                    bestText = meta.txt;
                }
            }

            if (!bestHandle || maxScore <= 0) {
                throw new Error('No valid download button found via scoring.');
            }

            console.log(`🎯 Clicking Target: "${bestText}" (Score: ${maxScore})`);
            
            // CLICK THE BUTTON
            try {
                // Ensure the click mimics a real user
                await bestHandle.click();
            } catch (e) {
                console.log('Click warning (might still work):', e.message);
            }

            console.log('Waiting for download to start...');
            
            // MONITOR DOWNLOAD FOLDER
            // Wait up to 5 minutes (300s) for the file
            let downloadedFile = null;
            const maxWaitTime = 300000; // 5 mins
            const startTime = Date.now();

            while (Date.now() - startTime < maxWaitTime) {
                const files = fs.readdirSync(DOWNLOAD_DIR);
                
                // Look for files that are NOT .crdownload (Chrome partial file)
                const finishedFile = files.find(f => !f.endsWith('.crdownload') && f.endsWith('.apk'));
                const inProgress = files.find(f => f.endsWith('.crdownload'));

                if (finishedFile) {
                    downloadedFile = path.join(DOWNLOAD_DIR, finishedFile);
                    // Double check size to ensure it's not empty
                    const stats = fs.statSync(downloadedFile);
                    if (stats.size > 0) break;
                }

                if (inProgress) {
                    process.stdout.write('.'); // progress indicator
                }

                await delay(2000); // Check every 2s
            }

            console.log('\n');

            if (!downloadedFile) {
                throw new Error('Download timed out or failed.');
            }

            console.log(`File downloaded: ${path.basename(downloadedFile)}`);
            
            // Move to Final Output
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

    // --- 3. DIRECT MODE (FALLBACK) ---
    const runDirect = async () => {
        return new Promise((resolve, reject) => {
            const file = fs.createWriteStream(OUTPUT_FILE);
            https.get(TARGET_URL, (response) => {
                if (response.statusCode === 200) {
                    response.pipe(file);
                    file.on('finish', () => {
                        file.close(() => resolve());
                    });
                } else if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
                    // Simple redirect handling for direct mode
                    console.log(`Redirecting to ${response.headers.location}`);
                    reject(new Error('Direct mode redirect (use scrape mode for complex sites)'));
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
            if (stats.size < 1024 * 1024) { // < 1MB
                console.warn(`⚠️ Warning: File is suspiciously small (${(stats.size/1024).toFixed(2)} KB).`);
            } else {
                console.log(`Final File Size: ${(stats.size/1024/1024).toFixed(2)} MB`);
            }
        }

    } catch (e) {
        console.error('Fatal: ' + e.message);
        process.exit(1);
    }
})();
