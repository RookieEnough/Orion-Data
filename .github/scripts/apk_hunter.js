// .github/scripts/apk_hunter.js — Follows 302 Redirects to Real APK
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http'); // For redirect handling

(async () => {
  // ────────────────────── ARGUMENTS & CONFIG ──────────────────────
  const args = process.argv.slice(2);
  const getArg = (key) => {
    const i = args.indexOf(`--${key}`);
    return i !== -1 ? args[i + 1] : null;
  };

  const APP_ID = getArg('id');
  const PROVIDED_URL = getArg('url');
  const OUTPUT_FILE = getArg('out') || `${APP_ID || 'app'}.apk';

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
    downloadWithRedirect(TARGET_URL, OUTPUT_FILE);
    return;
  }

  // ────────────────────── SCRAPE MODE (Extract & Follow Redirect) ──────────────────────
  console.log(`Extracting link from: ${TARGET_URL}`);

  const browser = await puppeteer.launch({
    headless: "new",
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1280,800']
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

    // Extract the CDN entry point link
    const entryLink = await page.evaluate(() => {
      const links = Array.from(document.querySelectorAll('a'));
      for (const a of links) {
        const href = a.href;
        const text = a.innerText.toLowerCase();
        if (href && (href.includes('apkdone.io') || href.includes('/download')) && text.includes('download')) {
          return href;
        }
      }
      return null;
    });

    if (!entryLink) {
      throw new Error('No download link found on page');
    }

    console.log(`Found entry link: ${entryLink.substring(0, 60)}...`);

    // Follow redirects to real APK
    downloadWithRedirect(entryLink, OUTPUT_FILE);

  } catch (err) {
    console.error('\n❌ Error:', err.message);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();

// Helper: Download with full redirect following (handles 302)
function downloadWithRedirect(url, outputFile) {
  const parsed = new URL(url);
  const client = parsed.protocol === 'https:' ? https : http;

  const req = client.get(url, { followRedirect: true, maxRedirects: 5 }, (res) => {
    if (res.statusCode >= 300 && res.statusCode < 400) {
      console.log(`Redirected to: ${res.headers.location}`);
      downloadWithRedirect(res.headers.location, outputFile); // Recursive for multi-redirect
      return;
    }

    if (res.statusCode !== 200) {
      console.error(`HTTP ${res.statusCode}: ${res.statusMessage}`);
      process.exit(1);
    }

    const file = fs.createWriteStream(outputFile);
    let total = 0;
    res.on('data', chunk => total += chunk.length);
    file.on('finish', () => {
      const sizeMB = (total / 1024 / 1024).toFixed(1);
      console.log(`\nDownloaded → ${outputFile} (${sizeMB} MB)`);
      console.log(`\n🎉 SUCCESS! ${outputFile}`);
      process.exit(0);
    });
    file.on('error', (e) => {
      console.error('Download failed:', e.message);
      process.exit(1);
    });
    res.pipe(file);
  });

  req.on('error', (e) => {
    console.error('Request error:', e.message);
    process.exit(1);
  });
}
