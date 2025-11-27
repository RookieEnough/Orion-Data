const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
const fs = require('fs');
const path = require('path');
const https = require('https');

// Enable Stealth
puppeteer.use(StealthPlugin());

// --- UTILS ---
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));
const randomRange = (min, max) => Math.floor(Math.random() * (max - min + 1)) + min;

// --- FINGERPRINT & NOISE INJECTION ---
async function injectTrustProfile(page) {
    await page.evaluateOnNewDocument(() => {
        // 1. CANVAS NOISE (Make us look unique/imperfect)
        const toBlob = HTMLCanvasElement.prototype.toBlob;
        const toDataURL = HTMLCanvasElement.prototype.toDataURL;
        const getImageData = CanvasRenderingContext2D.prototype.getImageData;
        
        // Add tiny random noise to pixel data
        const noise = {
            r: Math.floor(Math.random() * 10) - 5,
            g: Math.floor(Math.random() * 10) - 5,
            b: Math.floor(Math.random() * 10) - 5
        };

        CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {
            const image = getImageData.call(this, x, y, w, h);
            // We don't actually modify the pixels (too slow), we just hook the method
            // to show we have a "custom" rendering path if inspected deeply
            return image;
        };

        // 2. FAKE PLUGINS (The "Fake Files" Cloudflare looks for)
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const PDF = { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' };
                const Media = { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' };
                return [PDF, Media];
            }
        });
        
        // 3. REMOVE ROBOT FLAGS
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    });
}

// --- HISTORY WARMER (Builds Trust) ---
async function warmUpBrowser(page) {
    console.log("🔥 Warming up browser history (Building Trust)...");
    try {
        // Go to Google first. This sets a "Referrer" and cookies.
        await page.goto('https://www.google.com', { waitUntil: 'domcontentloaded' });
        await delay(randomRange(1000, 2000));
        
        // Move mouse around on Google
        await page.mouse.move(randomRange(100, 500), randomRange(100, 500), { steps: 10 });
        
        // Maybe go to Bing too
        await page.goto('https://www.bing.com', { waitUntil: 'domcontentloaded' });
        await delay(1000);
        
        console.log("   ✅ Browser warmed up.");
    } catch (e) {
        console.log("   ⚠️ Warm-up skipped (network issue?)");
    }
}

// --- SMART CLOUDFLARE SOLVER ---
const findChallengeElement = async (page) => {
    const frames = page.frames();
    for (const frame of frames) {
        const btn = await frame.$('#challenge-stage, .ctp-checkbox-label, input[type="checkbox"]');
        if (btn) return { type: 'iframe', handle: btn, frame: frame };
    }
    const shadowHandle = await page.evaluateHandle(() => {
        function findInShadow(root) {
            const targets = ['#challenge-stage', '.ctp-checkbox-label', 'input[name="cf-turnstile-response"]'];
            for (const t of targets) {
                const el = root.querySelector(t);
                if (el) return el;
            }
            const children = root.querySelectorAll('*');
            for (const child of children) {
                if (child.shadowRoot) {
                    const res = findInShadow(child.shadowRoot);
                    if (res) return res;
                }
            }
            return null;
        }
        return findInShadow(document.body);
    });
    if (shadowHandle.asElement()) return { type: 'shadow', handle: shadowHandle, frame: page };
    return null;
};

const solveCloudflare = async (page) => {
    console.log('   🛡️  Checking Cloudflare status...');
    await delay(3000); 

    let attempt = 0;
    const maxAttempts = 15;

    while (attempt < maxAttempts) {
        const title = await page.title();
        const content = (await page.content()).toLowerCase();
        const isBlocked = title.includes('Just a moment') || content.includes('challenge-platform');
        
        if (!isBlocked) {
            console.log('   ✅ Cloudflare cleared!');
            return;
        }

        console.log(`   ⏳ Waiting for Cloudflare (Attempt ${attempt+1})...`);
        
        // 1. Mouse Jitter (Human Nervousness)
        await page.mouse.move(randomRange(100, 700), randomRange(100, 500), { steps: 20 });

        // 2. Try to Click
        const target = await findChallengeElement(page);
        if (target && target.handle) {
            console.log('   👉 Clicking Challenge Widget...');
            try {
                const box = await target.handle.boundingBox();
                if (box) {
                    // Click slightly off-center (Human)
                    const x = box.x + box.width / 2 + (Math.random() * 10 - 5);
                    const y = box.y + box.height / 2 + (Math.random() * 10 - 5);
                    await page.mouse.click(x, y, { delay: randomRange(50, 150) });
                } else {
                    await target.handle.click();
                }
            } catch (e) {}
        }

        await delay(5000);
        attempt++;
    }
    console.log("   ⚠️ Timeout. Reloading...");
    await page.reload({ waitUntil: 'domcontentloaded' });
    await delay(5000);
};

// --- NAVIGATION LOGIC ---
async function runApkDoneStrategy(page, appSlug) {
    let cleanName = appSlug.replace(/-/g, ' ').replace(/\b(mod|apk|premium|pro)\b/gi, '').trim();
    if (cleanName.length < 3) cleanName = appSlug;
    
    console.log(`🧠 Strategy: "${cleanName}" via Search Injection`);

    // STEP 1: Direct Entry with Referrer spoof via Warmup
    await page.goto(`https://apkdone.com/?s=${encodeURIComponent(cleanName)}`, { waitUntil: 'domcontentloaded' });
    await solveCloudflare(page);

    // STEP 2: Find Result
    const firstRes = await page.$('article a');
    if (firstRes) {
        console.log('   👉 Found App. Clicking...');
        await Promise.all([
            page.waitForNavigation().catch(()=>null),
            firstRes.click()
        ]);
        await solveCloudflare(page);
    }

    // STEP 3: Download Page
    console.log('📍 Finding Download Link...');
    const downloadPageBtn = await page.evaluateHandle(() => {
        return Array.from(document.querySelectorAll('a')).find(a => 
            a.href.endsWith('/download') || a.innerText.toLowerCase().includes('download apk')
        );
    });

    if (downloadPageBtn && downloadPageBtn.asElement()) {
        await Promise.all([
            page.waitForNavigation().catch(()=>null),
            downloadPageBtn.click()
        ]);
        await solveCloudflare(page);
    }

    // STEP 4: Final File
    console.log('📍 Waiting for file generation...');
    await delay(2000);
    const finalBtn = await page.evaluateHandle(() => {
        return Array.from(document.querySelectorAll('a')).find(a => /\(\s*\d+(\.\d+)?\s*[M|G]B\s*\)/i.test(a.innerText));
    });

    if (finalBtn && finalBtn.asElement()) {
        console.log('   🚀 STARTING DOWNLOAD');
        await finalBtn.click();
        await delay(20000); 
    } else {
        // Fallback for auto-start links
        const fallback = await page.$('a[href$=".apk"]');
        if (fallback) { await fallback.click(); await delay(20000); }
        else throw new Error("Link not found");
    }
}

// --- MAIN ---
(async () => {
    const args = process.argv.slice(2);
    const getArg = (key) => args.indexOf('--' + key) !== -1 ? args[args.indexOf('--' + key) + 1] : null;
    const TARGET_URL = getArg('url');
    const OUTPUT_FILE = getArg('out') || 'output.apk';
    const ID = getArg('id') || 'unknown';
    const IS_VISUAL = args.includes('--visual');

    if (!TARGET_URL) { console.error('❌ No URL'); process.exit(1); }
    
    // PERSISTENCE
    const PROFILE_PATH = path.join(process.cwd(), 'chrome_profile');
    if (!fs.existsSync(PROFILE_PATH)) fs.mkdirSync(PROFILE_PATH);

    const browser = await puppeteer.launch({
        // IF VISUAL, USE HEADLESS: FALSE (TRUE HEADFUL). 'new' IS DETECTABLE.
        headless: IS_VISUAL ? false : "new", 
        userDataDir: PROFILE_PATH,
        ignoreDefaultArgs: ['--enable-automation'],
        defaultViewport: null,
        args: [
            '--no-sandbox', 
            '--disable-setuid-sandbox', 
            '--window-size=1920,1080',
            '--start-maximized',
            '--disable-blink-features=AutomationControlled',
            '--disable-infobars',
            '--lang=en-US,en;q=0.9'
        ]
    });

    let foundApkUrl = null;

    try {
        const page = await browser.newPage();
        
        // 1. INJECT FINGERPRINT
        await injectTrustProfile(page);

        // 2. WARM UP (History Builder)
        await warmUpBrowser(page);

        // 3. NETWORK INTERCEPTION
        await page.setRequestInterception(true);
        page.on('request', req => {
            if (['image', 'media', 'font'].includes(req.resourceType())) req.continue();
            else req.continue();
        });

        page.on('response', async (response) => {
            const url = response.url();
            const type = response.headers()['content-type'] || '';
            if ((type.includes('android.package-archive') || url.endsWith('.apk')) && !url.includes('favicon')) {
                console.log(`\n🎣 CAUGHT APK URL: ${url}`);
                foundApkUrl = url;
            }
        });

        // 4. EXECUTE
        if (TARGET_URL.includes('apkdone.com')) {
            let slug = ID;
            try { slug = new URL(TARGET_URL).pathname.split('/').filter(p=>p)[0]; } catch(e){}
            await runApkDoneStrategy(page, slug);
        } else {
            await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded' });
            await solveCloudflare(page);
        }

        if (foundApkUrl) {
            console.log('\n✅ Downloading...');
            await downloadFile(foundApkUrl, OUTPUT_FILE);
            console.log(`🎉 SUCCESS! Saved to ${OUTPUT_FILE}`);
        } else {
            throw new Error("No APK URL intercepted.");
        }

    } catch (err) {
        console.error(`\n🔥 ERROR: ${err.message}`);
        if(IS_VISUAL) {
            console.log("⚠️  Window open for inspection (60s)...");
            await delay(60000);
        }
        process.exit(1);
    } finally {
        await browser.close();
    }
})();

function downloadFile(url, dest) {
    return new Promise((resolve, reject) => {
        const file = fs.createWriteStream(dest);
        https.get(url, (res) => {
            if (res.statusCode > 300 && res.statusCode < 400 && res.headers.location) {
                return downloadFile(res.headers.location, dest).then(resolve).catch(reject);
            }
            res.pipe(file);
            file.on('finish', () => file.close(resolve));
        }).on('error', (e) => { fs.unlink(dest, ()=>{}); reject(e); });
    });
}
