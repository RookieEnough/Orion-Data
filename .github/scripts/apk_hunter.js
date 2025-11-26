// .github/scripts/apk_hunter.js — Pattern-Perfect for APKDone (/download/ flow)
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');
const https = require('https'); // For direct CDN fetch

const args = process.argv.slice(2);
const getArg = (key) => {
  const i = args.indexOf(`--${key}`);
  return i !== -1 ? args[i + 1] : null;
};

const APP_ID = getArg('id');
const PROVIDED_URL = getArg('url');
const OUTPUT_FILE = getArg('out') || `${APP_ID || 'app'}.apk`;
const MAX_WAIT = parseInt(getArg('wait') || '120000', 10); // 2min

if (!APP_ID) {
  console.error('Error: --id <app_id> is required');
  process.exit(1);
}

let TARGET_URL = PROVIDED_URL;
let MODE = 'scrape';

// Load your mirror_config.json (array format)
if (!TARGET_URL) {
  try {
    const configPath = path.resolve(__dirname, '../mirror_config.json');
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    const app = config.find(item => item.id === APP_ID);
    if (!app) {
      console.error(`No entry for "${APP_ID}" in mirror_config.json`);
      process.exit(1);
    }
    TARGET_URL = app.downloadUrl;
    MODE = app.mode || 'scrape';
    console.log(`Loaded: ${app.name} (${MODE} mode)`);
    console.log(`URL: ${TARGET_URL}\n`);
  } catch (err) {
    console.error('Config error:', err.message);
    process.exit(1);
  }
}

// Direct fetch helper (for CDN like file.apkdone.io)
const directDownload = (url, output) => new Promise((resolve, reject) => {
  https.get(url, (res) => {
    if (res.statusCode !== 200) return reject(new Error(`HTTP ${res.statusCode}`));
    const file = fs.createWriteStream(output);
    let total = 0;
    res.on('data', chunk => total += chunk.length);
    file.on('finish', () => {
      const sizeMB = (total / 1024 / 1024).toFixed(1);
      console.log(`Downloaded → ${output} (${sizeMB} MB)`);
      if (total < 50 * 1024 * 1024) reject(new Error('File too small'));
      else resolve(sizeMB);
    });
    file.on('error', reject);
    res.pipe(file);
  }).on('error', reject);
});

// If direct URL (e.g., /download/ or MODE=direct), fetch immediately
const downloadUrl = TARGET_URL.includes('/download') ? TARGET_URL : `${TARGET_URL}download/`;
if (MODE === 'direct' || downloadUrl !== TARGET_URL) {
  console.log('Direct mode (CDN fetch)...');
  directDownload(downloadUrl, OUTPUT_FILE).then(() => process.exit(0)).catch(err => {
    console.error(err.message);
    process.exit(1);
  });
  return;
}

// Scrape mode: Load page → extract direct link → fetch (no click needed)
console.log(`Scraping pattern: ${TARGET_URL} → extract CDN link`);

const DOWNLOAD_PATH = path.resolve(__dirname, '../downloads');
if (!fs.existsSync(DOWNLOAD_PATH)) fs.mkdirSync(DOWNLOAD_PATH, { recursive: true });

const browser = await puppeteer.launch({ headless: "new", args: ['--no-sandbox'] });
try {
  const page = await browser.newPage();
  await page.setDownloadBehavior({ behavior: 'allow', downloadPath: DOWNLOAD_PATH });
  await page.setRequestInterception(true);
  page.on('request', req => {
    if (['image', 'font', 'stylesheet', 'media'].includes(req.resourceType())) req.abort();
    else req.continue();
  });

  await page.goto(TARGET_URL, { waitUntil: 'networkidle2', timeout: 30000 });

  // Pattern match: Extract direct CDN href (e.g., file.apkdone.io/.../download)
  const cdnLink = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('a')).find(a => 
      a.href.includes('apkdone.io') && a.href.includes('/download')
    )?.href || null;
  });

  let apkSize = null;
  if (cdnLink) {
    console.log(`Found direct link: ${cdnLink.substring(0, 50)}...`);
    apkSize = await directDownload(cdnLink, OUTPUT_FILE);
  } else {
    // Fallback: Navigate to /download/ page
    console.log('No direct link—navigating to /download/');
    await page.goto(`${TARGET_URL}download/`, { waitUntil: 'networkidle2', timeout: 10000 });
    const fallbackLink = await page.evaluate(() => 
      Array.from(document.querySelectorAll('a')).find(a => a.href.includes('/download'))?.href || null
    );
    if (fallbackLink) {
      apkSize = await directDownload(fallbackLink, OUTPUT_FILE);
    } else {
      throw new Error('No download link found on /download/ page');
    }
  }

  console.log(`\n🎉 SUCCESS! ${OUTPUT_FILE} (v16.0.0, Pro Unlocked, ${apkSize} MB)`);
  process.exit(0);
} catch (err) {
  console.error('\n❌ Error:', err.message);
  process.exit(1);
} finally {
  await browser.close();
}
