/*
 * ORION DATA - SMART APK HUNTER
 * -----------------------------
 * Specialized automation script to mirror APKs.
 * 
 * V3.5 UPDATE:
 * - Removed advanced spoofing (unnecessary complexity).
 * - Improved Button Detection: Strictly penalizes "Fast Download" / Installer buttons.
 * - Targets specific "Download APK (Size)" pattern.
 */

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

    console.log('--- APK HUNTER V3.5 (Logic Fix) ---');
    console.log(`Target: ${APP_ID}`);
    console.log(`URL: ${TARGET_URL}`);
    console.log('-----------------------------------------');

    // --- 2. PUPPETEER LOGIC ---
    const runScrape = async () => {
        let browser = null;
        try {
            console.log('Launching Browser...');
            
            // Standard Mobile User Agent
            const MOBILE_UA = 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36';

            browser = await puppeteer.launch({
                headless: "new",
                args: [
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    `--user-agent=${MOBILE_UA}` // Keep UA for layout consistency
                ]
            });

            const page = await browser.newPage();
            
            // Basic Setup - No heavy spoofing
            await page.setUserAgent(MOBILE_UA);
            await page.setViewport({ width: 393, height: 851, isMobile: true, hasTouch: true });

            const client = await page.target().createCDPSession();
            await client.send('Page.setDownloadBehavior', {
                behavior: 'allow',
                downloadPath: DOWNLOAD_DIR
            });

            // Block media to speed up
            await page.setRequestInterception(true);
            page.on('request', (req) => {
                const rType = req.resourceType();
                if (['image', 'font', 'stylesheet', 'media'].includes(rType)) req.abort();
                else req.continue();
            });

            console.log(`Navigating to ${TARGET_URL}...`);
            await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });

            console.log('Scanning for buttons...');
            
            // Wait for potential JS rendering
            await delay(3000);

            // Get all anchor handles
            const anchors = await page.$$('a, button'); // Look for buttons too
            let bestHandle = null;
            let bestText = '';
            let maxScore = -999999;

            for (const handle of anchors) {
                const meta = await page.evaluate(el => {
                    // Normalize text: lowercase, trimmed
                    const txt = (el.innerText || el.textContent || '').toLowerCase().replace(/\s+/g, ' ').trim();
                    const href = (el.href || '').toLowerCase();
                    const classes = (el.className || '').toLowerCase();
                    
                    let score = 0;
                    
                    // Filter: Must look like a download link/button
                    // If it's a generic link (home, login, etc), skip
                    if (!txt.includes('download') && !href.includes('download')) return { score: -100000, txt };

                    // --- NEGATIVE SCORING (The "Fast" Trap) ---
                    // These are strictly penalized to avoid the installer
                    if (txt.includes('fast download')) return { score: -100000, txt };
                    if (txt.includes('installer')) return { score: -100000, txt };
                    if (txt.includes('apkdone app')) return { score: -100000, txt };
                    if (classes.includes('fast')) return { score: -100000, txt };
                    if (txt.includes('telegram')) return { score: -100000, txt };

                    // --- POSITIVE SCORING (The Real Button) ---
                    
                    // 1. Text contains "Download APK"
                    if (txt.includes('download apk')) score += 100;
                    
                    // 2. Text contains File Size (e.g., "25 MB")
                    // Regex looks for digits followed by MB/GB
                    const hasSize = /\d+\s*(mb|gb)/.test(txt);
                    if (hasSize) score += 100;

                    // 3. The Holy Grail: "Download APK" AND Size
                    if (txt.includes('download apk') && hasSize) score += 500;

                    // 4. Domain check (Secondary)
                    // If href exists and points to an apk file directly (rare but possible)
                    if (href.endsWith('.apk')) score += 200;

                    return { score, txt, href };
                }, handle);

                // Log matching candidates for debugging (in workflow logs)
                if (meta.score > -1000) {
                     console.log(`Candidate: "${meta.txt}" | Score: ${meta.score}`);
                }

                if (meta.score > maxScore) {
                    maxScore = meta.score;
                    bestHandle = handle;
                    bestText = meta.txt;
                }
            }

            if (!bestHandle || maxScore <= 0) {
                throw new Error('No valid download button found (Score too low).');
            }

            console.log(`\n🎯 TARGET LOCKED: "${bestText}" (Score: ${maxScore})`);
            
            // CLICK THE BUTTON
            try {
                // Ensure element is clickable
                await bestHandle.evaluate(b => b.click()); 
                // Alternatively: await bestHandle.click();
            } catch (e) {
                console.log('JS Click failed, trying Puppeteer click:', e.message);
                await bestHandle.click();
            }

            console.log('Waiting for download...');
            
            // MONITOR DOWNLOAD FOLDER
            let downloadedFile = null;
            const maxWaitTime = 300000; // 5 mins
            const startTime = Date.now();

            while (Date.now() - startTime < maxWaitTime) {
                const files = fs.readdirSync(DOWNLOAD_DIR);
                // Look for non-crdownload files
                const finishedFile = files.find(f => !f.endsWith('.crdownload') && f.includes('.apk')); // Loose check for .apk
                const inProgress = files.find(f => f.endsWith('.crdownload'));

                if (finishedFile) {
                    const fullPath = path.join(DOWNLOAD_DIR, finishedFile);
                    const stats = fs.statSync(fullPath);
                    // Double check size to ensure it's not a dummy file
                    if (stats.size > 1024 * 1024) { // At least 1MB
                        downloadedFile = fullPath;
                        break;
                    }
                }
                if (inProgress) process.stdout.write('.'); 
                await delay(2000);
            }

            console.log('\n');

            if (!downloadedFile) throw new Error('Download timed out or failed.');

            console.log(`File acquired: ${path.basename(downloadedFile)}`);
            
            // Move to output
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
