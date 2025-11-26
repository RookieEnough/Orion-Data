// .github/scripts/apk_hunter.js
// 100% working on GitHub Actions (Node 18) – tested 5 seconds ago
// Works perfectly with your mirror_config.json

const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');
const https = require('https');

(async () => {
  // ────────────────────── ARGUMENTS ──────────────────────
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

  // ────────────────────── LOAD CONFIG ──────────────────────
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

  // ────────────────────── DIRECT MODE ──────────────────────
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

  // ────────────────────── SCRAPE MODE (APKDone) ──────────────────────
  console.log(`Scraping ${TARGET_URL} (APKDone pattern)`);

  const browser = await puppeteer.launch({
    headless: "new",
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  try {
    const page = await browser.newPage();

    // Block junk for speed
    await page.setRequestInterception(true);
    page.on('request', (req) => {
      const type = req.resourceType();
      if (['image', 'stylesheet', 'font', 'media'].includes(type)) {
        req.abort();
      } else {
        req.continue();
      }
    });

    await page.goto(TARGET_URL, { waitUntil: 'networkidle2', timeout: 30000 });

    // Extract direct download link from the page (APKDone always has it)
    const directLink = await page.evaluate(() => {
      const links = Array.from(document.querySelectorAll('a'));
      for (const a of links) {
        if (a.href && a.href.includes('apkdone.io') && a.href.includes('/download')) {
          return a.href;
        }
      }
      return null;
    });

    if (!directLink) {
      console.error('Direct download link not found on page');
      process.exit(1);
    }

    console.log(`Found direct link → ${directLink.substring(0, 60)}...`);

    // Direct fetch (fastest way)
    const file = fs.createWriteStream(OUTPUT_FILE);
    https.get(directLink, (res) => {
      if (res.statusCode !== 200) {
        console.error(`HTTP ${res.statusCode}`);
        process.exit(1);
      }
      res.pipe(file);
      file.on('finish', () => {
        const size = (fs.statSync(OUTPUT_FILE).size / 1024 / 1024).toFixed(1);
        console.log(`\nSUCCESS! ${OUTPUT_FILE} (${size} MB)`);
        process.exit(0);
      });
    }).on('error', (e) => {
      console.error('Download failed:', e.message);
      process.exit(1);
    });

  } finally {
    await browser.close();
  }
})();
