// .github/scripts/apk_hunter.js — Handles Double-Click Ad Redirect Pattern
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');
const https = require('https');

(async () => {
  // ────────────────────── ARGUMENTS & CONFIG ──────────────────────
  const args = process.argv.slice(2);
  const getArg = (key) => {
    const i = args.indexOf(`--${key}`);
    return i !== -1 ? args[i + 1] : null;
  };

  const APP_ID = getArg('id');
  const PROVIDED_URL = getArg('url');
  const OUTPUT_FILE = getArg('out') || `${APP_ID || 'app'}.apk`;

  if (!APP_ID) {
    console.error('Error: --id <app_id> is required');
    process.exit(1);
  }

  let TARGET_URL = PROVIDED_URL;
  let MODE = 'scrape';

  if (!TARGET_URL) {
    const configPath = path.resolve(__dirname, '../mirror_config.json');
    if (!fs.existsSync(configPath)) {
      console.error('mirror_config.json not found!');
      process.exit(1);
    }
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    const app = config.find(item => item.id === APP_ID);
    if (!app) {
      console.error(`No entry for id "${APP_ID}" in mirror_config.json`);
      process.exit(1);
    }
    TARGET_URL = app.downloadUrl;
    MODE = app.mode || 'scrape';
    console.log(`Loaded: ${app.name} (${MODE} mode)`);
    console.log(`URL: ${TARGET_URL}\n`);
  }

  // Direct mode (e.g., telegram-example)
  if (MODE === 'direct') {
    console.log('Direct download mode...');
    const file = fs.createWriteStream(OUTPUT_FILE);
    https.get(TARGET_URL, (res) => {
      if (res.statusCode !== 200) {
        console.error(`HTTP ${res.statusCode}`);
        process.exit(1);
      }
      res.pipe(file);
      file.on('finish', () => {
        const size = (fs.statSync(OUTPUT_FILE).size / 1024 / 1024).toFixed(1);
        console.log(`Downloaded → ${OUTPUT_FILE} (${size} MB)`);
        process.exit(0);
      });
    }).on('error', (e) => {
      console.error('Download failed:', e.message);
      process.exit(1);
    });
    return;
  }

  // ────────────────────── SCRAPE MODE (Ad Redirect Pattern) ──────────────────────
  console.log(`Starting scrape with ad redirect handling for: ${TARGET_URL}`);

  const browser = await puppeteer.launch({
    headless: "new",
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1280,800']
  });

  try {
    const page = await browser.newPage();

    // Block junk
    await page.setRequestInterception(true);
    page.on('request', (req) => {
      const type = req.resourceType();
      if (['image', 'stylesheet', 'font', 'media'].includes(type)) {
        req.abort();
      } else {
        req.continue();
      }
    });

    // Enable downloads
    const client = await page.target().createCDPSession();
    await client.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: path.resolve(__dirname, '../downloads') });

    await page.goto(TARGET_URL, { waitUntil: 'networkidle2', timeout: 30000 });

    // Step 1: First page - Double-click orange "Download" (first = ad, back, second = /download/)
    console.log('First page: Double-clicking orange Download (handle ad redirect)...');
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('a, button')).find(el => 
        el.innerText.toLowerCase().includes('download') && 
        (el.className.includes('orange') || el.style.backgroundColor === 'orange' || el.innerText === 'Download')
      );
      if (btn) {
        btn.click();
      }
    });
    await page.waitForTimeout(3000); // Let redirect to ad happen

    // Go back from ad
    await page.goBack({ waitUntil: 'networkidle2', timeout: 10000 });
    await page.waitForTimeout(2000); // Stabilize

    // Second click on orange - now goes to /download/
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('a, button')).find(el => 
        el.innerText.toLowerCase().includes('download')
      );
      if (btn) {
        btn.click();
      }
    });
    await page.waitForTimeout(5000); // Wait for /download/ load

    // Now on /download/ page
    console.log('On /download/ page: Double-clicking white Download APK (XXX MB)...');
    await page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 10000 });

    // Ignore orange "Fast Download with APKDone" - find white "Download APK (MB)"
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('a, button')).find(el => 
        el.innerText.toLowerCase().includes('download apk') && el.innerText.includes('mb') &&
        (!el.className.includes('orange') && el.style.backgroundColor !== 'orange') // White/non-orange
      );
      if (btn) {
        btn.click();
      }
    });
    await page.waitForTimeout(3000); // Ad redirect

    // Go back from ad
    await page.goBack({ waitUntil: 'networkidle2', timeout: 10000 });
    await page.waitForTimeout(2000);

    // Second click on white button - real download starts
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('a, button')).find(el => 
        el.innerText.toLowerCase().includes('download apk') && el.innerText.includes('mb')
      );
      if (btn) {
        btn.click();
      }
    });

    console.log('Real download triggered... waiting for APK');

    // Wait for download (monitor folder)
    const DOWNLOAD_PATH = path.resolve(__dirname, '../downloads');
    if (!fs.existsSync(DOWNLOAD_PATH)) fs.mkdirSync(DOWNLOAD_PATH, { recursive: true });

    const start = Date.now();
    let apkFile = null;
    while (Date.now() - start < 60000) { // 60s max
      const files = fs.readdirSync(DOWNLOAD_PATH);
      apkFile = files.find(f => f.endsWith('.apk') && !f.includes('.crdownload'));
      if (apkFile) {
        const stats = fs.statSync(path.join(DOWNLOAD_PATH, apkFile));
        if (stats.size > 50 * 1024 * 1024) {
          console.log(`Downloaded: ${apkFile} (${(stats.size / 1024 / 1024).toFixed(1)} MB)`);
          break;
        }
      }
      process.stdout.write('.');
      await new Promise(r => setTimeout(r, 3000));
    }

    if (apkFile) {
      const finalPath = path.resolve(__dirname, '../', OUTPUT_FILE);
      fs.renameSync(path.join(DOWNLOAD_PATH, apkFile), finalPath);
      console.log(`\nSUCCESS! ${OUTPUT_FILE}`);
      process.exit(0);
    } else {
      throw new Error('No APK downloaded');
    }

  } catch (err) {
    console.error('\n❌ Error:', err.message);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
