// .github/scripts/apk_hunter.js
// Fully compatible with your mirror_config.json (array format)

const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
const getArg = (key) => {
  const i = args.indexOf(`--${key}`);
  return i !== -1 ? args[i + 1] : null;
};

const APP_ID = getArg('id');
const PROVIDED_URL = getArg('url');
const OUTPUT_FILE = getArg('out') || `${APP_ID || 'app'}.apk`;
const MAX_WAIT = parseInt(getArg('wait') || '90000', 10);

if (!APP_ID) {
  console.error('Error: --id <app_id> is required');
  process.exit(1);
}

let TARGET_URL = PROVIDED_URL;
let MODE = 'scrape'; // default

// Auto-load from mirror_config.json if no --url provided
if (!TARGET_URL) {
  try {
    const configPath = path.resolve(__dirname, '../mirror_config.json');
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    
    const app = config.find(item => item.id === APP_ID);
    if (!app) {
      console.error(`Error: No entry found for id "${APP_ID}" in mirror_config.json`);
      process.exit(1);
    }

    TARGET_URL = app.downloadUrl;
    MODE = app.mode || 'scrape';
    console.log(`Loaded from mirror_config.json → ${app.name}`);
    console.log(`URL: ${TARGET_URL}`);
    console.log(`Mode: ${MODE}\n`);
  } catch (err) {
    console.error('Failed to read mirror_config.json:', err.message);
    process.exit(1);
  }
}

// DIRECT MODE: Just download straight from URL
if (MODE === 'direct') {
  console.log('Direct download mode...');
  const response = await fetch(TARGET_URL);
  const buffer = Buffer.from(await response.arrayBuffer());
  fs.writeFileSync(OUTPUT_FILE, buffer);
  console.log(`Downloaded → ${OUTPUT_FILE} (${(buffer.length / 1024 / 1024).toFixed(1)} MB)`);
  process.exit(0);
}

// SCRAPE MODE (APKDone, HappyMod, etc.)
console.log(`Starting Puppeteer scrape for: ${APP_ID}`);
console.log(`Target: ${TARGET_URL}\n`);

const DOWNLOAD_PATH = path.resolve(__dirname, '../downloads');
if (!fs.existsSync(DOWNLOAD_PATH)) fs.mkdirSync(DOWNLOAD_PATH, { recursive: true });

const browser = await puppeteer.launch({
  headless: "new",
  args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1280,800']
});

const page = await browser.newPage();

// Enable downloads
const client = await page.target().createCDPSession();
await client.send('Page.setDownloadBehavior', {
  behavior: 'allow',
  downloadPath: DOWNLOAD_PATH,
});

// Block images/fonts/ads for speed & stealth
await page.setRequestInterception(true);
page.on('request', (req) => {
  const type = req.resourceType();
  if (['image', 'media', 'font', 'stylesheet'].includes(type)) {
    req.abort().catch(() => {});
  } else {
    req.continue().catch(() => {});
  }
});

await page.goto(TARGET_URL, { waitUntil: 'networkidle2', timeout: 30000 });

// Step 1: Click first "Download" button
await page.evaluate(() => {
  const btn = Array.from(document.querySelectorAll('a, button, div'))
    .find(el => /download/i.test(el.innerText) && el.offsetHeight > 0);
  if (btn) btn.click();
});

// Wait & switch to new tab (APKDone opens new tab)
await page.waitForTimeout(4000);
const pages = await browser.pages();
const detailPage = pages[pages.length - 1];
await detailPage.bringToFront();

// Scroll down to reveal final button
await detailPage.evaluate(() => window.scrollBy(0, 1200));
await detailPage.waitForTimeout(1500);

// Step 2: Click final download button (with size in MB)
await detailPage.evaluate(() => {
  const btn = Array.from(document.querySelectorAll('a, button, div'))
    .find(el => {
      const text = el.innerText.toLowerCase();
      return (
        (text.includes('download apk') && text.includes('mb')) ||
        text.includes('fast download') ||
        text.includes('download mod')
      );
    });
  if (btn) btn.click();
});

console.log('Download started... waiting for file');

// Wait for real APK (>50 MB)
const start = Date.now();
let apkFile = null;
while (Date.now() - start < MAX_WAIT) {
  const files = fs.readdirSync(DOWNLOAD_PATH);
  apkFile = files.find(f => f.endsWith('.apk') && !f.includes('.crdownload'));
  if (apkFile) {
    const stats = fs.statSync(path.join(DOWNLOAD_PATH, apkFile));
    if (stats.size > 50 * 1024 * 1024) {
      console.log(`\nDownloaded: ${apkFile} (${(stats.size / 1024 / 1024).toFixed(1)} MB)`);
      break;
    }
  }
  process.stdout.write('.');
  await new Promise(r => setTimeout(r, 2000));
}

await browser.close();

if (apkFile) {
  const finalPath = path.resolve(__dirname, '../', OUTPUT_FILE);
  fs.renameSync(path.join(DOWNLOAD_PATH, apkFile), finalPath);
  console.log(`\nSUCCESS! Saved as → ${OUTPUT_FILE}`);
  process.exit(0);
} else {
  console.error('\nFailed — no valid APK downloaded');
  process.exit(1);
}
