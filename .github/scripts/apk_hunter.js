/*
 * ORION DATA - DEEP DIVE APK HUNTER (V4.1 - VISUAL DEBUG)
 * -------------------------------------------------------
 * A complete rewrite of the scraping logic to handle multi-step
 * download flows (Landing -> Version Select -> Final Link).
 * 
 * FEATURES:
 * - Network Interception: sniffs for .apk headers directly.
 * - Deep Navigation: Follows clicks across multiple pages.
 * - Anti-Deception: Aggressively ignores "Fast Download" / Installers.
 * - VISUAL DEBUGGING: Takes screenshots at every step.
 */

const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');
const https = require('https');
const { URL } = require('url');

// --- UTILS ---
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

// --- SCREENSHOT MANAGER ---
const SCREENSHOT_DIR = path.resolve(process.cwd(), 'debug_screenshots');
if (!fs.existsSync(SCREENSHOT_DIR)) fs.mkdirSync(SCREENSHOT_DIR);

const takeScreenshot = async (page, name) => {
    try {
        const filename = `${Date.now()}_${name}.jpg`;
        const filepath = path.join(SCREENSHOT_DIR, filename);
        // Take full page to see context, quality 60 to save space
        await page.screenshot({ path: filepath, type: 'jpeg', quality: 60, fullPage: true });
        console.log(`📸 Captured: ${filename}`);
    } catch (e) {
        console.log(`⚠️ Screenshot failed: ${e.message}`);
    }
};

// --- SCORING ALGORITHM ---
// Decides which button is the "Real" download button
const evaluateButton = (element) => {
    let txt = (element.innerText || element.textContent || '').toLowerCase().replace(/\s+/g, ' ').trim();
    let href = (element.href || '').toLowerCase();
    let classes = (element.className || '').toLowerCase();
    
    let score = 0;

    // --- INSTANT DISQUALIFICATION (The "Fast" Trap) ---
    if (txt.includes('fast download')) return -99999;
    if (txt.includes('high speed')) return -99999;
    if (txt.includes('apkdone installer')) return -99999;
    if (txt.includes('play store')) return -99999;
    if (txt.includes('telegram')) return -99999;
    if (classes.includes('fast')) return -99999;

    // --- POSITIVE SIGNALS ---
    if (txt.includes('download')) score += 10;
    if (txt.includes('apk')) score += 20;
    if (txt.includes('mod')) score += 15;
    if (txt.includes('original')) score += 15;
    
    // File Size is a huge indicator of the real button (e.g. "150 MB")
    // Installers are usually small, but the text usually indicates the real size
    if (/\d+\s*(mb|gb)/.test(txt)) score += 50;
    
    // Version numbers often indicate the specific file
    if (/v\d+\.\d+/.test(txt)) score += 30;

    // Link pointing to an apk file directly
    if (href.includes('.apk')) score += 100;

    // APKDone specific: The "green" buttons are usually real, "red" or flashy are ads.
    // We can't see color, but we can check class names for generic 'btn' vs 'ad'.
    if (classes.includes('active')) score += 5;

    return { score, txt, element };
};

(async () => {
    // --- ARGUMENT PARSING ---
    const args = process.argv.slice(2);
    const getArg = (key) => {
        const index = args.indexOf('--' + key);
        return index !== -1 ? args[index + 1] : null;
    };

    const TARGET_URL = getArg('url');
    const OUTPUT_FILE = getArg('out') || 'output.apk';
    const ID = getArg('id') || 'unknown';

    if (!TARGET_URL) {
        console.error('❌ No URL provided');
        process.exit(1);
    }

    console.log(`\n🚀 STARTING DEEP DIVE HUNTER: ${ID}`);
    console.log(`Target: ${TARGET_URL}`);

    // --- BROWSER SETUP ---
    const browser = await puppeteer.launch({
        headless: "new",
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--window-size=1280,1024' // Desktop size often hides mobile ads
        ]
    });

    let foundApkUrl = null;

    try {
        const page = await browser.newPage();
        
        // Use a generic Desktop UA to avoid mobile redirect loops
        await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36');

        // --- NETWORK INTERCEPTOR ---
        // This is the magic. We watch every request. If we see an APK, we grab the URL and abort the browser load.
        await page.setRequestInterception(true);
        
        page.on('request', req => {
            // Block ads/media to speed up
            const rType = req.resourceType();
            if (['image', 'media', 'font'].includes(rType)) {
                req.abort();
                return;
            }
            req.continue();
        });

        page.on('response', async (response) => {
            try {
                const url = response.url();
                const contentType = response.headers()['content-type'] || '';
                const contentDisposition = response.headers()['content-disposition'] || '';

                // Check signatures of a real APK file
                const isApkType = contentType.includes('application/vnd.android.package-archive') || 
                                  contentType.includes('application/octet-stream');
                
                const isApkName = url.toLowerCase().endsWith('.apk') || 
                                  contentDisposition.toLowerCase().includes('.apk');

                if (isApkType && isApkName && !url.includes('google-analytics')) {
                    console.log(`\n🎣 CAUGHT APK STREAM: ${url}`);
                    foundApkUrl = url;
                }
            } catch (e) { /* Ignore intercept errors */ }
        });

        // --- NAVIGATION LOOP ---
        // We will traverse up to 3 pages deep.
        let currentUrl = TARGET_URL;
        
        for (let step = 1; step <= 3; step++) {
            if (foundApkUrl) break;

            console.log(`\n📍 [Step ${step}] Navigating: ${currentUrl}`);
            
            // Go to page
            try {
                await page.goto(currentUrl, { waitUntil: 'domcontentloaded', timeout: 45000 });
                await takeScreenshot(page, `step${step}_loaded`);
            } catch (e) {
                console.log('Navigation timeout, continuing anyway...');
            }

            // 1. Scroll to trigger lazy loads
            console.log('   Scrolling page...');
            await page.evaluate(async () => {
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    const distance = 100;
                    const timer = setInterval(() => {
                        const scrollHeight = document.body.scrollHeight;
                        window.scrollBy(0, distance);
                        totalHeight += distance;
                        if(totalHeight >= scrollHeight || totalHeight > 5000){
                            clearInterval(timer);
                            resolve();
                        }
                    }, 50);
                });
            });
            await delay(2000); // Wait for post-scroll content
            await takeScreenshot(page, `step${step}_scrolled`);

            if (foundApkUrl) break;

            // 2. Scan for buttons
            const handles = await page.$$('a, button, div[role="button"], input[type="submit"]');
            let bestCandidate = null;
            let maxScore = -1000;

            for (const h of handles) {
                const data = await page.evaluate(evaluateButton, h);
                if (data.score > maxScore) {
                    maxScore = data.score;
                    bestCandidate = h;
                }
            }

            if (!bestCandidate || maxScore <= 0) {
                console.log('   ❌ No valid download buttons found on this page.');
                await takeScreenshot(page, `step${step}_failed_nobutton`);
                break; // Stop if dead end
            }

            // VISUAL DEBUG: Highlight the target
            await page.evaluate((el) => {
                el.style.border = '10px solid red';
                el.style.boxShadow = '0 0 50px red';
                el.style.backgroundColor = 'yellow';
                el.style.color = 'black';
                el.setAttribute('data-target-debug', 'true');
                el.scrollIntoView({block: 'center'});
            }, bestCandidate);
            
            console.log(`   👉 Clicking best candidate (Score: ${maxScore})`);
            await takeScreenshot(page, `step${step}_target_locked`);

            // 3. Click the best button
            const navPromise = page.waitForNavigation({ timeout: 15000 }).catch(() => 'timeout');
            
            try {
                await bestCandidate.click();
            } catch (e) {
                await page.evaluate(el => el.click(), bestCandidate);
            }

            // Wait to see what happens (New Page? or File Download?)
            const result = await Promise.race([
                navPromise,
                delay(5000) // Wait 5s for network interceptor to maybe catch a file
            ]);
            
            if (foundApkUrl) break;

            // If we navigated to a new URL, update loop variable
            const newUrl = page.url();
            if (newUrl !== currentUrl && !newUrl.includes('google_vignette')) {
                // If it's a google ad interstitial, go back or wait
                if (newUrl.includes('#google_vignette')) {
                    console.log('   ⚠️ Ad Interstitial detected. Waiting...');
                    await delay(3000); // Sometimes they auto-close
                } else {
                    currentUrl = newUrl;
                }
            } else {
                // If URL didn't change, maybe a hidden countdown appeared?
                // Wait a bit more then try again or stop
                console.log('   ⏳ URL did not change. Waiting for dynamic content...');
                await delay(5000);
            }
        }

        // --- FINAL RESULT HANDLING ---
        if (foundApkUrl) {
            console.log('\n✅ DOWNLOADING FILE...');
            // Download using Node native HTTPS to avoid puppeteer file management issues
            await downloadFile(foundApkUrl, OUTPUT_FILE);
            
            const stats = fs.statSync(OUTPUT_FILE);
            if (stats.size < 1024 * 1024) {
                 throw new Error(`Downloaded file is too small (${stats.size} bytes). Likely an installer stub.`);
            }
            console.log(`🎉 Success! Saved ${OUTPUT_FILE} (${(stats.size / 1024 / 1024).toFixed(2)} MB)`);
        } else {
            throw new Error('Could not find a valid APK link after 3 steps.');
        }

    } catch (err) {
        console.error(`\n🔥 FATAL ERROR: ${err.message}`);
        // Capture the error state
        try {
             if (browser) {
                 const pages = await browser.pages();
                 if (pages.length > 0) await takeScreenshot(pages[0], 'fatal_error');
             }
        } catch(e) {}
        process.exit(1);
    } finally {
        await browser.close();
    }
})();

// --- NATIVE DOWNLOADER ---
// Puppeteer's download behavior can be flaky in headless.
// We use the intercepted URL to download natively.
function downloadFile(url, dest) {
    return new Promise((resolve, reject) => {
        const file = fs.createWriteStream(dest);
        const request = https.get(url, (response) => {
            // Handle Redirects
            if (response.statusCode === 301 || response.statusCode === 302) {
                return downloadFile(response.headers.location, dest).then(resolve).catch(reject);
            }

            if (response.statusCode !== 200) {
                reject(new Error(`Failed to download: HTTP ${response.statusCode}`));
                return;
            }

            response.pipe(file);
            file.on('finish', () => {
                file.close(() => resolve());
            });
        });

        request.on('error', (err) => {
            fs.unlink(dest, () => {}); // Delete failed file
            reject(err);
        });
    });
}
