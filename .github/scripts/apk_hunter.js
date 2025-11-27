const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');
const https = require('https');
const { URL } = require('url');

// Wrap in IIFE
(async () => {
    // --- 1. CONFIGURATION & SETUP ---
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

    // Load from JSON if ID is present but URL is missing
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
            console.log('Warning: Could not read mirror_config.json: ' + e.message);
        }
    }

    // Defaults / Validation
    if (!APP_ID) APP_ID = 'unknown-app';
    if (!OUTPUT_FILE) OUTPUT_FILE = APP_ID + '.apk';
    
    if (!TARGET_URL) {
        console.error('Error: No target URL provided. Use --url or ensure ID exists in mirror_config.json');
        process.exit(1);
    }

    console.log('--- APK HUNTER V2.1 (Selector Fix) ---');
    console.log('Target ID: ' + APP_ID);
    console.log('Target URL: ' + TARGET_URL);
    console.log('Mode: ' + MODE);
    console.log('Output: ' + OUTPUT_FILE);
    console.log('-----------------------');

    // --- 2. DOWNLOAD UTILITY (Native Node.js with Redirects) ---
    const downloadFile = (url, destPath, headers = {}) => {
        return new Promise((resolve, reject) => {
            const file = fs.createWriteStream(destPath);
            
            const makeRequest = (currentUrl, redirectCount = 0) => {
                if (redirectCount > 10) {
                    reject(new Error('Too many redirects'));
                    return;
                }

                const options = {
                    headers: headers,
                    timeout: 30000 // 30s connection timeout
                };

                console.log('Requesting: ' + currentUrl);
                
                https.get(currentUrl, options, (response) => {
                    // Handle Redirects (301, 302, 303, 307, 308)
                    if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
                        const newUrl = new URL(response.headers.location, currentUrl).href;
                        console.log('Redirect (' + response.statusCode + ') -> ' + newUrl);
                        makeRequest(newUrl, redirectCount + 1);
                        return;
                    }

                    // Handle Success
                    if (response.statusCode === 200) {
                        const size = parseInt(response.headers['content-length'] || '0');
                        console.log('Response 200 OK. Content-Length: ' + (size ? (size / 1024 / 1024).toFixed(2) + ' MB' : 'Unknown'));
                        
                        // Check content type just in case
                        const contentType = response.headers['content-type'] || '';
                        if (contentType.includes('text/html')) {
                            console.warn('Warning: Response looks like HTML, not APK.');
                        }

                        response.pipe(file);
                        
                        file.on('finish', () => {
                            file.close(() => resolve());
                        });
                    } else {
                        reject(new Error('HTTP Status ' + response.statusCode));
                    }
                }).on('error', (err) => {
                    fs.unlink(destPath, () => {});
                    reject(err);
                });
            };

            makeRequest(url);
        });
    };

    // --- 3. SCRAPE MODE (Puppeteer) ---
    const runScrape = async () => {
        let browser = null;
        try {
            console.log('Launching Headless Browser...');
            browser = await puppeteer.launch({
                headless: "new",
                args: [
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--window-size=1280,800'
                ]
            });

            const page = await browser.newPage();
            
            // Log browser console output to node console for debugging selector
            page.on('console', msg => console.log('PAGE LOG:', msg.text()));

            // Optimization: Block images/fonts
            await page.setRequestInterception(true);
            page.on('request', (req) => {
                const rType = req.resourceType();
                if (['image', 'font', 'stylesheet', 'media'].includes(rType)) req.abort();
                else req.continue();
            });

            const userAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
            await page.setUserAgent(userAgent);

            console.log('Navigating to ' + TARGET_URL);
            await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });

            // Locate the gateway link
            console.log('Hunting for download button...');
            
            // APKDone Logic: Use a SCORING system to find the right button
            const result = await page.evaluate(() => {
                const anchors = Array.from(document.querySelectorAll('a'));
                
                const cleanText = (el) => (el.innerText || el.textContent || '').toLowerCase().trim();

                let bestCandidate = null;
                let maxScore = -1;

                anchors.forEach(a => {
                    let score = 0;
                    const txt = cleanText(a);
                    const href = a.href;

                    // 1. Basic Eligibility: Must contain /download/ or point to file.apkdone
                    // (Some links are relative "/capcut.../download", some are absolute)
                    if (!href.includes('/download') && !href.includes('file.apkdone.io')) return;

                    // 2. CRITICAL FILTER: "Fast Download" is the installer (Ad)
                    if (txt.includes('fast download')) {
                        console.log('Ignored Fast Download: ' + txt);
                        return;
                    }
                    if (txt.includes('with apkdone')) {
                        console.log('Ignored Installer: ' + txt);
                        return;
                    }

                    // 3. SCORING
                    
                    // Boost for "Download APK" (The text on the real button)
                    if (txt.includes('download apk')) score += 20;

                    // Boost for mentioning size (e.g. "277 MB")
                    // Regex looks for digits followed by optional space and MB/GB
                    if (/\d+\s*(mb|gb)/.test(txt)) score += 10;

                    // Boost for having the gateway domain
                    if (href.includes('file.apkdone.io')) score += 5;

                    console.log(`Candidate: "${txt}" | Score: ${score} | Href: ${href.substring(0, 50)}...`);

                    if (score > maxScore && score > 0) {
                        maxScore = score;
                        bestCandidate = { url: href, method: `score-${score}`, text: txt };
                    }
                });

                return bestCandidate;
            });

            if (!result || !result.url) {
                throw new Error('Could not find a valid download URL on the page.');
            }

            console.log(`\n🎯 Selected Target:\nText: "${result.text}"\nMethod: ${result.method}\nURL: ${result.url}\n`);

            // EXTRACT COOKIES FOR AUTHENTICATED DOWNLOAD
            const cookies = await page.cookies();
            const cookieString = cookies.map(c => c.name + '=' + c.value).join('; ');
            
            console.log('Extracted Session Cookies (' + cookies.length + ')');

            // Close browser before downloading to save resources
            await browser.close();
            browser = null;

            // --- HANDOFF TO NODE DOWNLOADER ---
            console.log('Starting Authenticated Download...');
            await downloadFile(result.url, OUTPUT_FILE, {
                'Cookie': cookieString,
                'Referer': TARGET_URL,
                'User-Agent': userAgent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9'
            });

            console.log('Download Complete: ' + OUTPUT_FILE);

        } catch (err) {
            console.error('Scrape Failed: ' + err.message);
            if (browser) await browser.close();
            process.exit(1);
        }
    };

    // --- 4. EXECUTION ---
    try {
        if (MODE === 'scrape') {
            await runScrape();
        } else {
            console.log('Starting Direct Download...');
            await downloadFile(TARGET_URL, OUTPUT_FILE, {
                'User-Agent': 'Mozilla/5.0 (Android 13; Mobile; rv:109.0) Gecko/113.0 Firefox/113.0'
            });
            console.log('Download Complete: ' + OUTPUT_FILE);
        }
        
        // Final Verification
        if (fs.existsSync(OUTPUT_FILE)) {
            const stats = fs.statSync(OUTPUT_FILE);
            // Just warn if small, don't fail, so we can debug the file artifacts if needed
            if (stats.size < 1000 * 1000 * 20) { // < 20MB
                 console.warn('\n⚠️ WARNING: File is small (' + (stats.size / 1024 / 1024).toFixed(2) + ' MB). Check if it is the installer or the real App.');
            }
            process.exit(0);
        } else {
            console.error('Error: Output file not found.');
            process.exit(1);
        }

    } catch (e) {
        console.error('Critical Error: ' + e.message);
        process.exit(1);
    }
})();
