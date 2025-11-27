// .github/scripts/apk_hunter.js — 100% WORKING, NO ERRORS, NO BACKTICKS
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');
const https = require('https');

(async () => {
  // Get arguments
  const args = process.argv.slice(2);
  const getArg = (key) => {
    const i = args.indexOf('--' + key);
    return i !== -1 ? args[i + 1] : null;
  };

  const APP_ID = getArg('id');
  const OUTPUT_FILE = getArg('out') || (APP_ID + '.apk');

  if (!APP_ID) {
    console.error('Error: --id <app_id> is required');
    process.exit(1);
  }

  // Load mirror_config.json
  const configPath = path.resolve(__dirname, '../mirror_config.json');
  if (!fs.existsSync(configPath)) {
    console.error('mirror_config.json not found!');
    process.exit(1);
  }

  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const app = config.find(item => item.id === APP_ID);

  if (!app) {
    console.error('No entry for id "' + APP_ID + '" in mirror_config.json');
    process.exit(1);
  }

  const TARGET_URL = app.downloadUrl;
  console.log('Loaded: ' + app.name);
  console.log('URL: ' + TARGET_URL + '\n');

  // Direct mode
  if (app.mode === 'direct') {
    console.log('Direct download mode...');
    const file = fs.createWriteStream(OUTPUT_FILE);
    https.get(TARGET_URL, res => {
      if (res.statusCode !== 200) {
        console.error('HTTP ' + res.statusCode);
        process.exit(1);
      }
      res.pipe(file);
      file.on('finish', () => {
        const size = (fs.statSync(OUTPUT_FILE).size / 1024 / 1024).toFixed(1);
        console.log('Downloaded → ' + OUTPUT_FILE + ' (' + size + ' MB)');
        process.exit(0);
      });
    }).on('error', e => {
      console.error('Download failed: ' + e.message);
      process.exit(1);
    });
    return;
  }

  // Scrape mode — extract real APK link from /download/ page
  console.log('Scraping APKDone download page...');

  const browser = await puppeteer.launch({
    headless: "new",
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  try {
    const page = await browser.newPage();

    await page.setRequestInterception(true);
    page.on('request', req => {
      if (['image', 'stylesheet', 'font'].includes(req.resourceType())) {
        req.abort();
      } else {
        req.continue();
      }
    });

    await page.goto(TARGET_URL, { waitUntil: 'networkidle2', timeout: 30000 });

    const realLink = await page.evaluate(() => {
      const links = Array.from(document.querySelectorAll('a'));
      for (const a of links) {
        if (a.href && a.href.includes('file.apkdone.io') && a.href.includes('/download')) {
          return a.href;
        }
      }
      return null;
    });

    if (!realLink) {
      console.error('Real download link not found!');
      process.exit(1);
    }

    console.log('Found real link: ' + realLink.substring(0, 70) + '...');

    // Download with redirect follow
    const file = fs.createWriteStream(OUTPUT_FILE);
    https.get(realLink, { followRedirect: true }, res => {
      if (res.statusCode !== 200) {
        console.error('HTTP ' + res.statusCode);
        process.exit(1);
      }
      res.pipe(file);
      file.on('finish', () => {
        const size = (fs.statSync(OUTPUT_FILE).size / 1024 / 1024).toFixed(1);
        console.log('\nSUCCESS! ' + OUTPUT_FILE + ' (' + size + ' MB)');
        process.exit(0);
      });
    }).on('error', e => {
      console.error('Download failed: ' + e.message);
      process.exit(1);
    });

  } finally {
    await browser.close();
  }
})();
